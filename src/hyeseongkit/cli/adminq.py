"""`hk queue` / `hk admin device` (설계서 §11-2, §5-2).

hk admin은 NAS `docker exec` 전용 — HK_ADMIN_TOKEN이 없으면 즉시 안내 후 종료 (D18).
"""

from __future__ import annotations

import json
import os

from ..core import offline_queue
from ..core.transport import HubClient, HubError, HubUnreachable
from . import io


def cmd_queue(args, _settings, _project, client: HubClient | None) -> int:
    if args.flush:
        if client is None:
            io.eprint("허브 미설정 — flush 불가")
            return 2
        # 사용자가 직접 지시한 재전송이므로 백오프 대기를 무시한다
        ok, remain = offline_queue.flush(client, force=True)
        print(f"재전송 {ok}건 성공, {remain}건 대기")
        failed = offline_queue.failed()
        if failed:
            print(f"실패 보관함: {len(failed)}건 (queue/failed/)")
        return 0
    pending = offline_queue.pending()
    print(f"대기 {len(pending)}건:")
    for p in pending:
        try:
            item = json.loads(p.read_text(encoding="utf-8"))
            nxt = item.get("next_attempt")
            when = f", 다음 시도 {nxt}" if nxt else ""
            print(
                f"- {p.name} → {item.get('endpoint')} "
                f"(attempts {item.get('attempts', 0)}/{offline_queue.MAX_ATTEMPTS}{when})"
            )
        except (OSError, ValueError):
            print(f"- {p.name} (읽기 실패)")
    failed = offline_queue.failed()
    if failed:
        print(f"실패 보관함 {len(failed)}건:")
        for p in failed:
            print(f"- failed/{p.name}")
    return 0


def _admin_client(settings) -> HubClient | None:
    admin_token = os.environ.get("HK_ADMIN_TOKEN", "")
    if not admin_token:
        io.eprint(
            "HK_ADMIN_TOKEN이 없습니다 — hk admin은 NAS docker exec 전용입니다 (§5-2):\n"
            '  docker exec hyeseongkit-hub hk admin device add <id> --name "<이름>"'
        )
        return None
    base = settings.hub_url or "http://localhost:9100"
    return HubClient(base, admin_token)


def cmd_admin(args, settings, _project, _client) -> int:
    client = _admin_client(settings)
    if client is None:
        return 5
    try:
        if args.device_cmd == "add":
            resp = client.request(
                "POST",
                "/v1/admin/devices",
                json={"device_id": args.device_id, "name": args.name or args.device_id},
            )
            print(f"device: {resp['device_id']}")
            print(f"token:  {resp['token']}")
            print("⚠️ 이 토큰은 다시 조회할 수 없다 — 지금 해당 기기의 HK_API_TOKEN에 입력")
        elif args.device_cmd == "revoke":
            client.request("DELETE", f"/v1/admin/devices/{args.device_id}")
            print(f"revoked: {args.device_id}")
        elif args.device_cmd == "list":
            resp = client.request("GET", "/v1/admin/devices")
            for d in (resp or {}).get("devices", []):
                state = "revoked" if d.get("revoked") else "active"
                print(
                    f"- {d.get('device_id')} ({d.get('name', '')}) — {state}, "
                    f"last_seen {d.get('last_seen')}"
                )
        return 0
    except HubError as err:
        io.eprint(f"허브 거부: {err.code} — {err.message}")
        return 5 if err.status in (401, 403) else 6
    except HubUnreachable as exc:
        io.eprint(f"허브 불통: {exc}")
        return 4
    finally:
        client.close()
