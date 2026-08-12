"""`hk mcp serve` — stdio MCP → 허브 HTTP 프록시 (설계서 §4, §9).

Claude Code가 프로젝트 cwd에서 기동하므로 project.toml로 프로젝트 컨텍스트를 얻는다 (F2).
도구는 허브 HTTP API와 1:1이며, 전송 전 마스킹은 이 브리지가 수행한다 (§6-1 ①).
"""

from __future__ import annotations

import json
import os

from mcp.server import MCPServer

from ..core import redact
from ..core.config import current_project, load_client_settings
from ..core.mcp_desc import (
    CLOSE_DESCRIPTION,
    DECIDE_DESCRIPTION,
    PUSH_DESCRIPTION,
    RESUME_DESCRIPTION,
    SEARCH_DESCRIPTION,
    STATUS_DESCRIPTION,
)
from ..core.transport import HubClient, HubError, HubUnreachable


def serve() -> int:
    settings = load_client_settings()
    tool_name = os.environ.get("HK_TOOL") or "claude-code"  # §9 — 기본 등록 대상이 Claude Code

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

    def _guard(fn):
        def wrapped(*a, **kw):
            try:
                return fn(*a, **kw)
            except redact.RedactionError as exc:
                return {"error": f"마스킹 실패(fail-closed): {exc}"}
            except HubError as exc:
                return {"error": f"{exc.status} {exc.code}: {exc.message}", "detail": exc.detail}
            except (HubUnreachable, RuntimeError) as exc:
                return {"error": str(exc)}

        wrapped.__name__ = fn.__name__
        wrapped.__doc__ = fn.__doc__
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
        with _client() as c:
            resp = c.request("POST", "/v1/session/push", json=body)
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
        with _client() as c:
            if not thread:
                resp = c.request("GET", "/v1/session/status", params={"project_id": project_id})
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
            return c.request("POST", "/v1/session/decide", json=body)

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
        with _client() as c:
            resp = c.request("POST", "/v1/session/close", json=body)
        return {"status": resp["status"], "thread": resp["thread"]}

    mcp.run(transport="stdio")
    return 0
