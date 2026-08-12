"""HTTP API 통합 (FakeCouch) — 수용 기준 T2(핵심)·T3·T4·T5 로직 검증."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from hyeseongkit.hub.app import HubSettings, create_app

ADMIN_TOKEN = "test-admin-token"


@pytest.fixture
def env(fake_couch, tmp_path):
    settings = HubSettings(
        couchdb_url="http://fake:5984",
        couchdb_user="",
        couchdb_password="",
        admin_token=ADMIN_TOKEN,
        encryption_key=Fernet.generate_key().decode(),
        vault_out=str(tmp_path / "vault-out"),
        data_dir=str(tmp_path / "data"),
    )
    app = create_app(settings, fake_couch, enable_renderer=False, enable_mcp=False)
    with TestClient(app) as client:
        yield client, app, fake_couch


def _admin(client, method, path, **kw):
    return client.request(method, path, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}, **kw)


def _issue_token(client, device_id="desktop"):
    r = _admin(client, "POST", "/v1/admin/devices", json={"device_id": device_id, "name": "테스트"})
    assert r.status_code == 201
    return r.json()["token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _push_body(**kw):
    body = {
        "project_id": "p-test",
        "title": "hyeseongkit initial implementation",
        "sections": {
            "context": "컨텍스트",
            "done": "한 일",
            "todo": "1. 이어서 구현",
            "know": "- 포트 9100",
            "questions": "",
        },
        "sensitivity": "tech",
        "tool": "claude-code",
        "device": "desktop",
    }
    body.update(kw)
    return body


def test_healthz_no_auth(env):
    client, _, _ = env
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["couchdb"] == "ok"


def test_push_requires_auth(env):
    client, _, _ = env
    r = client.post("/v1/session/push", json=_push_body())
    assert r.status_code == 401
    assert r.json()["error"] == "AUTH_MISSING"


def test_push_resume_roundtrip(env):
    """T2 핵심 — push 후 다른 경로에서 resume하면 todo/know가 원문 그대로."""
    client, _, _ = env
    token = _issue_token(client)
    r = client.post("/v1/session/push", json=_push_body(), headers=_h(token))
    assert r.status_code == 201
    data = r.json()
    assert data["created_thread"] is True
    thread = data["thread"]
    assert thread.startswith("T-")
    assert "hyeseongkit" in thread  # 제목에서 유도된 slug (§3-3)

    r = client.get(
        "/v1/session/resume",
        params={"last": 1, "project_id": "p-test"},
        headers=_h(token),
    )
    assert r.status_code == 200
    packet = r.json()["content"]
    assert "1. 이어서 구현" in packet
    assert "- 포트 9100" in packet


def test_whoami(env):
    client, _, _ = env
    token = _issue_token(client)
    r = client.get("/v1/whoami", headers=_h(token))
    assert r.status_code == 200
    assert r.json()["device_id"] == "desktop"


def test_encrypted_at_rest(env):
    """D29 — CouchDB 문서에 본문 평문이 없다."""
    client, _, couch = env
    token = _issue_token(client)
    client.post("/v1/session/push", json=_push_body(), headers=_h(token))
    stored = "".join(str(d) for d in couch.dbs["hyeseongkit_sessions"].values())
    assert "이어서 구현" not in stored
    assert "포트 9100" not in stored


def test_device_mismatch(env):
    client, _, _ = env
    token = _issue_token(client)
    r = client.post("/v1/session/push", json=_push_body(device="macbook"), headers=_h(token))
    assert r.status_code == 400
    assert r.json()["error"] == "DEVICE_MISMATCH"


def test_schema_missing_know(env):
    client, _, _ = env
    token = _issue_token(client)
    body = _push_body()
    body["sections"].pop("know")
    r = client.post("/v1/session/push", json=body, headers=_h(token))
    assert r.status_code == 400
    assert r.json()["error"] == "SCHEMA_INVALID"


def test_server_redaction_rejects_raw_secret(env):
    """T5 — 클라 마스킹 우회(직접 호출) 시 422, 저장 안 됨. 값은 응답에 없음."""
    client, _, couch = env
    token = _issue_token(client)
    secret = "sk-abcdefghijklmnopqrstuvwx"
    body = _push_body()
    body["sections"]["know"] = f"- 키는 {secret}"
    r = client.post("/v1/session/push", json=body, headers=_h(token))
    assert r.status_code == 422
    assert r.json()["error"] == "REDACTION_REQUIRED"
    assert secret not in r.text
    stored = "".join(str(d) for d in couch.dbs.get("hyeseongkit_sessions", {}).values())
    assert secret not in stored


def test_revoked_token_rejected(env):
    """T4 — 폐기 토큰으로 push → 403, 저장 안 됨."""
    client, _, couch = env
    token = _issue_token(client)
    r = _admin(client, "DELETE", "/v1/admin/devices/desktop")
    assert r.status_code == 200
    r = client.post("/v1/session/push", json=_push_body(), headers=_h(token))
    assert r.status_code == 403
    assert r.json()["error"] == "AUTH_REVOKED"
    assert not any(k.startswith("evt:") for k in couch.dbs.get("hyeseongkit_sessions", {}))


def test_thread_limit(env):
    """T3 — 새 스레드 4번째 생성 시도 → 409 THREAD_LIMIT + 목록."""
    client, _, _ = env
    token = _issue_token(client)
    for i in range(3):
        r = client.post(
            "/v1/session/push",
            json=_push_body(title=f"work item {i}", slug=f"work-{i}"),
            headers=_h(token),
        )
        assert r.status_code == 201
        # 뷰 생성 (렌더러 비활성 환경 — resume이 fold-on-demand로 뷰를 만든다)
        client.get(
            "/v1/session/resume",
            params={"thread": r.json()["thread"]},
            headers=_h(token),
        )
    r = client.post(
        "/v1/session/push",
        json=_push_body(title="work item 3", slug="work-3"),
        headers=_h(token),
    )
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "THREAD_LIMIT"
    assert len(body["detail"]["active_threads"]) == 3


def test_thread_exists_conflict(env):
    """같은 날 같은 slug 신규 스레드 → 409 THREAD_EXISTS (사용자 결정 2026-08-12)."""
    client, _, _ = env
    token = _issue_token(client)
    r = client.post("/v1/session/push", json=_push_body(), headers=_h(token))
    assert r.status_code == 201
    r = client.post("/v1/session/push", json=_push_body(), headers=_h(token))
    assert r.status_code == 409
    assert r.json()["error"] == "THREAD_EXISTS"
    assert "hint" in r.json()["detail"]


def test_close_then_push_requires_reopen(env):
    client, _, _ = env
    token = _issue_token(client)
    thread = client.post("/v1/session/push", json=_push_body(), headers=_h(token)).json()["thread"]
    r = client.post(
        "/v1/session/close",
        json={"thread": thread, "project_id": "p-test", "outcome": "done", "device": "desktop"},
        headers=_h(token),
    )
    assert r.status_code == 201
    r = client.post("/v1/session/push", json=_push_body(thread=thread), headers=_h(token))
    assert r.status_code == 409
    assert r.json()["error"] == "THREAD_CLOSED"
    r = client.post(
        "/v1/session/push", json=_push_body(thread=thread, reopen=True), headers=_h(token)
    )
    assert r.status_code == 201


def test_decide_and_packet_includes_decision(env):
    client, _, _ = env
    token = _issue_token(client)
    thread = client.post("/v1/session/push", json=_push_body(), headers=_h(token)).json()["thread"]
    r = client.post(
        "/v1/session/decide",
        json={
            "thread": thread,
            "project_id": "p-test",
            "decision": {"text": "결정 원문 유지", "rationale": "근거", "rejected": "기각안"},
            "device": "desktop",
        },
        headers=_h(token),
    )
    assert r.status_code == 201
    packet = client.get("/v1/session/resume", params={"thread": thread}, headers=_h(token)).json()[
        "content"
    ]
    assert "결정 원문 유지" in packet
    assert "기각안" in packet


def test_decide_unknown_thread_404(env):
    client, _, _ = env
    token = _issue_token(client)
    r = client.post(
        "/v1/session/decide",
        json={
            "thread": "T-20260811-nope",
            "project_id": "p-test",
            "decision": {"text": "x"},
            "device": "desktop",
        },
        headers=_h(token),
    )
    assert r.status_code == 404


def test_checkpoint_no_active_204(env):
    client, _, _ = env
    token = _issue_token(client)
    r = client.post(
        "/v1/session/checkpoint",
        json={"project_id": "p-none", "reason": "session-end", "device": "desktop"},
        headers=_h(token),
    )
    assert r.status_code == 204


def test_checkpoint_attaches_to_latest_active(env):
    client, _, _ = env
    token = _issue_token(client)
    thread = client.post("/v1/session/push", json=_push_body(), headers=_h(token)).json()["thread"]
    client.get("/v1/session/resume", params={"thread": thread}, headers=_h(token))
    r = client.post(
        "/v1/session/checkpoint",
        json={
            "project_id": "p-test",
            "reason": "precompact",
            "git": {"branch": "main", "head": "b82f82b", "dirty": 3},
            "transcript_path": "~/.claude/projects/x/y.jsonl",
            "device": "desktop",
        },
        headers=_h(token),
    )
    assert r.status_code == 201
    assert r.json()["thread"] == thread


def test_search(env):
    client, _, _ = env
    token = _issue_token(client)
    thread = client.post("/v1/session/push", json=_push_body(), headers=_h(token)).json()["thread"]
    client.get("/v1/session/resume", params={"thread": thread}, headers=_h(token))
    r = client.get(
        "/v1/session/search", params={"q": "9100", "project_id": "p-test"}, headers=_h(token)
    )
    assert r.status_code == 200
    assert any(m["thread"] == thread for m in r.json()["matches"])


def test_status(env):
    client, _, _ = env
    token = _issue_token(client)
    thread = client.post("/v1/session/push", json=_push_body(), headers=_h(token)).json()["thread"]
    client.get("/v1/session/resume", params={"thread": thread}, headers=_h(token))
    r = client.get("/v1/session/status", headers=_h(token))
    assert r.status_code == 200
    assert r.json()["hub"]["couchdb"] == "ok"
    assert any(t["thread"] == thread for t in r.json()["threads"])


def test_projects_register_idempotent_and_alias(env):
    client, _, _ = env
    token = _issue_token(client)
    body = {
        "project_id": "p-abc123",
        "canonical": "github.com/owner/repo",
        "name": "repo",
        "sensitivity": "tech",
    }
    r = client.post("/v1/projects", json=body, headers=_h(token))
    assert r.status_code == 201
    r = client.post("/v1/projects", json=body, headers=_h(token))
    assert r.status_code == 200  # idempotent (§3-10)
    r = client.post(
        "/v1/projects/p-abc123/aliases",
        json={"canonical": "github.com/newowner/repo"},
        headers=_h(token),
    )
    assert r.status_code == 200
    r = client.get(
        "/v1/projects", params={"canonical": "github.com/newowner/repo"}, headers=_h(token)
    )
    assert r.json()["projects"][0]["project_id"] == "p-abc123"  # aliases 검색 (C1)
    r = client.get("/v1/projects/p-missing", headers=_h(token))
    assert r.status_code == 404


def test_admin_requires_admin_token(env):
    client, _, _ = env
    token = _issue_token(client)
    r = client.post("/v1/admin/devices", json={"device_id": "macbook"}, headers=_h(token))
    assert r.status_code == 403
