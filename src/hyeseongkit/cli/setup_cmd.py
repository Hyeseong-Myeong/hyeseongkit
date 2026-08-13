"""`hk setup` — 기기(사용자) 단위 어댑터 설치 (설계서 §9, F2).

산출물은 전부 비커밋·사용자 스코프 — 저장소 안에는 아무것도 쓰지 않는다 (D32):
[1] ~/.claude/commands/hk/*.md  [2] ~/.claude/settings.json 훅 병합 (§10-4)
[3] user 스코프 stdio MCP 등록  [4] Codex/Antigravity 사용자 AGENTS.md (§9-4)  [5] 검증
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from importlib import resources
from pathlib import Path

from ..core.config import GLOBAL_DIR
from .marker import backup_file, merge_marker_block

COMMANDS = ("push", "resume", "status", "decide", "search", "close")


def _claude_dir() -> Path:
    return Path.home() / ".claude"


def _template_text(rel: str) -> str:
    return resources.files("hyeseongkit.templates").joinpath(rel).read_text(encoding="utf-8")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def install_commands() -> list[str]:
    """[1] 슬래시 커맨드 — 파일명 전부 소문자 (R7). 변경된 파일 목록 반환."""
    target = _claude_dir() / "commands" / "hk"
    target.mkdir(parents=True, exist_ok=True)
    changed = []
    for name in COMMANDS:
        text = _template_text(f"claude/commands/hk/{name}.md")
        dst = target / f"{name}.md"
        if not dst.is_file() or dst.read_text(encoding="utf-8") != text:
            dst.write_text(text, encoding="utf-8", newline="\n")
            changed.append(name + ".md")
    return changed


def _is_hk_group(group: dict) -> bool:
    hooks = group.get("hooks", [])
    return bool(hooks) and all(
        h.get("type") == "command" and str(h.get("command", "")).startswith("hk ") for h in hooks
    )


def merge_hooks() -> str:
    """[2] §10-4 키 단위 병합 — `hk `로 시작하는 항목만 hyeseongkit 소유로 간주하고 교체."""
    settings_path = _claude_dir() / "settings.json"
    ours = json.loads(_template_text("claude/hooks.json"))["hooks"]
    if settings_path.is_file():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except ValueError:
            return "⚠️ settings.json 파싱 실패 — 수동 확인 필요, 건드리지 않음"
    else:
        data = {}
    original = json.dumps(data, ensure_ascii=False, sort_keys=True)
    hooks = data.setdefault("hooks", {})
    for event, groups in ours.items():
        existing = hooks.get(event, [])
        kept = [g for g in existing if not _is_hk_group(g)]  # 그 외 기존 항목은 절대 불변
        hooks[event] = kept + groups
    if json.dumps(data, ensure_ascii=False, sort_keys=True) == original:
        return "변경 없음"
    if settings_path.is_file():
        backup_file(GLOBAL_DIR / "backup", settings_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return "훅 병합 완료"


def _claude_cli() -> str | None:
    return shutil.which("claude")


def _run_claude(*args: str) -> tuple[int, str]:
    exe = _claude_cli()
    if not exe:
        return 127, ""
    try:
        r = subprocess.run(
            [exe, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def register_mcp() -> str:
    """[3] user 스코프 stdio MCP — `claude mcp add --scope user hyeseongkit -- hk mcp serve`."""
    if not _claude_cli():
        return (
            "⚠️ claude CLI를 찾을 수 없음 — 수동 등록: "
            "claude mcp add --scope user hyeseongkit -- hk mcp serve"
        )
    code, out = _run_claude("mcp", "list")
    if code == 0 and "hyeseongkit" in out:
        return "이미 등록됨"
    code, out = _run_claude(
        "mcp", "add", "--scope", "user", "hyeseongkit", "--", "hk", "mcp", "serve"
    )
    if code != 0:
        return f"⚠️ 등록 실패: {out.strip()[:200]}"
    return "등록 완료"


def agents_targets() -> list[tuple[str, Path, Path]]:
    """(툴, 존재해야 하는 루트, 사용자 단위 지침 파일) — R14 실측 2026-08-13.

    - Codex: codex-home의 전역 AGENTS.md (`codex-home/src/instructions/mod.rs`,
      "Failed to read global AGENTS.md instructions from"). `AGENTS.local.md`는 모른다
    - Antigravity: Global Customizations Root의 AGENTS.md
      ("append to AGENTS.md in the Global Customizations Root"). 같은 디렉터리에
      `hooks.json`·`mcp_config.json`이 있다
    """
    home = Path.home()
    return [
        ("Codex", home / ".codex", home / ".codex" / "AGENTS.md"),
        ("Antigravity", home / ".gemini" / "config", home / ".gemini" / "config" / "AGENTS.md"),
    ]


def install_agents_blocks() -> list[str]:
    """[4] Codex/Antigravity 사용자 단위 AGENTS.md에 마커 블록 병합 (§9-4, D32).

    저장소 안의 `AGENTS.md`·`.agents/AGENTS.md`는 커밋 대상일 수 있으므로 건드리지
    않는다. 사용자 단위 파일은 모든 프로젝트에서 읽히므로, 블록 본문이
    `.hyeseongkit/project.toml`이 있을 때만 적용된다고 스스로 한정한다.
    """
    block = _template_text("agents_block.md")
    out = []
    for tool, root, path in agents_targets():
        if not root.is_dir():
            out.append(f"{tool} 미설치 — 건너뜀")
            continue
        result = merge_marker_block(
            path, block, dry_run=False, backup_root=GLOBAL_DIR / "backup", create=True
        )
        out.append(f"{tool} {path.name} — {result}")
    return out


def verify() -> str:
    """[4] claude mcp list에 hyeseongkit 표시 확인."""
    if not _claude_cli():
        return "검증 생략 (claude CLI 없음)"
    code, out = _run_claude("mcp", "list")
    if code == 0 and "hyeseongkit" in out:
        return "OK — claude mcp list에 hyeseongkit 확인"
    return "⚠️ claude mcp list에서 hyeseongkit을 찾지 못함"


def installed_state() -> dict[str, str]:
    """doctor [8]용 — 설치본과 패키지 템플릿의 해시 비교 (§9-1 --refresh 동일 로직)."""
    out: dict[str, str] = {}
    target = _claude_dir() / "commands" / "hk"
    for name in COMMANDS:
        tmpl = _sha(_template_text(f"claude/commands/hk/{name}.md"))
        dst = target / f"{name}.md"
        cur = _sha(dst.read_text(encoding="utf-8")) if dst.is_file() else "(없음)"
        out[f"commands/hk/{name}.md"] = "최신" if cur == tmpl else f"갱신 필요({cur})"
    return out


def cmd_setup(args, _settings, _project, _client) -> int:
    label = "--refresh" if args.refresh else "설치"
    print(f"hk setup ({label})")
    changed = install_commands()
    print("[1] 슬래시 커맨드: " + (f"{len(changed)}개 갱신 {changed}" if changed else "변경 없음"))
    print(f"[2] 훅: {merge_hooks()}")
    print(f"[3] MCP: {register_mcp()}")
    print("[4] 사용자 AGENTS.md: " + " / ".join(install_agents_blocks()))
    print(f"[5] 검증: {verify()}")
    return 0
