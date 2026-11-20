from pathlib import Path

import pytest

from anvesha.crypto import (
    DecryptionError,
    SecretBox,
    SecretKeyError,
    generate_key,
    secret_box_from_settings,
)
from tests.conftest import make_settings


def test_round_trip_and_ciphertexts_are_unique() -> None:
    box = SecretBox(generate_key())
    first = box.encrypt("sk-secret", "ws:openai")
    second = box.encrypt("sk-secret", "ws:openai")
    assert first != second  # random nonce
    assert "sk-secret" not in first
    assert box.decrypt(first, "ws:openai") == "sk-secret"


def test_ciphertext_is_bound_to_its_associated_data() -> None:
    box = SecretBox(generate_key())
    token = box.encrypt("sk-secret", "workspace-a:openai")
    with pytest.raises(DecryptionError):
        box.decrypt(token, "workspace-b:openai")


def test_wrong_key_and_tampering_are_rejected() -> None:
    token = SecretBox(generate_key()).encrypt("sk-secret", "aad")
    with pytest.raises(DecryptionError):
        SecretBox(generate_key()).decrypt(token, "aad")
    box = SecretBox(generate_key())
    good = box.encrypt("sk-secret", "aad")
    tampered = good[:-4] + ("AAAA" if not good.endswith("AAAA") else "BBBB")
    with pytest.raises(DecryptionError):
        box.decrypt(tampered, "aad")
    with pytest.raises(DecryptionError):
        box.decrypt("garbage", "aad")


def test_rotation_keeps_old_ciphertexts_readable() -> None:
    old_key, new_key = generate_key(), generate_key()
    token = SecretBox(old_key).encrypt("sk-secret", "aad")
    rotated = SecretBox(new_key, previous_keys=[old_key])
    assert rotated.decrypt(token, "aad") == "sk-secret"


@pytest.mark.parametrize("bad", ["", "not base64!!", "c2hvcnQ"])
def test_malformed_keys_are_rejected(bad: str) -> None:
    with pytest.raises(SecretKeyError):
        SecretBox(bad)


def test_key_file_is_created_once_and_reused(tmp_path: Path) -> None:
    key_file = tmp_path / "nested" / "secret.key"
    settings = make_settings(secret_key=None, secret_key_file=key_file)
    first = secret_box_from_settings(settings)
    assert first is not None and key_file.exists()
    second = secret_box_from_settings(settings)
    assert second is not None
    assert second.decrypt(first.encrypt("sk", "aad"), "aad") == "sk"


def test_no_key_and_no_file_means_no_vault() -> None:
    assert secret_box_from_settings(make_settings(secret_key=None, secret_key_file=None)) is None
