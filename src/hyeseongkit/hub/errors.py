"""허브 공통 예외 — HTTP 에러 바디 {error, message, detail} 규칙 (설계서 §3-1)."""

from __future__ import annotations


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, detail: dict | None = None):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail or {}
