"""`hk` / `hyeseongkit` CLI 진입점 (설계서 §11, D15).

명령·종료 코드는 §11-2. 모든 명령 시작 시 오프라인 큐 flush (§11-3).
`--hook` 모드는 어떤 실패도 exit 0 (R11).
"""

from __future__ import annotations

import argparse
import sys

from ..core import offline_queue
from ..core.config import current_project, load_client_settings
from ..core.transport import HubClient
from . import io

# 큐 flush를 수행하는 명령 (§11-3 "모든 hk 명령 시작 시").
# 훅 모드는 3초 타임아웃(§9-2) 안에 끝나야 하므로 flush를 건너뛴다.
_FLUSH_COMMANDS = {"push", "resume", "status", "decide", "search", "close", "checkpoint", "doctor"}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hk", description="hyeseongkit — 세션 영속화 CLI (설계서 §11)")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("setup", help="기기당 1회 — Claude Code 어댑터 설치 (§9-1)")
    sp.add_argument("--refresh", action="store_true", help="패키지 템플릿과 비교해 갱신")

    sp = sub.add_parser("init", help="현재 프로젝트를 허브에 등록 (§10-1)")
    sp.add_argument("--name", help="remote 없는 저장소의 ASCII slug 이름")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--force-new", action="store_true", help="같은 이름 가드 무시하고 신규 생성")

    sp = sub.add_parser("link", help="현재 디렉터리를 기존 프로젝트에 수동 연결 (C1)")
    sp.add_argument("project_id", nargs="?", help="직접 지정 (생략 시 목록에서 선택)")

    sp = sub.add_parser("push", help="현재 작업 상태를 세션으로 저장")
    sp.add_argument("--title", help="제목 (생략 시 본문 첫 `# 제목`)")
    sp.add_argument("--slug", help="작업 주제 영문 요약 — 새 스레드 ID에 쓰임")
    sp.add_argument("--thread", help="기존 스레드에 이어서 저장")
    sp.add_argument("--file", help="본문 마크다운 파일 (한국어는 인자 대신 파일/stdin — R15)")
    sp.add_argument("--stdin", action="store_true", help="본문을 stdin에서 읽기")
    sp.add_argument("--sensitivity", choices=["public", "tech", "career", "personal"])
    sp.add_argument("--reopen", action="store_true", help="close된 스레드 재개 (§3-3)")
    sp.add_argument("--tool", help="tool 필드 (기본 manual)")

    sp = sub.add_parser("resume", help="세션을 컨텍스트 패킷으로 불러오기")
    sp.add_argument("thread", nargs="?", help="스레드 ID (생략 시 --last)")
    sp.add_argument("--last", action="store_true", help="프로젝트 최신 활성 스레드")
    sp.add_argument("--budget", type=int, default=None, help="토큰 예산 (0=무제한, §3-6)")
    sp.add_argument("--format", choices=["packet", "prompt", "json"], default="packet")
    sp.add_argument("--events", type=int, default=0, help="최근 N개 이벤트 원문 포함 (L2)")
    sp.add_argument("--hook", action="store_true", help="훅 모드 — 항상 exit 0 (R11)")

    sp = sub.add_parser("status", help="활성 스레드 목록과 허브 상태")
    sp.add_argument("--all", action="store_true", help="전체 프로젝트")

    sp = sub.add_parser("decide", help="결정 사항을 원문 그대로 기록")
    sp.add_argument("--thread", help="대상 스레드 (생략 시 프로젝트 최신 활성)")
    sp.add_argument("--file", help="결정문 파일")
    sp.add_argument("--stdin", action="store_true")
    sp.add_argument("--tool", help="tool 필드 (기본 manual)")

    sp = sub.add_parser("search", help="과거 세션 검색")
    sp.add_argument("query", nargs="+")
    sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--status", choices=["active", "done", "dropped"])
    sp.add_argument("--all", action="store_true", help="전체 프로젝트에서 검색")

    sp = sub.add_parser("close", help="세션 종료 및 아카이브")
    sp.add_argument("thread")
    sp.add_argument("--outcome", choices=["done", "dropped"], default="done")
    sp.add_argument("--tool", help="tool 필드 (기본 manual)")

    sp = sub.add_parser("checkpoint", help="훅 전용 — git 상태·전사 경로만 기록 (§9-2)")
    sp.add_argument("--reason", default="manual", help="precompact | session-end | manual")
    sp.add_argument("--thread", help="대상 스레드 (생략 시 프로젝트 최신 활성)")
    sp.add_argument("--hook", action="store_true")
    sp.add_argument("--tool", help="tool 필드 (기본 manual)")

    sub.add_parser("doctor", help="연결·설정·인증 진단 (§11-5)")

    sp = sub.add_parser("queue", help="오프라인 큐 관리")
    sp.add_argument("--flush", action="store_true")
    sp.add_argument("--list", action="store_true")

    sp = sub.add_parser("admin", help="기기 토큰 발급·폐기 — NAS docker exec 전용 (D18)")
    admin_sub = sp.add_subparsers(dest="admin_cmd", required=True)
    dev = admin_sub.add_parser("device")
    dev_sub = dev.add_subparsers(dest="device_cmd", required=True)
    d = dev_sub.add_parser("add")
    d.add_argument("device_id")
    d.add_argument("--name", help="사람이 알아보는 이름")
    d = dev_sub.add_parser("revoke")
    d.add_argument("device_id")
    dev_sub.add_parser("list")

    sp = sub.add_parser("mcp", help="MCP 관련")
    mcp_sub = sp.add_subparsers(dest="mcp_cmd", required=True)
    mcp_sub.add_parser("serve", help="stdio MCP 브리지 (§4)")

    return p


