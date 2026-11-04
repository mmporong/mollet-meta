from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
from pathlib import Path

import pytest

from rl_triage.behavior_authoring.client import (
    MAX_RESPONSE_BYTES,
    MetaAPIError,
    MetaClient,
)


def _request(model: str = "muse-spark-1.3") -> dict[str, object]:
    return {"model": model, "input": [{"role": "user", "content": "stairs"}]}


def test_database_handles_close_after_status_preflight_and_send(tmp_path,monkeypatch):
    client=_enabled_client(tmp_path,lambda *_:(b'{}','application/json',None),
                           token_transport=lambda *_:(b'{"input_tokens":100}','application/json',None))
    opened=[];original=client._connect
    def tracked():
        connection=original();opened.append(connection);return connection
    monkeypatch.setattr(client,'_connect',tracked)
    client.status();client.count_input_tokens(_request());client.send(_request(),estimated_cost_usd='.1',context={})
    assert opened
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError,match='closed'):
            connection.execute('SELECT 1')


def _enabled_client(tmp_path: Path, transport, **overrides) -> MetaClient:
    options = {
        "api_key": "meta-test-key",
        "credit_confirmed": True,
        "remaining_credit_usd": "150",
        "budget_usd": "1.00",
        "state_dir": tmp_path,
        "transport": transport,
    }
    options.update(overrides)
    return MetaClient(**options)


