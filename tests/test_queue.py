"""오프라인 큐 (설계서 §11-3) — 적재·flush·실패 보관함."""

from __future__ import annotations

import json

import pytest

from hyeseongkit.core import offline_queue
from hyeseongkit.core.transport import HubError, HubUnreachable


class StubClient:
    def __init__(self, behavior):
        self.behavior = behavior  # "ok" | "down" | "reject"
        self.calls = 0

    def request(self, method, path, *, json=None, params=None, timeout=None):
        self.calls += 1
        if self.behavior == "down":
            raise HubUnreachable("connect error")
        if self.behavior == "reject":
            raise HubError(409, "THREAD_LIMIT", "정책 위반")
        return {"ok": True}


@pytest.fixture(autouse=True)
def queue_dirs(tmp_path, monkeypatch):
    qdir = tmp_path / "queue"
    monkeypatch.setattr(offline_queue, "QUEUE_DIR", qdir)
    monkeypatch.setattr(offline_queue, "FAILED_DIR", qdir / "failed")
    return qdir


def test_enqueue_and_flush_ok():
    offline_queue.enqueue("/v1/session/push", {"a": 1})
    offline_queue.enqueue("/v1/session/checkpoint", {"b": 2})
    assert len(offline_queue.pending()) == 2
    client = StubClient("ok")
    ok, remain = offline_queue.flush(client)
    assert (ok, remain) == (2, 0)
    assert offline_queue.pending() == []


def test_flush_unreachable_increments_attempts():
    path = offline_queue.enqueue("/v1/session/push", {"a": 1})
    client = StubClient("down")
    ok, remain = offline_queue.flush(client)
    assert (ok, remain) == (0, 1)
    item = json.loads(path.read_text(encoding="utf-8"))
    assert item["attempts"] == 1


def test_attempts_exhausted_move_to_failed():
    """시도를 다 쓰면 최종 실패로 넘어간다.

    force=True로 백오프를 건너뛰어 소진 경로만 본다 — 실제 운용에서는
    백오프 때문에 이 지점까지 이틀 넘게 걸린다.
    """
    offline_queue.enqueue("/v1/session/push", {"a": 1})
    client = StubClient("down")
    for _ in range(offline_queue.MAX_ATTEMPTS):
        offline_queue.flush(client, force=True)
    assert offline_queue.pending() == []
    failed = offline_queue.failed()
    assert len(failed) == 1
    item = json.loads(failed[0].read_text(encoding="utf-8"))
    assert item["failed_at"], "최종 실패 시각이 기록돼야 한다"


def test_backoff_prevents_burning_attempts_during_a_short_outage():
    """attempts는 flush 호출 횟수를 센다 — 간격이 없으면 짧은 장애 중에
    명령을 몇 번 쓰는 것만으로 소진돼 조기에 최종 실패가 된다."""
    path = offline_queue.enqueue("/v1/session/push", {"a": 1})
    client = StubClient("down")
    for _ in range(10):  # 장애 중 연속 명령
        offline_queue.flush(client)
    item = json.loads(path.read_text(encoding="utf-8"))
    assert item["attempts"] == 1, "백오프 대기 중에는 시도가 늘지 않아야 한다"
    assert offline_queue.pending() == [path]
    assert offline_queue.failed() == []


def test_due_item_is_retried_after_backoff_elapses(monkeypatch):
    path = offline_queue.enqueue("/v1/session/push", {"a": 1})
    down = StubClient("down")
    offline_queue.flush(down)
    item = json.loads(path.read_text(encoding="utf-8"))
    item["next_attempt"] = "2000-01-01T00:00:00Z"  # 대기 시간이 지난 상태
    path.write_text(json.dumps(item), encoding="utf-8")
    ok, remain = offline_queue.flush(StubClient("ok"))
    assert (ok, remain) == (1, 0)
    assert offline_queue.pending() == []


def test_legacy_item_without_next_attempt_is_due():
    """백오프 도입 전에 적재된 파일이 큐에 묶여 있으면 안 된다."""
    path = offline_queue.enqueue("/v1/session/push", {"a": 1})
    item = json.loads(path.read_text(encoding="utf-8"))
    item.pop("next_attempt", None)
    path.write_text(json.dumps(item), encoding="utf-8")
    ok, _ = offline_queue.flush(StubClient("ok"))
    assert ok == 1


def test_4xx_not_retried():
    # 4xx는 재시도해도 실패 → 즉시 failed로 (§11-3)
    offline_queue.enqueue("/v1/session/push", {"a": 1})
    client = StubClient("reject")
    ok, remain = offline_queue.flush(client)
    assert (ok, remain) == (0, 0)
    assert offline_queue.pending() == []
    assert len(offline_queue.failed()) == 1
