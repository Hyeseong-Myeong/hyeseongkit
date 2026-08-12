"""렌더러 (설계서 §8-1, §8-2) — 허브 내부 단일 asyncio 태스크 (제약 L1·L3).

CouchDB `_changes` continuous 구독 → evt 변경 감지 → 스레드별 2초 디바운스
→ fold → view 저장 → /vault-out/sessions/<thread>.md 원자적 쓰기 → HOME.md 재생성
→ seq 체크포인트(/data/render.seq).

- 스트림 단절 시 지수 백오프(1초→최대 60초) 자동 재연결 (§8-1)
- close 후 15일 지난 스레드는 sessions/archive/<YYYY>/ 이동 (D12, 일 1회)
- 파일명 = thread ID 그대로(ASCII 보장), 인코딩 UTF-8 BOM 없음·LF (R7)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import timedelta
from pathlib import Path

from ..core.util import kst_human, kst_minute, parse_iso, utc_now
from .couch import CouchClient, CouchDBDown
from .store import EventStore

log = logging.getLogger("hyeseongkit.render")

ARCHIVE_AFTER_DAYS = 15  # D12
DEBOUNCE_SECONDS = 2.0
WARNING_HEADER = (
    "> ⚠️ 이 파일은 hyeseongkit이 생성한 **열람용 뷰**입니다.\n"
    "> 직접 수정해도 다음 렌더에서 덮어써집니다. 수정은 `hk push`로 하세요.\n"
)


def _yaml_str(v: str) -> str:
    return json.dumps(v or "", ensure_ascii=False)


def render_markdown(view: dict, events: list[dict], project_name: str) -> str:
    """§8-2 렌더 파일 형식."""
    sections = view.get("sections") or {}
    fm = (
        "---\n"
        "kit: hyeseongkit/session\n"
        "v: 1\n"
        f"thread: {view['thread']}\n"
        f"title: {_yaml_str(view.get('title', ''))}\n"
        f"status: {view.get('status', '')}\n"
        f"sensitivity: {view.get('sensitivity', '')}\n"
        f"project: {_yaml_str(project_name)}\n"
        f"created: {kst_minute(view.get('created', ''))}\n"
        f"updated: {kst_minute(view.get('updated', ''))}\n"
        f"last_tool: {view.get('last_tool', '')}\n"
        f"last_device: {view.get('last_device', '')}\n"
        f"events: {view.get('events', 0)}\n"
        f"tags: {json.dumps(view.get('tags', []), ensure_ascii=False)}\n"
        "---\n"
    )
    decisions = view.get("decisions") or []
    dec_lines = []
    for d in decisions:
        parts = [d.get("date", ""), d.get("text", "")]
        if d.get("rationale"):
            parts.append(f"근거: {d['rationale']}")
        if d.get("rejected"):
            parts.append(f"기각: {d['rejected']}")
        dec_lines.append("- " + " | ".join(p for p in parts if p))
    know_block = "## 4. 알아야 할 것\n"
    if dec_lines:
        know_block += "### 4-1. 결정\n" + "\n".join(dec_lines) + "\n\n"
    know_block += (sections.get("know") or "").strip() + "\n"
    carry = view.get("know_carryover") or []
    if carry:
        know_block += "\n### 4-N. 이월 (자동 보존)\n" + "\n".join(carry) + "\n"
    evt_lines = [
        f"- {e.get('_id', '')} — {e.get('type', '')} ({e.get('tool', '')}@{e.get('device', '')})"
        for e in events
    ]
    return (
        fm
        + "\n"
        + WARNING_HEADER
        + "\n## 1. 컨텍스트\n"
        + (sections.get("context") or "").strip()
        + "\n\n## 2. 한 일\n"
        + (sections.get("done") or "").strip()
        + "\n\n## 3. 할 일\n"
        + (sections.get("todo") or "").strip()
        + "\n\n"
        + know_block
        + "\n## 5. 미결 질문\n"
        + (sections.get("questions") or "").strip()
        + "\n\n## 6. 이벤트 로그\n"
        + "\n".join(evt_lines)
        + "\n"
    )


def atomic_write(path: Path, text: str) -> None:
    """같은 디렉터리에 .tmp를 쓰고 os.replace (§8-1)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


