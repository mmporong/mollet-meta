"""저장된 실제 Meta 복구 데모 빌더의 증거 계약을 검사한다."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image

from rl_triage.behavior_authoring.datasets import ROOT, load_example_view
from rl_triage.behavior_authoring.evaluate import evaluate_contract
from rl_triage.behavior_authoring.saved_run import load_saved_recovery
from rl_triage.behavior_authoring.schema import contract_digest


DEMO_DIR = ROOT / "docs" / "demo"
if str(DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_DIR))
import build_meta_recovery_demo as demo  # noqa: E402


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


def _record(path: Path) -> dict:
    raw = path.read_bytes()
    return {"file": path.name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def _reviewed_result(contract: dict, observations: dict) -> dict:
    approval = {"contract_sha256": contract_digest(contract), "actor": "test_browser_review",
                "scope": "local_semantic_review"}
    return evaluate_contract(contract, observations, approval)


def _validator_fixture(tmp_path: Path) -> dict:
    """캡처 validator만 검사하는 저장소 fixture이며 실제 provider 인증을 대신하지 않는다."""
    replay = load_saved_recovery(runs_root=tmp_path / "no-local-provider-files")
    replay["replay"]["verification_level"] = "local_provider_receipts_verified"
    replay["replay"]["provider_authentication"] = "not_performed_for_replay"
    return replay


def test_scene_contract_is_exactly_twelve_fifteen_second_parts():
    assert len(demo.SCENES) == 12
    assert demo.SCENE_DURATION * len(demo.SCENES) == demo.DURATION == 180.0
    assert all("직접" not in narration for _, _, narration, _ in demo.SCENES)


def test_capture_validator_recomputes_results_and_file_hashes(tmp_path: Path):
    replay = _validator_fixture(tmp_path)
    for name in demo.SCREENSHOTS:
        Image.new("RGB", (16, 16), "white").save(tmp_path / name)

    provider = {
        "clip": {"id": "h1-up", "video_sha256": replay["clip"]["video_sha256"]},
        "contract": replay["contract"], "observations": replay["observations"],
        "result": _reviewed_result(replay["contract"], replay["observations"]),
    }
    manual = copy.deepcopy(provider)
    manual["contract"]["condition"]["hold_s"] = 8.0
    manual["contract"]["source"] = {"kind": "manual_form", "model_called": False}
    manual["result"] = _reviewed_result(manual["contract"], manual["observations"])
    strict = copy.deepcopy(manual)
    strict["contract"]["condition"]["threshold"] = 1.0
    strict["contract"]["condition"]["deadline_s"] = 0.0
    strict["result"] = _reviewed_result(strict["contract"], strict["observations"])

    go2_view = load_example_view("go2-down-slow")
    go2_contract = copy.deepcopy(replay["contract"])
    go2_contract["condition"].update(deadline_s=0.0, hold_s=12.48)
    go2_contract["source"] = {"kind": "manual_form", "model_called": False}
    go2 = {
        "clip": {"id": "go2-down-slow", "video_sha256": go2_view["video_sha256"]},
        "contract": go2_contract, "observations": go2_view["observations"],
        "result": _reviewed_result(go2_contract, go2_view["observations"]),
    }
    exports = (provider, manual, strict, go2)
    for name, value in zip(demo.DOWNLOADS, exports, strict=True):
        _write_json(tmp_path / name, value)

    recording = tmp_path / "browser-video" / "capture.webm"
    recording.parent.mkdir()
    recording.write_bytes(b"browser recording placeholder")
    report = {
        "schema": demo.CAPTURE_SCHEMA, "passed": True, "source_commit": demo.current_commit(),
        "new_model_calls": 0, "gpu_jobs": 0, "browser_closed": True,
        "page_errors": [], "blocked_provider_requests": [],
        "video_sha256": replay["clip"]["video_sha256"], "replay": replay["replay"],
        "provider_result": provider["result"],
        "manual_eight_second_result": manual["result"], "go2_result": go2["result"],
        "screenshots": [{"file": name, "sha256": demo.sha256(tmp_path / name)} for name in demo.SCREENSHOTS],
        "downloads": [_record(tmp_path / name) for name in demo.DOWNLOADS],
        "browser_video": recording.name, "browser_video_sha256": demo.sha256(recording),
        "browser_video_bytes": recording.stat().st_size,
    }
    _write_json(tmp_path / "verification.json", report)
    checked = demo.validate_capture(tmp_path, replay)
    assert checked["source_commit"] == demo.current_commit()
    assert len(checked["screenshots"]) == 6
    assert len(checked["downloads"]) == 4
    assert checked["exports"][demo.DOWNLOADS[1]]["result"]["interval"] == {"start_s": 0.0, "end_s": 8.0}


def test_capture_validator_rejects_a_changed_screenshot(tmp_path: Path):
    replay = _validator_fixture(tmp_path)
    report = {
        "schema": demo.CAPTURE_SCHEMA, "passed": True, "source_commit": demo.current_commit(),
        "new_model_calls": 0, "gpu_jobs": 0, "browser_closed": True,
        "page_errors": [], "blocked_provider_requests": [],
        "video_sha256": replay["clip"]["video_sha256"], "replay": replay["replay"],
        "screenshots": [{"file": name, "sha256": "0" * 64} for name in demo.SCREENSHOTS],
        "downloads": [], "browser_video": "missing.webm",
    }
    for name in demo.SCREENSHOTS:
        (tmp_path / name).write_bytes(b"changed")
    _write_json(tmp_path / "verification.json", report)
    try:
        demo.validate_capture(tmp_path, replay)
    except ValueError as exc:
        assert "SHA-256" in str(exc)
    else:
        raise AssertionError("변경된 스크린샷을 거부해야 합니다")
