"""고정된 실제 Meta 실행 증거를 네트워크 호출 없이 복구한다."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

from .datasets import ROOT, load_example_view
from .evaluate import evaluate_contract
from .schema import METRICS, validate_contract, validate_observations


EVIDENCE_DIR = ROOT / "evals" / "results" / "meta_live_provider_20261104_v1"
EXPORT_NAME = "mollet-behavior-contract.json"
EXPORT_SHA256 = "775cc2c62082b50b27c2ce0d3f6811681ca3325542cb736a4dd12769d8bedb1a"
VIDEO_SHA256 = "82107853548545c0977b8488acd45931689c1de737db29d931d1735505367f10"
TRACE_SHA256 = "ef305c390f11842ed04dd0d8a835af5159e02c93a9a51763008591d2f6831e07"

KNOWN_RUNS = {
    "spark": {
        "run_id": "9c20912b-fa37-4f3c-a118-11bc13bda76a",
        "model": "muse-spark-1.3",
        "content_type": "application/json",
        "provider_request_id": "1358757f-6636-4659-85a5-8e3c001e84d2",
        "response_id": "resp_6aeaebc268240c6c192e444b",
        "request_sha256": "363f1a845534f077a3ec9bfb5acdaa5dfda9a9cb88b4db0445ba84b60fa76012",
        "response_sha256": "47a20cb67dee98c9bbb6db90488307decee8ead9855134ab0c4f4da04e55e0e5",
        "response_file": "response.json",
    },
    "sam": {
        "run_id": "ee92f2fd-f138-4198-838e-d52b5472b91c",
        "model": "sam-3.1",
        "content_type": "text/event-stream",
        "provider_request_id": "ce259a12-ef5d-40b9-aa9b-6291932674c8",
        "response_id": None,
        "request_sha256": "344aa7419708eade5bf2746f917e6778cd40200d8c382581e38b9dbfb1b03f8b",
        "response_sha256": "88f0b62f64badc3a57ac538c926bcc10c6d9cd118ad98207bc6ddf0caf12afb1",
        "response_file": "response.sse",
    },
}


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"저장된 Meta 증거가 JSON 객체가 아닙니다: {path.name}")
    return value


def _default_runs_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return Path("__mollet_localappdata_unavailable__")
    return Path(base) / "Mollet" / "meta-model-api" / "runs"


def _repository_evidence(evidence_dir: Path) -> tuple[dict, dict]:
    verification_path = evidence_dir / "verification.json"
    export_path = evidence_dir / EXPORT_NAME
    if not verification_path.is_file() or not export_path.is_file():
        raise ValueError("저장된 실제 Meta 실행의 저장소 증거가 없습니다.")
    raw_export = export_path.read_bytes()
    if _sha256(raw_export) != EXPORT_SHA256:
        raise ValueError("저장된 실제 Meta 내보내기 원문의 SHA-256이 다릅니다.")
    verification = _read_json(verification_path)
    saved = json.loads(raw_export.decode("utf-8"))
    if (verification.get("schema") != "meta-live-provider-verification-v1"
            or verification.get("example_id") != "h1-up"
            or verification.get("export_file") != EXPORT_NAME
            or verification.get("export_sha256") != EXPORT_SHA256
            or verification.get("provider_completed") != {"spark": "completed", "sam": "completed"}
            or verification.get("sample_count") != 625
            or verification.get("tracked_sample_count") != 624
            or verification.get("original_state_values_unchanged") is not True
            or verification.get("capture_approved_value") is not False
            or verification.get("model_ready_value") is not False):
        raise ValueError("저장된 실제 Meta 검증 기록의 고정 조건이 일치하지 않습니다.")
    receipts = verification.get("receipts")
    if not isinstance(receipts, dict):
        raise ValueError("저장된 실제 Meta 영수증 색인이 없습니다.")
    for name, expected in KNOWN_RUNS.items():
        receipt = receipts.get(name)
        if not isinstance(receipt, dict) or any(receipt.get(key) != expected[key] for key in (
                "run_id", "model", "content_type", "provider_request_id", "response_id",
                "request_sha256", "response_sha256")) or receipt.get("status") != "completed_http":
            raise ValueError(f"저장소의 {name} 실행 영수증 색인이 고정 증거와 다릅니다.")
        context = receipt.get("context") or {}
        if (context.get("example_id") != "h1-up" or context.get("video_sha256") != VIDEO_SHA256
                or context.get("source_trace_sha256") != TRACE_SHA256):
            raise ValueError(f"저장소의 {name} 실행 입력 결합이 고정 원본과 다릅니다.")
    return verification, saved


def _provider_completed(name: str, raw: bytes, expected: dict) -> bool:
    if name == "spark":
        response = json.loads(raw.decode("utf-8"))
        return (response.get("id") == expected["response_id"]
                and response.get("model") == expected["model"]
                and response.get("status") == "completed")
    markers = (
        b'"type":"response.completed"', b'"status":"completed"',
        expected["model"].encode("ascii"),
        f'"id":"msg_{expected["provider_request_id"]}"'.encode("ascii"),
        b'data: [DONE]',
    )
    return all(marker in raw for marker in markers)


def _local_receipts(runs_root: Path, verification: dict) -> list[dict] | None:
    """모든 고정 원문이 있을 때만 로컬 인증 등급을 반환한다.

    파일 부재는 저장소 증거 등급으로 낮추지만, 존재하는 파일의 불일치는 실패로 닫는다.
    """
    for expected in KNOWN_RUNS.values():
        run_dir = runs_root / expected["run_id"]
        required = (run_dir / "receipt.json", run_dir / "request.json", run_dir / expected["response_file"])
        if not run_dir.is_dir() or not all(path.is_file() for path in required):
            return None
    verified = []
    for name, expected in KNOWN_RUNS.items():
        run_dir = runs_root / expected["run_id"]
        receipt = _read_json(run_dir / "receipt.json")
        indexed = verification["receipts"][name]
        for key in ("run_id", "model", "content_type", "provider_request_id", "response_id",
                    "request_sha256", "response_sha256", "status"):
            wanted = "completed_http" if key == "status" else expected.get(key)
            if receipt.get(key) != wanted or receipt.get(key) != indexed.get(key):
                raise ValueError(f"로컬 {name} 영수증의 {key}가 검증 기록과 다릅니다.")
        context = receipt.get("context") or {}
        if (context.get("example_id") != "h1-up" or context.get("video_sha256") != VIDEO_SHA256
                or context.get("source_trace_sha256") != TRACE_SHA256):
            raise ValueError(f"로컬 {name} 영수증의 입력 원본 결합이 다릅니다.")
        request_raw = (run_dir / "request.json").read_bytes()
        response_raw = (run_dir / expected["response_file"]).read_bytes()
        if _sha256(request_raw) != expected["request_sha256"]:
            raise ValueError(f"로컬 {name} 요청 원문의 SHA-256이 다릅니다.")
        if _sha256(response_raw) != expected["response_sha256"]:
            raise ValueError(f"로컬 {name} 응답 원문의 SHA-256이 다릅니다.")
        request = json.loads(request_raw.decode("utf-8"))
        if request.get("model") != expected["model"]:
            raise ValueError(f"로컬 {name} 요청의 모델 식별자가 다릅니다.")
        if not _provider_completed(name, response_raw, expected):
            raise ValueError(f"로컬 {name} 공급자 응답이 완료 상태가 아닙니다.")
        verified.append({
            "model": expected["model"], "run_id": expected["run_id"],
            "provider_request_id": expected["provider_request_id"],
            "response_id": expected["response_id"], "status": "completed_http",
            "request_sha256": expected["request_sha256"],
            "response_sha256": expected["response_sha256"],
        })
    return verified


def _canonical_payload(saved: dict, verification_level: str, receipts: list[dict]) -> dict:
    view = load_example_view("h1-up")
    canonical = view.get("observations")
    imported = saved.get("observations")
    if (view.get("video_sha256") != VIDEO_SHA256 or not isinstance(canonical, dict)
            or canonical.get("video_sha256") != VIDEO_SHA256
            or canonical.get("provenance", {}).get("source_trace_sha256") != TRACE_SHA256
            or canonical.get("provenance", {}).get("capture_approved") is not False
            or canonical.get("provenance", {}).get("model_ready") is not False):
        raise ValueError("현재 H1 원본 영상·상태 기록이 고정된 실제 실행 입력과 다릅니다.")
    if (not isinstance(saved.get("clip"), dict) or saved["clip"].get("id") != "h1-up"
            or saved["clip"].get("video_sha256") != VIDEO_SHA256
            or not isinstance(imported, dict) or imported.get("schema") != "behavior-observations-v1"
            or imported.get("clip_id") != "h1-up" or imported.get("video_sha256") != VIDEO_SHA256):
        raise ValueError("저장된 실제 Meta 결과의 클립 결합이 H1 원본과 다릅니다.")
    saved_samples = imported.get("samples")
    canonical_samples = canonical["samples"]
    if not isinstance(saved_samples, list) or len(saved_samples) != len(canonical_samples):
        raise ValueError("저장된 실제 Meta 결과의 상태 표본 수가 H1 원본과 다릅니다.")
    samples = []
    for index, (source, original) in enumerate(zip(saved_samples, canonical_samples)):
        if (not isinstance(source, dict) or source.get("frame") != original["frame"]
                or source.get("t_s") != original["t_s"]
                or not isinstance(source.get("values"), dict)
                or set(source["values"]) != set(METRICS)
                or any(source["values"].get(metric) != original["values"].get(metric) for metric in METRICS)
                or not isinstance(source.get("tracks"), list)):
            raise ValueError(f"저장된 실제 Meta 결과의 {index}번 프레임·시각·상태값이 원본과 다릅니다.")
        samples.append({**original, "tracks": copy.deepcopy(source["tracks"])})
    authentication = "not_performed_for_replay"
    provenance = copy.deepcopy(canonical["provenance"])
    provenance["saved_meta_replay"] = {
        "kind": "saved_actual_meta_response_replay", "provider_authentication": authentication,
        "evidence_verification": verification_level,
        "historical_model_calls": 2, "new_model_calls": 0,
        "spark_run_id": KNOWN_RUNS["spark"]["run_id"], "sam_run_id": KNOWN_RUNS["sam"]["run_id"],
        "sam_response_sha256": KNOWN_RUNS["sam"]["response_sha256"],
    }
    observations = validate_observations({**canonical, "provenance": provenance, "samples": samples})
    contract_saved = validate_contract(saved.get("contract"))
    contract = validate_contract({**contract_saved, "source": {
        "kind": "saved_meta_response_replay",
        "provider_authentication": authentication,
        "evidence_verification": verification_level,
        "historical_run_id": KNOWN_RUNS["spark"]["run_id"],
        "response_sha256": KNOWN_RUNS["spark"]["response_sha256"],
        "model_called": False, "historical_model_calls": 2, "new_model_calls": 0,
    }})
    result = evaluate_contract(contract, observations, None)
    result["input_provenance"] = observations["provenance"]
    result["model_called"] = False
    clip = {key: copy.deepcopy(value) for key, value in view.items() if key != "observations"}
    return {
        "available": True, "clip": clip, "contract": contract, "observations": observations,
        "result": result,
        "replay": {
            "historical_model_calls": 2, "new_model_calls": 0,
            "verification_level": verification_level, "receipts": receipts,
            "provider_authentication": authentication,
            "evidence_verification": verification_level,
            "notice": "저장된 실제 응답을 검증해 이어 봅니다. 현재 모델 호출은 수행하지 않았습니다.",
        },
    }


def load_saved_recovery(*, evidence_dir: Path = EVIDENCE_DIR, runs_root: Path | None = None) -> dict:
    """허용 목록에 고정된 실행만 검증해 복구용 응답을 만든다."""
    verification, saved = _repository_evidence(Path(evidence_dir))
    local = _local_receipts(Path(runs_root) if runs_root is not None else _default_runs_root(), verification)
    if local is None:
        receipts = [{
            "model": expected["model"], "run_id": expected["run_id"],
            "provider_request_id": expected["provider_request_id"], "response_id": expected["response_id"],
            "status": "repository_recorded_completed",
            "request_sha256": expected["request_sha256"], "response_sha256": expected["response_sha256"],
        } for expected in KNOWN_RUNS.values()]
        return _canonical_payload(saved, "repository_evidence_verified", receipts)
    return _canonical_payload(saved, "local_provider_receipts_verified", local)