class Renderer:
    def __init__(
        self,
        couch: CouchClient,
        store: EventStore,
        *,
        sessions_db: str,
        vault_out: str,
        data_dir: str,
    ):
        self._couch = couch
        self._store = store
        self._db = sessions_db
        self._vault = Path(vault_out)
        self._seq_file = Path(data_dir) / "render.seq"
        self._pending: dict[str, asyncio.Task] = {}
        self._last_seq: str | None = None

    # ── seq 체크포인트 ───────────────────────────────────────

    def _read_seq(self) -> str:
        try:
            return self._seq_file.read_text(encoding="utf-8").strip() or "0"
        except OSError:
            return "0"

    def _write_seq(self) -> None:
        if self._last_seq is None:
            return
        try:
            self._seq_file.parent.mkdir(parents=True, exist_ok=True)
            self._seq_file.write_text(str(self._last_seq), encoding="utf-8")
        except OSError as exc:
            log.warning("seq 체크포인트 실패: %s", exc)

    # ── 메인 루프 ────────────────────────────────────────────

    async def run(self) -> None:
        backoff = 1.0
        while True:
            try:
                since = self._read_seq()
                async for change in self._couch.changes(self._db, since=since):
                    backoff = 1.0
                    seq = change.get("seq")
                    if seq:
                        self._last_seq = seq
                    doc_id = change.get("id", "")
                    if doc_id.startswith("evt:"):
                        thread = doc_id.split(":", 2)[1]
                        self._schedule(thread)
            except asyncio.CancelledError:
                raise
            except (CouchDBDown, OSError) as exc:
                log.warning("_changes 단절: %s — %.0f초 후 재연결", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)  # 지수 백오프 (§8-1)

    def _schedule(self, thread: str) -> None:
        prev = self._pending.get(thread)
        if prev and not prev.done():
            prev.cancel()
        self._pending[thread] = asyncio.create_task(self._debounced(thread))

    async def _debounced(self, thread: str) -> None:
        try:
            await asyncio.sleep(DEBOUNCE_SECONDS)
            await self.refresh(thread)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001 — 렌더 실패가 허브를 죽이면 안 된다
            log.error("렌더 실패 (%s): %s", thread, exc)

    # ── 렌더 ────────────────────────────────────────────────

    async def refresh(self, thread: str) -> None:
        events = await self._store.load_events(thread)
        if not events:
            return
        from .fold import fold_events

        view = fold_events(events)
        if view is None:
            return
        await self._store.put_view(view)
        project_name = await self._project_name(view.get("project_id", ""))
        text = render_markdown(view, events, project_name)
        target = self._vault / "sessions" / f"{thread}.md"
        atomic_write(target, text)
        # 재개된 스레드의 낡은 아카이브 사본 제거
        if view.get("status") == "active":
            for stale in (self._vault / "sessions" / "archive").glob(f"*/{thread}.md"):
                stale.unlink(missing_ok=True)
        await self.render_home()
        self._write_seq()

    async def _project_name(self, project_id: str) -> str:
        if not project_id:
            return ""
        doc = await self._couch.get(self._db, f"proj:{project_id}")
        return (doc or {}).get("name") or project_id

    async def render_home(self) -> None:
        """HOME.md — active 스레드 인덱스 (§8-1)."""
        views = await self._store.find_views(None, "active", limit=100)
        lines = [
            f"- [[{v['thread']}]] — {v.get('title', '')} · {kst_human(v.get('updated', ''))}"
            f" · {v.get('last_tool', '')}@{v.get('last_device', '')}"
            for v in views
        ]
        text = (
            "# hyeseongkit 세션\n\n"
            + WARNING_HEADER
            + "\n## 활성 스레드\n"
            + ("\n".join(lines) if lines else "(없음)")
            + "\n"
        )
        atomic_write(self._vault / "HOME.md", text)

    # ── 아카이브 (D12) ──────────────────────────────────────

    async def archive_pass(self) -> int:
        """close 후 15일 지난 스레드 파일을 sessions/archive/<YYYY>/로 이동. 이동 수 반환."""
        moved = 0
        cutoff = utc_now() - timedelta(days=ARCHIVE_AFTER_DAYS)
        for status in ("done", "dropped"):
            for v in await self._store.find_views(None, status, limit=500):
                updated = v.get("updated", "")
                try:
                    if parse_iso(updated) > cutoff:
                        continue
                except ValueError:
                    continue
                src = self._vault / "sessions" / f"{v['thread']}.md"
                if not src.is_file():
                    continue
                year = updated[:4]
                dst = self._vault / "sessions" / "archive" / year / src.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                src.replace(dst)
                moved += 1
        return moved

    async def archive_loop(self) -> None:
        while True:
            try:
                moved = await self.archive_pass()
                if moved:
                    log.info("아카이브 이동: %d개", moved)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("아카이브 실패: %s", exc)
            await asyncio.sleep(24 * 3600)  # 일 1회 (§8-1)
