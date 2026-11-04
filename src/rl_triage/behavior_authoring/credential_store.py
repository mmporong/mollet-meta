"""Windows 사용자 계정에 묶인 Meta API 설정 저장소."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import tempfile
from typing import Any


CRYPTPROTECT_UI_FORBIDDEN = 0x1

_ENTROPY = b"rl-triage-meta:model-api-settings:v1"
_FILE_MAGIC = b"RLT-META-DPAPI\x00\x01"
_SCHEMA = "rl-triage-meta.model-api-settings.v1"
_SETTINGS_KEYS = {
    "api_key",
    "credit_confirmed",
    "remaining_credit_usd",
    "budget_usd",
}
_MAX_PLAINTEXT_BYTES = 16 * 1024
_MAX_CIPHERTEXT_BYTES = 64 * 1024


class CredentialStoreError(RuntimeError):
    """보호 저장소를 안전하게 읽거나 쓸 수 없을 때 발생한다."""


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _require_windows() -> None:
    if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
        raise CredentialStoreError(
            "Meta API 설정 보호 저장소는 Windows CurrentUser DPAPI에서만 지원됩니다."
        )


def _money(value: Any, field: str, *, maximum: Decimal | None = None) -> None:
    if type(value) is not str or not value or value.strip() != value:
        raise ValueError(f"{field} 값은 공백 없는 문자열이어야 합니다.")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"{field} 값은 유한한 0 이상의 금액이어야 합니다.") from None
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{field} 값은 유한한 0 이상의 금액이어야 합니다.")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{field} 값은 {maximum} 이하여야 합니다.")


def _validated(settings: Any) -> dict[str, str | bool | None]:
    if type(settings) is not dict or set(settings) != _SETTINGS_KEYS:
        raise ValueError("Meta API 설정의 필드 구성이 올바르지 않습니다.")

    api_key = settings["api_key"]
    if api_key is not None and (
        type(api_key) is not str
        or not api_key
        or api_key.strip() != api_key
        or len(api_key.encode("utf-8")) > 8 * 1024
    ):
        raise ValueError("api_key 값은 공백 없는 비어 있지 않은 문자열 또는 미연결 null이어야 합니다.")
    if type(settings["credit_confirmed"]) is not bool:
        raise ValueError("credit_confirmed 값은 bool이어야 합니다.")

    _money(settings["remaining_credit_usd"], "remaining_credit_usd")
    _money(settings["budget_usd"], "budget_usd", maximum=Decimal("1"))
    return dict(settings)


def _to_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_ubyte]]:
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    return blob, buffer


def _crypt32() -> tuple[Any, Any]:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def _protect(plaintext: bytes) -> bytes:
    _require_windows()
    if len(plaintext) > _MAX_PLAINTEXT_BYTES:
        raise CredentialStoreError("Meta API 설정이 보호 저장소의 크기 제한을 넘었습니다.")

    input_blob, input_buffer = _to_blob(plaintext)
    entropy_blob, entropy_buffer = _to_blob(_ENTROPY)
    output_blob = _DataBlob()
    crypt32, kernel32 = _crypt32()
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    ):
        error = ctypes.get_last_error()
        raise CredentialStoreError(f"Windows DPAPI 암호화에 실패했습니다 (오류 {error}).")
    _ = input_buffer, entropy_buffer

    try:
        ciphertext = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))
    if not ciphertext or len(ciphertext) > _MAX_CIPHERTEXT_BYTES:
        raise CredentialStoreError("암호화된 Meta API 설정의 크기가 올바르지 않습니다.")
    return ciphertext


def _unprotect(ciphertext: bytes) -> bytes:
    _require_windows()
    if not ciphertext or len(ciphertext) > _MAX_CIPHERTEXT_BYTES:
        raise CredentialStoreError("암호화된 Meta API 설정의 크기가 올바르지 않습니다.")

    input_blob, input_buffer = _to_blob(ciphertext)
    entropy_blob, entropy_buffer = _to_blob(_ENTROPY)
    output_blob = _DataBlob()
    crypt32, kernel32 = _crypt32()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    ):
        error = ctypes.get_last_error()
        raise CredentialStoreError(f"Windows DPAPI 복호화에 실패했습니다 (오류 {error}).")
    _ = input_buffer, entropy_buffer

    try:
        plaintext = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))
    if len(plaintext) > _MAX_PLAINTEXT_BYTES:
        raise CredentialStoreError("복호화된 Meta API 설정이 크기 제한을 넘었습니다.")
    return plaintext


def save_settings(path: Path, settings: dict[str, Any]) -> None:
    """설정을 CurrentUser DPAPI로 암호화해 원자적으로 저장한다."""

    _require_windows()
    validated = _validated(settings)
    payload = json.dumps(
        {"schema": _SCHEMA, "settings": validated},
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    ciphertext = _protect(payload)
    stored = _FILE_MAGIC + ciphertext

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = -1
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        with os.fdopen(descriptor, "wb") as temporary:
            descriptor = -1
            temporary.write(stored)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def load_settings(path: Path) -> dict[str, str | bool | None] | None:
    """저장된 설정을 복호화하며, 파일이 없으면 ``None``을 반환한다."""

    source = Path(path)
    if not source.exists():
        return None
    _require_windows()

    maximum_stored_size = len(_FILE_MAGIC) + _MAX_CIPHERTEXT_BYTES
    with source.open("rb") as stored_file:
        stored = stored_file.read(maximum_stored_size + 1)
    if len(stored) <= len(_FILE_MAGIC) or len(stored) > maximum_stored_size:
        raise CredentialStoreError("암호화된 Meta API 설정 파일의 크기가 올바르지 않습니다.")
    if not stored.startswith(_FILE_MAGIC):
        raise CredentialStoreError("Meta API 설정 파일 형식을 인식할 수 없습니다.")

    plaintext = _unprotect(stored[len(_FILE_MAGIC) :])
    try:
        payload = json.loads(plaintext.decode("utf-8"))
        if type(payload) is not dict or set(payload) != {"schema", "settings"}:
            raise ValueError
        if payload["schema"] != _SCHEMA:
            raise ValueError
        return _validated(payload["settings"])
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise CredentialStoreError("복호화된 Meta API 설정 형식이 올바르지 않습니다.") from None
