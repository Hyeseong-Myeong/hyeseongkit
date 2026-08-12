"""SSOT 본문 필드 암호화 (D29, 설계서 §0-2-2) — Fernet(AES-128-CBC + HMAC-SHA256).

- 허브만 키를 보유한다 (`.env`의 HK_ENCRYPTION_KEY)
- 암호화 대상: 본문 필드(title, sections, decision, know_carryover, decisions)
- 비암호화 대상: thread, status, project_id, sensitivity, created, updated 등 메타데이터
- 저장 표현: 본문 필드 묶음을 JSON 직렬화 후 문서당 하나의 `enc` 블록으로 저장
"""

from __future__ import annotations

import json

from cryptography.fernet import Fernet, InvalidToken

KEYGEN_HINT = (
    'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
)


class CryptoError(Exception):
    pass


class BodyCrypto:
    def __init__(self, key: str):
        if not key:
            raise CryptoError(f"HK_ENCRYPTION_KEY가 비어 있음 — 생성: {KEYGEN_HINT}")
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except Exception as exc:
            raise CryptoError("HK_ENCRYPTION_KEY 형식 오류 — Fernet 키가 아님") from exc

    def seal(self, body: dict) -> dict:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        return {"v": 1, "alg": "fernet", "data": self._fernet.encrypt(data).decode("ascii")}

    def open(self, enc: dict) -> dict:
        try:
            raw = self._fernet.decrypt(enc["data"].encode("ascii"))
        except (InvalidToken, KeyError, AttributeError) as exc:
            raise CryptoError("복호화 실패 — 키 불일치 또는 문서 손상") from exc
        return json.loads(raw.decode("utf-8"))
