"""`hk mcp serve` — stdio MCP → 허브 HTTP 프록시 (설계서 §4, §9).

Claude Code가 프로젝트 cwd에서 기동하므로 project.toml로 프로젝트 컨텍스트를 얻는다 (F2).
도구는 허브 HTTP API와 1:1이며, 전송 전 마스킹은 이 브리지가 수행한다 (§6-1 ①).
"""

from __future__ import annotations

import functools
import json
import os

from mcp.server import MCPServer

from ..core import offline_queue, redact
from ..core.config import ClientSettings, current_project, load_client_settings
from ..core.mcp_desc import (
    CLOSE_DESCRIPTION,
    DECIDE_DESCRIPTION,
    PUSH_DESCRIPTION,
    RESUME_DESCRIPTION,
    SEARCH_DESCRIPTION,
    STATUS_DESCRIPTION,
)
from ..core.transport import HubClient, HubError, HubUnreachable


def build_bridge_mcp(settings: ClientSettings, tool_name: str) -> MCPServer:
    """도구를 등록한 서버 객체만 돌려준다 — 실행은 serve()가 한다.

    허브 build_mcp와 같은 구조. 도구 스키마를 테스트에서 검사할 수 있게 하려는 분리다.
    """

    def _client() -> HubClient:
        if not settings.hub_url or not settings.api_token:
            raise RuntimeError("HK_HUB_URL/HK_API_TOKEN 미설정 — 허브에 접속할 수 없다")
        return HubClient(settings.hub_url, settings.api_token)

    def _project_id() -> str:
        project = current_project()
        if project is None:
            raise RuntimeError("이 디렉터리는 hk 프로젝트가 아니다 — 먼저 `hk init`")
        return project.project_id

    def _extra_rules() -> list[str]:
        project = current_project()
        return project.extra_rules if project else []

    def _flush_quietly(c: HubClient) -> None:
        """브리지는 main()의 flush 블록을 타지 않는다 — `hk mcp`가 그 앞에서 return하기 때문.

        여기서 직접 비우지 않으면 MCP만 쓰는 기기에서 큐가 영원히 쌓인다.
        """
        try:
            offline_queue.flush(c)
        except OSError:
            pass  # 큐 파일 입출력 실패로 본 요청을 막지 않는다

    def _queued(endpoint: str, body: dict, why: str) -> dict:
        try:
            path = offline_queue.enqueue(endpoint, body)
        except OSError as exc:
            # 여기서만 내용이 실제로 사라진다 — 모델이 사용자에게 알릴 수 있게 명시한다
            raise RuntimeError(f"{why} + 큐 적재 실패 — 내용이 저장되지 않았다: {exc}") from exc
        return {"queued": path.name, "note": f"{why} — 큐에 적재됨 (다음 hk 명령에서 재전송)"}

    def _post(endpoint: str, body: dict) -> dict:
        """POST — 불통·미설정이면 큐에 적재한다 (CLI _send_or_queue와 같은 의미, K4/§11-3).

        4xx(HubError)는 재시도해도 실패하므로 적재하지 않고 _guard가 에러로 돌려준다.
        """
        try:
            client = _client()
        except RuntimeError:
            return _queued(endpoint, body, "허브 미설정")
        with client as c:
            _flush_quietly(c)
            try:
                return c.request("POST", endpoint, json=body) or {}
            except HubUnreachable:
                return _queued(endpoint, body, "허브 불통")

    def _guard(fn):
        # 반환형이 그대로 출력 스키마가 되므로 에러도 같은 형으로 돌려준다.
        # `-> str`은 문자열, `-> dict`은 {"error": ...} — 허브 mcp_server._err와 같은 규약.
        # (future annotations 탓에 애너테이션은 문자열로 들어온다)
        returns_str = fn.__annotations__.get("return") in ("str", str)

        def _fail(message: str, detail=None):
            if returns_str:
                return message + (f" — {json.dumps(detail, ensure_ascii=False)}" if detail else "")
            return {"error": message, "detail": detail}

        # wraps 없이 감싸면 @mcp.tool이 wrapped의 (*a, **kw)를 도구 스키마로 노출한다.
        # __wrapped__가 있어야 inspect.signature가 원본 시그니처를 따라간다.
        @functools.wraps(fn)
        def wrapped(*a, **kw):
            try:
                return fn(*a, **kw)
            except redact.RedactionError as exc:
                return _fail(f"마스킹 실패(fail-closed): {exc}")
            except HubError as exc:
                return _fail(f"{exc.status} {exc.code}: {exc.message}", exc.detail)
            except (HubUnreachable, RuntimeError) as exc:
                return _fail(str(exc))

        return wrapped

    mcp = MCPServer("hyeseongkit")

    @mcp.tool(description=PUSH_DESCRIPTION)
    @_guard
    def hk_push(
        title: str,
        sections: dict[str, str],
        thread: str | None = None,
        sensitivity: str | None = None,
        slug: str | None = None,
    ) -> dict:
        masked, report = redact.mask_obj({"title": title, "sections": sections}, _extra_rules())
        body = {
            "thread": thread,
            "project_id": _project_id(),
            "title": masked["title"],
            "slug": slug,
            "sections": masked["sections"],
            "sensitivity": sensitivity,
            "tool": tool_name,
            "device": settings.device_id or "",
            "mask_report": report,
        }
        resp = _post("/v1/session/push", body)
        if "queued" in resp:
            return resp
        return {"thread": resp["thread"], "event_id": resp["event_id"]}

    @mcp.tool(description=RESUME_DESCRIPTION)
    @_guard
    def hk_resume(
        thread: str | None = None,
        last: bool = False,
        budget: int = 2000,
        format: str = "packet",  # noqa: A002 — 도구 인자 이름은 스펙 고정 (§4)
        events: int = 0,
    ) -> str:
        params: dict = {"budget": budget, "format": format, "events": events}
        if thread:
            params["thread"] = thread
        else:
            params["last"] = 1
            params["project_id"] = _project_id()
        with _client() as c:
            resp = c.request("GET", "/v1/session/resume", params=params)
        if (resp or {}).get("format") == "json":
            return json.dumps(resp.get("view"), ensure_ascii=False, indent=1)
        return (resp or {}).get("content", "")

    @mcp.tool(description=STATUS_DESCRIPTION)
    @_guard
    def hk_status() -> str:
        params = {}
        project = current_project()
        if project:
            params["project_id"] = project.project_id
        with _client() as c:
            _flush_quietly(c)
            resp = c.request("GET", "/v1/session/status", params=params)
        hub = (resp or {}).get("hub", {})
        lines = [f"hub v{hub.get('version', '?')} · couchdb {hub.get('couchdb', '?')}"]
        for t in (resp or {}).get("threads", []):
            lines.append(
                f"- {t['thread']} — {t['title']} ({t['status']}, "
                f"{t['last_tool']}@{t['last_device']}, updated {t['updated']})"
            )
        if len(lines) == 1:
            lines.append("(활성 스레드 없음)")
        # 큐 적체는 클라이언트 로컬 신호다 — 허브 쪽 모니터링으로는 보이지 않는다
        pending = offline_queue.pending()
        failed = offline_queue.failed()
        if pending or failed:
            lines.append(
                f"오프라인 큐 대기 {len(pending)}건 · 실패 {len(failed)}건 (hk queue --list)"
            )
        return "\n".join(lines)

    @mcp.tool(description=DECIDE_DESCRIPTION)
    @_guard
    def hk_decide(
        decision_text: str,
        rationale: str | None = None,
        rejected: str | None = None,
        thread: str | None = None,
    ) -> dict:
        project_id = _project_id()
        if not thread:
            # 스레드 해석에는 허브가 살아 있어야 한다 (CLI cmd_decide와 같다).
            # 불통이면 body를 만들 수 없어 적재도 못 하므로, 내용을 지킬 방법을 알려 준다.
            try:
                with _client() as c:
                    resp = c.request("GET", "/v1/session/status", params={"project_id": project_id})
            except HubUnreachable as exc:
                return {
                    "error": f"허브 불통으로 활성 스레드를 확인할 수 없다: {exc}",
                    "detail": "thread를 직접 지정해 다시 부르면 큐에 적재된다",
                }
            threads = (resp or {}).get("threads", [])
            if not threads:
                return {"error": "활성 스레드가 없습니다 — thread를 지정하세요"}
            thread = threads[0]["thread"]
        masked, report = redact.mask_obj(
            {"text": decision_text, "rationale": rationale, "rejected": rejected},
            _extra_rules(),
        )
        body = {
            "thread": thread,
            "project_id": project_id,
            "decision": masked,
            "tool": tool_name,
            "device": settings.device_id or "",
            "mask_report": report,
        }
        return _post("/v1/session/decide", body)

    @mcp.tool(description=SEARCH_DESCRIPTION)
    @_guard
    def hk_search(query: str, limit: int = 10) -> str:
        params: dict = {"q": query, "limit": limit}
        project = current_project()
        if project:
            params["project_id"] = project.project_id
        with _client() as c:
            resp = c.request("GET", "/v1/session/search", params=params)
        matches = (resp or {}).get("matches", [])
        lines = [
            f"- {m['thread']} — {m['title']} ({m['status']}, updated {m['updated']})"
            for m in matches
        ] or ["(일치 없음)"]
        if (resp or {}).get("truncated"):
            lines.append("(스캔 상한 초과 — 결과가 잘렸을 수 있음)")
        return "\n".join(lines)

    @mcp.tool(description=CLOSE_DESCRIPTION)
    @_guard
    def hk_close(thread: str, outcome: str = "done") -> dict:
        body = {
            "thread": thread,
            "project_id": _project_id(),
            "outcome": outcome,
            "tool": tool_name,
            "device": settings.device_id or "",
        }
        resp = _post("/v1/session/close", body)
        if "queued" in resp:
            return resp
        return {"status": resp["status"], "thread": resp["thread"]}

    return mcp


def serve() -> int:
    settings = load_client_settings()
    tool_name = os.environ.get("HK_TOOL") or "claude-code"  # §9 — 기본 등록 대상이 Claude Code
    build_bridge_mcp(settings, tool_name).run(transport="stdio")
    return 0
