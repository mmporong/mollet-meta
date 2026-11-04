"""Meta 영상 가설을 기존 사람 승인형 재학습·회복 판정 경로에 연결한다.

이 모듈은 제안과 사전등록만 만든다. 승인, 교정 적용, 학습 프로세스 실행은 하지 않는다.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import threading

from rl_triage import behavior_oracle, correction_registry, grounded_recovery, triage_tools

from . import improvement
from .schema import validate_observations


_WRITER_LOCK = threading.Lock()
_CORRECT_MODULE = None
_ALLOWED_CORRECTIONS = {"none", "restore_body_frame_reward", "restore_reward_family_scope"}
_CORRECTION_TO_MECHANISM = {
    entry["correction_id"]: mechanism for mechanism, entry in correction_registry.REGISTRY.items()
}
_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


def _digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _text(value: object, label: str, limit: int = 4000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"{label}은 1~{limit}자의 문자열이어야 합니다.")
    return value.strip()


def _analysis(value: dict, bundle: dict) -> dict:
    # 개선 엔진과 같은 폐쇄형 검증을 공유한다. 이미 정규화된 UI 세션 값도
    # 출처 네 항목이 정확할 때만 source를 벗겨 다시 검증한다.
    return improvement.validate_analysis(improvement._strip_analysis_schema(value), bundle)


def bridge_options() -> dict:
    """모델이 선택할 수 있는 범위와 사람이 통제하는 소비 지점을 반환한다."""
    registered = []
    for mechanism, entry in correction_registry.REGISTRY.items():
        registered.append({"mechanism": mechanism, "correction_id": entry["correction_id"],
                           "recovery_modes": list(entry["recovery_modes"]),
                           "protocol_case": entry["protocol_case"]})
    return {
        "schema": "meta-triage-bridge-options-v1",
        "registered_corrections": registered,
        "meta_correction_options": [
            {"correction_id": correction_id,
             "registered_in_correction_registry": correction_id in _CORRECTION_TO_MECHANISM,
             "eligible_for_config_revert_preregistration": correction_id == "none",
             "requires_confirmed_probe_receipt": correction_id in _CORRECTION_TO_MECHANISM,
             "role": "config_revert_only" if correction_id == "none" else "registered_code_correction_candidate"}
            for correction_id in sorted(_ALLOWED_CORRECTIONS)
        ],
        "consumers": {
            "preregistration": "rl_triage.triage_tools.write_preregistration",
            "pending_request": "rl_triage.eval_bridge.submit",
            "human_execution_gate": "rl_triage.eval_bridge.approve",
            "registered_correction_proposal": "evals.correct.propose",
            "registered_correction_ledger": "rl_triage.correction_ledger.CorrectionLedger",
            "recovery_oracle": "rl_triage.behavior_oracle.recovery_outcome",
            "controlled_triplet": "rl_triage.grounded_recovery.summarize_triplet",
        },
        "model_may_approve_or_execute": False,
    }


def _case_id(bundle: dict) -> str:
    case_id = bundle["provenance"].get("triage_case_id", bundle["clip_id"])
    case_id = _text(case_id, "triage_case_id", 200)
    if _CASE_ID.fullmatch(case_id) is None:
        raise ValueError("triage_case_id는 경로 문자가 없는 안전한 식별자여야 합니다.")
    return case_id


def _condition_text(contract: dict) -> str:
    condition = contract["condition"]
    return (f"{condition['metric']} {condition['op']} {condition['threshold']}를 "
            f"{condition['deadline_s']}초 안에 시작해 {condition['hold_s']}초 유지")


def _checked_workspace(workspace: str | Path, case_id: str) -> Path:
    supplied = Path(workspace).expanduser()
    if supplied.is_symlink():
        raise ValueError("workspace 심볼릭 링크는 사용할 수 없습니다.")
    root = supplied.resolve(strict=True)
    paths = [root / "cases", root / "cases" / case_id,
             root / "cases" / case_id / "case.json", root / "reference",
             root / "reference" / "params", root / "reference" / "params" / "env.yaml",
             root / "reference" / "params" / "agent.yaml"]
    for path in paths:
        if path.is_symlink():
            raise ValueError(f"재학습 입력 경로에 심볼릭 링크를 사용할 수 없습니다: {path.name}")
        resolved = path.resolve(strict=True)
        if root not in (resolved, *resolved.parents):
            raise ValueError("재학습 입력이 workspace 밖을 가리킵니다.")
    prereg_dir = root / "preregistrations"
    if prereg_dir.exists() and prereg_dir.is_symlink():
        raise ValueError("preregistrations 심볼릭 링크는 사용할 수 없습니다.")
    return root


def _checked_loops(loops: str | Path, case_id: str) -> Path:
    supplied = Path(loops).expanduser()
    if supplied.is_symlink():
        raise ValueError("loops 심볼릭 링크는 사용할 수 없습니다.")
    root = supplied.resolve(strict=True)
    case = root / case_id
    for path in (case, case / "prereg.json", case / "state.json", case / "ledger.jsonl"):
        if path.is_symlink():
            raise ValueError(f"교정 loop 입력에 심볼릭 링크를 사용할 수 없습니다: {path.name}")
        resolved = path.resolve(strict=True)
        if root not in (resolved, *resolved.parents):
            raise ValueError("교정 loop 입력이 지정한 loops 밖을 가리킵니다.")
    correction_ledger = case / "correction_ledger.jsonl"
    if correction_ledger.exists() and correction_ledger.is_symlink():
        raise ValueError("correction ledger 심볼릭 링크는 사용할 수 없습니다.")
    return root


def _correct_module():
    """저장소의 기존 교정 CLI 구현을 같은 프로세스에서 재사용한다."""
    global _CORRECT_MODULE
    if _CORRECT_MODULE is not None:
        return _CORRECT_MODULE
    path = Path(__file__).resolve().parents[3] / "evals" / "correct.py"
    spec = importlib.util.spec_from_file_location("mollet_existing_correct_cli", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("기존 교정 소비 모듈을 불러올 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _CORRECT_MODULE = module
    return module


def prepare_training(analysis: dict, bundle: dict, hypothesis_id: str, *, change_id: str | None = None,
                     workspace: str | Path | None = None, recovery_mode: str = "restart") -> dict:
    """Meta 가설을 실행 권한 없는 제안 또는 기존 eval bridge용 사전등록으로 만든다."""
    observations = validate_observations(bundle)
    checked = _analysis(analysis, observations)
    selected = next((item for item in checked["hypotheses"] if item["id"] == hypothesis_id), None)
    if selected is None:
        raise ValueError("선택한 가설 ID가 analysis에 없습니다.")
    correction_id = selected["correction_id"]
    mechanism = _CORRECTION_TO_MECHANISM.get(correction_id)
    status = "proposed"
    reason = None
    if correction_id != "none" and mechanism is None:
        status, reason = correction_registry.HOLD, "현재 교정 등록 표에 없는 교정입니다."
    elif mechanism is not None and recovery_mode not in correction_registry.REGISTRY[mechanism]["recovery_modes"]:
        raise ValueError(f"{correction_id}에 등록되지 않은 회복 방식입니다.")
    elif mechanism is not None:
        # Meta 가설은 probe_loop의 confirmed 상태와 receipt를 대신할 수 없다.
        status = correction_registry.HOLD
        reason = "등록 코드 교정은 확인 probe 영수증 뒤 correction ledger가 제안해야 합니다."
        if change_id is not None:
            reason += " 설정 change_id와 코드 교정을 결합하지 않았습니다."

    analysis_sha = _digest(checked)
    observations_sha = _digest(observations)
    binding = {
        "analysis_sha256": analysis_sha,
        "observations_sha256": observations_sha,
        "video_sha256": observations["video_sha256"],
        "hypothesis_id": selected["id"],
        "claim": selected["claim"],
        "evidence_frames": selected["evidence_frames"],
        "correction_id": correction_id,
        "recovery_mode": recovery_mode,
        "model_generated_execution_authority": False,
        "approval_status": "pending_human_approval",
    }
    proposal = {
        "schema": "meta-training-proposal-v1", "status": status, "reason": reason,
        "case_id": _case_id(observations), "mechanism": mechanism,
        "change_id": change_id, "contract": deepcopy(selected["contract"]),
        "meta_binding": binding, "consumer_ready": False,
        "execution_authority": "none", "approval_required": True,
        "next_consumer": ("rl_triage.behavior_authoring.triage_bridge.prepare_registered_correction"
                          if mechanism is not None
                          else "rl_triage.triage_tools.write_preregistration"),
        "gpu_started": False, "training_started": False,
    }
    if workspace is None or status == correction_registry.HOLD:
        return proposal
    if not isinstance(change_id, str) or not change_id:
        raise ValueError("기존 재학습 소비 경로에는 실제 change_id가 필요합니다.")

    case_id = proposal["case_id"]
    workspace_path = _checked_workspace(workspace, case_id)
    with _WRITER_LOCK:
        previous = triage_tools.WORKSPACE
        triage_tools.WORKSPACE = workspace_path
        try:
            changes = triage_tools.list_changes(case_id)
            ids = [item["change_id"] for item in changes]
            if change_id not in ids:
                raise ValueError(f"없는 change_id {change_id!r}. 가능한 값: {ids}")
            ranking = [change_id, *[item for item in ids if item != change_id]]
            prereg_path = workspace_path / "preregistrations" / f"{case_id}.json"
            prereg_path.parent.mkdir(exist_ok=True)
            if prereg_path.parent.is_symlink():
                raise ValueError("preregistrations 심볼릭 링크는 사용할 수 없습니다.")
            reservation = '{"schema":"meta-preregistration-reservation-v1"}\n'
            try:
                with prereg_path.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(reservation)
            except FileExistsError:
                raise FileExistsError("기존 사전등록은 덮어쓰지 않습니다. 새 case_id를 사용하세요.")
            try:
                written = triage_tools.write_preregistration(
                    case_id, change_id, ranking, selected["claim"],
                    f"{change_id} 하나만 기존 사람 승인형 eval bridge에서 되돌림",
                    _condition_text(selected["contract"]),
                    f"고정 평가와 행동 조건 {_condition_text(selected['contract'])}을 함께 재검사",
                )
                if "error" in written:
                    raise ValueError(written["error"])
            except Exception:
                if prereg_path.exists() and prereg_path.read_text(encoding="utf-8") == reservation:
                    prereg_path.unlink()
                raise
            persisted = json.loads(prereg_path.read_text(encoding="utf-8"))
            persisted.update({"meta_binding": binding, "requested_config_revert": {
                "change_id": change_id, "correction_id": "none",
                "consumer": "rl_triage.eval_bridge.approve",
            }, "execution_authority": "pending_human_approval"})
            prereg_path.write_text(json.dumps(persisted, ensure_ascii=False, allow_nan=False, indent=1),
                                   encoding="utf-8")
        finally:
            triage_tools.WORKSPACE = previous
    return {**proposal, "consumer_ready": True,
            "next_consumer": "rl_triage.eval_bridge.submit", "preregistration": persisted,
            "preregistration_file": str(prereg_path)}


def prepare_registered_correction(analysis: dict, bundle: dict, hypothesis_id: str, *, case_id: str,
                                  loops: str | Path, source_report: dict) -> dict:
    """확인된 probe loop만 기존 correction ledger의 미승인 제안으로 연결한다.

    CLI가 명시한 loops만 읽고, 예산 0의 pending 제안까지만 만든다. 승인·consume·run은 호출하지 않는다.
    """
    observations = validate_observations(bundle)
    checked = _analysis(analysis, observations)
    selected = next((item for item in checked["hypotheses"] if item["id"] == hypothesis_id), None)
    if selected is None:
        raise ValueError("선택한 가설 ID가 analysis에 없습니다.")
    case_id = _text(case_id, "case_id", 200)
    if _CASE_ID.fullmatch(case_id) is None:
        raise ValueError("case_id는 경로 문자가 없는 안전한 식별자여야 합니다.")
    provenance_case = observations["provenance"].get("triage_case_id")
    if provenance_case is not None and provenance_case != case_id:
        raise ValueError("관측 묶음의 triage_case_id와 교정 loop case_id가 다릅니다.")
    correction_id = selected["correction_id"]
    selected_mechanism = _CORRECTION_TO_MECHANISM.get(correction_id)
    if selected_mechanism is None:
        raise ValueError("선택 가설은 현재 correction registry의 등록 교정이어야 합니다.")
    loops_path = _checked_loops(loops, case_id)
    correct = _correct_module()

    # 기존 소비자의 세 검증 단계를 명시적으로 거친다. 모델 분석은 confirmed 상태를 만들지 않는다.
    prereg, state, loop_ledger = correct._loop_state(case_id, loops_path)
    confirmed_mechanism, confirming_request = correct._confirming_request(case_id, state, loop_ledger)
    if confirmed_mechanism != selected_mechanism:
        raise ValueError("확인 probe의 메커니즘과 Meta 가설의 등록 교정이 다릅니다.")
    expected = correction_registry.REGISTRY[confirmed_mechanism]["correction_id"]
    if correction_id != expected:
        raise ValueError("확인된 메커니즘의 등록 교정과 선택 가설이 다릅니다.")
    report_sha = _digest(source_report)
    packet = correct.evidence_packet(case_id, source_report, loops_path, source_report_sha=report_sha)
    if packet.get("mechanism") != confirmed_mechanism:
        raise ValueError("기존 교정 evidence packet의 메커니즘 결합이 다릅니다.")

    proposed = correct.propose(case_id, source_report, budget_gpu_s=0, loops=loops_path,
                               source_report_sha=report_sha)
    if proposed.get("decision") != "proposed" or proposed.get("correction_id") != correction_id:
        raise ValueError("기존 correction ledger가 선택 교정을 제안하지 않았습니다.")
    status = correct.status(case_id, loops_path)
    request = status["requests"].get(proposed["request_id"])
    if request is None or request.get("state") != "pending" or request.get("budget_gpu_s") != 0:
        raise ValueError("교정 제안이 GPU 예산 0의 승인 대기 상태가 아닙니다.")
    return {
        "schema": "meta-registered-correction-proposal-v1",
        "decision": "proposed", "case_id": case_id,
        "request": request,
        "meta_binding": {
            "analysis_sha256": _digest(checked),
            "observations_sha256": _digest(observations),
            "video_sha256": observations["video_sha256"],
            "hypothesis_id": selected["id"],
            "correction_id": correction_id,
            "confirmed_mechanism": confirmed_mechanism,
            "confirming_request_id": confirming_request["request_id"],
            "source_report_sha256": report_sha,
            "model_generated_confirmation": False,
        },
        "approval_status": "pending_human_approval",
        "gpu_budget_s": 0,
        "approved": False, "consumed": False, "training_started": False,
        "recovery_mode_source": "rl_triage.correction_registry.recommend_recovery_mode",
        "next_consumer": "evals.correct.approve_then_run",
    }


def _intervention(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"correction_id", "recovery_mode"}:
        raise ValueError("intervention에는 correction_id와 recovery_mode가 필요합니다.")
    correction_id = value["correction_id"]
    mechanism = _CORRECTION_TO_MECHANISM.get(correction_id)
    if mechanism is None:
        raise ValueError("회복 비교에는 등록된 교정만 사용할 수 있습니다.")
    mode = value["recovery_mode"]
    if mode not in correction_registry.REGISTRY[mechanism]["recovery_modes"]:
        raise ValueError("등록 교정과 회복 방식이 일치하지 않습니다.")
    return {"correction_id": correction_id, "recovery_mode": mode, "mechanism": mechanism}


def compare_recovery(payload: dict) -> dict:
    """기존 고정 평가 판정기를 재사용하며 Meta의 인과·성능 개선 주장을 만들지 않는다."""
    if not isinstance(payload, dict) or set(payload) != {
            "schema", "evaluation", "intervention", "before", "after", "reference", "contract", "reports"}:
        raise ValueError("회복 비교 입력 필드가 폐쇄형 스키마와 다릅니다.")
    if payload["schema"] != "meta-recovery-comparison-v1":
        raise ValueError("지원하지 않는 회복 비교 스키마입니다.")
    intervention = _intervention(payload["intervention"])
    evaluation = payload["evaluation"]
    if evaluation == "behavior_oracle":
        if payload["reports"] is not None:
            raise ValueError("behavior_oracle 비교에는 reports를 함께 넣을 수 없습니다.")
        if payload["before"] is None or payload["reference"] is None or payload["contract"] is None:
            result = {"outcome": "undetermined", "reason": "missing_fixed_eval"}
        else:
            result = behavior_oracle.recovery_outcome(
                payload["before"], payload["after"], payload["reference"], payload["contract"], True)
    elif evaluation == "grounded_triplet":
        if any(payload[key] is not None for key in ("before", "after", "reference", "contract")):
            raise ValueError("grounded_triplet 비교에는 개별 oracle 입력을 함께 넣을 수 없습니다.")
        reports = payload["reports"]
        if (not isinstance(reports, dict) or set(reports) != set(grounded_recovery.ARMS)
                or any(reports[arm] is None for arm in grounded_recovery.ARMS)):
            result = {"status": "unknown", "reason": "missing_controlled_triplet"}
        else:
            result = grounded_recovery.summarize_triplet(reports)
    else:
        raise ValueError("evaluation은 behavior_oracle 또는 grounded_triplet이어야 합니다.")
    return {
        "schema": "meta-recovery-comparison-result-v1", "evaluation": evaluation,
        "intervention": intervention, "result": result,
        "causal_confirmation": False, "performance_improvement_measured": False,
        "meta_model_judged_outcome": False,
    }
