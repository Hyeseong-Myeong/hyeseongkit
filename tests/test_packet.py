"""패킷 예산 계층 (설계서 §3-6, §3-7) — L0는 절대 잘리지 않는다."""

from hyeseongkit.hub.packet import CARRY_NOTE, OMITTED_NOTE, build_packet, build_prompt

VIEW = {
    "thread": "T-20260811-test",
    "project_id": "p-1",
    "title": "테스트 세션",
    "status": "active",
    "sensitivity": "tech",
    "created": "2026-08-11T01:00:00Z",
    "updated": "2026-08-11T02:31:00Z",
    "last_tool": "claude-code",
    "last_device": "desktop",
    "events": 3,
    "sections": {
        "context": "긴 컨텍스트 문단.\n\n" * 50,
        "done": "한 일 목록.\n\n" * 30,
        "todo": "1. 이어서 구현\n2. 테스트",
        "know": "- 포트 9100\n- 커밋 b82f82b",
        "questions": "미결 질문?",
    },
    "know_carryover": ["- 이월된 항목"],
    "decisions": [
        {"date": "2026-08-11", "text": "결정 원문", "rationale": "근거", "rejected": "기각"}
    ],
    "tags": [],
}


def test_l0_survives_tiny_budget():
    packet = build_packet(VIEW, project_name="proj", budget=200)
    assert "1. 이어서 구현" in packet  # todo (L0)
    assert "- 포트 9100" in packet  # know (L0)
    assert "- 이월된 항목" in packet  # carryover — 절단 대상 아님 (§2-4)
    assert "결정 원문" in packet  # decisions (L0)
    assert OMITTED_NOTE in packet  # L1은 생략 표기


def test_full_when_budget_zero():
    packet = build_packet(VIEW, project_name="proj", budget=0)
    assert "긴 컨텍스트 문단." in packet
    assert OMITTED_NOTE not in packet


def test_guard_text_present():
    packet = build_packet(VIEW, project_name="proj", budget=0)
    assert "자료" in packet.splitlines()[1]  # 프롬프트 인젝션 가드 (R8)
    assert packet.startswith('<hyeseongkit-packet thread="T-20260811-test" v="1">')
    assert packet.rstrip().endswith("</hyeseongkit-packet>")


def test_other_active_threads_max_two():
    others = [
        {"thread": f"T-20260811-o{i}", "title": f"다른 {i}", "updated": "2026-08-11T01:00:00Z"}
        for i in range(3)
    ]
    packet = build_packet(VIEW, project_name="proj", other_active=others, budget=0)
    assert "T-20260811-o0" in packet
    assert "T-20260811-o1" in packet
    assert "T-20260811-o2" not in packet  # C4: 최대 2줄 (D14=3)


def test_events_block_not_budgeted():
    events = [{"_id": "evt:T-x:1:d:push", "type": "push", "sections": {"todo": "원문"}}]
    packet = build_packet(VIEW, project_name="proj", budget=200, events_raw=events)
    assert "## 이벤트 원문" in packet
    assert "evt:T-x:1:d:push" in packet


def test_carryover_capped_by_budget():
    """이월은 L0지만 무한 증가하므로 예산의 1/4까지만 싣는다 — 전량은 볼트 파일에."""
    view = dict(VIEW, know_carryover=[f"- 이월 항목 {i} 상세한 한국어 본문" for i in range(200)])
    packet = build_packet(view, project_name="proj", budget=2000)
    assert "- 이월 항목 0 상세한 한국어 본문" in packet  # 최신은 남는다
    assert "- 이월 항목 199 상세한 한국어 본문" not in packet
    assert CARRY_NOTE in packet
    full = build_packet(view, project_name="proj", budget=0)
    assert "- 이월 항목 199 상세한 한국어 본문" in full  # budget 0이면 전량
    assert CARRY_NOTE not in full


def test_prompt_wraps_packet():
    packet = build_packet(VIEW, project_name="proj", budget=0)
    prompt = build_prompt(packet)
    assert packet in prompt
    assert "이어서 진행" in prompt
