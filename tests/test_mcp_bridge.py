"""stdio 브리지 도구 스키마 회귀 — `_guard`가 원본 시그니처를 가리지 않는지 (§4).

허브 build_mcp만 검사하던 탓에 브리지의 도구 스키마가 무방비였고,
`_guard`가 wraps 없이 감싸 6개 도구 전부 (*a, **kw)를 필수 인자로 노출했다.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from hyeseongkit.cli import mcp_bridge
from hyeseongkit.cli.mcp_bridge import build_bridge_mcp
from hyeseongkit.core import offline_queue
from hyeseongkit.core.config import ClientSettings
from hyeseongkit.core.transport import HubError, HubUnreachable

# 허브 미설정 — _client()가 RuntimeError를 내 _guard의 에러 경로로 들어간다
UNCONFIGURED = ClientSettings(hub_url=None, api_token=None, device_id="testdev")
CONFIGURED = ClientSettings(hub_url="http://hub.invalid", api_token="tok", device_id="testdev")


@pytest.fixture(autouse=True)
def queue_dirs(tmp_path, monkeypatch):
    """큐를 tmp로 격리 — 없으면 테스트가 실제 ~/.hyeseongkit/queue/에 쓰고,
    다음 hk 명령이 그 쓰레기를 진짜 허브로 전송한다."""
    qdir = tmp_path / "queue"
    monkeypatch.setattr(offline_queue, "QUEUE_DIR", qdir)
    monkeypatch.setattr(offline_queue, "FAILED_DIR", qdir / "failed")


class _Project:
    project_id = "proj-test"
    extra_rules: list[str] = []


@pytest.fixture
def in_project(monkeypatch):
    monkeypatch.setattr(mcp_bridge, "current_project", lambda: _Project())


def _stub_hub(monkeypatch, behavior):
    """mcp_bridge가 쓰는 HubClient를 갈아끼운다 — _client()는 클로저라 직접 못 건드린다."""

    class StubClient:
        def __init__(self, *_a, **_kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

        def request(self, _method, path, *, json=None, params=None, timeout=None):
            if behavior == "down":
                raise HubUnreachable("connect error")
            if behavior == "reject":
                raise HubError(409, "THREAD_LIMIT", "정책 위반")
            if "status" in path:
                return {"hub": {"version": "1", "couchdb": "ok"}, "threads": []}
            return {"thread": "T-1", "event_id": "e1", "status": "closed"}

    monkeypatch.setattr(mcp_bridge, "HubClient", StubClient)


EXPECTED_PARAMS = {
    "hk_push": {"title", "sections", "thread", "sensitivity", "slug"},
    "hk_resume": {"thread", "last", "budget", "format", "events"},
    "hk_status": set(),
    "hk_decide": {"decision_text", "rationale", "rejected", "thread"},
    "hk_search": {"query", "limit"},
    "hk_close": {"thread", "outcome"},
}

# `-> str` 도구 — 에러도 문자열이어야 한다 (나머지는 {"error": ...} dict)
TEXT_TOOLS = {"hk_resume", "hk_status", "hk_search"}

# 에러 경로를 타되 인자 검증은 통과해야 하는 최소 호출
MINIMAL_ARGS = {
    "hk_push": {"title": "t", "sections": {"한 일": "x"}},
    "hk_resume": {"thread": "T-x"},
    "hk_status": {},
    "hk_decide": {"decision_text": "d", "thread": "T-x"},
    "hk_search": {"query": "q"},
    "hk_close": {"thread": "T-x"},
}


def _tools(settings=UNCONFIGURED):
    mcp = build_bridge_mcp(settings, "claude-code")
    return mcp, {t.name: t for t in asyncio.run(mcp.list_tools())}


def _call(mcp, name, args):
    return asyncio.run(mcp.call_tool(name, args))


def _payload(result):
    """도구 반환값을 dict로 — `-> dict` 도구는 content가 JSON 텍스트다."""
    return json.loads(result.content[0].text)


def test_bridge_registers_all_tools():
    _mcp, tools = _tools()
    assert set(tools) == set(EXPECTED_PARAMS)


@pytest.mark.parametrize("name", sorted(EXPECTED_PARAMS))
def test_bridge_tool_exposes_real_signature(name):
    """`_guard`의 (*a, **kw)가 아니라 원본 인자가 스키마에 나와야 한다."""
    _mcp, tools = _tools()
    props = set(tools[name].input_schema.get("properties", {}))
    assert not props & {"a", "kw"}, f"{name}: _guard 시그니처가 새어 나왔다"
    assert props == EXPECTED_PARAMS[name]


def test_no_tool_has_required_args_beyond_spec():
    """required는 기본값 없는 인자뿐 — a/kw가 필수로 잡히던 게 원증상이다."""
    _mcp, tools = _tools()
    required = {n: set(t.input_schema.get("required", [])) for n, t in tools.items()}
    assert required == {
        "hk_push": {"title", "sections"},
        "hk_resume": set(),
        "hk_status": set(),
        "hk_decide": {"decision_text"},
        "hk_search": {"query"},
        "hk_close": {"thread"},
    }


@pytest.mark.parametrize("name", sorted(MINIMAL_ARGS))
def test_guard_error_matches_declared_return_type(name, tmp_path, monkeypatch):
    """허브가 없을 때 에러가 그대로 전달돼야 한다.

    반환형 애너테이션이 그대로 출력 스키마가 되므로, `-> str` 도구가 dict를 돌려주면
    call_tool이 pydantic 검증 실패로 죽어 원인이 가려진다.
    메시지는 cwd(프로젝트 유무)에 따라 갈리므로 형태만 본다.
    """
    monkeypatch.chdir(tmp_path)  # hk 프로젝트 밖 — 어느 쪽 RuntimeError든 _guard가 받는다
    mcp, _tools_ = _tools()
    result = asyncio.run(mcp.call_tool(name, MINIMAL_ARGS[name]))
    assert not result.is_error
    if name in TEXT_TOOLS:
        assert isinstance(result.structured_content["result"], str)
        assert result.structured_content["result"].strip()
    else:
        assert json.loads(result.content[0].text)["error"].strip()


def test_minimal_args_covers_every_tool():
    """새 도구가 무검증으로 새는 걸 막는다 — 반환형 판정이 애너테이션 문자열 매칭이라 취약하다."""
    assert set(MINIMAL_ARGS) == set(EXPECTED_PARAMS)


@pytest.mark.parametrize("name", ["hk_push", "hk_close"])
def test_unreachable_hub_queues_mutation(name, monkeypatch, in_project):
    """허브 불통 시 내용을 버리지 않는다 — CLI _send_or_queue와 같은 의미 (K4/§11-3)."""
    _stub_hub(monkeypatch, "down")
    mcp, _ = _tools(CONFIGURED)
    payload = _payload(_call(mcp, name, MINIMAL_ARGS[name]))
    assert "queued" in payload, payload
    assert len(offline_queue.pending()) == 1


@pytest.mark.parametrize("name", ["hk_push", "hk_close"])
def test_hub_4xx_is_not_queued(name, monkeypatch, in_project):
    """4xx는 재시도해도 실패한다 — 적재하면 큐가 영영 막힌다."""
    _stub_hub(monkeypatch, "reject")
    mcp, _ = _tools(CONFIGURED)
    payload = _payload(_call(mcp, name, MINIMAL_ARGS[name]))
    assert "THREAD_LIMIT" in payload["error"]
    assert offline_queue.pending() == []


def test_unconfigured_hub_queues_instead_of_losing(in_project):
    """허브 미설정 기기에서도 내용은 남는다 (CLI client is None 경로와 같다)."""
    mcp, _ = _tools(UNCONFIGURED)
    payload = _payload(_call(mcp, "hk_push", MINIMAL_ARGS["hk_push"]))
    assert "queued" in payload, payload
    assert len(offline_queue.pending()) == 1


def test_queued_body_is_masked(monkeypatch, in_project):
    """큐 파일은 디스크에 평문으로 남는다 — 마스킹이 적재보다 먼저여야 한다 (§6-1)."""
    _stub_hub(monkeypatch, "down")
    mcp, _ = _tools(CONFIGURED)
    secret = "abcd1234efgh"  # gitleaks:allow — 마스킹 동작 확인용 가짜
    _call(mcp, "hk_push", {"title": "t", "sections": {"한 일": f"password: {secret}"}})
    raw = offline_queue.pending()[0].read_text(encoding="utf-8")
    assert secret not in raw, "마스킹 전 본문이 큐 파일에 남았다"


def test_status_drains_queue(monkeypatch, in_project):
    """MCP만 쓰는 기기 — main()의 flush를 안 타므로 브리지가 직접 비워야 한다."""
    offline_queue.enqueue("/v1/session/push", {"title": "old"})
    assert len(offline_queue.pending()) == 1
    _stub_hub(monkeypatch, "ok")
    mcp, _ = _tools(CONFIGURED)
    _call(mcp, "hk_status", {})
    assert offline_queue.pending() == []


def test_decide_without_thread_when_hub_down_reports_recovery(monkeypatch, in_project):
    """스레드 해석은 허브가 필요하다 — 내용을 지킬 방법을 알려 줘야 한다."""
    _stub_hub(monkeypatch, "down")
    mcp, _ = _tools(CONFIGURED)
    payload = _payload(_call(mcp, "hk_decide", {"decision_text": "d"}))
    assert "thread" in payload["detail"]
    assert offline_queue.pending() == []


def test_bridge_and_hub_expose_the_same_tool_surface(fake_couch):
    """ "하나의 코어, 세 개의 표면" 원칙을 코드로 강제한다.

    허브 build_mcp만 테스트되던 탓에 브리지 스키마 결함이 잡히지 않았다.
    한쪽 표면에만 인자를 더하는 드리프트도 여기서 걸린다.
    """
    from cryptography.fernet import Fernet

    from hyeseongkit.hub.auth import AuthService
    from hyeseongkit.hub.crypto import BodyCrypto
    from hyeseongkit.hub.mcp_server import build_mcp
    from hyeseongkit.hub.service import SessionService
    from hyeseongkit.hub.store import EventStore

    crypto = BodyCrypto(Fernet.generate_key().decode())
    store = EventStore(fake_couch, crypto, "hyeseongkit_sessions")
    service = SessionService(store, fake_couch, "hyeseongkit_sessions")
    hub = build_mcp(service, AuthService(fake_couch, "admin-token"))

    def params(tool):
        # 허브 도구의 ctx는 접속 헤더 해석용이라 도구 인자가 아니다.
        # FastMCP가 Context 인자를 이미 스키마에서 빼지만, 그 동작에 기대지 않는다
        return set(tool.input_schema.get("properties", {})) - {"ctx"}

    hub_surface = {t.name: params(t) for t in asyncio.run(hub.list_tools())}
    _mcp, bridge_tools = _tools()
    bridge_surface = {n: params(t) for n, t in bridge_tools.items()}
    assert hub_surface == bridge_surface
