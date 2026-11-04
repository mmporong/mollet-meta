"""고정된 실제 Meta 결과의 무호출 복구 경로를 검사한다."""
from __future__ import annotations

import http.client
import copy
import hashlib
import json
import shutil
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from rl_triage.behavior_authoring.datasets import ROOT, load_example_view
from rl_triage.behavior_authoring import saved_run
from rl_triage.behavior_authoring.saved_run import EVIDENCE_DIR, KNOWN_RUNS, load_saved_recovery
from rl_triage.behavior_authoring.web import Handler


def _request(port: int, path: str) -> tuple[int, dict]:
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    client.request("GET", path)
    response = client.getresponse()
    value = json.loads(response.read())
    client.close()
    return response.status, value


@pytest.fixture
def local_receipt_fixture(tmp_path: Path, monkeypatch):
    """고정 검증 분기를 검사하는 합성 영수증이며 실제 공급자 실행 증거가 아니다."""
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    shutil.copyfile(EVIDENCE_DIR / "mollet-behavior-contract.json", evidence / "mollet-behavior-contract.json")
    verification = json.loads((EVIDENCE_DIR / "verification.json").read_text(encoding="utf-8"))
    known = copy.deepcopy(KNOWN_RUNS)
    runs_root = tmp_path / "runs"
    for name, expected in known.items():
        folder = runs_root / expected["run_id"]
        folder.mkdir(parents=True)
        request = json.dumps({"model": expected["model"], "input": "unit-test-fixture"}).encode()
        if name == "spark":
            response = json.dumps({"id": expected["response_id"], "model": expected["model"], "status": "completed"}).encode()
        else:
            event = {"type": "response.completed", "status": "completed", "model": expected["model"],
                     "id": "msg_" + expected["provider_request_id"]}
            response = ("data: " + json.dumps(event, separators=(",", ":")) + "\n\ndata: [DONE]\n").encode()
        expected["request_sha256"] = hashlib.sha256(request).hexdigest()
        expected["response_sha256"] = hashlib.sha256(response).hexdigest()
        receipt = verification["receipts"][name]
        receipt.update(request_sha256=expected["request_sha256"], response_sha256=expected["response_sha256"])
        (folder / "request.json").write_bytes(request)
        (folder / expected["response_file"]).write_bytes(response)
        (folder / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    (evidence / "verification.json").write_text(json.dumps(verification), encoding="utf-8")
    monkeypatch.setattr(saved_run, "KNOWN_RUNS", known)
    return evidence, runs_root


def test_local_receipt_verification_branch_without_new_calls(local_receipt_fixture):
    evidence, runs_root = local_receipt_fixture
    value = load_saved_recovery(evidence_dir=evidence, runs_root=runs_root)
    assert value["available"] is True
    assert value["replay"]["verification_level"] == "local_provider_receipts_verified"
    assert value["replay"]["historical_model_calls"] == 2
    assert value["replay"]["new_model_calls"] == 0
    assert value["replay"]["provider_authentication"] == "not_performed_for_replay"
    assert value["replay"]["evidence_verification"] == "local_provider_receipts_verified"
    assert len(value["replay"]["receipts"]) == 2
    assert {row["run_id"] for row in value["replay"]["receipts"]} == {
        item["run_id"] for item in KNOWN_RUNS.values()
    }
    assert all(len(row["request_sha256"]) == len(row["response_sha256"]) == 64
               for row in value["replay"]["receipts"])

    canonical = load_example_view("h1-up")["observations"]
    observations = value["observations"]
    assert value["clip"]["id"] == observations["clip_id"] == "h1-up"
    assert value["clip"]["video_sha256"] == observations["video_sha256"]
    assert len(observations["samples"]) == 625
    assert sum(bool(sample["tracks"]) for sample in observations["samples"]) == 624
    assert [(row["frame"], row["t_s"], row["values"]) for row in observations["samples"]] == [
        (row["frame"], row["t_s"], row["values"]) for row in canonical["samples"]
    ]
    assert observations["provenance"]["capture_approved"] is False
    assert observations["provenance"]["model_ready"] is False
    assert observations["provenance"]["saved_meta_replay"]["new_model_calls"] == 0
    assert value["contract"]["source"]["kind"] == "saved_meta_response_replay"
    assert value["contract"]["source"]["model_called"] is False
    assert value["result"]["stage"] == "draft"
    assert value["result"]["approval_status"] == "unreviewed"
    assert value["result"]["verdict"] == "PASS"
    assert value["result"]["interval"] == {"start_s": 0.0, "end_s": 0.5}
    assert value["result"]["model_called"] is False
    assert "approval" not in value["result"]


def test_missing_local_receipts_downgrades_to_repository_evidence(tmp_path: Path):
    value = load_saved_recovery(runs_root=tmp_path / "no-local-runs")
    assert value["available"] is True
    assert value["replay"]["verification_level"] == "repository_evidence_verified"
    assert value["replay"]["provider_authentication"] == "not_performed_for_replay"
    assert value["contract"]["source"]["provider_authentication"] == "not_performed_for_replay"
    assert value["contract"]["source"]["evidence_verification"] == "repository_evidence_verified"
    assert value["replay"]["new_model_calls"] == 0


def test_existing_but_changed_provider_response_fails_closed(local_receipt_fixture):
    evidence, runs_root = local_receipt_fixture
    changed = runs_root / KNOWN_RUNS["spark"]["run_id"] / "response.json"
    changed.write_bytes(changed.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="응답 원문의 SHA-256"):
        load_saved_recovery(evidence_dir=evidence, runs_root=runs_root)


def test_changed_canonical_export_fails_closed(tmp_path: Path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    for name in ("verification.json", "mollet-behavior-contract.json"):
        shutil.copyfile(EVIDENCE_DIR / name, evidence / name)
    export = evidence / "mollet-behavior-contract.json"
    saved = json.loads(export.read_text(encoding="utf-8"))
    saved["observations"]["samples"][0]["values"]["up_axis_z"] -= 0.01
    export.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="내보내기 원문의 SHA-256"):
        load_saved_recovery(evidence_dir=evidence, runs_root=tmp_path / "missing")


def test_saved_recovery_http_route_has_no_path_or_url_input():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, value = _request(server.server_port, "/api/recovery/saved")
        assert status == 200
        assert value["available"] is True
        assert value["replay"]["new_model_calls"] == 0
        status, value = _request(server.server_port, "/api/recovery/saved?path=C:%5Csecret&url=https://example.com")
        assert status == 400
        assert "파일 경로나 URL" in value["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert not thread.is_alive()


def test_saved_recovery_ui_is_explicit_and_does_not_replace_file_restore():
    html = (ROOT / "src/rl_triage/behavior_authoring/web/index.html").read_text(encoding="utf-8")
    app = (ROOT / "src/rl_triage/behavior_authoring/web/app.js").read_text(encoding="utf-8")
    assert 'id="savedMetaRestore"' in html
    assert "Restore saved Meta results" in html
    assert 'id="savedMetaRecoveryStatus"' in html
    assert 'id="restoreExport"' in html
    assert "saved_meta_response_replay:'Saved verified response'" in app
    assert "current model calls ${saved.replay.new_model_calls}" in app
    assert "state.approval=null;$('reviewed').checked=false;showResult(saved.result)" in app


def test_user_edited_export_source_roundtrips_without_gaining_model_authority():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js가 없어 저장 조건 source 회귀 검사를 실행할 수 없습니다.")
    app_path = ROOT / "src/rl_triage/behavior_authoring/web/app.js"
    script = r"""
const assert=require('assert');
const fs=require('fs');
const source=fs.readFileSync(process.argv[1],'utf8');
const body=source.match(/function restoreSource\(source\)\{.*?\}\nfunction restoredBundle/s)?.[0];
assert(body,'restoreSource 함수를 찾지 못했습니다.');
eval(body.replace(/\nfunction restoredBundle[\s\S]*$/,''));
for(const kind of ['manual_form','manual_example_contract','restored_manual_contract']){
  const restored=restoreSource({kind,model_called:false,provider_authentication:'forged'});
  assert.deepStrictEqual(restored,{kind:'restored_manual_contract',provider_authentication:'not_applicable_user_authored',model_called:false});
}
assert.throws(()=>restoreSource({kind:'manual_form',model_called:true}),/model-call result/);
assert.throws(()=>restoreSource({kind:'live_meta_spark_response',response_sha256:'bad'}),/SHA-256/);
const imported=restoreSource({kind:'live_meta_spark_response',response_sha256:'a'.repeat(64),provider_authentication:'authenticated_https_api',model_called:true});
assert.deepStrictEqual(imported,{kind:'imported_spark_response',response_sha256:'a'.repeat(64),provider_authentication:'unverified_saved_response',model_called:false});
"""
    completed = subprocess.run([node, "-e", script, str(app_path)], cwd=ROOT, text=True, encoding="utf-8",
                               capture_output=True, timeout=10)
    assert completed.returncode == 0, completed.stdout + completed.stderr
