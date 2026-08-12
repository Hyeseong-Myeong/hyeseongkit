"""MCP 서버 구성 스모크 — FastMCP 시그니처·도구 등록이 현재 SDK와 맞는지 확인."""

from __future__ import annotations

import asyncio

from cryptography.fernet import Fernet

from hyeseongkit.hub.auth import AuthService
from hyeseongkit.hub.crypto import BodyCrypto
from hyeseongkit.hub.mcp_server import build_mcp
from hyeseongkit.hub.service import SessionService
from hyeseongkit.hub.store import EventStore


def test_build_mcp_tools(fake_couch):
    crypto = BodyCrypto(Fernet.generate_key().decode())
    store = EventStore(fake_couch, crypto, "hyeseongkit_sessions")
    auth = AuthService(fake_couch, "admin-token")
    service = SessionService(store, fake_couch, "hyeseongkit_sessions")
    mcp = build_mcp(service, auth)
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {"hk_push", "hk_resume", "hk_status", "hk_decide", "hk_search", "hk_close"}
    push_tool = next(t for t in tools if t.name == "hk_push")
    assert "원문 그대로" in push_tool.description  # D21 규약이 도구 정의에 있는지 (§4)
    # 허브 HTTP MCP 앱 생성 (Streamable HTTP 마운트 경로 확인)
    app = mcp.streamable_http_app()
    assert app is not None


def test_app_with_mcp_mounted_starts(fake_couch, tmp_path):
    """enable_mcp=True lifespan(session_manager.run) 통합 스모크."""
    from fastapi.testclient import TestClient

    from hyeseongkit.hub.app import HubSettings, create_app

    settings = HubSettings(
        couchdb_url="http://fake:5984",
        couchdb_user="",
        couchdb_password="",
        admin_token="admin-token",
        encryption_key=Fernet.generate_key().decode(),
        vault_out=str(tmp_path / "vault-out"),
        data_dir=str(tmp_path / "data"),
    )
    app = create_app(settings, fake_couch, enable_renderer=False, enable_mcp=True)
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
    # /mcp 마운트 확인 — GET은 SSE 스트림을 열어 블록되므로 라우트 존재만 검사
    assert any(getattr(r, "path", "") == "/mcp" for r in app.routes)
