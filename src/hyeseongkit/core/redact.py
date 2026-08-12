"""시크릿 마스킹 (설계서 §6) — fail-closed (R1).

- 규칙 MK01~MK12를 순서대로 적용. 치환: 매치 전체 → ⟦REDACTED:<id>⟧
- MK08만 키 이름·구분자를 남기고 값 부분만 치환 (§6-2)
- 치환 후 재스캔에서 재적중하면 치환 로직 버그로 간주하고 중단 (§6-3)
- 프로젝트 추가 규칙(project.toml [mask] extra_rules)은 코어 규칙에 추가만 가능
"""

from __future__ import annotations

import re
from collections.abc import Iterable

MARKER = "⟦REDACTED:{rule}⟧"  # ⟦REDACTED:MKxx⟧


class RedactionError(Exception):
    """마스킹 실패 — 어떤 것도 저장·전송하면 안 된다 (§6-3, exit 3)."""


_MK08 = (
    r"(?i)\b(api[_-]?key|apikey|secret|token|passwd|password|client[_-]?secret"
    r"|access[_-]?key|authorization)"
    r"(\s*[:=]\s*[\"']?)"
    r"([A-Za-z0-9_\-.+/=]{8,})"  # 값 문자 집합을 라틴·기호로 한정 — 한국어 산문 오탐 방지 (C2)
)

_RAW_RULES: list[tuple[str, str]] = [
    (
        "MK01",
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    ),
    ("MK02", r"\bAKIA[0-9A-Z]{16}\b"),
    ("MK03", r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"),
    ("MK04", r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
    ("MK05", r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b"),
    ("MK06", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("MK07", r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ("MK08", _MK08),
    ("MK09", r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*"),
    ("MK10", r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@"),
    ("MK11", r"\b100\.(?:6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.\d{1,3}\.\d{1,3}\b"),
    ("MK12", r"\bhk_[a-f0-9]{40}\b"),
]

# re.ASCII: \b·\w를 ASCII로 한정 — 한글이 붙은 식별자("100.64.0.1에")에서도
# 경계가 성립하게 한다 (§6-5 벡터. 유니코드 \w에서는 한글이 word 문자라 \b가 실패)
CORE_RULES: list[tuple[str, re.Pattern[str]]] = [
    (rid, re.compile(p, re.ASCII)) for rid, p in _RAW_RULES
]


def _compiled_extra(extra: Iterable[str]) -> list[tuple[str, re.Pattern[str]]]:
    return [(f"EX{i:02d}", re.compile(pat, re.ASCII)) for i, pat in enumerate(extra or (), start=1)]


def detect(text: str, extra: Iterable[str] = ()) -> list[str]:
    """탐지 모드 — 원시 매치가 있는 규칙 id 목록 (§6-4 서버측 재검사)."""
    return [rid for rid, pat in CORE_RULES + _compiled_extra(extra) if pat.search(text)]


def _mk08_repl(m: re.Match[str]) -> str:
    return m.group(1) + m.group(2) + MARKER.format(rule="MK08")


def mask(text: str, extra: Iterable[str] = ()) -> tuple[str, list[str]]:
    """치환 모드 — (마스킹된 텍스트, 적중 규칙 id). 실패 시 RedactionError (fail-closed)."""
    try:
        hits: list[str] = []
        for rid, pat in CORE_RULES + _compiled_extra(extra):
            if rid == "MK08":
                text, n = pat.subn(_mk08_repl, text)
            else:
                text, n = pat.subn(MARKER.format(rule=rid), text)
            if n:
                hits.append(rid)
        if detect(text, extra):
            raise RedactionError("치환 후 재스캔 재적중 — 치환 로직 버그로 간주 (§6-3)")
        return text, hits
    except RedactionError:
        raise
    except Exception as exc:  # noqa: BLE001 — 어떤 예외든 fail-closed
        raise RedactionError(f"마스킹 실행 실패: {exc}") from exc


def mask_obj(obj: object, extra: Iterable[str] = ()) -> tuple[object, list[str]]:
    """dict/list/str을 재귀 마스킹. 문자열이 아닌 값은 그대로 둔다."""
    hits: list[str] = []

    def _walk(v: object) -> object:
        if isinstance(v, str):
            masked, h = mask(v, extra)
            hits.extend(h)
            return masked
        if isinstance(v, dict):
            return {k: _walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_walk(x) for x in v]
        return v

    out = _walk(obj)
    seen: set[str] = set()
    ordered = [h for h in hits if not (h in seen or seen.add(h))]
    return out, ordered


def detect_obj(obj: object, extra: Iterable[str] = ()) -> list[str]:
    """dict/list/str 재귀 탐지 — 서버측 재검사용 (§6-4)."""
    hits: list[str] = []

    def _walk(v: object) -> None:
        if isinstance(v, str):
            hits.extend(detect(v, extra))
        elif isinstance(v, dict):
            for x in v.values():
                _walk(x)
        elif isinstance(v, list):
            for x in v:
                _walk(x)

    _walk(obj)
    seen: set[str] = set()
    return [h for h in hits if not (h in seen or seen.add(h))]
