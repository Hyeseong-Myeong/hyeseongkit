"""오프라인 큐 (설계서 §11-3, K4) — ~/.hyeseongkit/queue/.

- 적재 조건: 연결 오류·타임아웃·5xx. 4xx는 적재하지 않는다
- 재전송: hk 명령 시작 시 오래된 순 flush (건당 타임아웃 2초)
- 3회 재전송 실패 → queue/failed/ 이동
- 큐 파일은 마스킹 완료된 요청 바디만 담는다
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from .config import GLOBAL_DIR
from .transport import HubClient, HubError, HubUnreachable
from .util import iso_to_compact, iso_utc

QUEUE_DIR = GLOBAL_DIR / "queue"
FAILED_DIR = QUEUE_DIR / "failed"
MAX_ATTEMPTS = 3


def enqueue(endpoint: str, body: dict) -> Path:
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{iso_to_compact(iso_utc())}-{secrets.token_hex(2)}.json"
    path = QUEUE_DIR / name
    payload = {"endpoint": endpoint, "method": "POST", "body": body, "attempts": 0}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n"
    )
    return path


def pending() -> list[Path]:
    if not QUEUE_DIR.is_dir():
        return []
    return sorted(p for p in QUEUE_DIR.glob("*.json"))


def failed() -> list[Path]:
    if not FAILED_DIR.is_dir():
        return []
    return sorted(p for p in FAILED_DIR.glob("*.json"))


def _move_failed(path: Path) -> None:
    FAILED_DIR.mkdir(parents=True, exist_ok=True)
    path.replace(FAILED_DIR / path.name)


def flush(client: HubClient, *, per_item_timeout: float = 2.0) -> tuple[int, int]:
    """(성공 수, 잔여 수) 반환. 실패한 항목은 남겨 두고 본 명령을 계속한다."""
    ok = 0
    remain = 0
    for path in pending():
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _move_failed(path)
            continue
        try:
            client.request(
                item.get("method", "POST"),
                item["endpoint"],
                json=item.get("body"),
                timeout=per_item_timeout,
            )
        except HubError:
            _move_failed(path)  # 4xx — 재시도 무의미
        except HubUnreachable:
            item["attempts"] = int(item.get("attempts", 0)) + 1
            if item["attempts"] >= MAX_ATTEMPTS:
                path.write_text(
                    json.dumps(item, ensure_ascii=False, indent=1),
                    encoding="utf-8",
                    newline="\n",
                )
                _move_failed(path)
            else:
                path.write_text(
                    json.dumps(item, ensure_ascii=False, indent=1),
                    encoding="utf-8",
                    newline="\n",
                )
                remain += 1
        else:
            path.unlink(missing_ok=True)
            ok += 1
    return ok, remain
