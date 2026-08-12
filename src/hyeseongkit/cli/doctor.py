"""`hk doctor` — 연결·설정·인증 진단 (설계서 §11-5). 진단 도구이므로 exit 0."""

from __future__ import annotations

from ..core import offline_queue, redact
from ..core.config import ClientSettings, ProjectConfig
from ..core.transport import HubClient, HubError, HubUnreachable


def _check(label: str, ok: bool, note: str = "") -> None:
    mark = "✓" if ok else "✗"
    print(f"[{mark}] {label}" + (f" — {note}" if note else ""))


def cmd_doctor(
    _args, settings: ClientSettings, project: ProjectConfig | None, client: HubClient | None
) -> int:
    # [1] project.toml
    _check(
        "project.toml",
        project is not None,
        project.project_id if project else "없음 — hk init 필요",
    )
    # [2] 환경변수
    _check("HK_HUB_URL", bool(settings.hub_url), settings.hub_url or "미설정")
    _check("HK_API_TOKEN", bool(settings.api_token), "설정됨" if settings.api_token else "미설정")
    _check("device_id", bool(settings.device_id), settings.device_id or "미설정")

    healthz = None
    if client is not None:
        # [3] 허브 도달
        try:
            healthz = client.request("GET", "/healthz")
            _check("허브 도달 (/healthz)", True, f"v{(healthz or {}).get('version', '?')}")
        except (HubError, HubUnreachable) as exc:
            _check("허브 도달 (/healthz)", False, str(exc))
        # [4] CouchDB 상태
        if healthz is not None:
            couch = (healthz or {}).get("couchdb", "?")
            _check("CouchDB (healthz 응답)", couch == "ok", couch)
        # [5] 토큰 유효
        try:
            who = client.request("GET", "/v1/whoami")
            device = (who or {}).get("device_id", "?")
            note = device
            if settings.device_id and device != settings.device_id:
                note = f"{device} ⚠️ 설정 device_id({settings.device_id})와 불일치"
            _check("토큰 유효 (/v1/whoami)", True, note)
        except (HubError, HubUnreachable) as exc:
            _check("토큰 유효 (/v1/whoami)", False, str(exc))
    else:
        _check("허브 도달 (/healthz)", False, "허브 미설정")

    # [6] 푸시 드라이런 — healthz + 스키마·마스킹 자가검증 (전송 없음)
    try:
        masked, _ = redact.mask("password: abcd1234efgh")
        ok = "abcd1234efgh" not in masked and (healthz is not None or client is None)
        _check("푸시 드라이런 (스키마·마스킹 자가검증)", ok)
    except redact.RedactionError as exc:
        _check("푸시 드라이런 (스키마·마스킹 자가검증)", False, str(exc))

    # [7] 큐 상태
    pending = offline_queue.pending()
    failed = offline_queue.failed()
    _check(
        "오프라인 큐",
        not failed,
        f"대기 {len(pending)}건, 실패 {len(failed)}건",
    )

    # [8] hk setup 산출물 최신 여부
    try:
        from .setup_cmd import installed_state

        states = installed_state()
        stale = {k: v for k, v in states.items() if v != "최신"}
        _check(
            "hk setup 산출물 (~/.claude/)",
            not stale,
            "최신" if not stale else f"갱신 필요: {sorted(stale)} — hk setup --refresh",
        )
    except OSError as exc:
        _check("hk setup 산출물 (~/.claude/)", False, str(exc))
    return 0
