"""패키징 회귀 방지 — 템플릿이 실제로 배포물에 들어가는지.

편집 가능 설치(`pip install -e`)는 소스 트리를 직접 읽으므로, 템플릿이 저장소에서
빠져 있어도 테스트가 전부 통과한다. **정식 설치(pipx/wheel)에서만 터지는** 종류의 결함이다.
실제로 `.gitignore`의 `HYESEONGKIT.md` 규칙이 같은 이름의 템플릿까지 잡아
`hk init`이 `FileNotFoundError`로 죽었다 (2026-08-13).
"""

from __future__ import annotations

import subprocess
from importlib import resources
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "src" / "hyeseongkit" / "templates"


def test_no_template_is_gitignored():
    """템플릿이 하나라도 무시되면 배포물에서 빠진다.

    "추적 중인가"가 아니라 "무시되는가"를 묻는다 — 아직 `git add` 하지 않은 새 템플릿을
    결함으로 오인하지 않기 위해서다.
    """
    on_disk = [p.relative_to(REPO).as_posix() for p in TEMPLATES.rglob("*") if p.is_file()]
    assert on_disk, "템플릿 디렉터리가 비어 있다"

    # 경로를 stdin이 아니라 인자로 넘긴다 — Windows에서 text=True로 stdin에 쓰면
    # 파이썬이 \n을 \r\n으로 바꿔 git이 경로 끝의 \r까지 이름으로 받아 아무것도 매칭하지 않는다
    r = subprocess.run(
        ["git", "check-ignore", *on_disk],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    ignored = [line.strip() for line in r.stdout.splitlines() if line.strip()]
    assert not ignored, (
        f"다음 템플릿이 .gitignore에 걸린다: {ignored}. "
        "이대로 배포하면 정식 설치에서 FileNotFoundError가 난다."
    )


def test_templates_the_cli_needs_are_readable():
    """코드가 이름으로 여는 템플릿이 패키지 리소스로 실제 읽히는지."""
    needed = [
        "hyeseongkit_manual.md",
        "agents_block.md",
        "claude/hooks.json",
        *[
            f"claude/commands/hk/{n}.md"
            for n in ("push", "resume", "status", "decide", "search", "close")
        ],
    ]
    for rel in needed:
        text = resources.files("hyeseongkit.templates").joinpath(rel).read_text(encoding="utf-8")
        assert text.strip(), f"{rel}가 비어 있다"
