"""세션 명령 — push/resume/status/decide/search/close/checkpoint (설계서 §11).

exit 코드 (§11-2): 0 성공(큐 적재 포함) · 2 인자/스키마 · 3 마스킹 실패 ·
4 허브 불통+큐 실패 · 5 인증 · 6 정책 위반. `--hook` 모드는 항상 0 (R11).
"""

from __future__ import annotations

import json

from ..core import offline_queue, redact
from ..core.config import ClientSettings, ProjectConfig
from ..core.identity import git_state
from ..core.transport import HubClient, HubError, HubUnreachable
from ..core.util import REQUIRED_SECTIONS
from . import io


def _exit_for(err: HubError) -> int:
    if err.status in (401, 403):
        return 5
    if err.status == 400:
        return 2
    return 6


def _print_hub_error(err: HubError) -> None:
    io.eprint(f"허브 거부: {err.code} — {err.message}")
    if err.detail:
        io.eprint(json.dumps(err.detail, ensure_ascii=False, indent=1))


def _print_queue_notice() -> None:
    """큐 적체·최종 실패를 세션 시작 지점에서 드러낸다.

    훅은 실패해도 exit 0이고(R11) flush도 건너뛰므로(§11-3), resume 출력이
    사용자가 적체를 알아챌 수 있는 유일한 자동 지점이다.
    packet 블록은 허브가 닫아서 보내므로(§packet) 그 뒤에 붙여 블록을 건드리지 않는다.
    """
    pending = offline_queue.pending()
    failed = offline_queue.failed()
    if not pending and not failed:
        return
    parts = []
    if pending:
        parts.append(f"대기 {len(pending)}건")
    if failed:
        parts.append(f"최종 실패 {len(failed)}건")
    print(f"\n> ⚠️ 오프라인 큐: {' · '.join(parts)} — `hk queue --list`로 확인")


def _send_or_queue(client: HubClient | None, endpoint: str, body: dict) -> tuple[int, dict | None]:
    """(exit code, 응답). 불통 시 큐 적재 후 exit 0 (K4)."""
    if client is None:
        path = offline_queue.enqueue(endpoint, body)
        print(f"허브 미설정 — 큐에 적재됨: {path.name}")
        return 0, None
    try:
        resp = client.request("POST", endpoint, json=body)
        return 0, resp
    except HubError as err:
        _print_hub_error(err)
        return _exit_for(err), None
    except HubUnreachable:
        try:
            path = offline_queue.enqueue(endpoint, body)
        except OSError as exc:
            io.eprint(f"허브 불통 + 큐 적재 실패: {exc}")
            return 4, None
        print(f"허브 불통 — 큐에 적재됨 (다음 명령에서 자동 재전송): {path.name}")
        return 0, None


def cmd_push(
    args, settings: ClientSettings, project: ProjectConfig | None, client: HubClient | None
) -> int:
    if project is None:
        io.eprint("이 디렉터리는 hk 프로젝트가 아닙니다 — 먼저 `hk init`을 실행하세요")
        return 2
    if not settings.device_id:
        io.eprint("device_id 미설정 — HK_DEVICE_ID 또는 ~/.hyeseongkit/config.toml 참조")
        return 2
    try:
        text = io.read_body(args.file, args.stdin)
    except OSError as exc:
        io.eprint(f"본문 읽기 실패: {exc}")
        return 2
    parsed_title, sections = io.parse_sections(text)
    title = args.title or parsed_title or ""
    if not title:
        io.eprint("제목이 없습니다 — --title 또는 본문 첫 `# 제목` heading")
        return 2
    for key in REQUIRED_SECTIONS:
        if not sections.get(key):
            io.eprint(f"'{key}' 섹션이 비어 있습니다 (todo·know 필수 — L0 보호)")
            return 2
    try:
        masked, report = redact.mask_obj(
            {"title": title, "sections": sections}, project.extra_rules
        )
    except redact.RedactionError as exc:
        io.eprint(f"마스킹 실패(fail-closed) — 아무것도 전송되지 않았습니다: {exc}")
        return 3
    body = {
        "thread": args.thread,
        "project_id": project.project_id,
        "title": masked["title"],
        "slug": args.slug,
        "sections": masked["sections"],
        "sensitivity": args.sensitivity or project.sensitivity,
        "tool": args.tool or settings.tool,
        "model": None,
        "device": settings.device_id,
        "reopen": bool(args.reopen),
        "mask_report": report,
    }
    code, resp = _send_or_queue(client, "/v1/session/push", body)
    if resp:
        print(f"thread: {resp['thread']}")
        print(f"event_id: {resp['event_id']}")
    return code


