"""`hk resume`가 오프라인 큐 적체를 드러내는지 (§11-3, R11).

훅은 실패해도 exit 0이고 flush도 건너뛰므로, resume 출력이 적체를 알아챌 수 있는
유일한 자동 지점이다. 알림은 packet 블록을 깨지 않도록 그 뒤에 붙는다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hyeseongkit.cli.session import cmd_resume
from hyeseongkit.core import offline_queue
from hyeseongkit.core.config import ClientSettings
from hyeseongkit.core.transport import HubUnreachable

PACKET = '<hyeseongkit-packet thread="T-1" v="1">\n본문\n</hyeseongkit-packet>'


@pytest.fixture(autouse=True)
def queue_dirs(tmp_path, monkeypatch):
    qdir = tmp_path / "queue"
    monkeypatch.setattr(offline_queue, "QUEUE_DIR", qdir)
    monkeypatch.setattr(offline_queue, "FAILED_DIR", qdir / "failed")


class StubClient:
    def __init__(self, behavior="ok"):
        self.behavior = behavior

    def request(self, _method, _path, **_kw):
        if self.behavior == "down":
            raise HubUnreachable("connect error")
        return {"format": "packet", "content": PACKET}


def _args(**over):
    base = {"hook": False, "budget": None, "format": "packet", "events": 0, "thread": None}
    base.update(over)
    return SimpleNamespace(**base)


def _project(tmp_path):
    return SimpleNamespace(project_id="proj-test", root=tmp_path, extra_rules=[])


SETTINGS = ClientSettings(hub_url="http://hub.invalid", api_token="tok", device_id="testdev")


def test_no_notice_when_queue_empty(tmp_path, capsys):
    cmd_resume(_args(), SETTINGS, _project(tmp_path), StubClient())
    out = capsys.readouterr().out
    assert PACKET in out
    assert "오프라인 큐" not in out


def test_pending_is_reported_after_the_packet(tmp_path, capsys):
    offline_queue.enqueue("/v1/session/push", {"title": "x"})
    cmd_resume(_args(), SETTINGS, _project(tmp_path), StubClient())
    out = capsys.readouterr().out
    assert "대기 1건" in out
    # packet 블록 바깥이어야 한다 — 안에 섞이면 허브가 만든 구조를 깬다
    assert out.index("</hyeseongkit-packet>") < out.index("오프라인 큐")


def test_final_failure_is_reported(tmp_path, capsys):
    path = offline_queue.enqueue("/v1/session/push", {"title": "x"})
    offline_queue.FAILED_DIR.mkdir(parents=True, exist_ok=True)
    path.replace(offline_queue.FAILED_DIR / path.name)
    cmd_resume(_args(), SETTINGS, _project(tmp_path), StubClient())
    assert "최종 실패 1건" in capsys.readouterr().out


def test_notice_shown_even_when_hub_is_down_in_hook_mode(tmp_path, capsys):
    """불통일 때가 적체가 가장 중요한 순간이다 — 훅이라도 이 사실은 알린다."""
    offline_queue.enqueue("/v1/session/push", {"title": "x"})
    rc = cmd_resume(_args(hook=True), SETTINGS, _project(tmp_path), StubClient("down"))
    assert rc == 0  # R11 — 훅은 실패해도 세션 시작을 막지 않는다
    assert "대기 1건" in capsys.readouterr().out
