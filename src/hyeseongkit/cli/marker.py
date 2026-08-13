"""마커 블록 병합 · 수정 전 백업 — `hk setup`/`hk init` 공용 (설계서 §10-5, R10).

기존 파일은 덮어쓰지 않고 `<!-- hyeseongkit:start -->` ~ `<!-- hyeseongkit:end -->`
안에만 삽입/갱신한다. 백업 위치는 호출자가 정한다 — `hk init`은 저장소 안
(`.hyeseongkit/backup/`), `hk setup`은 사용자 스코프(`~/.hyeseongkit/backup/`).
"""

from __future__ import annotations

import difflib
import shutil
from pathlib import Path

from ..core.util import iso_to_compact, iso_utc

MARKER_START = "<!-- hyeseongkit:start"
MARKER_END = "<!-- hyeseongkit:end -->"


def backup_file(backup_root: Path, target: Path) -> None:
    """수정 전 원본을 `<backup_root>/<ts>/<파일명>`에 복사 (R10)."""
    dst = backup_root / iso_to_compact(iso_utc()) / target.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, dst)


def merge_marker_block(
    path: Path, block: str, *, dry_run: bool, backup_root: Path, create: bool = False
) -> str:
    """마커 쌍이 있으면 내부만 교체, 없으면 파일 끝에 추가. 결과 설명 반환.

    create=False면 파일이 없을 때 아무것도 하지 않는다 — 그 툴을 쓰지 않는
    환경에 파일을 만들어 두지 않기 위해서다.
    """
    exists = path.is_file()
    if not exists and not create:
        return "건너뜀 (파일 없음)"
    text = path.read_text(encoding="utf-8") if exists else ""
    if MARKER_START in text and MARKER_END in text:
        start = text.index(MARKER_START)
        end = text.index(MARKER_END) + len(MARKER_END)
        new_text = text[:start] + block.strip() + text[end:]
        action = "마커 블록 갱신"
    else:
        if not text:
            sep = ""
        elif text.endswith("\n\n"):
            sep = ""
        else:
            sep = "\n" if text.endswith("\n") else "\n\n"
        new_text = text + sep + block.strip() + "\n"
        action = "마커 블록 추가" if exists else "생성"
    if new_text == text:
        return "변경 없음"
    diff = "\n".join(
        difflib.unified_diff(
            text.splitlines(), new_text.splitlines(), fromfile=str(path), tofile=str(path), n=1
        )
    )
    print(f"[{path.name}] {action} 예정:\n{diff}")
    if not dry_run:
        if exists:
            backup_file(backup_root, path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text, encoding="utf-8", newline="\n")
    return action