def main(argv: list[str] | None = None) -> int:
    io.setup_stdio()
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code not in (0, None) else 0

    if args.command == "mcp":
        from .mcp_bridge import serve

        return serve()

    settings = load_client_settings()
    project = current_project()
    client = (
        HubClient(settings.hub_url, settings.api_token)
        if settings.hub_url and settings.api_token
        else None
    )
    hook_mode = bool(getattr(args, "hook", False))

    try:
        if client is not None and args.command in _FLUSH_COMMANDS and not hook_mode:
            try:
                ok, _remain = offline_queue.flush(client)
                if ok:
                    io.eprint(f"(오프라인 큐 {ok}건 재전송 완료)")
            except OSError:
                pass

        if args.command == "setup":
            from .setup_cmd import cmd_setup as fn
        elif args.command == "init":
            from .project import cmd_init as fn
        elif args.command == "link":
            from .project import cmd_link as fn
        elif args.command == "push":
            from .session import cmd_push as fn
        elif args.command == "resume":
            from .session import cmd_resume as fn
        elif args.command == "status":
            from .session import cmd_status as fn
        elif args.command == "decide":
            from .session import cmd_decide as fn
        elif args.command == "search":
            from .session import cmd_search as fn
        elif args.command == "close":
            from .session import cmd_close as fn
        elif args.command == "checkpoint":
            from .session import cmd_checkpoint as fn
        elif args.command == "doctor":
            from .doctor import cmd_doctor as fn
        elif args.command == "queue":
            from .adminq import cmd_queue as fn
        elif args.command == "admin":
            from .adminq import cmd_admin as fn
        else:  # pragma: no cover
            io.eprint(f"알 수 없는 명령: {args.command}")
            return 2
        return fn(args, settings, project, client)
    except KeyboardInterrupt:
        return 0 if hook_mode else 130
    except Exception as exc:  # noqa: BLE001 — 훅은 절대 실패하지 않는다 (R11)
        if hook_mode:
            io.eprint(f"hk --hook 오류 무시: {exc}")
            return 0
        raise
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    sys.exit(main())
