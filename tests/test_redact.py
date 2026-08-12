"""마스킹 테스트 벡터 (설계서 §6-5) + fail-closed 경로 — T1."""

import pytest

from hyeseongkit.core import redact


def test_vector_env_api_key():
    # §6-5 벡터: 기대 "sk-... 잔존 없음". 규칙 순서상 MK05가 MK08보다 먼저 값을 치환한다.
    # (§6-5 표는 MK08로 표기 — 오탈: `OPENAI_API_KEY`는 `_` 때문에 \b 경계가 없어
    #  MK08이 매치되지 않고, 값은 MK05가 잡는다. 보안 결과는 동일 — 문서 정정 후보)
    masked, hits = redact.mask("OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwx")
    assert "sk-abcdefghijklmnopqrstuvwx" not in masked
    assert "sk-" not in masked
    assert hits


def test_vector_bearer_jwt():
    _, hits = redact.mask("Authorization: Bearer eyJhbGciOi.eyJzdWIi.SflKxwRJ")
    assert "MK07" in hits or "MK09" in hits


def test_vector_url_credentials():
    masked, hits = redact.mask("http://user:pass1234@nas.local:5984")
    assert masked == "⟦REDACTED:MK10⟧nas.local:5984"
    assert "MK10" in hits


def test_vector_tailscale_ip():
    masked, hits = redact.mask("100.64.0.1에 배포")
    assert "MK11" in hits
    assert "100.64.0.1" not in masked


def test_vector_no_false_positive_identifiers():
    text = "포트 9100, 커밋 b82f82b"
    masked, hits = redact.mask(text)
    assert masked == text
    assert hits == []


def test_vector_no_false_positive_korean_prose():
    # MK08 값은 라틴·기호만 — 한국어 산문 오탐 금지 (C2)
    text = "password: 로그인후변경하도록안내"
    masked, hits = redact.mask(text)
    assert masked == text
    assert hits == []


def test_mk08_keeps_key_name():
    masked, hits = redact.mask("client_secret: abcdef123456")  # gitleaks:allow
    assert "MK08" in hits
    assert masked.startswith("client_secret: ")
    assert "abcdef123456" not in masked
    assert "⟦REDACTED:MK08⟧" in masked


def test_mk12_own_token():
    token = "hk_" + "a1" * 20
    masked, hits = redact.mask(f"토큰은 {token} 이다")
    assert "MK12" in hits
    assert token not in masked


def test_pem_block():
    text = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"
    masked, hits = redact.mask(text)
    assert "MK01" in hits
    assert "BEGIN RSA" not in masked


def test_fail_closed_on_broken_extra_rule():
    with pytest.raises(redact.RedactionError):
        redact.mask("아무 내용", extra=["("])  # 잘못된 정규식 → fail-closed (§6-3)


def test_extra_rule_applied():
    masked, hits = redact.mask("내부 호스트 my-nas-01 접속", extra=[r"my-nas-\d+"])
    assert "EX01" in hits
    assert "my-nas-01" not in masked


def test_detect_mode_reports_raw_matches():
    assert redact.detect("AKIAABCDEFGHIJKLMNOP") == ["MK02"]  # gitleaks:allow
    assert redact.detect("이미 처리됨 ⟦REDACTED:MK02⟧") == []


def test_mask_obj_nested():
    obj = {"title": "제목", "sections": {"know": "key AKIAABCDEFGHIJKLMNOP"}}  # gitleaks:allow
    masked, hits = redact.mask_obj(obj)
    assert "MK02" in hits
    assert "AKIA" not in masked["sections"]["know"]
    assert masked["title"] == "제목"
