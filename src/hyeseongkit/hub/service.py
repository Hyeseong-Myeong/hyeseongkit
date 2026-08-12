"""세션 스킬 비즈니스 로직 — CLI/MCP/HTTP 세 표면이 호출하는 단일 코어 (K1~K3).

push 처리 순서 (§3-3): ① 토큰 검증(라우트) → ② device 일치 → ③ 서버측 마스킹 재검사
→ ④ 스키마 검증 → ⑤ 새 스레드면 D14 검사 → ⑥ evt append → ⑦ 201 (렌더는 비동기)
"""

from __future__ import annotations

import logging
import time

from .. import __version__
from ..core import redact
from ..core.util import (
    REQUIRED_SECTIONS,
    SECTION_KEYS,
    SENSITIVITIES,
    ascii_slug,
    iso_utc,
    kst_date_compact,
    random_slug,
)
from .errors import ApiError
from .store import EventStore

MAX_ACTIVE_THREADS = 3  # D14
SEARCH_SCAN_LIMIT = 200  # §3-9

log = logging.getLogger("hyeseongkit.service")


class SessionService:
    def __init__(self, store: EventStore, couch, sessions_db: str):
        self._store = store
        self._couch = couch
        self._db = sessions_db

    @property
    def store(self) -> EventStore:
        return self._store

    # ── 내부 헬퍼 ────────────────────────────────────────────

    @staticmethod
    def _check_device(device_doc: dict, claimed: str) -> None:
        if claimed != device_doc.get("device_id"):
            raise ApiError(
                400, "DEVICE_MISMATCH", "요청 device가 토큰의 device_id와 다릅니다 (§5-4)"
            )

    @staticmethod
    def _server_redaction_check(obj: object) -> None:
        """§6-4 — 원시 시크릿 발견 시 저장 거부. 값은 응답에 싣지 않는다."""
        rules = redact.detect_obj(obj)
        if rules:
            raise ApiError(
                422,
                "REDACTION_REQUIRED",
                "본문에서 원시 시크릿이 탐지되어 저장을 거부했습니다",
                {"rules": rules},
            )

    async def _append_and_fold(self, evt: dict) -> str:
        """evt append 후 뷰를 즉시 fold — resume·D14가 렌더러 디바운스(2초)에
        의존하지 않게 한다. 마크다운 렌더는 여전히 _changes 구독으로 비동기 (§3-3 ⑦).
        뷰 갱신 실패는 이벤트(SSOT)에 영향 없음 — 렌더러가 재계산한다.
        """
        evt = {**evt, "ord": time.time_ns()}  # 같은 초 이벤트의 순서 보장 (fold 정렬 키)
        event_id = await self._store.append(evt)
        try:
            await self._store.refresh_view(evt["thread"])
        except Exception as exc:  # noqa: BLE001
            log.warning("뷰 즉시 갱신 실패(%s) — 렌더러가 재계산: %s", evt["thread"], exc)
        return event_id

    async def _thread_status(self, thread: str) -> str | None:
        """이벤트 기준 상태 — 뷰의 비동기 지연과 무관하게 정확."""
        events = await self._store.load_events(thread)
        if not events:
            return None
        status = "active"
        for e in events:
            if e.get("type") == "close":
                status = e.get("outcome", "done")
            elif e.get("type") == "push":
                status = "active"
        return status

    # ── push (§3-3) ─────────────────────────────────────────

    async def push(self, device_doc: dict, p: dict) -> dict:
        self._check_device(device_doc, p.get("device", ""))
        title = (p.get("title") or "").strip()
        sections = p.get("sections") or {}
        self._server_redaction_check({"title": title, "sections": sections, "slug": p.get("slug")})

        if not title:
            raise ApiError(400, "SCHEMA_INVALID", "title은 필수입니다")
        unknown = set(sections) - set(SECTION_KEYS)
        if unknown:
            raise ApiError(400, "SCHEMA_INVALID", f"sections 키 오탈자: {sorted(unknown)}")
        for key in REQUIRED_SECTIONS:  # todo·know 필수 — L0 보호
            if not (sections.get(key) or "").strip():
                raise ApiError(400, "SCHEMA_INVALID", f"sections.{key}는 필수입니다 (L0 보호)")
        sensitivity = p.get("sensitivity") or "tech"
        if sensitivity not in SENSITIVITIES:
            raise ApiError(400, "SCHEMA_INVALID", f"sensitivity 오류: {sensitivity}")

        thread = p.get("thread")
        created_thread = False
        if thread:
            status = await self._thread_status(thread)
            if status is None:
                raise ApiError(404, "THREAD_NOT_FOUND", f"스레드 없음: {thread}")
            if status != "active" and not p.get("reopen"):
                raise ApiError(
                    409,
                    "THREAD_CLOSED",
                    "close된 스레드 — 재개는 reopen:true로 명시 (§3-3)",
                    {"thread": thread, "status": status},
                )
        else:
            thread = await self._new_thread_id(p, title)
            await self._check_thread_limit(p.get("project_id", ""))
            created_thread = True

        evt = {
            "type": "push",
            "thread": thread,
            "project_id": p.get("project_id", ""),
            "ts": iso_utc(),
            "tool": p.get("tool") or "manual",
            "model": p.get("model"),
            "device": p["device"],
            "sensitivity": sensitivity,
            "masked": True,
            "mask_report": list(p.get("mask_report") or []),
            "title": title,
            "sections": {k: sections.get(k, "") for k in SECTION_KEYS if k in sections},
        }
        if p.get("reopen"):
            evt["reopen"] = True
        event_id = await self._append_and_fold(evt)
        return {"thread": thread, "event_id": event_id, "created_thread": created_thread}

    async def _new_thread_id(self, p: dict, title: str) -> str:
        """T-<YYYYMMDD(KST)>-<slug> (§3-3).

        slug 우선순위: 요청 slug(작업 주제 영문 요약, 사용자 결정 2026-08-12)
        → title의 ASCII 변환 → t-<랜덤4hex>.
        같은 날 같은 slug 충돌 시 409 — 이어갈지, 주제를 구체화해 재시도할지 클라이언트가 결정.
        """
        slug = ascii_slug(p.get("slug") or "") or ascii_slug(title) or random_slug()
        thread = f"T-{kst_date_compact()}-{slug}"
        if await self._store.thread_exists(thread):
            existing = await self._store.get_view(thread)
            detail = {
                "thread": thread,
                "existing_title": (existing or {}).get("title", ""),
                "existing_status": (existing or {}).get("status", ""),
                "hint": "같은 작업이면 thread를 지정해 push, 새 작업이면 slug에 "
                "주제를 더 구체적으로 요약해 재시도",
            }
            raise ApiError(409, "THREAD_EXISTS", "같은 날짜·주제의 스레드가 이미 있습니다", detail)
        return thread

    async def _check_thread_limit(self, project_id: str) -> None:
        active = await self._store.find_views(project_id or None, "active", limit=10)
        if len(active) >= MAX_ACTIVE_THREADS:
            raise ApiError(
                409,
                "THREAD_LIMIT",
                f"프로젝트당 동시 활성 스레드 {MAX_ACTIVE_THREADS}개 초과 (D14)",
                {
                    "active_threads": [
                        {
                            "thread": v["thread"],
                            "title": v.get("title", ""),
                            "updated": v.get("updated", ""),
                        }
                        for v in active
                    ]
                },
            )

    # ── decide (§3-4) ───────────────────────────────────────

    async def decide(self, device_doc: dict, p: dict) -> dict:
        self._check_device(device_doc, p.get("device", ""))
        decision = p.get("decision") or {}
        if not (decision.get("text") or "").strip():
            raise ApiError(400, "SCHEMA_INVALID", "decision.text는 필수입니다")
        self._server_redaction_check(decision)
        thread = p.get("thread") or ""
        if await self._thread_status(thread) is None:
            raise ApiError(404, "THREAD_NOT_FOUND", f"스레드 없음: {thread}")
        evt = {
            "type": "decide",
            "thread": thread,
            "project_id": p.get("project_id", ""),
            "ts": iso_utc(),
            "tool": p.get("tool") or "manual",
            "model": p.get("model"),
            "device": p["device"],
            "sensitivity": p.get("sensitivity") or "tech",
            "masked": True,
            "mask_report": list(p.get("mask_report") or []),
            "decision": {
                "text": decision["text"],
                "rationale": decision.get("rationale"),
                "rejected": decision.get("rejected"),
                "date": decision.get("date"),
            },
        }
        event_id = await self._append_and_fold(evt)
        return {"event_id": event_id}

    # ── checkpoint / close (§3-5) ───────────────────────────

    async def checkpoint(self, device_doc: dict, p: dict) -> dict | None:
        """thread=null이면 프로젝트 최신 active에 붙인다. active 없으면 None(→204)."""
        self._check_device(device_doc, p.get("device", ""))
        thread = p.get("thread")
        if thread:
            if await self._thread_status(thread) is None:
                raise ApiError(404, "THREAD_NOT_FOUND", f"스레드 없음: {thread}")
        else:
            views = await self._store.find_views(p.get("project_id") or None, "active", limit=1)
            if not views:
                return None
            thread = views[0]["thread"]
        self._server_redaction_check(p.get("git") or {})
        evt = {
            "type": "checkpoint",
            "thread": thread,
            "project_id": p.get("project_id", ""),
            "ts": iso_utc(),
            "tool": p.get("tool") or "manual",
            "model": p.get("model"),
            "device": p["device"],
            "sensitivity": p.get("sensitivity") or "tech",
            "masked": True,
            "mask_report": [],
            "checkpoint": {
                "reason": p.get("reason") or "manual",
                "git": p.get("git"),
                "transcript_path": p.get("transcript_path"),
            },
        }
        event_id = await self._append_and_fold(evt)
        return {"thread": thread, "event_id": event_id}

    async def close(self, device_doc: dict, p: dict) -> dict:
        self._check_device(device_doc, p.get("device", ""))
        thread = p.get("thread") or ""
        outcome = p.get("outcome") or "done"
        if outcome not in ("done", "dropped"):
            raise ApiError(400, "SCHEMA_INVALID", f"outcome 오류: {outcome}")
        if await self._thread_status(thread) is None:
            raise ApiError(404, "THREAD_NOT_FOUND", f"스레드 없음: {thread}")
        evt = {
            "type": "close",
            "thread": thread,
            "project_id": p.get("project_id", ""),
            "ts": iso_utc(),
            "tool": p.get("tool") or "manual",
            "model": p.get("model"),
            "device": p["device"],
            "sensitivity": p.get("sensitivity") or "tech",
            "masked": True,
            "mask_report": [],
            "outcome": outcome,
        }
        event_id = await self._append_and_fold(evt)
        return {"thread": thread, "status": outcome, "event_id": event_id}

    # ── resume (§3-6) ───────────────────────────────────────

    async def resume(
        self,
        *,
        thread: str | None = None,
        last: bool = False,
        project_id: str | None = None,
        budget: int = 2000,
        fmt: str = "packet",
        events: int = 0,
    ) -> dict:
        from .packet import build_packet, build_prompt

        if fmt not in ("packet", "prompt", "json"):
            raise ApiError(400, "SCHEMA_INVALID", f"format 오류: {fmt}")
        if not thread:
            if not last:
                raise ApiError(400, "SCHEMA_INVALID", "thread 또는 last=1이 필요합니다")
            if not project_id:
                raise ApiError(400, "SCHEMA_INVALID", "last=1에는 project_id가 필요합니다")
            views = await self._store.find_views(project_id, "active", limit=1)
            if not views:
                raise ApiError(404, "NO_ACTIVE_THREAD", "활성 스레드가 없습니다")
            thread = views[0]["thread"]
        # 항상 이벤트에서 재계산 — 최신성 보장 (뷰는 캐시)
        view = await self._store.refresh_view(thread)
        if view is None:
            raise ApiError(404, "THREAD_NOT_FOUND", f"스레드 없음: {thread}")

        if fmt == "json":
            return {"format": "json", "view": view}

        others = [
            v
            for v in await self._store.find_views(
                view.get("project_id") or None, "active", limit=MAX_ACTIVE_THREADS
            )
            if v["thread"] != thread
        ]
        events_raw: list[dict] | None = None
        if events > 0:
            all_events = await self._store.load_events(thread)
            events_raw = all_events[-events:]
        project_name = await self._project_name(view.get("project_id", ""))
        packet = build_packet(
            view,
            project_name=project_name,
            other_active=others,
            budget=budget,
            events_raw=events_raw,
        )
        if fmt == "prompt":
            return {"format": "prompt", "content": build_prompt(packet), "thread": thread}
        return {"format": "packet", "content": packet, "thread": thread}

    async def _project_name(self, project_id: str) -> str:
        if not project_id:
            return ""
        doc = await self._couch.get(self._db, f"proj:{project_id}")
        return (doc or {}).get("name") or project_id

    # ── status / search (§3-8, §3-9) ────────────────────────

    async def status(self, project_id: str | None = None) -> dict:
        couch_ok = await self._couch.ping()
        views = await self._store.find_views(project_id, "active", limit=50)
        return {
            "hub": {"version": __version__, "couchdb": "ok" if couch_ok else "down"},
            "threads": [
                {
                    "thread": v["thread"],
                    "title": v.get("title", ""),
                    "status": v.get("status", ""),
                    "project_id": v.get("project_id", ""),
                    "updated": v.get("updated", ""),
                    "last_tool": v.get("last_tool", ""),
                    "last_device": v.get("last_device", ""),
                    "events": v.get("events", 0),
                }
                for v in views
            ],
        }

    async def search(
        self,
        q: str,
        *,
        project_id: str | None = None,
        status: str | None = None,
        limit: int = 10,
    ) -> dict:
        if not q:
            raise ApiError(400, "SCHEMA_INVALID", "q는 필수입니다")
        limit = max(1, min(limit, 50))
        views = await self._store.find_views(project_id, status, limit=SEARCH_SCAN_LIMIT + 1)
        truncated = len(views) > SEARCH_SCAN_LIMIT
        needle = q.lower()
        matches = []
        for v in views[:SEARCH_SCAN_LIMIT]:
            hay = " ".join(
                [
                    v.get("title", ""),
                    " ".join(v.get("tags", [])),
                    " ".join((v.get("sections") or {}).values()),
                ]
            ).lower()
            if needle in hay:
                matches.append(
                    {
                        "thread": v["thread"],
                        "title": v.get("title", ""),
                        "status": v.get("status", ""),
                        "project_id": v.get("project_id", ""),
                        "updated": v.get("updated", ""),
                    }
                )
            if len(matches) >= limit:
                break
        return {"matches": matches, "truncated": truncated}

    # ── 프로젝트 (§3-10, §7) ─────────────────────────────────

    async def project_register(self, p: dict) -> tuple[dict, bool]:
        """idempotent — 같은 project_id 재등록 시 기존 문서 유지·반환 (200)."""
        project_id = p.get("project_id") or ""
        if not project_id:
            raise ApiError(400, "SCHEMA_INVALID", "project_id는 필수입니다")
        doc_id = f"proj:{project_id}"
        existing = await self._couch.get(self._db, doc_id)
        if existing:
            return existing, False
        doc = {
            "_id": doc_id,
            "kind": "proj",
            "project_id": project_id,
            "canonical": p.get("canonical", ""),
            "aliases": [],
            "name": p.get("name", ""),
            "sensitivity": p.get("sensitivity", "tech"),
            "created": iso_utc(),
        }
        await self._couch.put(self._db, doc)
        return doc, True

    async def project_get(self, project_id: str) -> dict:
        doc = await self._couch.get(self._db, f"proj:{project_id}")
        if not doc:
            raise ApiError(404, "PROJECT_NOT_FOUND", f"프로젝트 없음: {project_id}")
        return doc

    async def project_list(self) -> list[dict]:
        docs = await self._couch.all_docs_prefix(self._db, "proj:")
        return sorted((d for d in docs if d.get("kind") == "proj"), key=lambda d: d.get("name", ""))

    async def project_find(
        self, canonical: str | None = None, name: str | None = None
    ) -> list[dict]:
        """canonical 또는 aliases 일치 (C1) / name 일치(오분기 방지 가드, §7)."""
        docs = await self.project_list()
        if canonical:
            c = canonical.lower()
            return [d for d in docs if d.get("canonical") == c or c in d.get("aliases", [])]
        if name:
            return [d for d in docs if d.get("name") == name]
        return docs

    async def project_add_alias(self, project_id: str, canonical: str) -> dict:
        if not canonical:
            raise ApiError(400, "SCHEMA_INVALID", "canonical은 필수입니다")
        doc = await self.project_get(project_id)
        c = canonical.lower()
        if c != doc.get("canonical") and c not in doc.get("aliases", []):
            doc.setdefault("aliases", []).append(c)
            await self._couch.put(self._db, doc)
        return doc
