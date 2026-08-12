"""`hk init` / `hk link` — 프로젝트 등록·수동 연결 (설계서 §7, §10).

- 산출물은 전부 gitignore (D20). project.toml이 유일한 프로젝트 단위 산출물 (F2)
- 기존 파일은 마커 블록 안에만 삽입/갱신, 수정 전 백업 (R10)
- 오분기 방지 가드: 같은 name의 기존 프로젝트가 있으면 생성을 멈추고 hk link 안내 (§7)
"""

from __future__ import annotations

import difflib
import shutil
from importlib import resources
from pathlib import Path

from ..core import identity
from ..core.config import (
    PROJECT_DIR_NAME,
    ClientSettings,
    ProjectConfig,
    load_project,
    write_project_toml,
)
from ..core.transport import HubClient, HubError, HubUnreachable
from ..core.util import ascii_slug, iso_to_compact, iso_utc
from . import io

GITIGNORE_BLOCK = "# hyeseongkit (커밋하지 않음 — D20/F2)\n.hyeseongkit/\nHYESEONGKIT.md\n"

MARKER_START = "<!-- hyeseongkit:start"
MARKER_END = "<!-- hyeseongkit:end -->"


def _template(name: str) -> str:
    return resources.files("hyeseongkit.templates").joinpath(name).read_text(encoding="utf-8")


def _backup(root: Path, target: Path) -> None:
    """수정 전 원본을 .hyeseongkit/backup/<ts>/에 복사 (§10-1)."""
    ts = iso_to_compact(iso_utc())
    dst = root / PROJECT_DIR_NAME / "backup" / ts / target.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, dst)


def _merge_marker_block(path: Path, block: str, *, dry_run: bool, root: Path) -> str:
    """마커 쌍이 있으면 내부만 교체, 없으면 파일 끝에 추가 (§10-5). 결과 설명 반환."""
    text = path.read_text(encoding="utf-8")
    if MARKER_START in text and MARKER_END in text:
        start = text.index(MARKER_START)
        end = text.index(MARKER_END) + len(MARKER_END)
        new_text = text[:start] + block.strip() + text[end:]
        action = "마커 블록 갱신"
    else:
        sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        new_text = text + sep + block.strip() + "\n"
        action = "마커 블록 추가"
    if new_text == text:
        return "변경 없음"
    diff = "\n".join(
        difflib.unified_diff(
            text.splitlines(), new_text.splitlines(), fromfile=str(path), tofile=str(path), n=1
        )
    )
    print(f"[{path.name}] {action} 예정:\n{diff}")
    if not dry_run:
        _backup(root, path)
        path.write_text(new_text, encoding="utf-8", newline="\n")
    return action


def _merge_gitignore(root: Path, *, dry_run: bool) -> str:
    path = root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    missing = [
        line
        for line in GITIGNORE_BLOCK.strip().splitlines()
        if not line.startswith("#") and line not in existing.splitlines()
    ]
    if not missing:
        return "변경 없음"
    addition = "\n# hyeseongkit (커밋하지 않음 — D20/F2)\n" + "\n".join(missing) + "\n"
    if not dry_run:
        if path.is_file():
            _backup(root, path)
        sep = "" if (not existing or existing.endswith("\n")) else "\n"
        path.write_text(existing + sep + addition.lstrip("\n"), encoding="utf-8", newline="\n")
    return f"항목 추가: {missing}"


def _resolve_identity(root: Path, name_arg: str | None) -> tuple[str, str, str]:
    """(project_id, canonical, name) — §7-1 알고리즘 1·2·5단계."""
    url = identity.remote_url(str(root))
    canonical = identity.normalize_remote(url) if url else None
    if not canonical:
        slug = ascii_slug(name_arg or "")
        while not slug:
            entered = input("git remote가 없습니다 — 프로젝트 이름(ASCII slug): ").strip()
            slug = ascii_slug(entered)
        canonical = f"named:{slug}"
    return identity.project_id_of(canonical), canonical, identity.name_of(canonical)


