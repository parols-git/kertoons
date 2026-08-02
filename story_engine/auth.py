"""
Password hashing - stdlib only (hashlib.pbkdf2_hmac + secrets), so no new
dependency (bcrypt/passlib/etc.) is needed for a simple accounts system.
"""
import hashlib
import hmac
import secrets

_PBKDF2_ITERATIONS = 200_000
_ALGORITHM = "sha256"


def hash_password(password: str) -> tuple:
    """Returns (salt_hex, hash_hex). A fresh random salt is generated per
    password so two users with the same password never produce the same
    stored hash."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(_ALGORITHM, password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    salt = bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac(_ALGORITHM, password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    # constant-time compare - a plain `==` would leak timing information
    # about how many leading bytes of the hash matched.
    return hmac.compare_digest(digest.hex(), hash_hex)
