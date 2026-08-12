"""공용 유틸 — 시간(UTC 저장·KST 표기), slug, 토큰 추정, 라인 단위 처리.

저장은 전부 UTC ISO-8601, 렌더 시에만 KST 표기 (설계서 §1).
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9), "KST")

SENSITIVITIES = ("public", "tech", "career", "personal")
_SENS_ORDER = {s: i for i, s in enumerate(SENSITIVITIES)}

SECTION_KEYS = ("context", "done", "todo", "know", "questions")
REQUIRED_SECTIONS = ("todo", "know")  # L0 보호 (§3-3 ④)


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_utc(dt: datetime | None = None) -> str:
    """저장용 — 예: 2026-08-11T02:31:00Z."""
    return (dt or utc_now()).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_to_compact(ts: str) -> str:
    """이벤트 _id용 — 2026-08-11T02:31:00Z → 20260811T023100Z (§2-2)."""
    return ts.replace("-", "").replace(":", "")


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def kst_minute(ts: str) -> str:
    """렌더 frontmatter 표기 — 예: 2026-08-11T11:31+09:00 (§8-2)."""
    return parse_iso(ts).astimezone(KST).strftime("%Y-%m-%dT%H:%M+09:00")


def kst_human(ts: str) -> str:
    """패킷 메타 표기 — 예: 2026-08-11 11:31 (§3-7)."""
    return parse_iso(ts).astimezone(KST).strftime("%Y-%m-%d %H:%M")


def kst_date(ts: str | None = None) -> str:
    dt = parse_iso(ts) if ts else utc_now()
    return dt.astimezone(KST).strftime("%Y-%m-%d")


def kst_date_compact(dt: datetime | None = None) -> str:
    return (dt or utc_now()).astimezone(KST).strftime("%Y%m%d")


def ascii_slug(text: str, max_len: int = 40) -> str:
    """비ASCII 제거 → 공백 하이픈화 → 소문자, 최대 40자 (§3-3, R7)."""
    s = text.encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:max_len].rstrip("-")


def random_slug() -> str:
    """title에서 slug가 전부 소실됐을 때의 폴백 — t-<랜덤4hex> (§3-3)."""
    return "t-" + secrets.token_hex(2)


def estimate_tokens(text: str) -> int:
    """한글 보수 추정 len//3 (§3-6)."""
    return len(text) // 3


def normalize_ws(line: str) -> str:
    return " ".join(line.split())


_CARRY_LINE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|\|.+\|\s*$)")


def carryover_lines(text: str) -> list[str]:
    """know 이월 비교 단위 — 불릿·표 행 (§2-4)."""
    return [line.strip() for line in (text or "").splitlines() if _CARRY_LINE.match(line)]


def sensitivity_max(a: str | None, b: str | None) -> str:
    """의심 시 높은 쪽 (D22 fail-safe)."""
    av = _SENS_ORDER.get(a or "", -1)
    bv = _SENS_ORDER.get(b or "", -1)
    if av < 0 and bv < 0:
        return "tech"
    return a if av >= bv else b  # type: ignore[return-value]