def cmd_resume(
    args, settings: ClientSettings, project: ProjectConfig | None, client: HubClient | None
) -> int:
    hook = bool(args.hook)
    if project is None or client is None:
        if hook:
            return 0  # 비-hk 프로젝트/허브 미설정 — 조용히 스킵 (R11)
        io.eprint("프로젝트/허브 설정이 없습니다 — hk init · HK_HUB_URL/HK_API_TOKEN 확인")
        return 2
    params: dict = {
        "budget": args.budget if args.budget is not None else settings.budget,
        "format": args.format,
        "events": args.events,
    }
    if args.thread:
        params["thread"] = args.thread
    else:
        params["last"] = 1
        params["project_id"] = project.project_id
    try:
        resp = client.request("GET", "/v1/session/resume", params=params)
    except HubError as err:
        if hook:
            io.eprint(f"hk resume 스킵: {err.code}")
            return 0
        _print_hub_error(err)
        return _exit_for(err)
    except HubUnreachable as exc:
        # 불통일 때가 적체가 가장 중요한 순간이다 — 훅이라도 이 사실은 알린다
        _print_queue_notice()
        if hook:
            io.eprint(f"hk resume 스킵(허브 불통): {exc}")
            return 0
        io.eprint(f"허브 불통: {exc}")
        return 4
    if resp is None:
        return 0
    if resp.get("format") == "json":
        print(json.dumps(resp.get("view"), ensure_ascii=False, indent=1))
    else:
        print(resp.get("content", ""))
    _print_queue_notice()
    return 0


def cmd_status(
    args, settings: ClientSettings, project: ProjectConfig | None, client: HubClient | None
) -> int:
    if client is None:
        io.eprint("허브 미설정 — HK_HUB_URL/HK_API_TOKEN 확인")
        return 2
    params = {}
    if project and not args.all:
        params["project_id"] = project.project_id
    try:
        resp = client.request("GET", "/v1/session/status", params=params)
    except HubError as err:
        _print_hub_error(err)
        return _exit_for(err)
    except HubUnreachable as exc:
        io.eprint(f"허브 불통: {exc}")
        return 4
    hub = (resp or {}).get("hub", {})
    print(f"hub v{hub.get('version', '?')} · couchdb {hub.get('couchdb', '?')}")
    threads = (resp or {}).get("threads", [])
    if not threads:
        print("(활성 스레드 없음)")
    for t in threads:
        print(
            f"- {t['thread']} — {t['title']} ({t['status']}, "
            f"{t['last_tool']}@{t['last_device']}, updated {t['updated']}, "
            f"events {t['events']})"
        )
    q = offline_queue.pending()
    if q:
        print(f"오프라인 큐 대기: {len(q)}건 (hk queue --list)")
    return 0


def _resolve_active_thread(client: HubClient, project_id: str) -> str | None:
    resp = client.request("GET", "/v1/session/status", params={"project_id": project_id})
    threads = (resp or {}).get("threads", [])
    return threads[0]["thread"] if threads else None


