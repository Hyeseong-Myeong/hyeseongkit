import pytest
from cryptography.fernet import Fernet

from hyeseongkit.hub.crypto import BodyCrypto, CryptoError


def test_roundtrip(crypto):
    body = {"title": "한글 제목", "sections": {"know": "- 항목"}}
    enc = crypto.seal(body)
    assert enc["alg"] == "fernet"
    assert "한글" not in enc["data"]
    assert crypto.open(enc) == body


def test_wrong_key_fails(crypto):
    enc = crypto.seal({"a": 1})
    other = BodyCrypto(Fernet.generate_key().decode())
    with pytest.raises(CryptoError):
        other.open(enc)


def test_empty_key_rejected():
    with pytest.raises(CryptoError):
        BodyCrypto("")


def test_malformed_key_rejected():
    with pytest.raises(CryptoError):
        BodyCrypto("not-a-fernet-key")
