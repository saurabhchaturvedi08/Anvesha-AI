"""Encryption for secrets stored in the database (provider API keys).

Ciphertexts look like ``v1.<key id>.<base64url(nonce + AES-256-GCM ciphertext)>``. The key
id lets old ciphertexts be decrypted after the master key is rotated. Every encryption
is bound to "associated data" describing where the secret lives (for example its
workspace and provider), so a ciphertext copied to another row fails to decrypt.

Generate a key with ``uv run python -m anvesha.crypto``.
"""

import base64
import hashlib
import os
import secrets
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from anvesha.config import Settings

_VERSION = "v1"
_NONCE_BYTES = 12


class SecretKeyError(RuntimeError):
    """The master key is missing or malformed."""


class DecryptionError(RuntimeError):
    """A ciphertext could not be decrypted (wrong key, tampered, or moved)."""


def generate_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


def _decode_key(encoded: str) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(encoded.strip() + "=" * (-len(encoded.strip()) % 4))
    except ValueError as exc:
        raise SecretKeyError("The secret key is not valid base64url.") from exc
    if len(raw) != 32:
        raise SecretKeyError("The secret key must decode to exactly 32 bytes.")
    return raw


def _key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:8]


class SecretBox:
    def __init__(self, current_key: str, previous_keys: list[str] | None = None) -> None:
        self._current = _decode_key(current_key)
        self._keys = {_key_id(self._current): self._current}
        for encoded in previous_keys or []:
            key = _decode_key(encoded)
            self._keys.setdefault(_key_id(key), key)

    def encrypt(self, plaintext: str, associated_data: str) -> str:
        nonce = os.urandom(_NONCE_BYTES)
        sealed = AESGCM(self._current).encrypt(nonce, plaintext.encode(), associated_data.encode())
        payload = base64.urlsafe_b64encode(nonce + sealed).decode()
        return f"{_VERSION}.{_key_id(self._current)}.{payload}"

    def decrypt(self, token: str, associated_data: str) -> str:
        try:
            version, key_id, payload = token.split(".", 2)
            raw = base64.urlsafe_b64decode(payload)
        except ValueError as exc:
            raise DecryptionError("Malformed ciphertext.") from exc
        key = self._keys.get(key_id)
        if version != _VERSION or key is None:
            raise DecryptionError("Ciphertext was made with an unknown key or format.")
        try:
            plaintext = AESGCM(key).decrypt(
                raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], associated_data.encode()
            )
        except InvalidTag as exc:
            raise DecryptionError("Ciphertext failed authentication.") from exc
        return plaintext.decode()


def _load_or_create_key_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    key = generate_key()
    try:
        # O_EXCL: if another process created it first, use theirs instead.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text(encoding="utf-8").strip()
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(key + "\n")
    return key


def secret_box_from_settings(settings: Settings) -> SecretBox | None:
    """The configured SecretBox, or None if no key is set and no key file is configured."""
    previous = [
        item
        for item in (
            settings.previous_secret_keys.get_secret_value().split(",")
            if settings.previous_secret_keys
            else []
        )
        if item.strip()
    ]
    if settings.secret_key is not None:
        return SecretBox(settings.secret_key.get_secret_value(), previous)
    if settings.secret_key_file is not None:
        return SecretBox(_load_or_create_key_file(settings.secret_key_file), previous)
    return None


if __name__ == "__main__":
    print(generate_key())