def cmd_decide(
    args, settings: ClientSettings, project: ProjectConfig | None, client: HubClient | None
) -> int:
    if project is None or not settings.device_id:
        io.eprint("hk 프로젝트가 아니거나 device_id 미설정")
        return 2
    try:
        text = io.read_body(
            args.file,
            args.stdin,
            editor_template="(결정 원문을 쓴다. 선택: `근거:` / `기각:` 로 시작하는 줄)\n",
        )
    except OSError as exc:
        io.eprint(f"본문 읽기 실패: {exc}")
        return 2
    rationale = None
    rejected = None
    kept: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("근거:"):
            rationale = s[len("근거:") :].strip()
        elif s.startswith("기각:"):
            rejected = s[len("기각:") :].strip()
        else:
            kept.append(line)
    decision_text = "\n".join(kept).strip()
    if not decision_text:
        io.eprint("결정 원문이 비어 있습니다")
        return 2
    thread = args.thread
    if not thread:
        if client is None:
            io.eprint("--thread 없이 결정을 기록하려면 허브 연결이 필요합니다")
            return 4
        try:
            thread = _resolve_active_thread(client, project.project_id)
        except (HubError, HubUnreachable) as exc:
            io.eprint(f"활성 스레드 확인 실패: {exc}")
            return 4
        if not thread:
            io.eprint("활성 스레드가 없습니다 — --thread를 지정하세요")
            return 6
    try:
        masked, report = redact.mask_obj(
            {"text": decision_text, "rationale": rationale, "rejected": rejected},
            project.extra_rules,
        )
    except redact.RedactionError as exc:
        io.eprint(f"마스킹 실패(fail-closed): {exc}")
        return 3
    body = {
        "thread": thread,
        "project_id": project.project_id,
        "decision": masked,
        "tool": args.tool or settings.tool,
        "device": settings.device_id,
        "mask_report": report,
    }
    code, resp = _send_or_queue(client, "/v1/session/decide", body)
    if resp:
        print(f"event_id: {resp['event_id']}")
    return code


def cmd_search(
    args, settings: ClientSettings, project: ProjectConfig | None, client: HubClient | None
) -> int:
    if client is None:
        io.eprint("허브 미설정")
        return 2
    params: dict = {"q": " ".join(args.query), "limit": args.limit}
    if project and not args.all:
        params["project_id"] = project.project_id
    if args.status:
        params["status"] = args.status
    try:
        resp = client.request("GET", "/v1/session/search", params=params)
    except HubError as err:
        _print_hub_error(err)
        return _exit_for(err)
    except HubUnreachable as exc:
        io.eprint(f"허브 불통: {exc}")
        return 4
    matches = (resp or {}).get("matches", [])
    if not matches:
        print("(일치 없음)")
    for m in matches:
        print(f"- {m['thread']} — {m['title']} ({m['status']}, updated {m['updated']})")
    if (resp or {}).get("truncated"):
        print("(스캔 상한 초과 — 결과가 잘렸을 수 있음)")
    return 0


def cmd_close(
    args, settings: ClientSettings, project: ProjectConfig | None, client: HubClient | None
) -> int:
    if project is None or not settings.device_id:
        io.eprint("hk 프로젝트가 아니거나 device_id 미설정")
        return 2
    body = {
        "thread": args.thread,
        "project_id": project.project_id,
        "outcome": args.outcome,
        "tool": args.tool or settings.tool,
        "device": settings.device_id,
    }
    code, resp = _send_or_queue(client, "/v1/session/close", body)
    if resp:
        print(f"{resp['thread']} → {resp['status']}")
    return code


def cmd_checkpoint(
    args, settings: ClientSettings, project: ProjectConfig | None, client: HubClient | None
) -> int:
    hook = bool(args.hook)
    if project is None:
        if hook:
            return 0  # 비-hk 프로젝트에 무해 (§9)
        io.eprint("이 디렉터리는 hk 프로젝트가 아닙니다")
        return 2
    if not settings.device_id:
        if hook:
            io.eprint("hk checkpoint 스킵: device_id 미설정")
            return 0
        io.eprint("device_id 미설정")
        return 2
    hook_data = io.read_hook_stdin() if hook else {}
    body = {
        "thread": args.thread,
        "project_id": project.project_id,
        "reason": args.reason,
        "git": git_state(str(project.root)),
        "transcript_path": io.normalize_transcript_path(hook_data.get("transcript_path")),
        "tool": args.tool or settings.tool,
        "device": settings.device_id,
    }
    code, resp = _send_or_queue(client, "/v1/session/checkpoint", body)
    if resp:
        print(f"checkpoint → {resp.get('thread')}")
    return 0 if hook else code