def test_missing_key_or_credit_blocks_before_transport(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    calls = 0

    def transport(body: bytes, key: str):
        nonlocal calls
        calls += 1
        return b"{}", "application/json", None

    no_key = MetaClient(
        credit_confirmed=True,
        remaining_credit_usd="2",
        state_dir=tmp_path / "no-key",
        transport=transport,
    )
    with pytest.raises(MetaAPIError, match="비활성화"):
        no_key.send(_request(), estimated_cost_usd="0.10", context={"clip": "a"})

    no_credit = MetaClient(
        api_key="key",
        remaining_credit_usd="2",
        state_dir=tmp_path / "no-credit",
        transport=transport,
    )
    with pytest.raises(MetaAPIError, match="비활성화"):
        no_credit.send(_request(), estimated_cost_usd="0.10", context={"clip": "a"})
    assert calls == 0
    assert no_key.status()["live_requests_started"] == 0
    assert no_credit.status()["live_requests_started"] == 0


@pytest.mark.parametrize("amount", [True, "NaN", "Infinity", "-0.01", "0"])
def test_invalid_estimated_cost_is_rejected_without_transport(tmp_path: Path, amount) -> None:
    calls = 0

    def transport(body: bytes, key: str):
        nonlocal calls
        calls += 1
        return b"{}", "application/json", None

    client = _enabled_client(tmp_path, transport)
    with pytest.raises(MetaAPIError):
        client.send(_request(), estimated_cost_usd=amount, context={})
    assert calls == 0


def test_configuration_and_model_allowlist_validation(tmp_path: Path) -> None:
    with pytest.raises(MetaAPIError):
        MetaClient(api_key="bad\nheader", state_dir=tmp_path / "newline")
    with pytest.raises(MetaAPIError):
        MetaClient(credit_confirmed=1, state_dir=tmp_path / "bool")
    with pytest.raises(MetaAPIError):
        MetaClient(api_key="contains space", state_dir=tmp_path / "space")
    with pytest.raises(MetaAPIError):
        MetaClient(api_key="비ASCII", state_dir=tmp_path / "unicode")
    with pytest.raises(MetaAPIError):
        MetaClient(api_key="x" * 4097, state_dir=tmp_path / "long")
    with pytest.raises(MetaAPIError, match=r"US\$1.00"):
        MetaClient(budget_usd="1.01", state_dir=tmp_path / "budget")

    client = _enabled_client(tmp_path / "model", lambda *_: (b"{}", "application/json", None))
    with pytest.raises(MetaAPIError, match="허용되지 않은"):
        client.send(_request("llama-unknown"), estimated_cost_usd="0.1", context={})
    assert client.status()["live_requests_started"] == 0


def test_json_and_sse_are_returned_and_preserved_verbatim(tmp_path: Path) -> None:
    json_body = b'{"id":"resp_1","status":"queued","output":[]}'
    json_client = _enabled_client(
        tmp_path / "json",
        lambda *_: (json_body, "application/json; charset=utf-8", "req-1"),
    )
    json_result = json_client.send(
        _request(), estimated_cost_usd="0.35", context={"clip_id": "stairs-1"}
    )
    assert json_result["response"] == json_body.decode()
    assert json_result["receipt"]["status"] == "completed_http"
    assert json_result["receipt"]["response_id"] == "resp_1"
    json_dir = Path(json_result["receipt"]["directory"])
    assert (json_dir / "response.json").read_bytes() == json_body
    assert json.loads((json_dir / "receipt.json").read_text(encoding="utf-8"))["request_sha256"]

    sse_body = b'event: response.output_text.delta\ndata: {"delta":"ok"}\n\n'
    sse_client = _enabled_client(
        tmp_path / "sse",
        lambda *_: (sse_body, "text/event-stream", "req-2"),
    )
    sse_result = sse_client.send(
        _request("sam-3.1"), estimated_cost_usd="0.25", context={"clip_id": "stairs-2"}
    )
    assert sse_result["response"] == sse_body.decode()
    sse_dir = Path(sse_result["receipt"]["directory"])
    assert (sse_dir / "response.sse").read_bytes() == sse_body


def test_budget_reservation_survives_restart_and_competing_request(tmp_path: Path) -> None:
    calls: list[str] = []
    lock = threading.Lock()

    def transport(body: bytes, key: str):
        with lock:
            calls.append(key)
        return b'{"id":"ok"}', "application/json", None

    first = _enabled_client(tmp_path, transport)
    first.send(_request(), estimated_cost_usd="0.60", context={})

    restarted = _enabled_client(tmp_path, transport)
    status = restarted.status()
    assert status["reserved_usd"] == "0.60"
    assert status["live_requests_started"] == 1
    with pytest.raises(MetaAPIError, match="남은 로컬 예산"):
        restarted.send(_request(), estimated_cost_usd="0.41", context={})
    assert len(calls) == 1
    assert restarted.status()["reserved_usd"] == "0.60"


def test_parallel_clients_cannot_overbook_shared_ledger(tmp_path: Path) -> None:
    transport_calls = 0
    lock = threading.Lock()

    def transport(body: bytes, key: str):
        nonlocal transport_calls
        with lock:
            transport_calls += 1
        return b'{"id":"ok"}', "application/json", None

    clients = [_enabled_client(tmp_path, transport) for _ in range(2)]
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def run(client: MetaClient) -> None:
        barrier.wait()
        try:
            client.send(_request(), estimated_cost_usd="0.75", context={})
            outcomes.append("sent")
        except MetaAPIError:
            outcomes.append("blocked")

    threads = [threading.Thread(target=run, args=(client,)) for client in clients]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["blocked", "sent"]
    assert transport_calls == 1
    assert clients[0].status()["reserved_usd"] == "0.75"


def test_failure_is_not_retried_or_refunded_and_key_is_redacted(tmp_path: Path) -> None:
    key = "do-not-leak-this-key"
    calls = 0

    def transport(body: bytes, supplied_key: str):
        nonlocal calls
        calls += 1
        raise MetaAPIError(f"provider echoed {supplied_key}")

    client = _enabled_client(tmp_path, transport, api_key=key)
    with pytest.raises(MetaAPIError) as caught:
        client.send(_request(), estimated_cost_usd="0.35", context={"source": "test"})
    assert calls == 1
    assert key not in str(caught.value)
    assert client.status()["reserved_usd"] == "0.35"
    assert client.status()["live_requests_started"] == 1
    all_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in tmp_path.rglob("*")
        if path.is_file() and path.suffix != ".sqlite3"
    )
    assert key not in all_text
    receipt = json.loads(next((tmp_path / "runs").glob("*/receipt.json")).read_text(encoding="utf-8"))
    assert receipt["status"] == "network_error"


def test_success_body_echoing_key_is_redacted_before_return_and_storage(tmp_path: Path) -> None:
    key = "redact-me"
    body = json.dumps({"id": "resp", "echo": key}).encode()
    client = _enabled_client(
        tmp_path,
        lambda *_: (body, "application/json", None),
        api_key=key,
    )
    result = client.send(_request(), estimated_cost_usd="0.1", context={})
    assert key not in result["response"]
    assert "[REDACTED]" in result["response"]
    directory = Path(result["receipt"]["directory"])
    assert key not in (directory / "response.json").read_text(encoding="utf-8")


