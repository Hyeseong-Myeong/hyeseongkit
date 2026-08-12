"""`hk setup` — 기기(사용자) 단위 Claude Code 어댑터 설치 (설계서 §9, F2).

산출물은 전부 비커밋·사용자 스코프:
[1] ~/.claude/commands/hk/*.md  [2] ~/.claude/settings.json 훅 병합 (§10-4)
[3] user 스코프 stdio MCP 등록  [4] 검증
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from importlib import resources
from pathlib import Path

from ..core.config import GLOBAL_DIR
from ..core.util import iso_to_compact, iso_utc

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
        backup = GLOBAL_DIR / "backup" / iso_to_compact(iso_utc())
        backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(settings_path, backup / "settings.json")
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
    print(f"[4] 검증: {verify()}")
    return 0
