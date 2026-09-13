import pytest
from cryptography.fernet import Fernet

from app.security import TokenCipher, sign_payload, verify_signature

SECRET = "s3cret"
BODY = b'{"event":"rfq.approved"}'


def test_valid_signature_verifies():
    header = sign_payload(SECRET, BODY, timestamp=1_700_000_000)
    assert verify_signature(SECRET, BODY, header, now=1_700_000_010)


def test_tampered_body_is_rejected():
    header = sign_payload(SECRET, BODY, timestamp=1_700_000_000)
    assert not verify_signature(SECRET, b'{"event":"rfq.approved","total":"0.01"}', header, now=1_700_000_000)


def test_wrong_secret_is_rejected():
    header = sign_payload("other", BODY, timestamp=1_700_000_000)
    assert not verify_signature(SECRET, BODY, header, now=1_700_000_000)


def test_replayed_old_signature_is_rejected():
    header = sign_payload(SECRET, BODY, timestamp=1_700_000_000)
    assert not verify_signature(SECRET, BODY, header, now=1_700_000_000 + 3600)


@pytest.mark.parametrize("header", ["", "garbage", "t=abc,v1=00", "v1=deadbeef"])
def test_malformed_headers_are_rejected(header):
    assert not verify_signature(SECRET, BODY, header)


def test_token_cipher_round_trip_and_requires_key():
    cipher = TokenCipher(Fernet.generate_key().decode())
    token = cipher.encrypt('{"refresh_token": "abc"}')
    assert "abc" not in token
    assert cipher.decrypt(token) == '{"refresh_token": "abc"}'
    with pytest.raises(RuntimeError):
        TokenCipher("")
