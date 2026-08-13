"""오프라인 큐 (설계서 §11-3, K4) — ~/.hyeseongkit/queue/.

- 적재 조건: 연결 오류·타임아웃·5xx. 4xx는 적재하지 않는다
- 재전송: hk 명령 시작 시 오래된 순 flush (건당 타임아웃 2초)
- 재시도는 백오프 간격을 둔다 — attempts는 flush 호출 횟수라, 간격이 없으면
  짧은 장애 중에 명령을 몇 번 쓰는 것만으로 시도가 소진돼 조기에 최종 실패가 된다
- MAX_ATTEMPTS 소진 → queue/failed/ 이동 (최종 실패, 자동 재시도 대상에서 빠짐)
- 큐 파일은 마스킹 완료된 요청 바디만 담는다
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from .config import GLOBAL_DIR
from .transport import HubClient, HubError, HubUnreachable
from .util import iso_to_compact, iso_utc, parse_iso, utc_now

QUEUE_DIR = GLOBAL_DIR / "queue"
FAILED_DIR = QUEUE_DIR / "failed"
MAX_ATTEMPTS = 8

# attempts회 실패 후 다음 시도까지 기다리는 분. 마지막 값이 상한.
# 8회를 다 쓰면 실제 경과가 이틀을 넘으므로, 그때의 failed/ 이동은 진짜 최종 실패다.
_BACKOFF_MINUTES = (1, 5, 30, 120, 360, 1440)


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


def _write(path: Path, item: dict) -> None:
    path.write_text(json.dumps(item, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")


def _next_attempt_at(attempts: int) -> str:
    idx = min(attempts, len(_BACKOFF_MINUTES)) - 1
    return iso_utc(utc_now() + timedelta(minutes=_BACKOFF_MINUTES[idx]))


def _is_due(item: dict, now: datetime) -> bool:
    ts = item.get("next_attempt")
    if not ts:
        return True  # 구 형식 파일 — 즉시 대상
    try:
        return parse_iso(ts) <= now
    except ValueError:
        return True  # 손상된 값이 항목을 영구히 묶어 두게 두지 않는다


def flush(
    client: HubClient, *, per_item_timeout: float = 2.0, force: bool = False
) -> tuple[int, int]:
    """(성공 수, 잔여 수) 반환. 실패한 항목은 남겨 두고 본 명령을 계속한다.

    force=True는 백오프 대기를 무시한다 — 사용자가 `hk queue --flush`로 직접
    지시한 경우, 기다리게 할 이유가 없다.
    """
    ok = 0
    remain = 0
    now = utc_now()
    for path in pending():
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _move_failed(path)
            continue
        if not force and not _is_due(item, now):
            remain += 1
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
                item["failed_at"] = iso_utc(now)
                _write(path, item)
                _move_failed(path)
            else:
                item["next_attempt"] = _next_attempt_at(item["attempts"])
                _write(path, item)
                remain += 1
        else:
            path.unlink(missing_ok=True)
            ok += 1
    return ok, remain
