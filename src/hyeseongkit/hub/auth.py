"""인증 — 기기별 토큰 (설계서 §5, D17/D18).

- 토큰 형식: hk_<40자 소문자 hex>. 허브는 sha256만 저장, 발급 응답에서 단 한 번 노출
- admin 토큰은 환경변수 HK_ADMIN_TOKEN (NAS에만 존재)
- last_seen 갱신은 시간당 1회 스로틀 (§2-6)
- 토큰을 로그에 남기지 않는다 — 로그에는 device_id만
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time

from ..core.util import iso_utc
from .couch import CouchClient, CouchConflict
from .errors import ApiError

AUTH_DB = "hyeseongkit_auth"
_DEVICE_ID_RE = re.compile(r"^[a-z0-9-]{1,32}$")


def new_token() -> str:
    return "hk_" + secrets.token_hex(20)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class AuthService:
    def __init__(self, couch: CouchClient, admin_token: str):
        self._couch = couch
        self._admin_token = admin_token
        self._last_seen: dict[str, float] = {}

    def is_admin(self, bearer: str | None) -> bool:
        return bool(
            bearer and self._admin_token and secrets.compare_digest(bearer, self._admin_token)
        )

    def verify_admin(self, bearer: str | None) -> None:
        if not bearer:
            raise ApiError(401, "AUTH_MISSING", "Authorization 헤더가 없습니다")
        if not self.is_admin(bearer):
            raise ApiError(403, "SCOPE_DENIED", "admin 토큰이 필요합니다 (§5-2)")

    async def verify_device(self, bearer: str | None) -> dict:
        """§5-3 검증 순서. 통과 시 device 문서 반환."""
        if not bearer:
            raise ApiError(401, "AUTH_MISSING", "Authorization 헤더가 없습니다")
        rows = await self._couch.find(
            AUTH_DB, {"kind": "device", "token_sha256": token_hash(bearer)}, limit=1
        )
        if not rows:
            raise ApiError(401, "AUTH_INVALID", "알 수 없는 토큰")
        doc = rows[0]
        if doc.get("revoked"):
            raise ApiError(403, "AUTH_REVOKED", "폐기된 토큰")
        if "session:rw" not in doc.get("scopes", []):
            raise ApiError(403, "SCOPE_DENIED", "session:rw 권한 없음")
        await self._touch(doc)
        return doc

    async def _touch(self, doc: dict) -> None:
        device_id = doc.get("device_id", "")
        now = time.monotonic()
        if now - self._last_seen.get(device_id, -3600.0) < 3600.0:
            return
        self._last_seen[device_id] = now
        try:
            doc["last_seen"] = iso_utc()
            await self._couch.put(AUTH_DB, doc)
        except CouchConflict:
            pass  # best-effort

    # ── 기기 관리 (admin, §5-2) ──────────────────────────────

    async def add_device(self, device_id: str, name: str) -> tuple[dict, str]:
        if not _DEVICE_ID_RE.match(device_id or ""):
            raise ApiError(400, "SCHEMA_INVALID", "device_id는 ASCII 소문자·숫자·하이픈 (§2-6)")
        doc_id = f"device:{device_id}"
        existing = await self._couch.get(AUTH_DB, doc_id)
        if existing and not existing.get("revoked"):
            raise ApiError(409, "DEVICE_EXISTS", "이미 활성인 기기 — revoke 후 add로 재발급 (§5-2)")
        token = new_token()
        doc = {
            "_id": doc_id,
            "kind": "device",
            "device_id": device_id,
            "name": name or device_id,
            "token_sha256": token_hash(token),
            "scopes": ["session:rw"],
            "created": iso_utc(),
            "revoked": False,
            "revoked_at": None,
            "last_seen": None,
        }
        if existing:
            doc["_rev"] = existing["_rev"]
        await self._couch.put(AUTH_DB, doc)
        return doc, token

    async def revoke_device(self, device_id: str) -> dict:
        doc = await self._couch.get(AUTH_DB, f"device:{device_id}")
        if not doc:
            raise ApiError(404, "DEVICE_NOT_FOUND", f"기기 없음: {device_id}")
        doc["revoked"] = True
        doc["revoked_at"] = iso_utc()  # 문서 삭제 아님 — 감사 기록 (§5-2)
        await self._couch.put(AUTH_DB, doc)
        return doc

    async def list_devices(self) -> list[dict]:
        rows = await self._couch.all_docs_prefix(AUTH_DB, "device:")
        return [
            {k: v for k, v in d.items() if k != "token_sha256"}
            for d in rows
            if d.get("kind") == "device"
        ]
