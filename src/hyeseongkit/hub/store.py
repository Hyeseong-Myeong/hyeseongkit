"""저장 계층 (설계서 §2-7, D5) — 구현체는 EventStore 하나만. document 모드는 미구현.

문서 스키마 (D29 암호화 적용):
- evt:  평문 메타데이터 + `enc` 블록(push: {title, sections} / decide: {decision})
- view: 평문 메타데이터 + `enc` 블록({title, sections, know_carryover, decisions})
"""

from __future__ import annotations

from typing import Protocol

from .couch import CouchClient, CouchConflict
from .crypto import BodyCrypto
from .fold import fold_events

SCHEMA = "hyeseongkit/session@1"

# type별 암호화 대상 본문 필드 (D29)
_EVT_BODY_FIELDS: dict[str, tuple[str, ...]] = {
    "push": ("title", "sections"),
    "decide": ("decision",),
    "checkpoint": (),
    "close": (),
}
_VIEW_BODY_FIELDS = ("title", "sections", "know_carryover", "decisions")


class SessionStore(Protocol):
    async def append(self, evt: dict) -> str: ...

    async def load_events(self, thread: str) -> list[dict]: ...

    async def get_view(self, thread: str) -> dict | None: ...

    async def put_view(self, view: dict) -> None: ...

    async def find_views(
        self, project_id: str | None, status: str | None, limit: int
    ) -> list[dict]: ...


class EventStore:
    def __init__(self, couch: CouchClient, crypto: BodyCrypto, db: str):
        self._couch = couch
        self._crypto = crypto
        self._db = db

    # ── 이벤트 ───────────────────────────────────────────────

    async def append(self, evt: dict) -> str:
        """평문 evt(dict)를 받아 본문 필드를 암호화해 append. 반환: _id (§2-2)."""
        from ..core.util import iso_to_compact

        evt = dict(evt)
        body_fields = _EVT_BODY_FIELDS.get(evt.get("type", ""), ())
        body = {k: evt.pop(k) for k in list(body_fields) if k in evt}
        base_id = f"evt:{evt['thread']}:{iso_to_compact(evt['ts'])}:{evt['device']}:{evt['type']}"
        doc = {**evt, "kind": "evt", "schema": SCHEMA}
        if body:
            doc["enc"] = self._crypto.seal(body)
        for n in range(1, 10):  # 같은 초 충돌 시 :2, :3 접미사 (§2-2)
            doc["_id"] = base_id if n == 1 else f"{base_id}:{n}"
            try:
                await self._couch.put(self._db, doc)
                return doc["_id"]
            except CouchConflict:
                continue
        raise CouchConflict(base_id)

    def _open_evt(self, doc: dict) -> dict:
        d = dict(doc)
        enc = d.pop("enc", None)
        if enc:
            d.update(self._crypto.open(enc))
        return d

    async def load_events(self, thread: str) -> list[dict]:
        rows = await self._couch.find(self._db, {"kind": "evt", "thread": thread}, limit=1000)
        rows.sort(key=lambda r: (r.get("ts", ""), r.get("ord", 0), r.get("_id", "")))
        return [self._open_evt(r) for r in rows]

    async def thread_exists(self, thread: str) -> bool:
        rows = await self._couch.find(
            self._db, {"kind": "evt", "thread": thread}, limit=1, fields=["_id"]
        )
        return bool(rows)

    # ── 뷰 ──────────────────────────────────────────────────

    def _open_view(self, doc: dict) -> dict:
        d = dict(doc)
        enc = d.pop("enc", None)
        if enc:
            d.update(self._crypto.open(enc))
        return d

    async def get_view(self, thread: str) -> dict | None:
        doc = await self._couch.get(self._db, f"view:{thread}")
        return self._open_view(doc) if doc else None

    async def put_view(self, view: dict) -> None:
        """평문 view 본문을 암호화 저장. 재생성 가능 문서라 충돌 시 rev 재취득 후 1회 재시도."""
        body = {k: view.get(k) for k in _VIEW_BODY_FIELDS}
        meta = {k: v for k, v in view.items() if k not in _VIEW_BODY_FIELDS and k not in ("_rev",)}
        doc_id = f"view:{view['thread']}"
        doc = {**meta, "_id": doc_id, "kind": "view", "enc": self._crypto.seal(body)}
        for _ in range(2):
            existing = await self._couch.get(self._db, doc_id)
            if existing:
                doc["_rev"] = existing["_rev"]
            else:
                doc.pop("_rev", None)
            try:
                await self._couch.put(self._db, doc)
                return
            except CouchConflict:
                continue
        raise CouchConflict(doc_id)

    async def find_views(
        self,
        project_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """updated 역순. 정렬은 허브 메모리에서 수행 (문서 수 소규모 전제, §3-9)."""
        selector: dict = {"kind": "view"}
        if project_id:
            selector["project_id"] = project_id
        if status:
            selector["status"] = status
        rows = await self._couch.find(self._db, selector, limit=max(limit, 200))
        rows.sort(key=lambda r: r.get("updated", ""), reverse=True)
        return [self._open_view(r) for r in rows[:limit]]

    async def refresh_view(self, thread: str) -> dict | None:
        """이벤트에서 뷰를 재계산해 저장 — 렌더러·resume 폴백 공용 (§8-1)."""
        events = await self.load_events(thread)
        view = fold_events(events)
        if view is None:
            return None
        await self.put_view(view)
        return view
