"""AGENTS.md 지침 블록 — 커밋 대상 무수정(D32) · 사용자 스코프 설치 (§9-3, §10-5).

`hk init`은 저장소 안 `AGENTS.md`를 **읽지도 쓰지도** 않는다. 블록은 기기 단위
`hk setup`이 Codex/Antigravity의 사용자 단위 파일에만 넣는다.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from hyeseongkit.cli import project, setup_cmd
from hyeseongkit.cli.marker import MARKER_END, MARKER_START, merge_marker_block

TEAM_RULES = "# Agent Rules\n\n- 팀 공용 규칙이다. 개인 도구 지침이 섞이면 안 된다.\n"


class _FakeClient:
    """HubClient.request와 같은 시그니처 — 프로젝트가 아직 없는 허브를 흉내낸다."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, path: str, *, params=None, json=None):
        self.calls.append((method, path))
        if path == "/v1/projects" and method == "GET":
            return {"projects": []}
        return {"project_id": "p-test", "name": "demo", "sensitivity": "tech"}


@pytest.fixture
def isolated_home(monkeypatch, tmp_path) -> Path:
    """Path.home()과 백업 위치를 tmp로 격리 — 실 사용자 홈에 절대 쓰지 않는다."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(setup_cmd, "GLOBAL_DIR", home / ".hyeseongkit")
    return home


# ── hk init: 커밋 대상 무수정 (D32) ────────────────────────────────


def test_init_leaves_committed_agents_md_untouched(monkeypatch, tmp_path, capsys):
    root = tmp_path / "repo"
    (root / ".agents").mkdir(parents=True)
    for rel in ("AGENTS.md", ".agents/AGENTS.md"):
        (root / rel).write_text(TEAM_RULES, encoding="utf-8", newline="\n")
    before = {rel: (root / rel).read_bytes() for rel in ("AGENTS.md", ".agents/AGENTS.md")}

    monkeypatch.chdir(root)
    monkeypatch.setattr("hyeseongkit.cli.doctor.cmd_doctor", lambda *a, **k: 0)
    args = types.SimpleNamespace(dry_run=False, name="demo", force_new=False)

    assert project.cmd_init(args, None, None, _FakeClient()) == 0

    for rel, raw in before.items():
        assert (root / rel).read_bytes() == raw, f"{rel}이 수정됐다 — D32 위반"
        assert MARKER_START not in raw.decode("utf-8")
    # 프로젝트 단위 산출물은 그대로 생성된다 (전부 gitignore 대상)
    assert (root / ".hyeseongkit" / "project.toml").is_file()
    assert (root / "HYESEONGKIT.md").is_file()
    assert "건드리지 않음" in capsys.readouterr().out


# ── hk setup: 사용자 스코프에만 설치 ───────────────────────────────


def test_agents_targets_are_outside_any_repo(isolated_home):
    targets = setup_cmd.agents_targets()
    assert [t[0] for t in targets] == ["Codex", "Antigravity"]
    for _tool, _root, path in targets:
        assert path.name == "AGENTS.md"
        assert isolated_home in path.parents


def test_setup_creates_block_only_for_installed_tools(isolated_home):
    (isolated_home / ".codex").mkdir()  # Antigravity(~/.gemini/config)는 없는 상태

    out = setup_cmd.install_agents_blocks()

    codex_md = isolated_home / ".codex" / "AGENTS.md"
    assert codex_md.is_file()
    text = codex_md.read_text(encoding="utf-8")
    assert MARKER_START in text and MARKER_END in text
    # 기기 전역 파일이므로 블록이 스스로 적용 범위를 한정해야 한다
    assert ".hyeseongkit/project.toml" in text
    assert not (isolated_home / ".gemini" / "config" / "AGENTS.md").exists()
    assert any("Antigravity 미설치" in line for line in out)


def test_setup_preserves_existing_content_and_is_idempotent(isolated_home):
    (isolated_home / ".codex").mkdir()
    codex_md = isolated_home / ".codex" / "AGENTS.md"
    codex_md.write_text("# 내 전역 규칙\n\n- 한국어로 답한다.\n", encoding="utf-8", newline="\n")

    setup_cmd.install_agents_blocks()
    first = codex_md.read_text(encoding="utf-8")
    assert first.startswith("# 내 전역 규칙")
    assert "- 한국어로 답한다." in first

    out = setup_cmd.install_agents_blocks()
    assert codex_md.read_text(encoding="utf-8") == first
    assert any("변경 없음" in line for line in out)


# ── merge_marker_block 단위 ────────────────────────────────────────


def test_marker_block_updates_in_place(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text(
        f"머리말\n\n{MARKER_START} -->\n낡은 내용\n{MARKER_END}\n\n꼬리말\n",
        encoding="utf-8",
        newline="\n",
    )
    new = f"{MARKER_START} -->\n새 내용\n{MARKER_END}"

    assert merge_marker_block(path, new, dry_run=False, backup_root=tmp_path / "b") == (
        "마커 블록 갱신"
    )

    text = path.read_text(encoding="utf-8")
    assert "낡은 내용" not in text
    assert "새 내용" in text
    assert text.startswith("머리말") and text.rstrip().endswith("꼬리말")
    assert (tmp_path / "b").is_dir()  # 수정 전 백업 (R10)


def test_marker_block_skips_missing_file_unless_create(tmp_path):
    path = tmp_path / "AGENTS.md"
    block = f"{MARKER_START} -->\n내용\n{MARKER_END}"

    assert merge_marker_block(path, block, dry_run=False, backup_root=tmp_path / "b") == (
        "건너뜀 (파일 없음)"
    )
    assert not path.exists()

    assert (
        merge_marker_block(path, block, dry_run=False, backup_root=tmp_path / "b", create=True)
        == "생성"
    )
    assert path.read_text(encoding="utf-8") == block + "\n"


def test_marker_block_dry_run_writes_nothing(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text(TEAM_RULES, encoding="utf-8", newline="\n")
    block = f"{MARKER_START} -->\n내용\n{MARKER_END}"

    assert merge_marker_block(path, block, dry_run=True, backup_root=tmp_path / "b") == (
        "마커 블록 추가"
    )
    assert path.read_text(encoding="utf-8") == TEAM_RULES
    assert not (tmp_path / "b").exists()
