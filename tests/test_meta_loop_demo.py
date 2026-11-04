from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "docs/demo/build_meta_loop_demo.py"
SPEC = importlib.util.spec_from_file_location("build_meta_loop_demo", MODULE_PATH)
assert SPEC and SPEC.loader
DEMO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEMO)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def actual_evidence(tmp_path: Path) -> tuple[Path, Path]:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    video_sha = "a" * 64
    receipts = []
    for index, name in enumerate(("first", "reflection"), start=1):
        response = evidence / f"{name}-response.json"
        response.write_text(json.dumps({"id": f"response-{index}", "output": []}) + "\n", encoding="utf-8")
        run_dir = tmp_path / f"run-{index}"
        run_dir.mkdir()
        request = run_dir / "request.json"
        request.write_text(json.dumps({"model": "muse-spark-1.3", "round": index}) + "\n", encoding="utf-8")
        stored = run_dir / "response.json"
        stored.write_bytes(response.read_bytes())
        receipt = {
            "run_id": f"00000000-0000-0000-0000-00000000000{index}",
            "directory": str(run_dir),
            "status": "completed_http",
            "model": "muse-spark-1.3",
            "content_type": "application/json",
            "provider_request_id": f"provider-request-{index}",
            "request_sha256": sha(request),
            "response_sha256": sha(response),
            "context": {"video_sha256": video_sha, "input_kind": "video_and_synchronized_state"},
        }
        write_json(run_dir / "receipt.json", receipt)
        receipts.append(receipt)
    session = {
        "schema": "meta-research-session-v1",
        "example_id": "go2-down-slow",
        "input_manifest": {"sent": True, "video_sha256": video_sha},
        "analysis": {"hypotheses": [{"id": "second", "claim": "재검토 가설", "evidence_frames": [21, 34]}]},
        "history": [{"round": 1, "selected_hypothesis_id": "second",
                     "result": {"verdict": "UNKNOWN", "reason": "추가 상태가 필요함"}}],
        "cycles": [{"analysis": {"hypotheses": [{"id": "first", "claim": "초기 자세 가설", "evidence_frames": [10]}]},
                    "history": [{"round": 1, "selected_hypothesis_id": "first",
                                 "result": {"verdict": "FAIL", "reason": "임계값 미만"}}]}],
        "analysis_origin": "authenticated_live_spark_video_response",
        "provider_authentication": "authenticated_https_api",
        "receipts": receipts,
        "new_model_calls": 2,
    }
    write_json(evidence / "final-session.json", session)
    write_json(evidence / "first-analysis.json", session["cycles"][0]["analysis"])
    write_json(evidence / "first-rounds.json", {"history": session["cycles"][0]["history"]})
    write_json(evidence / "reflection-analysis.json", session["analysis"])
    write_json(evidence / "reflection-rounds.json", {"history": session["history"]})
    write_json(evidence / "baseline-before-model.json", {
        "schema": "meta-recorded-static-baseline-v1", "example_id": "go2-down-slow",
        "video_sha256": video_sha, "result": {"verdict": "FAIL"}, "model_called": False,
    })
    write_json(evidence / "summary.json", {
        "schema": "meta-actual-video-loop-presentation-v1",
        "example_id": "go2-down-slow",
        "status": "completed_actual_video_and_reflection",
        "actual_video_inference_executed": True,
        "actual_reflection_executed": True,
        "new_generation_requests": 2,
        "static_baseline_verdict": "FAIL",
        "new_training_executed": False,
        "performance_improvement_measured": False,
        "first": {**receipts[0], "provider_authentication": "authenticated_https_api"},
        "reflection": {**receipts[1], "provider_authentication": "authenticated_https_api"},
    })
    capture = tmp_path / "capture"
    capture.mkdir()
    for name in ("01-first-analysis.png", "02-first-check.png", "03-reflection-analysis.png",
                 "04-reflection-check.png", "05-full-workbench.png"):
        Image.new("RGB", (8, 8), "white").save(capture / name)
    write_json(capture / "capture.json", {
        "schema": "meta-actual-video-loop-capture-v1",
        "capture_api_calls": 0,
        "video_sha256": video_sha,
        "provider_runs": [{"run_id": receipt["run_id"]} for receipt in receipts],
    })
    return evidence, capture


def test_rejects_unverified_saved_response() -> None:
    folder = ROOT / "evals/results/meta_video_research_20261104_v5"
    if not folder.is_dir():
        pytest.skip("기존 합성 검증 자료 없음")
    with pytest.raises(ValueError, match="실제 근거 파일|인증된 실제"):
        DEMO.validate_evidence(folder)


def test_validates_two_hash_bound_provider_runs(tmp_path: Path) -> None:
    evidence, capture = actual_evidence(tmp_path)
    checked = DEMO.validate_evidence(evidence)
    captured = DEMO.validate_capture(capture, checked)
    assert [row["run_id"] for row in checked["receipts"]] == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ]
    assert captured["manifest"]["capture_api_calls"] == 0
    assert checked["presentation_facts"] == {
        "first": {"hypothesis_id": "first", "claim": "초기 자세 가설", "evidence_frames": [10],
                  "verdict": "FAIL", "reason": "임계값 미만"},
        "reflection": {"hypothesis_id": "second", "claim": "재검토 가설", "evidence_frames": [21, 34],
                       "verdict": "UNKNOWN", "reason": "추가 상태가 필요함"},
    }


def test_rejects_response_tampering(tmp_path: Path) -> None:
    evidence, _ = actual_evidence(tmp_path)
    (evidence / "reflection-response.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="2차 원응답"):
        DEMO.validate_evidence(evidence)


def test_capture_script_has_valid_node_syntax() -> None:
    capture = ROOT / "tools/capture_meta_presentation.cjs"
    subprocess.run(["node", "--check", str(capture)], check=True)
    assert "channel: 'chrome'" in capture.read_text(encoding="utf-8")


def test_presentation_copy_avoids_unverified_effect_claims_and_has_english_captions() -> None:
    rendered = " ".join(value for scene in DEMO.SCENES for value in scene)
    assert "같은 답을 반복하지 않고" not in rendered
    assert "원인 후보를 좁" not in rendered
    assert "가설과 교정 제안을 다시 구성" not in rendered
    assert "기존 수치 조건도 모델 호출 전에 자세 위반" in rendered
    assert len(DEMO.ENGLISH_CAPTIONS) == len(DEMO.SCENES) == 12
    assert "not a discovery or accuracy comparison" in " ".join(DEMO.ENGLISH_CAPTIONS)


def test_english_caption_filter_reserves_white_120px_band(tmp_path: Path) -> None:
    graph = DEMO.subtitle_filter(tmp_path / "captions.srt")
    assert "pad=1280:840:0:0:color=white" in graph
    assert "subtitles=filename=" in graph
    assert "MarginV=22" in graph