def cmd_init(
    args, settings: ClientSettings, _project: ProjectConfig | None, client: HubClient | None
) -> int:
    root = Path.cwd()
    dry = bool(args.dry_run)
    if client is None:
        io.eprint("허브 미설정 — HK_HUB_URL/HK_API_TOKEN 필요 (등록은 허브 경유, D20)")
        return 2

    existing = load_project(root) if (root / PROJECT_DIR_NAME).is_dir() else None
    if existing:
        project_id, canonical, name = existing.project_id, existing.canonical, existing.name
        print(f"기존 project.toml 사용: {project_id} ({canonical})")
    else:
        project_id, canonical, name = _resolve_identity(root, args.name)

    # [2] 허브 조회·등록 (§7-1 3·4단계, idempotent)
    try:
        found = client.request("GET", "/v1/projects", params={"canonical": canonical})
        projects = (found or {}).get("projects", [])
        if projects:
            doc = projects[0]
            project_id = doc["project_id"]  # 재계산 없음 (C1)
        else:
            same_name = (client.request("GET", "/v1/projects", params={"name": name}) or {}).get(
                "projects", []
            )
            if same_name and not existing and not args.force_new:
                io.eprint(
                    f"⚠️ 같은 이름의 기존 프로젝트가 허브에 있습니다: "
                    f"{same_name[0]['project_id']} ({same_name[0].get('canonical', '')})"
                )
                io.eprint(
                    "세션이 둘로 갈라지는 것을 막기 위해 중단합니다 — 같은 프로젝트면 "
                    "`hk link`, 정말 새 프로젝트면 `hk init --force-new` (§7)"
                )
                return 6
            if not dry:
                doc = client.request(
                    "POST",
                    "/v1/projects",
                    json={
                        "project_id": project_id,
                        "canonical": canonical,
                        "name": name,
                        "sensitivity": "tech",
                    },
                )
            else:
                doc = {"project_id": project_id, "name": name, "sensitivity": "tech"}
        server = client.request("GET", f"/v1/projects/{project_id}") if not dry else doc
    except HubError as err:
        io.eprint(f"허브 거부: {err.code} — {err.message}")
        return 5 if err.status in (401, 403) else 6
    except HubUnreachable as exc:
        io.eprint(f"허브 불통: {exc} — init은 허브 등록이 필요합니다")
        return 4

    # [1] project.toml (허브 문서가 공유 설정의 기준값 — D20)
    pc = ProjectConfig(
        root=root,
        project_id=project_id,
        canonical=canonical,
        name=(server or {}).get("name", name),
        sensitivity=(server or {}).get("sensitivity", "tech"),
    )
    if not dry:
        write_project_toml(root, pc)
    print(f"[1] project.toml — {project_id}")

    # [3] 어댑터 산출물
    print(f"[2] .gitignore — {_merge_gitignore(root, dry_run=dry)}")
    hk_md = root / "HYESEONGKIT.md"
    # 템플릿 파일명이 산출물과 다른 이유: `.gitignore`가 산출물 `HYESEONGKIT.md`를 막는데(D20),
    # 같은 이름의 템플릿까지 걸려 패키지에서 빠졌었다 (2026-08-13). 이름을 분리해 재발을 막는다
    template = _template("hyeseongkit_manual.md")
    if not hk_md.is_file() or hk_md.read_text(encoding="utf-8") != template:
        if not dry:
            hk_md.write_text(template, encoding="utf-8", newline="\n")
        print("[3] HYESEONGKIT.md — 생성/갱신")
    else:
        print("[3] HYESEONGKIT.md — 변경 없음")
    block = _template("agents_block.md")
    for rel in ("AGENTS.md", ".agents/AGENTS.md"):
        p = root / rel
        if p.is_file():
            print(f"[3] {rel} — {_merge_marker_block(p, block, dry_run=dry, root=root)}")
    hk_cmds = Path.home() / ".claude" / "commands" / "hk"
    if not hk_cmds.is_dir():
        print("[3] Claude Code 어댑터는 기기 단위입니다 — `hk setup`을 실행하세요 (§9)")

    # [4] 검증
    if not dry:
        from .doctor import cmd_doctor

        print("[4] hk doctor:")
        cmd_doctor(args, settings, load_project(root), client)
    return 0


def cmd_link(
    args, settings: ClientSettings, _project: ProjectConfig | None, client: HubClient | None
) -> int:
    """현재 디렉터리를 기존 프로젝트에 수동 연결 (C1, §7)."""
    root = Path.cwd()
    if client is None:
        io.eprint("허브 미설정 — HK_HUB_URL/HK_API_TOKEN 필요")
        return 2
    try:
        if args.project_id:
            doc = client.request("GET", f"/v1/projects/{args.project_id}")
        else:
            projects = (client.request("GET", "/v1/projects") or {}).get("projects", [])
            if not projects:
                io.eprint("허브에 등록된 프로젝트가 없습니다")
                return 6
            for i, d in enumerate(projects, start=1):
                print(f"[{i}] {d.get('name', '')} — {d['project_id']} ({d.get('canonical', '')})")
            choice = input("연결할 프로젝트 번호: ").strip()
            try:
                doc = projects[int(choice) - 1]
            except (ValueError, IndexError):
                io.eprint("잘못된 선택")
                return 2
        url = identity.remote_url(str(root))
        canonical = identity.normalize_remote(url) if url else None
        if canonical and canonical != doc.get("canonical"):
            doc = client.request(
                "POST", f"/v1/projects/{doc['project_id']}/aliases", json={"canonical": canonical}
            )
            print(f"별칭 등록: {canonical} → 이후 다른 기기의 새 클론은 자동 연결 (§7)")
    except HubError as err:
        io.eprint(f"허브 거부: {err.code} — {err.message}")
        return 5 if err.status in (401, 403) else 6
    except HubUnreachable as exc:
        io.eprint(f"허브 불통: {exc}")
        return 4
    pc = ProjectConfig(
        root=root,
        project_id=doc["project_id"],
        canonical=canonical or doc.get("canonical", ""),
        name=doc.get("name", ""),
        sensitivity=doc.get("sensitivity", "tech"),
    )
    write_project_toml(root, pc)
    print(f"project.toml — {doc['project_id']} 로 연결됨")
    return 0
