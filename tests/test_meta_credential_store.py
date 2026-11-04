from __future__ import annotations

import importlib
import os
from pathlib import Path
import secrets

import pytest

from rl_triage.behavior_authoring import credential_store


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows CurrentUser DPAPI 검증")


def _settings(secret: str, *, budget: str = "0.50") -> dict[str, str | bool]:
    return {
        "api_key": secret,
        "credit_confirmed": True,
        "remaining_credit_usd": "1.00",
        "budget_usd": budget,
    }


def test_dpapi_round_trip_survives_module_reload(tmp_path: Path) -> None:
    fixture_secret = secrets.token_urlsafe(32)
    path = tmp_path / "meta-settings.bin"

    credential_store.save_settings(path, _settings(fixture_secret))
    reloaded_store = importlib.reload(credential_store)
    loaded = reloaded_store.load_settings(path)

    assert loaded is not None
    round_trip_matches = loaded == _settings(fixture_secret)
    plaintext_absent = fixture_secret.encode("utf-8") not in path.read_bytes()
    assert round_trip_matches is True
    assert plaintext_absent is True


def test_corrupted_ciphertext_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "meta-settings.bin"
    credential_store.save_settings(path, _settings(secrets.token_urlsafe(32)))
    corrupted = bytearray(path.read_bytes())
    corrupted[-1] ^= 0x01
    path.write_bytes(corrupted)

    with pytest.raises(credential_store.CredentialStoreError):
        credential_store.load_settings(path)


def test_failed_replace_preserves_previous_encrypted_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_secret = secrets.token_urlsafe(32)
    new_secret = secrets.token_urlsafe(32)
    path = tmp_path / "meta-settings.bin"
    credential_store.save_settings(path, _settings(old_secret))
    original_ciphertext = path.read_bytes()

    def fail_replace(_source: str, _destination: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(credential_store.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        credential_store.save_settings(path, _settings(new_secret))

    ciphertext_preserved = path.read_bytes() == original_ciphertext
    old_settings_preserved = credential_store.load_settings(path) == _settings(old_secret)
    assert ciphertext_preserved is True
    assert old_settings_preserved is True
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize("budget", ["1.01", "NaN", "Infinity", "-0.01"])
def test_invalid_budget_is_rejected_without_creating_file(
    tmp_path: Path, budget: str
) -> None:
    path = tmp_path / "meta-settings.bin"

    with pytest.raises(ValueError):
        credential_store.save_settings(path, _settings(secrets.token_urlsafe(32), budget=budget))

    assert not path.exists()
