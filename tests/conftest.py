"""테스트 공용 — 인메모리 FakeCouch (실 CouchDB는 test_couch_integration.py에서만)."""

from __future__ import annotations

import copy

import pytest
from cryptography.fernet import Fernet

from hyeseongkit.hub.couch import CouchConflict
from hyeseongkit.hub.crypto import BodyCrypto


class FakeCouch:
    """CouchClient와 동일 시그니처의 인메모리 구현 — 등호 selector만 지원."""

    def __init__(self):
        self.dbs: dict[str, dict[str, dict]] = {}

    async def close(self) -> None:
        pass

    async def ping(self) -> bool:
        return True

    async def head(self, db: str) -> None:  # pragma: no cover — ensure_db 내부 경로용
        pass

    async def ensure_db(self, db: str) -> None:
        self.dbs.setdefault(db, {})

    async def ensure_index(self, db: str, fields, name: str) -> None:
        self.dbs.setdefault(db, {})

    async def get(self, db: str, doc_id: str) -> dict | None:
        doc = self.dbs.get(db, {}).get(doc_id)
        return copy.deepcopy(doc) if doc else None

    async def put(self, db: str, doc: dict) -> str:
        docs = self.dbs.setdefault(db, {})
        existing = docs.get(doc["_id"])
        if existing:
            if existing.get("_rev") != doc.get("_rev"):
                raise CouchConflict(doc["_id"])
            rev_n = int(existing["_rev"].split("-")[0]) + 1
        else:
            if doc.get("_rev"):
                raise CouchConflict(doc["_id"])
            rev_n = 1
        stored = copy.deepcopy(doc)
        stored["_rev"] = f"{rev_n}-fake"
        docs[doc["_id"]] = stored
        return stored["_rev"]

    async def find(self, db: str, selector: dict, *, limit: int = 100, fields=None) -> list[dict]:
        out = []
        for doc in self.dbs.get(db, {}).values():
            if all(doc.get(k) == v for k, v in selector.items()):
                d = copy.deepcopy(doc)
                if fields:
                    d = {k: d.get(k) for k in fields}
                out.append(d)
            if len(out) >= limit:
                break
        return out

    async def all_docs_prefix(self, db: str, prefix: str) -> list[dict]:
        return [
            copy.deepcopy(doc)
            for doc_id, doc in sorted(self.dbs.get(db, {}).items())
            if doc_id.startswith(prefix)
        ]

    async def changes(self, db: str, *, since: str = "0", heartbeat_ms: int = 30000):
        return
        yield  # pragma: no cover — 렌더러는 테스트에서 비활성


@pytest.fixture
def fake_couch() -> FakeCouch:
    return FakeCouch()


@pytest.fixture
def crypto() -> BodyCrypto:
    return BodyCrypto(Fernet.generate_key().decode())
