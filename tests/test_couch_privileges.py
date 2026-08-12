"""DB·인덱스 준비의 권한 처리 (설계서 §2-1, 수용 기준 T16).

hk_hub는 서버 관리자가 아니라 DB를 만들 수 없다(F4). 그때 허브는 조용히 반쪽 상태로
뜨는 대신, 무엇을 해야 하는지 적힌 메시지와 함께 기동을 멈춰야 한다.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from hyeseongkit.hub.couch import CouchClient, CouchDBDown


def _client(handler) -> CouchClient:
    couch = CouchClient("http://couch:5984", "hk_hub", "pw")
    couch._client = httpx.AsyncClient(
        base_url="http://couch:5984", transport=httpx.MockTransport(handler)
    )
    return couch


def test_ensure_db_passes_when_db_exists():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        return httpx.Response(200)

    asyncio.run(_client(handler).ensure_db("hyeseongkit_sessions"))
    assert calls == ["HEAD /hyeseongkit_sessions"]  # 있으면 PUT을 시도조차 하지 않는다


def test_ensure_db_creates_when_missing_and_permitted():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404) if request.method == "HEAD" else httpx.Response(201)

    asyncio.run(_client(handler).ensure_db("hyeseongkit_sessions"))


def test_ensure_db_reports_what_to_do_when_forbidden():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404) if request.method == "HEAD" else httpx.Response(403)

    with pytest.raises(CouchDBDown) as exc:
        asyncio.run(_client(handler).ensure_db("hyeseongkit_sessions"))
    msg = str(exc.value)
    assert "_security.admins" in msg  # 조치 방법이 담겨 있어야 한다
    assert "§12-3" in msg


def test_ensure_index_reports_what_to_do_when_forbidden():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    with pytest.raises(CouchDBDown) as exc:
        asyncio.run(
            _client(handler).ensure_index("hyeseongkit_sessions", ["kind", "ts"], "idx-test")
        )
    assert "_security.admins" in str(exc.value)