def test_key_rotation_during_success_uses_request_snapshot_for_redaction(tmp_path: Path) -> None:
    old_key = "old-request-key"
    entered = threading.Event()
    release = threading.Event()
    outcome: dict[str, object] = {}

    def transport(body: bytes, supplied_key: str):
        assert supplied_key == old_key
        entered.set()
        assert release.wait(timeout=5)
        response = json.dumps({"id": "resp", "echo": old_key}).encode()
        return response, "application/json", f"request-{old_key}"

    client = _enabled_client(tmp_path, transport, api_key=old_key)

    def send() -> None:
        outcome["result"] = client.send(
            _request(), estimated_cost_usd="0.1", context={"case": "rotation"}
        )

    thread = threading.Thread(target=send)
    thread.start()
    assert entered.wait(timeout=5)
    client.configure(api_key="new-request-key")
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    result = outcome["result"]
    assert isinstance(result, dict)
    assert old_key not in json.dumps(result, ensure_ascii=False)
    directory = Path(result["receipt"]["directory"])
    assert old_key not in (directory / "response.json").read_text(encoding="utf-8")
    assert old_key not in (directory / "receipt.json").read_text(encoding="utf-8")


def test_key_rotation_during_failure_uses_request_snapshot_for_redaction(tmp_path: Path) -> None:
    old_key = "old-failure-key"
    entered = threading.Event()
    release = threading.Event()
    outcome: dict[str, object] = {}

    def transport(body: bytes, supplied_key: str):
        assert supplied_key == old_key
        entered.set()
        assert release.wait(timeout=5)
        raise MetaAPIError(f"provider echoed {old_key}")

    client = _enabled_client(tmp_path, transport, api_key=old_key)

    def send() -> None:
        try:
            client.send(_request(), estimated_cost_usd="0.1", context={"case": "rotation"})
        except MetaAPIError as exc:
            outcome["error"] = str(exc)

    thread = threading.Thread(target=send)
    thread.start()
    assert entered.wait(timeout=5)
    client.configure(api_key="new-failure-key")
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert old_key not in str(outcome["error"])
    receipt_path = next((tmp_path / "runs").glob("*/receipt.json"))
    assert old_key not in receipt_path.read_text(encoding="utf-8")


def test_oversized_response_is_rejected_and_reservation_remains(tmp_path: Path) -> None:
    oversized = b"x" * (MAX_RESPONSE_BYTES + 1)
    client = _enabled_client(
        tmp_path, lambda *_: (oversized, "text/event-stream", None)
    )
    with pytest.raises(MetaAPIError, match="32 MiB"):
        client.send(_request(), estimated_cost_usd="0.2", context={})
    assert client.status()["reserved_usd"] == "0.20"


def test_default_transport_rejects_redirect_without_following_it(tmp_path: Path, monkeypatch) -> None:
    opens = 0

    class RedirectingOpener:
        def open(self, request, timeout):
            nonlocal opens
            opens += 1
            raise urllib.error.HTTPError(
                request.full_url,
                302,
                "Found",
                {"Location": "https://attacker.invalid"},
                None,
            )

    monkeypatch.setattr(
        "rl_triage.behavior_authoring.client.urllib.request.build_opener",
        lambda *_: RedirectingOpener(),
    )
    client = _enabled_client(tmp_path, transport=None)
    with pytest.raises(MetaAPIError, match="리디렉션"):
        client.send(_request(), estimated_cost_usd="0.1", context={})
    assert opens == 1
    assert client.status()["reserved_usd"] == "0.10"


def test_default_transport_does_not_retry_http_failure(tmp_path: Path, monkeypatch) -> None:
    opens = 0

    class FailingOpener:
        def open(self, request, timeout):
            nonlocal opens
            opens += 1
            raise urllib.error.HTTPError(request.full_url, 500, "failure", {}, None)

    monkeypatch.setattr(
        "rl_triage.behavior_authoring.client.urllib.request.build_opener",
        lambda *_: FailingOpener(),
    )
    client = _enabled_client(tmp_path, transport=None)
    with pytest.raises(MetaAPIError, match="HTTP 500"):
        client.send(_request(), estimated_cost_usd="0.1", context={})
    assert opens == 1
    assert client.status()["live_requests_started"] == 1
    assert client.status()["reserved_usd"] == "0.10"
