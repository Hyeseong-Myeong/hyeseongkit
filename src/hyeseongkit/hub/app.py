"""허브 앱 팩토리 (설계서 §1, §12) — FastAPI + 렌더러 태스크 + MCP 마운트.

uvicorn 워커 1개, 전 구간 async I/O (제약 L1). 진입점은 hyeseongkit.hub.main:app.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api import router
from .auth import AUTH_DB, AuthService
from .couch import CouchClient, CouchDBDown
from .crypto import BodyCrypto, CryptoError
from .errors import ApiError
from .render import Renderer
from .service import SessionService
from .store import EventStore

log = logging.getLogger("hyeseongkit.hub")


@dataclass
class HubSettings:
    couchdb_url: str
    couchdb_user: str
    couchdb_password: str
    sessions_db: str = "hyeseongkit_sessions"
    vault_db: str = "hyeseongkit_vault"
    admin_token: str = ""
    encryption_key: str = ""
    vault_out: str = "/vault-out"
    data_dir: str = "/data"

    @classmethod
    def from_env(cls) -> HubSettings:
        url = os.environ.get("HK_COUCHDB_URL", "")
        if not url:
            raise RuntimeError("HK_COUCHDB_URL이 설정되지 않았습니다 (§12-2)")
        return cls(
            couchdb_url=url,
            couchdb_user=os.environ.get("HK_COUCHDB_USER", ""),
            couchdb_password=os.environ.get("HK_COUCHDB_PASSWORD", ""),
            sessions_db=os.environ.get("HK_COUCHDB_DB", "hyeseongkit_sessions"),
            vault_db=os.environ.get("HK_VAULT_DB", "hyeseongkit_vault"),
            admin_token=os.environ.get("HK_ADMIN_TOKEN", ""),
            encryption_key=os.environ.get("HK_ENCRYPTION_KEY", ""),
            vault_out=os.environ.get("HK_VAULT_OUT", "/vault-out"),
            data_dir=os.environ.get("HK_DATA_DIR", "/data"),
        )


async def init_couch(couch: CouchClient, settings: HubSettings) -> None:
    """DB·인덱스 생성 — 최초 기동 시 idempotent 수행 (§2-1, §2-5)."""
    await couch.ensure_db(settings.sessions_db)
    await couch.ensure_db(AUTH_DB)
    await couch.ensure_db(settings.vault_db)  # 생성만. 문서는 브리지가 관리 (P4)
    await couch.ensure_index(settings.sessions_db, ["kind", "thread", "ts"], "idx-evt-thread-ts")
    await couch.ensure_index(
        settings.sessions_db, ["kind", "project_id", "status", "updated"], "idx-view-project"
    )
    await couch.ensure_index(settings.sessions_db, ["kind", "updated"], "idx-view-updated")
    await couch.ensure_index(AUTH_DB, ["token_sha256"], "idx-token")


def create_app(
    settings: HubSettings | None = None,
    couch: CouchClient | None = None,
    *,
    enable_renderer: bool = True,
    enable_mcp: bool = True,
) -> FastAPI:
    settings = settings or HubSettings.from_env()
    crypto = BodyCrypto(settings.encryption_key)  # D29 — 키 없으면 기동 실패 (fail-closed)
    couch = couch or CouchClient(
        settings.couchdb_url, settings.couchdb_user, settings.couchdb_password
    )
    store = EventStore(couch, crypto, settings.sessions_db)
    auth = AuthService(couch, settings.admin_token)
    service = SessionService(store, couch, settings.sessions_db)
    renderer = Renderer(
        couch,
        store,
        sessions_db=settings.sessions_db,
        vault_out=settings.vault_out,
        data_dir=settings.data_dir,
    )

    mcp = None
    mcp_asgi = None
    if enable_mcp:
        from mcp.server.transport_security import TransportSecuritySettings

        from .mcp_server import build_mcp

        mcp = build_mcp(service, auth)
        # Tailscale 사설망 내부 서비스 — Host 헤더가 기기마다 달라 DNS rebinding 검증은 끈다.
        # 인증은 Bearer 토큰(§5)이 담당한다.
        mcp_asgi = mcp.streamable_http_app(
            streamable_http_path="/",
            stateless_http=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await init_couch(couch, settings)
        tasks: list[asyncio.Task] = []
        if enable_renderer:
            tasks.append(asyncio.create_task(renderer.run(), name="hk-renderer"))
            tasks.append(asyncio.create_task(renderer.archive_loop(), name="hk-archive"))
        async with AsyncExitStack() as stack:
            if mcp is not None:
                await stack.enter_async_context(mcp.session_manager.run())
            try:
                yield
            finally:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await couch.close()

    app = FastAPI(title="hyeseongkit-hub", lifespan=lifespan)
    app.state.couch = couch
    app.state.store = store
    app.state.auth = auth
    app.state.service = service
    app.state.renderer = renderer
    app.include_router(router)

    if mcp_asgi is not None:
        app.mount("/mcp", mcp_asgi)

    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status,
            content={"error": exc.code, "message": exc.message, "detail": exc.detail},
        )

    @app.exception_handler(CouchDBDown)
    async def _couch_down(_req: Request, exc: CouchDBDown):
        log.error("CouchDB 불통: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"error": "COUCHDB_DOWN", "message": "CouchDB에 접속할 수 없습니다"},
        )

    @app.exception_handler(CryptoError)
    async def _crypto_error(_req: Request, exc: CryptoError):
        log.error("암호화 오류: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": "CRYPTO_FAILED", "message": str(exc)},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_req: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=400,
            content={
                "error": "SCHEMA_INVALID",
                "message": "필수 필드 누락 또는 필드 오탈자",
                "detail": {"errors": [str(e.get("loc")) for e in exc.errors()]},
            },
        )

    return app
