"""fold 규칙 (설계서 §2-4) — know 이월 보존(F1 (a)) 포함."""

from hyeseongkit.hub.fold import fold_events


def _push(ts: str, know: str, title: str = "제목", **kw) -> dict:
    return {
        "_id": f"evt:T-x:{ts}:desktop:push",
        "type": "push",
        "thread": "T-x",
        "project_id": "p-1",
        "ts": ts,
        "tool": "claude-code",
        "device": "desktop",
        "sensitivity": "tech",
        "title": title,
        "sections": {"todo": "- 할 일", "know": know},
        **kw,
    }


def test_fold_empty():
    assert fold_events([]) is None


def test_know_carryover_preserved():
    events = [
        _push("2026-08-11T01:00:00Z", "- 항목 A\n- 항목 B"),
        _push("2026-08-11T02:00:00Z", "- 항목 B\n- 항목 C"),
    ]
    view = fold_events(events)
    assert view["sections"]["know"] == "- 항목 B\n- 항목 C"
    assert view["know_carryover"] == ["- 항목 A"]  # 빠진 라인은 자동 보존 (§2-4)
    assert view["events"] == 2


def test_know_carryover_promotion():
    # 이월 항목을 다음 push의 know에 포함하면 본문으로 승격된다
    events = [
        _push("2026-08-11T01:00:00Z", "- 항목 A"),
        _push("2026-08-11T02:00:00Z", "- 항목 B"),
        _push("2026-08-11T03:00:00Z", "- 항목 A"),
    ]
    view = fold_events(events)
    assert view["sections"]["know"] == "- 항목 A"
    assert view["know_carryover"] == ["- 항목 B"]
    assert "- 항목 A" not in view["know_carryover"]


def test_decide_appends():
    events = [
        _push("2026-08-11T01:00:00Z", "- k"),
        {
            "_id": "evt:T-x:2026:desktop:decide",
            "type": "decide",
            "thread": "T-x",
            "ts": "2026-08-11T02:00:00Z",
            "tool": "codex",
            "device": "macbook",
            "decision": {"text": "결정 원문", "rationale": "근거", "rejected": "기각안"},
        },
    ]
    view = fold_events(events)
    assert len(view["decisions"]) == 1
    d = view["decisions"][0]
    assert d["text"] == "결정 원문"
    assert d["date"]  # ts에서 KST 날짜 보충
    # decide는 last_tool을 바꾸지 않는다 (§2-4 — push만 갱신)
    assert view["last_tool"] == "claude-code"


def test_checkpoint_updates_only_timestamp():
    events = [
        _push("2026-08-11T01:00:00Z", "- k"),
        {
            "_id": "evt:T-x:2026:desktop:checkpoint",
            "type": "checkpoint",
            "thread": "T-x",
            "ts": "2026-08-11T05:00:00Z",
            "tool": "claude-code",
            "device": "desktop",
            "checkpoint": {"reason": "precompact"},
        },
    ]
    view = fold_events(events)
    assert view["updated"] == "2026-08-11T05:00:00Z"
    assert view["sections"]["know"] == "- k"  # sections 불변


def test_close_and_reopen():
    events = [
        _push("2026-08-11T01:00:00Z", "- k"),
        {
            "_id": "evt:T-x:2026:desktop:close",
            "type": "close",
            "thread": "T-x",
            "ts": "2026-08-11T02:00:00Z",
            "device": "desktop",
            "outcome": "dropped",
        },
    ]
    view = fold_events(events)
    assert view["status"] == "dropped"
    events.append(_push("2026-08-11T03:00:00Z", "- k2", reopen=True))
    assert fold_events(events)["status"] == "active"


def test_sensitivity_never_downgrades():
    events = [
        _push("2026-08-11T01:00:00Z", "- k", sensitivity="career"),
        _push("2026-08-11T02:00:00Z", "- k", sensitivity="tech"),
    ]
    assert fold_events(events)["sensitivity"] == "career"  # 의심 시 높은 쪽 (D22)
