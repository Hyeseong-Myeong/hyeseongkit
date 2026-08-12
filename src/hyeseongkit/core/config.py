"""클라이언트 설정 (설계서 §11-1) — 우선순위: 환경변수 → project.toml → ~/.hyeseongkit/config.toml.

기기 고유값은 저장소가 아니라 전역 설정 파일(~/.hyeseongkit/config.env)이나
셸 환경변수에만 둔다 (D28, §0-3-1).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

GLOBAL_DIR = Path.home() / ".hyeseongkit"
PROJECT_DIR_NAME = ".hyeseongkit"
PROJECT_TOML = "project.toml"


def load_global_env() -> None:
    """~/.hyeseongkit/config.env → 환경변수. 이미 설정된 환경변수가 우선 (§0-3-1)."""
    f = GLOBAL_DIR / "config.env"
    if f.is_file():
        for k, v in (dotenv_values(f) or {}).items():
            if v is not None and k not in os.environ:
                os.environ[k] = v


def _load_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


@dataclass
class ProjectConfig:
    root: Path
    project_id: str
    canonical: str
    name: str
    sensitivity: str = "tech"
    store_mode: str = "event"
    extra_rules: list[str] = field(default_factory=list)


def find_project_root(start: Path | None = None) -> Path | None:
    """cwd에서 위로 올라가며 .hyeseongkit/project.toml 탐색."""
    cur = (start or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / PROJECT_DIR_NAME / PROJECT_TOML).is_file():
            return p
    return None


def load_project(root: Path) -> ProjectConfig | None:
    data = _load_toml(root / PROJECT_DIR_NAME / PROJECT_TOML)
    proj = data.get("project", {})
    if not proj.get("project_id"):
        return None
    return ProjectConfig(
        root=root,
        project_id=proj["project_id"],
        canonical=proj.get("canonical", ""),
        name=proj.get("name", ""),
        sensitivity=proj.get("sensitivity", "tech"),
        store_mode=proj.get("store_mode", "event"),
        extra_rules=list(data.get("mask", {}).get("extra_rules", [])),
    )


def current_project() -> ProjectConfig | None:
    root = find_project_root()
    return load_project(root) if root else None


def write_project_toml(root: Path, pc: ProjectConfig) -> Path:
    """§10-2 전문 형식으로 기록. 값은 전부 결정적이라 커밋 불필요 (D20)."""
    d = root / PROJECT_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    extra = ", ".join(f'"{r}"' for r in pc.extra_rules)
    text = (
        "[project]\n"
        "schema = 1\n"
        f'project_id = "{pc.project_id}"\n'
        f'canonical = "{pc.canonical}"\n'
        f'name = "{pc.name}"\n'
        f'sensitivity = "{pc.sensitivity}"        # D22(c): 프로젝트별 지정\n'
        f'store_mode = "{pc.store_mode}"        # D5. 현재 event만 구현됨\n'
        "\n"
        "[mask]\n"
        f"extra_rules = [{extra}]            # 프로젝트 전용 마스킹 정규식 (추가만 가능)\n"
    )
    path = d / PROJECT_TOML
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


@dataclass
class ClientSettings:
    hub_url: str | None
    api_token: str | None
    device_id: str | None
    budget: int = 2000
    tool: str = "manual"


def load_client_settings() -> ClientSettings:
    load_global_env()
    g = _load_toml(GLOBAL_DIR / "config.toml")
    dev = (
        os.environ.get("HK_DEVICE_ID") or g.get("device", {}).get("device_id") or g.get("device_id")
    )
    try:
        budget = int(os.environ.get("HK_TOKEN_BUDGET") or g.get("budget", 2000))
    except ValueError:
        budget = 2000
    return ClientSettings(
        hub_url=(os.environ.get("HK_HUB_URL") or "").rstrip("/") or None,
        api_token=os.environ.get("HK_API_TOKEN") or None,
        device_id=dev,
        budget=budget,
        tool=os.environ.get("HK_TOOL") or "manual",
    )
