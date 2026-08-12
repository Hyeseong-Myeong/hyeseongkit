"""CLI 입출력 헬퍼 — Windows 인코딩 대응 (R15) · 본문 입력 · 섹션 파싱.

한국어 본문은 CLI 인자가 아니라 stdin/파일/$EDITOR로 받는다 (§11-2).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ..core.util import SECTION_KEYS


def setup_stdio() -> None:
    """CP949 콘솔에서 UnicodeEncodeError 방지 (R15)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_SECTION_ALIASES = {
    "컨텍스트": "context",
    "context": "context",
    "한 일": "done",
    "한일": "done",
    "done": "done",
    "할 일": "todo",
    "할일": "todo",
    "todo": "todo",
    "알아야 할 것": "know",
    "알아야할것": "know",
    "know": "know",
    "미결 질문": "questions",
    "미결질문": "questions",
    "questions": "questions",
}

EDITOR_TEMPLATE = """# (첫 줄 level-1 heading이 제목이 된다 — 이 줄을 제목으로 교체)

## 컨텍스트

## 한 일

## 할 일

## 알아야 할 것

## 미결 질문
"""


def parse_sections(text: str) -> tuple[str | None, dict[str, str]]:
    """마크다운 heading으로 다섯 섹션을 파싱. 반환: (title, sections).

    heading 이름은 한국어(컨텍스트/한 일/할 일/알아야 할 것/미결 질문) 또는
    영문 키(context/done/todo/know/questions)를 허용한다. 번호 접두(`## 1.`)도 허용.
    """
    import re

    title: str | None = None
    sections: dict[str, list[str]] = {}
    current: str | None = None
    heading_re = re.compile(r"^(#{1,3})\s*(?:\d+[.)]\s*)?(.+?)\s*$")
    for line in text.splitlines():
        m = heading_re.match(line)
        if m:
            name = m.group(2).strip().lower()
            name_ko = m.group(2).strip()
            key = _SECTION_ALIASES.get(name) or _SECTION_ALIASES.get(name_ko)
            if key:
                current = key
                sections.setdefault(key, [])
                continue
            if len(m.group(1)) == 1 and title is None:
                title = m.group(2).strip()
                current = None
                continue
        if current:
            sections[current].append(line)
    out = {k: "\n".join(v).strip() for k, v in sections.items() if "\n".join(v).strip()}
    out = {k: v for k, v in out.items() if k in SECTION_KEYS}
    return title, out


def read_body(file: str | None, use_stdin: bool, *, editor_template: str | None = None) -> str:
    """--file / --stdin / $EDITOR 순서로 본문 확보 (§11-4 ②)."""
    if file:
        return Path(file).read_text(encoding="utf-8")
    if use_stdin or not sys.stdin.isatty():
        data = sys.stdin.buffer.read()
        return data.decode("utf-8", errors="replace")
    return _edit(editor_template or EDITOR_TEMPLATE)


def _edit(template: str) -> str:
    editor = os.environ.get("EDITOR") or ("notepad" if os.name == "nt" else "vi")
    fd, path = tempfile.mkstemp(suffix=".md", prefix="hk-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(template)
        subprocess.run([editor, path], check=False)
        return Path(path).read_text(encoding="utf-8")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def read_hook_stdin() -> dict:
    """훅 JSON(transcript_path, cwd 등)을 stdin에서 읽는다 (§9-2). 실패해도 빈 dict."""
    try:
        if sys.stdin.isatty():
            return {}
        raw = sys.stdin.buffer.read()
        if not raw.strip():
            return {}
        return json.loads(raw.decode("utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}


def normalize_transcript_path(p: str | None) -> str | None:
    """절대경로(사용자명 노출)를 `~` 표기로 정규화해 저장 (§2-2 금지 필드)."""
    if not p:
        return None
    norm = str(p).replace("\\", "/")
    home = str(Path.home()).replace("\\", "/")
    if norm.lower().startswith(home.lower()):
        norm = "~" + norm[len(home) :]
    return norm


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)
