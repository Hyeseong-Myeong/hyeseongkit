"""프로젝트 식별 (설계서 §7, D19) — git remote URL 정규화 → 해시. 절대경로 금지."""

from __future__ import annotations

import hashlib
import re
import subprocess

_DEFAULT_PORTS = {"http": "80", "https": "443", "ssh": "22", "git": "9418"}


def normalize_remote(url: str) -> str | None:
    """§7-1 2단계 정규화. 로컬 경로 remote는 None (절대경로 금지 — 대화형 명명으로 유도)."""
    u = (url or "").strip()
    if not u:
        return None
    if u.startswith(("/", ".", "\\", "~")) or re.match(r"^[A-Za-z]:[\\/]", u):
        return None  # 파일시스템 경로 remote — 식별자로 쓰지 않는다
    if "://" in u:
        m = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*)://(?:[^/@]*@)?([^/?#]+)(/[^?#]*)?$", u)
        if not m:
            return None
        scheme = m.group(1).lower()
        hostport = m.group(2)
        path = (m.group(3) or "").rstrip("/")
        if ":" in hostport:
            host, port = hostport.rsplit(":", 1)
        else:
            host, port = hostport, ""
        if port and port == _DEFAULT_PORTS.get(scheme):
            port = ""  # 기본 포트 제거, 비표준 포트는 host_port 형태로 유지 (§7-1)
        canonical = host + (f"_{port}" if port else "") + path
    else:
        m = re.match(r"^(?:[\w.+-]+@)?([\w.-]+):(.+)$", u)
        if not m:
            return None
        canonical = m.group(1) + "/" + m.group(2).lstrip("/")
    canonical = canonical.rstrip("/")
    if canonical.endswith(".git"):
        canonical = canonical[:-4].rstrip("/")
    return canonical.lower() or None


def project_id_of(canonical: str) -> str:
    return "p-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def name_of(canonical: str) -> str:
    return canonical.rsplit("/", 1)[-1]


def _git(cwd: str, *args: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def remote_url(cwd: str = ".") -> str | None:
    """remote.origin.url → 없으면 첫 remote (§7-1 1단계)."""
    url = _git(cwd, "config", "--get", "remote.origin.url")
    if url:
        return url
    names = _git(cwd, "remote")
    if names:
        first = names.splitlines()[0].strip()
        if first:
            return _git(cwd, "remote", "get-url", first)
    return None


def git_state(cwd: str = ".") -> dict | None:
    """checkpoint용 git 상태 (§2-2). git 저장소가 아니면 None."""
    branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    if branch is None:
        return None
    head = _git(cwd, "rev-parse", "--short", "HEAD")
    porcelain = _git(cwd, "status", "--porcelain") or ""
    dirty = len([line for line in porcelain.splitlines() if line.strip()])
    return {"branch": branch, "head": head, "dirty": dirty}
