"""stdio 브리지 도구 스키마 회귀 — `_guard`가 원본 시그니처를 가리지 않는지 (§4).

허브 build_mcp만 검사하던 탓에 브리지의 도구 스키마가 무방비였고,
`_guard`가 wraps 없이 감싸 6개 도구 전부 (*a, **kw)를 필수 인자로 노출했다.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from hyeseongkit.cli.mcp_bridge import build_bridge_mcp
from hyeseongkit.core.config import ClientSettings

# 허브 미설정 — _client()가 RuntimeError를 내 _guard의 에러 경로로 들어간다
UNCONFIGURED = ClientSettings(hub_url=None, api_token=None, device_id="testdev")

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


def _tools():
    mcp = build_bridge_mcp(UNCONFIGURED, "claude-code")
    return mcp, {t.name: t for t in asyncio.run(mcp.list_tools())}


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
