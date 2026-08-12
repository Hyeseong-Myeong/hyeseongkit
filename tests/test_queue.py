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


def test_three_failures_move_to_failed():
    offline_queue.enqueue("/v1/session/push", {"a": 1})
    client = StubClient("down")
    for _ in range(offline_queue.MAX_ATTEMPTS):
        offline_queue.flush(client)
    assert offline_queue.pending() == []
    assert len(offline_queue.failed()) == 1


def test_4xx_not_retried():
    # 4xx는 재시도해도 실패 → 즉시 failed로 (§11-3)
    offline_queue.enqueue("/v1/session/push", {"a": 1})
    client = StubClient("reject")
    ok, remain = offline_queue.flush(client)
    assert (ok, remain) == (0, 0)
    assert offline_queue.pending() == []
    assert len(offline_queue.failed()) == 1
