from copy import deepcopy

import pytest

from rl_triage.behavior_authoring.improvement import (
    analysis_schema,
    build_reflection_context,
    run_round,
    validate_analysis,
)
from rl_triage.behavior_authoring.schema import BehaviorSchemaError


METRICS = ("horizontal_speed_mps", "command_speed_mps", "base_height_m", "up_axis_z")


def bundle(values):
    samples = []
    for frame, value in enumerate(values):
        metrics = {name: None for name in METRICS}
        metrics["horizontal_speed_mps"] = value
        samples.append({"frame": frame, "t_s": frame * 0.5, "values": metrics, "tracks": []})
    return {"schema": "behavior-observations-v1", "clip_id": "clip", "video_sha256": "a" * 64,
            "provenance": {"kind": "test"}, "samples": samples}


def contract(threshold, *, deadline=1.0, hold=0.5):
    return {"schema": "behavior-contract-v1", "title": f"threshold {threshold}",
            "instruction": "저장 기록에서 속도를 검사한다.",
            "trigger": {"kind": "time", "at_s": 0.0, "region": None, "object_id": None},
            "condition": {"metric": "horizontal_speed_mps", "op": "gte", "threshold": threshold,
                          "deadline_s": deadline, "hold_s": hold}, "max_gap_s": 0.6}


def analysis(*hypotheses):
    return {"summary": "영상과 상태에서 가능한 원인을 비교한다.", "hypotheses": list(hypotheses),
            "limitations": ["저장 기록만 사용했다."]}


def hypothesis(identifier, threshold, frames=(0,), correction="none"):
    return {"id": identifier, "claim": f"{identifier} 원인", "evidence_frames": list(frames),
            "contract": contract(threshold), "correction_id": correction, "rationale": "영상 근거와 수치 조건"}


def test_schema_is_closed_and_contract_has_no_source():
    schema = analysis_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["hypotheses"]["minItems"] == 1
    assert "source" not in schema["properties"]["hypotheses"]["items"]["properties"]["contract"]["properties"]


def test_validation_binds_frames_and_downgrades_model_authority():
    value = validate_analysis(analysis(hypothesis("h1", 0.5, (0, 2))), bundle([0.0, 0.5, 1.0]))
    source = value["hypotheses"][0]["contract"]["source"]
    assert source["model_called"] is False
    assert source["execution_authorized"] is False
    assert source["provider_authentication"] == "unverified_analysis_input"

    invalid = analysis(hypothesis("h1", 0.5, (9,)))
    with pytest.raises(BehaviorSchemaError, match="프레임"):
        validate_analysis(invalid, bundle([0.0]))
    extra = analysis(hypothesis("h1", 0.5))
    extra["hypotheses"][0]["execution"] = "run"
    with pytest.raises(BehaviorSchemaError, match="필드"):
        validate_analysis(extra, bundle([0.0]))
    normalized = validate_analysis(analysis(hypothesis("h2", 0.5)), bundle([0.0]))
    normalized["hypotheses"][0]["contract"]["source"]["execution_authorized"] = True
    with pytest.raises(BehaviorSchemaError, match="변조"):
        run_round(normalized, bundle([0.0]))


def test_round_selects_most_balanced_predicate_and_runs_one_check():
    data = bundle([0.0, 0.0, 1.0, 1.0])
    value = analysis(hypothesis("flat", 2.0), hypothesis("balanced", 0.5))
    first = run_round(value, data)
    assert first["selected_hypothesis_id"] == "balanced"
    assert len(first["experiments"]) == 1
    assert first["experiments"][0]["information_gain_bits"] == 1.0
    assert first["experiments"][0]["result"]["evidence_scope"] == "local_recorded_check_not_causal_experiment"
    assert first["requires_new_evidence"] is True

    second = run_round(value, data, first["history"])
    assert second["selected_hypothesis_id"] == "flat"
    assert len(second["history"]) == 2
    stopped = run_round(value, data, second["history"])
    assert stopped["selected_hypothesis_id"] is None
    assert stopped["experiments"] == []
    assert stopped["stop_reason"] == "all_hypotheses_checked"


def test_round_budget_and_missing_values_are_truthful():
    data = bundle([None, None, None])
    value = analysis(hypothesis("a", 0.5), hypothesis("b", 0.6))
    first = run_round(value, data, max_rounds=1)
    assert first["experiments"][0]["information_gain_bits"] == 0.0
    assert first["experiments"][0]["result"]["verdict"] == "UNKNOWN"
    stopped = run_round(value, data, first["history"], max_rounds=1)
    assert stopped["stop_reason"] == "round_budget_reached"
    assert stopped["selected_hypothesis_id"] is None


def test_history_is_digest_bound_and_recomputed_before_reuse():
    data = bundle([0.0, 1.0, 1.0])
    value = analysis(hypothesis("h", 0.5))
    first = run_round(value, data)
    forged = deepcopy(first["history"])
    forged[0]["result"]["verdict"] = "PASS" if forged[0]["result"]["verdict"] != "PASS" else "FAIL"
    with pytest.raises(BehaviorSchemaError, match="재계산"):
        run_round(value, data, forged)
    changed = bundle([0.0, 0.0, 0.0])
    with pytest.raises(BehaviorSchemaError, match="묶여"):
        run_round(value, changed, first["history"])


def test_reflection_context_has_outcomes_and_no_raw_samples_or_authority():
    data = bundle([0.0, 1.0, 1.0])
    value = analysis(hypothesis("h", 0.5, correction="restore_body_frame_reward"))
    first = run_round(value, data)
    context = build_reflection_context(value, data, first["history"])
    assert context["outcomes"][0]["hypothesis_id"] == "h"
    assert context["hypotheses"][0]["correction_id"] == "restore_body_frame_reward"
    assert "samples" not in context
    assert all("승인" not in key and "execution" not in key for key in context)
    assert any("인과 실험" in item for item in context["uncertainty"])
