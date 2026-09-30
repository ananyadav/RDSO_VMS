"""Encrypted storage for third-party VMS integration credentials (RDSO 18.6.17.3).

Uses Fernet (symmetric AES) keyed from CCC_VMS_CREDENTIAL_KEY env.
Never log or return plaintext secrets to clients.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


class CredentialCryptoError(ValueError):
    pass


def _fernet() -> Fernet:
    raw = (
        os.getenv("CCC_VMS_CREDENTIAL_KEY", "").strip()
        or os.getenv("SESSION_SECRET", "").strip()
        or os.getenv("JWT_SECRET", "").strip()
    )
    if not raw:
        raise CredentialCryptoError(
            "CCC_VMS_CREDENTIAL_KEY (or SESSION_SECRET) required for VMS credential encryption"
        )
    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_credential(plaintext: str) -> str:
    if not plaintext:
        raise CredentialCryptoError("Empty credential")
    token = _fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_credential(ciphertext: str) -> str:
    if not ciphertext:
        raise CredentialCryptoError("Missing encrypted credential")
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialCryptoError("Invalid or corrupted credential blob") from exc
