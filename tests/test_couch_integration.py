"""실 CouchDB 통합 (CI service 컨테이너, §14-3) — HK_COUCHDB_URL 없으면 skip."""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from cryptography.fernet import Fernet

from hyeseongkit.hub.couch import CouchClient
from hyeseongkit.hub.crypto import BodyCrypto
from hyeseongkit.hub.store import EventStore

pytestmark = pytest.mark.skipif(
    not os.environ.get("HK_COUCHDB_URL"), reason="HK_COUCHDB_URL 미설정 — CI에서만 실행"
)


def test_store_roundtrip_real_couchdb():
    async def _run():
        db = f"hk_test_{uuid.uuid4().hex[:8]}"
        couch = CouchClient(
            os.environ["HK_COUCHDB_URL"],
            os.environ.get("HK_COUCHDB_USER", ""),
            os.environ.get("HK_COUCHDB_PASSWORD", ""),
        )
        try:
            assert await couch.ping()
            await couch.ensure_db(db)
            await couch.ensure_index(db, ["kind", "thread", "ts"], "idx-evt-thread-ts")
            store = EventStore(couch, BodyCrypto(Fernet.generate_key().decode()), db)
            evt = {
                "type": "push",
                "thread": "T-20260811-int-test",
                "project_id": "p-int",
                "ts": "2026-08-11T02:31:00Z",
                "tool": "claude-code",
                "model": None,
                "device": "ci",
                "sensitivity": "tech",
                "masked": True,
                "mask_report": [],
                "title": "통합 테스트",
                "sections": {"todo": "1. 검증", "know": "- 포트 9100"},
            }
            evt_id = await store.append(evt)
            assert evt_id.startswith("evt:T-20260811-int-test:")
            # 같은 초 재-append → :2 접미사 (§2-2)
            evt_id2 = await store.append(dict(evt))
            assert evt_id2 == evt_id + ":2"
            events = await store.load_events("T-20260811-int-test")
            assert len(events) == 2
            assert events[0]["sections"]["know"] == "- 포트 9100"  # 복호화 확인
            view = await store.refresh_view("T-20260811-int-test")
            assert view["title"] == "통합 테스트"
            got = await store.get_view("T-20260811-int-test")
            assert got["sections"]["todo"] == "1. 검증"
            # 저장 문서에는 평문이 없다 (D29)
            raw = await couch.get(db, evt_id)
            assert "포트 9100" not in str(raw)
        finally:
            await couch.close()

    asyncio.run(_run())
