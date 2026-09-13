import hashlib
import hmac
import time

from cryptography.fernet import Fernet, InvalidToken

SIGNATURE_HEADER = "X-RFQDesk-Signature"
IDEMPOTENCY_HEADER = "X-RFQDesk-Idempotency-Key"
SIGNATURE_TOLERANCE_SECONDS = 300


def sign_payload(secret: str, body: bytes, timestamp: int | None = None) -> str:
    """Stripe-style signature: `t=<unix>,v1=<hex hmac-sha256 of "<t>.<body>">`."""
    ts = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def verify_signature(secret: str, body: bytes, header: str, now: int | None = None) -> bool:
    try:
        fields = dict(part.split("=", 1) for part in header.split(","))
        ts = int(fields["t"])
        received = fields["v1"]
    except (ValueError, KeyError):
        return False
    current = int(time.time()) if now is None else now
    if abs(current - ts) > SIGNATURE_TOLERANCE_SECONDS:
        return False  # replay protection
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


class TokenCipher:
    def __init__(self, key: str | None):
        if not key:
            raise RuntimeError("TOKEN_ENCRYPTION_KEY must be set to store OAuth tokens")
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError("stored OAuth token could not be decrypted (key rotated?)") from exc
