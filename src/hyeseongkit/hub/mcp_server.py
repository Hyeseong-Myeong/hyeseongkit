"""허브 MCP 서버 — Streamable HTTP `/mcp` (설계서 §4).

도구는 HTTP API와 1:1 매핑이며 동일한 코어(SessionService)를 호출한다 (K3).
project_id·tool·device는 도구 인자가 아니라 접속에서 온다:
Bearer 토큰(device) + 요청 헤더 X-HK-Project / X-HK-Tool.

원격 클라이언트는 CLI 마스킹을 거치지 않으므로, 이 계층이 브리지와 동일하게
치환 마스킹을 수행한 뒤 코어에 넘긴다 (§6-1).
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Context

from ..core import redact
from ..core.mcp_desc import (
    CLOSE_DESCRIPTION,
    DECIDE_DESCRIPTION,
    PUSH_DESCRIPTION,
    RESUME_DESCRIPTION,
    SEARCH_DESCRIPTION,
    STATUS_DESCRIPTION,
)
from .auth import AuthService
from .errors import ApiError
from .service import SessionService


def _err(exc: ApiError) -> str:
    return f"오류 {exc.status} {exc.code}: {exc.message}" + (
        f" — {json.dumps(exc.detail, ensure_ascii=False)}" if exc.detail else ""
    )


def build_mcp(service: SessionService, auth: AuthService) -> MCPServer:
    mcp = MCPServer("hyeseongkit")

    async def _ctx_identity(ctx: Context) -> tuple[dict, str, str]:
        """(device 문서, project_id, tool) — 접속 헤더에서 해석 (§4)."""
        headers = ctx.headers
        if headers is None:
            raise ApiError(401, "AUTH_MISSING", "HTTP 헤더가 없습니다")
        auth_header = headers.get("authorization", "")
        bearer = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else None
        device = await auth.verify_device(bearer)
        project_id = headers.get("x-hk-project", "")
        tool = headers.get("x-hk-tool", "manual")
        return device, project_id, tool

    @mcp.tool(description=PUSH_DESCRIPTION)
    async def hk_push(
        title: str,
        sections: dict[str, str],
        thread: str | None = None,
        sensitivity: str | None = None,
        slug: str | None = None,
        ctx: Context = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        try:
            device, project_id, tool = await _ctx_identity(ctx)
            masked, report = redact.mask_obj({"title": title, "sections": sections})
            payload = {
                "thread": thread,
                "project_id": project_id,
                "title": masked["title"],  # type: ignore[index]
                "slug": slug,
                "sections": masked["sections"],  # type: ignore[index]
                "sensitivity": sensitivity,
                "tool": tool,
                "device": device["device_id"],
                "mask_report": report,
            }
            result = await service.push(device, payload)
            return {"thread": result["thread"], "event_id": result["event_id"]}
        except redact.RedactionError as exc:
            return {"error": f"마스킹 실패(fail-closed): {exc}"}
        except ApiError as exc:
            return {"error": _err(exc)}

    @mcp.tool(description=RESUME_DESCRIPTION)
    async def hk_resume(
        thread: str | None = None,
        last: bool = False,
        budget: int = 2000,
        format: str = "packet",  # noqa: A002 — 도구 인자 이름은 스펙 고정 (§4)
        events: int = 0,
        ctx: Context = None,  # type: ignore[assignment]
    ) -> str:
        try:
            _device, project_id, _tool = await _ctx_identity(ctx)
            result = await service.resume(
                thread=thread,
                last=last,
                project_id=project_id or None,
                budget=budget,
                fmt=format,
                events=events,
            )
            if result["format"] == "json":
                return json.dumps(result["view"], ensure_ascii=False, indent=1)
            return result["content"]
        except ApiError as exc:
            return _err(exc)

    @mcp.tool(description=STATUS_DESCRIPTION)
    async def hk_status(ctx: Context = None) -> str:  # type: ignore[assignment]
        try:
            _device, project_id, _tool = await _ctx_identity(ctx)
            result = await service.status(project_id or None)
            hub = result["hub"]
            lines = [f"hub v{hub['version']} · couchdb {hub['couchdb']}"]
            for t in result["threads"]:
                lines.append(
                    f"- {t['thread']} — {t['title']} ({t['status']}, "
                    f"{t['last_tool']}@{t['last_device']}, updated {t['updated']})"
                )
            if not result["threads"]:
                lines.append("(활성 스레드 없음)")
            return "\n".join(lines)
        except ApiError as exc:
            return _err(exc)

    @mcp.tool(description=DECIDE_DESCRIPTION)
    async def hk_decide(
        decision_text: str,
        rationale: str | None = None,
        rejected: str | None = None,
        thread: str | None = None,
        ctx: Context = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        try:
            device, project_id, tool = await _ctx_identity(ctx)
            if not thread:
                views = await service.store.find_views(project_id or None, "active", limit=1)
                if not views:
                    return {"error": "활성 스레드가 없습니다 — thread를 지정하세요"}
                thread = views[0]["thread"]
            decision = {"text": decision_text, "rationale": rationale, "rejected": rejected}
            masked, report = redact.mask_obj(decision)
            payload = {
                "thread": thread,
                "project_id": project_id,
                "decision": masked,
                "tool": tool,
                "device": device["device_id"],
                "mask_report": report,
            }
            return await service.decide(device, payload)
        except redact.RedactionError as exc:
            return {"error": f"마스킹 실패(fail-closed): {exc}"}
        except ApiError as exc:
            return {"error": _err(exc)}

    @mcp.tool(description=SEARCH_DESCRIPTION)
    async def hk_search(
        query: str,
        limit: int = 10,
        ctx: Context = None,  # type: ignore[assignment]
    ) -> str:
        try:
            _device, project_id, _tool = await _ctx_identity(ctx)
            result = await service.search(query, project_id=project_id or None, limit=limit)
            lines = [
                f"- {m['thread']} — {m['title']} ({m['status']}, updated {m['updated']})"
                for m in result["matches"]
            ]
            if not lines:
                lines = ["(일치 없음)"]
            if result.get("truncated"):
                lines.append("(스캔 상한 초과 — 결과가 잘렸을 수 있음)")
            return "\n".join(lines)
        except ApiError as exc:
            return _err(exc)

    @mcp.tool(description=CLOSE_DESCRIPTION)
    async def hk_close(
        thread: str,
        outcome: str = "done",
        ctx: Context = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        try:
            device, project_id, tool = await _ctx_identity(ctx)
            payload = {
                "thread": thread,
                "project_id": project_id,
                "outcome": outcome,
                "tool": tool,
                "device": device["device_id"],
            }
            result = await service.close(device, payload)
            return {"status": result["status"], "thread": result["thread"]}
        except ApiError as exc:
            return {"error": _err(exc)}

    return mcp
