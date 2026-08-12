"""허브 HTTP 클라이언트 — CLI·stdio 브리지 공용 (K3).

연결 오류·타임아웃·5xx는 HubUnreachable로 구분한다 — 오프라인 큐 적재 대상 (§11-3).
4xx는 HubError — 재시도해도 실패하므로 큐에 넣지 않는다.
"""

from __future__ import annotations

import httpx


class HubError(Exception):
    """허브가 거부한 요청 (4xx) — 에러 바디 {error, message, detail} (§3-1)."""

    def __init__(self, status: int, code: str, message: str = "", detail: dict | None = None):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail or {}


class HubUnreachable(Exception):
    """연결 오류·타임아웃·5xx — 큐 적재 대상 (K4)."""


class HubClient:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 10.0):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HubClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        timeout: float | None = None,
    ) -> dict | None:
        try:
            r = self._client.request(method, path, json=json, params=params, timeout=timeout)
        except httpx.HTTPError as exc:
            raise HubUnreachable(str(exc)) from exc
        if r.status_code >= 500:
            raise HubUnreachable(f"hub {r.status_code}")
        if r.status_code >= 400:
            try:
                body = r.json()
            except ValueError:
                body = {}
            raise HubError(
                r.status_code,
                body.get("error", "HTTP_ERROR"),
                body.get("message", r.text[:200]),
                body.get("detail"),
            )
        if r.status_code == 204 or not r.content:
            return None
        return r.json()
