"""Meta 개선 가설과 기존 사람 승인형 재학습 소비 경로의 연결 검사."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import itertools
import json
from pathlib import Path

import pytest

from rl_triage import behavior_oracle as oracle
from rl_triage import eval_bridge
from rl_triage import probe_loop
from rl_triage.correction_ledger import CorrectionLedger, LedgerError
from rl_triage.behavior_authoring import triage_bridge as bridge_module
from rl_triage.behavior_authoring.datasets import load_example_view, stairs_contract
from rl_triage.behavior_authoring.improvement import validate_analysis
from rl_triage.behavior_authoring.triage_bridge import (
    bridge_options,
    compare_recovery,
    prepare_registered_correction,
    prepare_training,
)


ENV_YAML = "episode_length_s: 20.0\nactions:\n  joint_pos:\n    scale: 0.25\n"
AGENT_YAML = "algorithm:\n  gamma: 0.99\n"


def _bundle(case_id="c01"):
    value = deepcopy(load_example_view("h1-up")["observations"])
    value["provenance"]["triage_case_id"] = case_id
    return value


def _analysis(correction="restore_body_frame_reward"):
    contract = stairs_contract()
    contract.pop("source")
    return {
        "summary": "영상의 흔들림과 상태 기록을 함께 점검했습니다.",
        "hypotheses": [{
            "id": "h-reward", "claim": "몸체 속도 보상 좌표계가 실패 원인일 수 있습니다.",
            "evidence_frames": [0, 20], "contract": contract,
            "correction_id": correction, "rationale": "자세 조건과 이동 속도가 함께 저하됐습니다.",
        }],
        "limitations": ["저장 기록 판정만으로 인과성을 확정할 수 없습니다."],
    }


def _workspace(path):
    (path / "reference" / "params").mkdir(parents=True)
    (path / "reference" / "params" / "env.yaml").write_text(ENV_YAML, encoding="utf-8")
    (path / "reference" / "params" / "agent.yaml").write_text(AGENT_YAML, encoding="utf-8")
    case = path / "cases" / "c01"
    case.mkdir(parents=True)
    (case / "case.json").write_text(json.dumps({
        "case_id": "c01", "overrides": ["agent.algorithm.gamma=0.5", "env.actions.joint_pos.scale=1.5"]
    }), encoding="utf-8")


def _confirmed_loop(loops, case="c1", mechanism="reward", confirm=True):
    directory = loops / case
    directory.mkdir(parents=True)
    prereg = {"case": case, "hypotheses": [mechanism], "checkpoint_sha256": "ab" * 32}
    state = {"hypotheses": [mechanism], "observed": {}, "skipped": [], "budget": 4,
             "status": "confirmed", "pending": None, "history": []}
    ledger = probe_loop.Ledger(directory / "ledger.jsonl")
    if confirm:
        probe = probe_loop.confirming_probe(mechanism)
        request_id = ledger.propose(prereg, probe, 100)["request_id"]
        ledger.approve(request_id, "tester")
        ledger.consume(request_id, prereg, probe)
        ledger.receipt(request_id, "abnormal", {}, 10.0, 0)
        state["observed"][probe] = "abnormal"
    else:
        (directory / "ledger.jsonl").write_text("", encoding="utf-8")
    (directory / "prereg.json").write_text(json.dumps(prereg), encoding="utf-8")
    (directory / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return directory


def test_options_expose_real_consumers_and_unregistered_family_scope():
    options = bridge_options()
    assert options["model_may_approve_or_execute"] is False
    assert options["consumers"]["preregistration"].endswith("write_preregistration")
    rows = {row["correction_id"]: row for row in options["meta_correction_options"]}
    assert rows["restore_body_frame_reward"]["registered_in_correction_registry"] is True
    assert rows["restore_body_frame_reward"]["eligible_for_config_revert_preregistration"] is False
    assert rows["restore_reward_family_scope"]["registered_in_correction_registry"] is False


def test_download_proposal_has_no_execution_authority_or_side_effect(tmp_path):
    bundle = _bundle()
    normalized = validate_analysis(_analysis("none"), bundle)
    result = prepare_training(normalized, bundle, "h-reward", change_id="ch1")
    assert result["status"] == "proposed" and result["consumer_ready"] is False
    assert result["execution_authority"] == "none" and result["approval_required"] is True
    assert result["gpu_started"] is False and result["training_started"] is False
    assert not list(tmp_path.iterdir())


def test_actual_writer_output_is_consumed_by_eval_bridge_as_pending_only(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    workspace = root / "workspace" / "seed42"
    _workspace(workspace)
    result = prepare_training(_analysis("none"), _bundle(), "h-reward", change_id="ch1",
                              workspace=workspace, recovery_mode="restart")
    prereg = json.loads((workspace / "preregistrations" / "c01.json").read_text(encoding="utf-8"))
    assert result["consumer_ready"] is True and prereg["suspected_change_id"] == "ch1"
    assert prereg["meta_binding"]["model_generated_execution_authority"] is False
    assert prereg["requested_config_revert"] == {
        "change_id": "ch1", "correction_id": "none",
        "consumer": "rl_triage.eval_bridge.approve",
    }
    monkeypatch.setattr(eval_bridge, "ROOT", root)
    monkeypatch.setattr(eval_bridge, "QUEUE", root / "queue")
    request = eval_bridge.submit("c01", 42)
    assert request["status"] == 202 and request["state"] == "pending_approval"
    assert request["preregistration"]["meta_binding"] == prereg["meta_binding"]
    assert not any(key in request for key in ("approved_at", "reverted_override", "verdict"))


def test_unknown_change_and_unregistered_or_none_correction_fail_closed(tmp_path):
    workspace = tmp_path / "workspace"
    _workspace(workspace)
    with pytest.raises(ValueError, match="없는 change_id"):
        prepare_training(_analysis("none"), _bundle(), "h-reward", change_id="ch9", workspace=workspace)
    registered = prepare_training(_analysis(), _bundle(), "h-reward", change_id="ch1", workspace=workspace)
    assert registered["status"] == "HOLD" and registered["consumer_ready"] is False
    assert "확인 probe" in registered["reason"]
    assert not (workspace / "preregistrations" / "c01.json").exists()
    family = prepare_training(_analysis("restore_reward_family_scope"), _bundle(), "h-reward",
                              change_id="ch1", workspace=workspace)
    assert family["status"] == "HOLD" and family["consumer_ready"] is False
    abstain = prepare_training(_analysis("none"), _bundle(), "h-reward")
    assert abstain["status"] == "proposed" and abstain["mechanism"] is None
    with pytest.raises(ValueError, match="등록되지 않은 correction_id"):
        prepare_training(_analysis("restore_motor_torque_speed"), _bundle(), "h-reward")


def test_existing_preregistration_and_symlinked_case_are_rejected(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    _workspace(workspace)
    prereg = workspace / "preregistrations"
    prereg.mkdir()
    existing = prereg / "c01.json"
    existing.write_text('{"owner":"existing"}', encoding="utf-8")
    with pytest.raises(FileExistsError, match="덮어쓰지"):
        prepare_training(_analysis("none"), _bundle(), "h-reward", change_id="ch1", workspace=workspace)
    assert json.loads(existing.read_text(encoding="utf-8")) == {"owner": "existing"}

    existing.unlink()
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "c01" or original(self))
    with pytest.raises(ValueError, match="심볼릭 링크"):
        prepare_training(_analysis("none"), _bundle(), "h-reward", change_id="ch1", workspace=workspace)


def test_confirmed_probe_connects_to_actual_correction_ledger_but_cannot_run_unapproved(tmp_path):
    loops = tmp_path / "loops"
    loops.mkdir()
    _confirmed_loop(loops)
    source_report = {"metrics": {"lin_vel_rmse_mps": 0.95},
                     "standstill": {"lin_vel_rmse_mps": 1.0}}
    result = prepare_registered_correction(
        _analysis(), _bundle("c1"), "h-reward", case_id="c1", loops=loops,
        source_report=source_report,
    )
    request = result["request"]
    assert request["state"] == "pending" and request["budget_gpu_s"] == 0
    assert request["correction_id"] == "restore_body_frame_reward"
    assert request["recovery_mode"] == "restart"
    assert result["approved"] is False and result["consumed"] is False
    assert result["training_started"] is False and result["meta_binding"]["model_generated_confirmation"] is False
    ledger = CorrectionLedger(loops / "c1" / "correction_ledger.jsonl")
    assert ledger.requests()[request["request_id"]]["state"] == "pending"

    calls = []
    with pytest.raises(LedgerError, match="approved 상태"):
        bridge_module._correct_module().run(
            "c1", request["request_id"], lambda value: calls.append(value), loops=loops)
    assert calls == []


def test_registered_correction_requires_real_confirming_receipt_and_matching_mechanism(tmp_path):
    loops = tmp_path / "loops"
    loops.mkdir()
    _confirmed_loop(loops, case="missing", confirm=False)
    report = {"metrics": {"lin_vel_rmse_mps": 0.5}, "standstill": {"lin_vel_rmse_mps": 1.0}}
    with pytest.raises(LedgerError, match="abnormal receipt"):
        prepare_registered_correction(
            _analysis(), _bundle("missing"), "h-reward", case_id="missing", loops=loops,
            source_report=report,
        )
    assert not (loops / "missing" / "correction_ledger.jsonl").exists()

    _confirmed_loop(loops, case="actuator", mechanism="actuator")
    with pytest.raises(ValueError, match="메커니즘과 Meta 가설"):
        prepare_registered_correction(
            _analysis(), _bundle("actuator"), "h-reward", case_id="actuator", loops=loops,
            source_report=report,
        )
    assert not (loops / "actuator" / "correction_ledger.jsonl").exists()


def _report(name="reference", checkpoint="a" * 64, fall=0.0, survival=20.0, lin=0.15, yaw=0.3):
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    protocol_sha = hashlib.sha256((root / "bench/protocols/fixed_eval_v1.json").read_bytes()
                                  .replace(b"\r\n", b"\n")).hexdigest()
    grid = [list(item) for item in itertools.product((-1.0, 0.0, 1.0), repeat=3)
            if item != (0.0, 0.0, 0.0)]
    rows = []
    for command in grid:
        rows.append({"command": command, "fall_rate": fall, "lin_vel_rmse_mps": lin,
                     "yaw_rate_rmse_radps": yaw})
    return {
        "protocol": "fixed_eval_v1", "protocol_sha256_lf": protocol_sha,
        "task": "Isaac-Velocity-Flat-Unitree-Go2-v0", "eval_seed": 2026, "num_envs": 1040,
        "horizon_steps": 1000, "step_dt": 0.02, "action_scale": 0.25, "name": name,
        "versions": {"torch": "2.7.0+cu128", "isaaclab": "0.41.3", "isaaclab_tasks": "0.10.36",
                     "isaaclab_rl": "0.1.8", "rsl-rl-lib": "2.3.3", "isaacsim": None},
        "checkpoint": {"file": "model_299.pt", "sha256": checkpoint},
        "metrics": {"fall_rate": fall, "mean_survival_s": survival,
                    "lin_vel_rmse_mps": lin, "yaw_rate_rmse_radps": yaw},
        "by_condition": rows,
    }


def _comparison(before, after, reference, contract):
    return {
        "schema": "meta-recovery-comparison-v1", "evaluation": "behavior_oracle",
        "intervention": {"correction_id": "restore_body_frame_reward", "recovery_mode": "restart"},
        "before": before, "after": after, "reference": reference, "contract": contract, "reports": None,
    }


def test_recovery_uses_existing_oracle_without_causal_or_improvement_claim():
    reference = _report()
    contract = oracle.register_contract(reference, [], {
        "min_survival_fraction": 0.9, "max_fall_rate": 0.05,
        "max_lin_vel_rmse_mps": 0.5, "max_yaw_rate_rmse_radps": 0.5,
    })
    result = compare_recovery(_comparison(_report("before", lin=0.8), _report("after"), reference, contract))
    assert result["result"]["outcome"] == "recovered"
    assert result["causal_confirmation"] is False
    assert result["performance_improvement_measured"] is False
    assert result["meta_model_judged_outcome"] is False


def test_missing_after_is_unknown_and_changed_evaluation_or_intervention_is_rejected():
    reference = _report()
    contract = oracle.register_contract(reference, [], {
        "min_survival_fraction": 0.9, "max_fall_rate": 0.05,
        "max_lin_vel_rmse_mps": 0.5, "max_yaw_rate_rmse_radps": 0.5,
    })
    payload = _comparison(_report("before", lin=0.8), None, reference, contract)
    assert compare_recovery(payload)["result"]["outcome"] == "undetermined"
    changed = _comparison(_report("before", lin=0.8), _report("after"), reference, contract)
    changed["after"]["eval_seed"] = 7
    with pytest.raises(oracle.OracleError, match="일치하지"):
        compare_recovery(changed)
    wrong = _comparison(_report("before"), _report("after"), reference, contract)
    wrong["intervention"] = {"correction_id": "restore_reward_family_scope", "recovery_mode": "restart"}
    with pytest.raises(ValueError, match="등록된 교정"):
        compare_recovery(wrong)


def test_missing_controlled_triplet_is_unknown_without_fabricating_recovery():
    payload = {
        "schema": "meta-recovery-comparison-v1", "evaluation": "grounded_triplet",
        "intervention": {"correction_id": "restore_body_frame_reward", "recovery_mode": "restart"},
        "before": None, "after": None, "reference": None, "contract": None,
        "reports": {"fault_before": None, "fault_sham": None, "callback_fixed": None},
    }
    result = compare_recovery(payload)
    assert result["result"] == {"status": "unknown", "reason": "missing_controlled_triplet"}
    assert result["causal_confirmation"] is False
