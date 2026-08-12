from hyeseongkit.core.util import (
    ascii_slug,
    carryover_lines,
    estimate_tokens,
    iso_to_compact,
    kst_minute,
    sensitivity_max,
)


def test_ascii_slug_korean_removed():
    assert ascii_slug("hyeseongkit 세션 영속화 — 설계") == "hyeseongkit"


def test_ascii_slug_basic():
    assert ascii_slug("Session Persistence Design!") == "session-persistence-design"


def test_ascii_slug_empty_when_all_korean():
    assert ascii_slug("세션 영속화") == ""


def test_ascii_slug_max_40():
    assert len(ascii_slug("a" * 100)) == 40


def test_iso_to_compact():
    assert iso_to_compact("2026-08-11T02:31:00Z") == "20260811T023100Z"


def test_kst_minute():
    # UTC 02:31 → KST 11:31 (§8-2 예시와 동일)
    assert kst_minute("2026-08-11T02:31:00Z") == "2026-08-11T11:31+09:00"


def test_estimate_tokens():
    assert estimate_tokens("가나다라마바") == 2  # len//3 (§3-6)


def test_carryover_lines_bullets_and_tables_only():
    text = "- 항목 A\n산문 줄은 제외\n1. 번호 항목\n| 표 | 행 |\n\n* 별 불릿"
    assert carryover_lines(text) == ["- 항목 A", "1. 번호 항목", "| 표 | 행 |", "* 별 불릿"]


def test_sensitivity_max():
    assert sensitivity_max("tech", "career") == "career"  # 의심 시 높은 쪽 (D22)
    assert sensitivity_max("personal", "public") == "personal"
    assert sensitivity_max(None, None) == "tech"
