"""Meta 모델 API의 보수적 단일 시도 전송 계층."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from typing import Any, Callable


META_ENDPOINT = "https://api.meta.ai/v1/responses"
TOKEN_COUNT_ENDPOINT = META_ENDPOINT + '/input_tokens'
ALLOWED_MODELS = frozenset({"muse-spark-1.3", "sam-3.1"})
MAX_BUDGET_USD = Decimal("1.00")
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
SOCKET_TIMEOUT_SECONDS = 20.0
TOTAL_TIMEOUT_SECONDS = 180.0
_MICRO_USD = Decimal("0.000001")


class MetaAPIError(ValueError):
    """안전하게 사용자에게 표시할 수 있는 Meta API 오류."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise MetaAPIError("Meta API 리디렉션을 거부했습니다.")


Transport = Callable[[bytes, str], tuple[bytes, str, str | None]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_state_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Mollet" / "meta-model-api"
    return Path.home() / ".local" / "state" / "mollet" / "meta-model-api"


def _validate_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MetaAPIError("Meta API 키가 비어 있습니다.")
    if len(value) > 4096:
        raise MetaAPIError("Meta API 키가 허용 길이를 초과했습니다.")
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        raise MetaAPIError("Meta API 키는 ASCII 문자만 사용할 수 있습니다.") from None
    if any(ord(character) < 33 or ord(character) > 126 for character in value):
        raise MetaAPIError("Meta API 키에 허용되지 않는 문자가 있습니다.")
    return value


def _money(value: object, field: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise MetaAPIError(f"{field} 금액 형식이 올바르지 않습니다.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise MetaAPIError(f"{field} 금액 형식이 올바르지 않습니다.") from None
    if not result.is_finite() or result < 0 or (positive and result <= 0):
        raise MetaAPIError(f"{field} 금액은 유한한 양수여야 합니다." if positive else f"{field} 금액은 유한한 0 이상 값이어야 합니다.")
    return result


def _money_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." not in text:
        return text + ".00"
    whole, fraction = text.split(".", 1)
    fraction = fraction.rstrip("0")
    return whole + "." + (fraction if len(fraction) >= 2 else fraction.ljust(2, "0"))


def _json_bytes(value: object, *, label: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        raise MetaAPIError(f"{label}에 JSON으로 저장할 수 없는 값이 있습니다.") from None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_once(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)


class MetaClient:
    """크레딧과 로컬 예산을 확인하고 Meta API 요청을 한 번만 전송한다."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        credit_confirmed: bool | None = None,
        remaining_credit_usd: Decimal | str | None = None,
        budget_usd: Decimal | str | None = None,
        state_dir: Path | None = None,
        transport: Transport | None = None,
        token_transport: Transport | None = None,
        persist_credentials: bool = False,
    ) -> None:
        if type(persist_credentials) is not bool:
            raise MetaAPIError('암호화 저장 설정은 bool이어야 합니다.')
        selected_state_dir = Path(state_dir) if state_dir is not None else _default_state_dir()
        self.state_dir = selected_state_dir.expanduser().resolve()
        self._persist_credentials = persist_credentials
        self._credential_path = self.state_dir / 'credentials.dpapi'
        configuration_override = any(value is not None for value in (api_key,credit_confirmed,remaining_credit_usd,budget_usd)) or os.environ.get('MODEL_API_KEY') is not None
        saved = None
        if persist_credentials:
            from .credential_store import CredentialStoreError, load_settings
            try:
                saved = load_settings(self._credential_path)
            except (CredentialStoreError,ValueError,OSError):
                raise MetaAPIError('저장한 Meta 연결 설정을 복원하지 못했습니다. 암호화 원본은 보존했습니다.') from None
        saved = saved or {}
        if credit_confirmed is None:
            credit_confirmed = saved.get('credit_confirmed',False)
        if remaining_credit_usd is None:
            remaining_credit_usd = saved.get('remaining_credit_usd','0')
        if budget_usd is None:
            budget_usd = saved.get('budget_usd','1.00')
        if not isinstance(credit_confirmed, bool):
            raise MetaAPIError("크레딧 확인 값은 bool이어야 합니다.")
        self._config_lock = threading.RLock()
        self._preflight_lock = threading.Lock()
        self._api_key = _validate_key(api_key) if api_key is not None else None
        if api_key is None:
            env_key = os.environ.get("MODEL_API_KEY")
            existing_key = env_key if env_key is not None else saved.get('api_key')
            self._api_key = _validate_key(existing_key) if existing_key is not None else None
        self._credit_confirmed = credit_confirmed
        self._remaining_credit_usd = _money(remaining_credit_usd, "남은 크레딧")
        self._budget_usd = self._validate_budget(budget_usd)
        self._transport = transport or self._default_transport
        self._token_transport = token_transport or (lambda raw, key: self._http_transport(raw, key, TOKEN_COUNT_ENDPOINT))
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "runs").mkdir(exist_ok=True)
        self._ledger_path = self.state_dir / "reservations.sqlite3"
        self._initialize_ledger()
        if self._persist_credentials and configuration_override:
            self._save_configuration(self._api_key,self._credit_confirmed,self._remaining_credit_usd,self._budget_usd)

    def _save_configuration(self,key: str | None,credit: bool,remaining: Decimal,budget: Decimal) -> None:
        from .credential_store import CredentialStoreError, save_settings
        try:
            save_settings(self._credential_path,{'api_key':key,'credit_confirmed':credit,
                                                'remaining_credit_usd':_money_text(remaining),
                                                'budget_usd':_money_text(budget)})
        except (CredentialStoreError,ValueError,OSError):
            raise MetaAPIError('Meta 연결 설정의 암호화 저장을 완료하지 못했습니다. 기존 저장값은 유지했습니다.') from None

    @staticmethod
    def _validate_budget(value: object) -> Decimal:
        budget = _money(value, "예산")
        if budget > MAX_BUDGET_USD:
            raise MetaAPIError("Meta API 로컬 예산은 US$1.00을 초과할 수 없습니다.")
        return budget

    def configure(
        self,
        *,
        api_key: str | None = None,
        credit_confirmed: bool | None = None,
        remaining_credit_usd: Decimal | str | None = None,
        budget_usd: Decimal | str | None = None,
    ) -> dict[str, Any]:
        with self._config_lock:
            next_key = self._api_key if api_key is None else _validate_key(api_key)
            next_credit_confirmed = self._credit_confirmed
            if credit_confirmed is not None:
                if not isinstance(credit_confirmed, bool):
                    raise MetaAPIError("크레딧 확인 값은 bool이어야 합니다.")
                next_credit_confirmed = credit_confirmed
            next_remaining = (
                self._remaining_credit_usd
                if remaining_credit_usd is None
                else _money(remaining_credit_usd, "남은 크레딧")
            )
            next_budget = (
                self._budget_usd if budget_usd is None else self._validate_budget(budget_usd)
            )
            if self._persist_credentials:
                self._save_configuration(next_key,next_credit_confirmed,next_remaining,next_budget)
            self._api_key = next_key
            self._credit_confirmed = next_credit_confirmed
            self._remaining_credit_usd = next_remaining
            self._budget_usd = next_budget
        return self.status()

    def _configuration_snapshot(self) -> tuple[str | None, bool, Decimal, Decimal]:
        with self._config_lock:
            return (
                self._api_key,
                self._credit_confirmed,
                self._remaining_credit_usd,
                self._budget_usd,
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._ledger_path, timeout=10.0)
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _database(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_ledger(self) -> None:
        with self._database() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS reservations (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    model TEXT NOT NULL,
                    reserved_micro_usd INTEGER NOT NULL CHECK (reserved_micro_usd > 0),
                    status TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL
                )
                """
            )
            connection.execute('''CREATE TABLE IF NOT EXISTS input_preflights (
                request_sha256 TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                status TEXT NOT NULL, input_units INTEGER, attempts INTEGER NOT NULL)''')

    def _ledger_totals(self) -> tuple[int, int]:
        with self._database() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(reserved_micro_usd), 0), COUNT(*) FROM reservations"
            ).fetchone()
        return int(row[0]), int(row[1])

    def status(self) -> dict[str, Any]:
        return self._status_from_snapshot(self._configuration_snapshot())

    def _status_from_snapshot(
        self, snapshot: tuple[str | None, bool, Decimal, Decimal]
    ) -> dict[str, Any]:
        api_key, credit_confirmed, remaining_credit, budget = snapshot
        reserved_micro, count = self._ledger_totals()
        with self._database() as connection:
            preflights = int(connection.execute('SELECT COALESCE(SUM(attempts),0) FROM input_preflights').fetchone()[0])
        reserved = Decimal(reserved_micro) * _MICRO_USD
        available = max(Decimal("0"), budget - reserved)
        blockers: list[str] = []
        if api_key is None:
            blockers.append("MODEL_API_KEY가 설정되지 않았습니다.")
        if not credit_confirmed:
            blockers.append("Meta 제공 크레딧 확인이 필요합니다.")
        if remaining_credit < budget:
            blockers.append("남은 Meta 크레딧이 로컬 예산보다 적습니다.")
        if available <= 0:
            blockers.append("Meta API 로컬 예산이 소진되었습니다.")
        enabled = (
            api_key is not None
            and credit_confirmed
            and remaining_credit >= budget
            and available > 0
        )
        return {
            'credential_persistence_enabled': self._persist_credentials,
            'credential_saved': self._persist_credentials and self._api_key is not None and self._credential_path.is_file(),
            'configuration_saved': self._persist_credentials and self._credential_path.is_file(),
            'preflight_requests_started': preflights,
            'total_external_requests_started': count + preflights,
            "key_configured": api_key is not None,
            "credit_confirmed": credit_confirmed,
            "remaining_credit_usd": _money_text(remaining_credit),
            "budget_usd": _money_text(budget),
            "reserved_usd": _money_text(reserved),
            "available_budget_usd": _money_text(available),
            "enabled": enabled,
            "blockers": blockers,
            "live_requests_started": count,
        }

    def _check_enabled(
        self, snapshot: tuple[str | None, bool, Decimal, Decimal]
    ) -> None:
        status = self._status_from_snapshot(snapshot)
        if not status["enabled"]:
            raise MetaAPIError("Meta API 전송이 비활성화되어 있습니다: " + " ".join(status["blockers"]))

    def _reserve(
        self,
        run_id: str,
        model: str,
        estimated: Decimal,
        request_sha256: str,
        budget: Decimal,
    ) -> tuple[Decimal, str]:
        reserved = estimated.quantize(_MICRO_USD, rounding=ROUND_CEILING)
        reserved_micro = int(reserved / _MICRO_USD)
        created_at = _utc_now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = int(
                connection.execute(
                    "SELECT COALESCE(SUM(reserved_micro_usd), 0) FROM reservations"
                ).fetchone()[0]
            )
            budget_micro = int((budget / _MICRO_USD).to_integral_value())
            if current + reserved_micro > budget_micro:
                raise MetaAPIError("요청의 보수적 예상 비용이 남은 로컬 예산을 초과합니다.")
            connection.execute(
                "INSERT INTO reservations VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, created_at, model, reserved_micro, "reserved", request_sha256),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return reserved, created_at

    def _mark_status(self, run_id: str, status: str) -> None:
        with self._database() as connection:
            connection.execute(
                "UPDATE reservations SET status = ? WHERE run_id = ?", (status, run_id)
            )

    @staticmethod
    def _redact(text: str, api_key: str | None) -> str:
        if api_key:
            return text.replace(api_key, "[REDACTED]")
        return text

    def send(
        self,
        request: dict[str, Any],
        *,
        estimated_cost_usd: Decimal | str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot = self._configuration_snapshot()
        api_key, _, _, budget = snapshot
        self._check_enabled(snapshot)
        if not isinstance(request, dict):
            raise MetaAPIError("Meta API 요청은 JSON 객체여야 합니다.")
        model = request.get("model")
        if model not in ALLOWED_MODELS:
            raise MetaAPIError("허용되지 않은 Meta 모델입니다.")
        if not isinstance(context, dict):
            raise MetaAPIError("요청 출처 정보는 JSON 객체여야 합니다.")
        context_bytes = _json_bytes(context, label="요청 출처 정보")
        if len(context_bytes) > 256 * 1024:
            raise MetaAPIError("요청 출처 정보가 너무 큽니다.")
        lowered_keys = {str(key).lower() for key in context}
        if any(any(word in key for word in ("authorization", "api_key", "token", "password", "secret")) for key in lowered_keys):
            raise MetaAPIError("요청 출처 정보에 비밀값으로 보이는 필드가 있습니다.")
        request_bytes = _json_bytes(request, label="Meta API 요청")
        if len(request_bytes) > MAX_REQUEST_BYTES:
            raise MetaAPIError("Meta API 요청 본문이 16 MiB를 초과합니다.")
        if api_key and (
            api_key.encode("utf-8") in request_bytes
            or api_key.encode("utf-8") in context_bytes
        ):
            raise MetaAPIError("요청 본문이나 출처 정보에 Meta API 키를 포함할 수 없습니다.")
        estimated = _money(estimated_cost_usd, "보수적 예상 비용", positive=True)
        run_id = str(uuid.uuid4())
        request_sha = _sha256(request_bytes)
        reserved, created_at = self._reserve(
            run_id, model, estimated, request_sha, budget
        )
        run_dir = self.state_dir / "runs" / run_id
        run_dir.mkdir()
        _write_once(run_dir / "request.json", request_bytes)

        receipt: dict[str, Any] = {
            "run_id": run_id,
            "directory": str(run_dir),
            "created_at": created_at,
            "endpoint": META_ENDPOINT,
            "model": model,
            "reserved_usd": _money_text(reserved),
            "request_sha256": request_sha,
            "context": json.loads(context_bytes),
            "status": "started",
        }
        try:
            response_bytes, content_type, request_id = self._transport(
                request_bytes, api_key or ""
            )
            if not isinstance(response_bytes, bytes):
                raise MetaAPIError("Meta API 응답 형식이 올바르지 않습니다.")
            if len(response_bytes) > MAX_RESPONSE_BYTES:
                raise MetaAPIError("Meta API 응답 본문이 32 MiB를 초과합니다.")
            try:
                response_text = response_bytes.decode("utf-8")
            except UnicodeDecodeError:
                raise MetaAPIError("Meta API 응답을 UTF-8로 해석할 수 없습니다.") from None
            response_text = self._redact(response_text, api_key)
            safe_response = response_text.encode("utf-8")
            normalized_type = content_type.split(";", 1)[0].strip().lower()
            if normalized_type == "application/json":
                try:
                    parsed = json.loads(response_text)
                except json.JSONDecodeError:
                    raise MetaAPIError("Meta API JSON 응답이 올바르지 않습니다.") from None
                response_id = parsed.get("id") if isinstance(parsed, dict) else None
                response_path = run_dir / "response.json"
            elif normalized_type == "text/event-stream":
                response_id = None
                response_path = run_dir / "response.sse"
            else:
                raise MetaAPIError("Meta API가 지원되지 않는 콘텐츠 형식을 반환했습니다.")
            _write_once(response_path, safe_response)
            receipt.update(
                {
                    "status": "completed_http",
                    "response_id": response_id,
                    "response_sha256": _sha256(safe_response),
                    "provider_request_id": self._redact(request_id, api_key) if request_id else None,
                    "content_type": normalized_type,
                }
            )
            _write_once(run_dir / "receipt.json", _json_bytes(receipt, label="영수증"))
            self._mark_status(run_id, "completed")
            return {"response": response_text, "receipt": receipt}
        except Exception as exc:
            safe_message = self._redact(str(exc), api_key)
            if not isinstance(exc, MetaAPIError):
                safe_message = "Meta API 전송에 실패했습니다. 자동 재시도하지 않았습니다."
            receipt.update({"status": "network_error", "error": safe_message})
            receipt_path = run_dir / "receipt.json"
            if not receipt_path.exists():
                _write_once(receipt_path, _json_bytes(receipt, label="오류 영수증"))
            self._mark_status(run_id, "error")
            if isinstance(exc, MetaAPIError):
                raise MetaAPIError(safe_message) from None
            raise MetaAPIError(safe_message) from None

    def count_input_tokens(self, request: dict) -> int:
        """공식 media-aware preflight. 생성 요청과 분리하며 자동 재시도하지 않는다."""
        snapshot = self._configuration_snapshot()
        self._check_enabled(snapshot)
        if request.get('model') != 'muse-spark-1.3':
            raise MetaAPIError('Spark 요청만 토큰을 확인할 수 있습니다.')
        body = {'model': request['model'], 'input': request['input']}
        if 'text' in request:
            body['text'] = request['text']
        raw = _json_bytes(body, label='입력 토큰 요청')
        if len(raw) > MAX_REQUEST_BYTES or (snapshot[0] and snapshot[0].encode('utf-8') in raw):
            raise MetaAPIError('입력 토큰 요청 크기 또는 비밀값 포함 여부를 확인하세요.')
        sha = _sha256(raw)
        with self._preflight_lock:
            with self._database() as connection:
                cached = connection.execute('SELECT status,input_units FROM input_preflights WHERE request_sha256=?',(sha,)).fetchone()
                if cached and cached[0] == 'completed':
                    return int(cached[1])
                connection.execute('''INSERT INTO input_preflights VALUES (?,?,?,NULL,1)
                    ON CONFLICT(request_sha256) DO UPDATE SET status='started',attempts=attempts+1''',
                                   (sha,_utc_now(),'started'))
            try:
                body, content_type, _ = self._token_transport(raw, snapshot[0] or '')
                if not isinstance(body, bytes) or len(body) > 65536 or content_type.split(';')[0] != 'application/json':
                    raise ValueError('invalid token count response')
                count = json.loads(body)['input_tokens']
                if type(count) is not int or not 0 < count <= 2000000:
                    raise ValueError('invalid input token count')
            except Exception:
                with self._database() as connection:
                    connection.execute("UPDATE input_preflights SET status='error' WHERE request_sha256=?",(sha,))
                raise MetaAPIError('영상 입력 토큰을 확인하지 못했습니다. 생성 요청을 보내지 않았습니다.') from None
            with self._database() as connection:
                connection.execute("UPDATE input_preflights SET status='completed',input_units=? WHERE request_sha256=?",(count,sha))
            return count

    def _default_transport(self, request_bytes: bytes, api_key: str) -> tuple[bytes, str, str | None]:
        return self._http_transport(request_bytes, api_key, META_ENDPOINT)

    def _http_transport(self, request_bytes: bytes, api_key: str, endpoint: str) -> tuple[bytes, str, str | None]:
        if endpoint not in {META_ENDPOINT, TOKEN_COUNT_ENDPOINT}:
            raise MetaAPIError('허용되지 않은 Meta API 주소입니다.')
        request = urllib.request.Request(
            endpoint,
            data=request_bytes,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        opener = urllib.request.build_opener(_NoRedirect())
        started = time.monotonic()
        try:
            with opener.open(request, timeout=SOCKET_TIMEOUT_SECONDS) as response:
                chunks: list[bytes] = []
                size = 0
                while True:
                    if time.monotonic() - started > TOTAL_TIMEOUT_SECONDS:
                        raise MetaAPIError("Meta API 전체 응답 제한 시간을 초과했습니다.")
                    chunk = response.read(min(64 * 1024, MAX_RESPONSE_BYTES + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise MetaAPIError("Meta API 응답 본문이 32 MiB를 초과합니다.")
                body = b"".join(chunks)
                content_type = response.headers.get("Content-Type", "")
                request_id = response.headers.get("x-request-id")
                status = getattr(response, "status", 200)
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                raise MetaAPIError("Meta API 리디렉션을 거부했습니다.") from None
            raise MetaAPIError(f"Meta API가 HTTP {exc.code} 오류를 반환했습니다.") from None
        except MetaAPIError:
            raise
        except (OSError, TimeoutError, urllib.error.URLError):
            raise MetaAPIError("Meta API 네트워크 전송에 실패했습니다. 자동 재시도하지 않았습니다.") from None
        if time.monotonic() - started > TOTAL_TIMEOUT_SECONDS:
            raise MetaAPIError("Meta API 전체 응답 제한 시간을 초과했습니다.")
        if status < 200 or status >= 300:
            raise MetaAPIError(f"Meta API가 HTTP {status} 상태를 반환했습니다.")
        if len(body) > MAX_RESPONSE_BYTES:
            raise MetaAPIError("Meta API 응답 본문이 32 MiB를 초과합니다.")
        return body, content_type, request_id
