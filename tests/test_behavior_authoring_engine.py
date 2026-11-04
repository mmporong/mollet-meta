"""합성 시계열로 오프라인 행동 계약 판정 경계를 검증한다.

이 입력은 테스트용 합성 bbox/지표이며 실제 SAM 출력이 아니다.
"""
from copy import deepcopy

import pytest

from rl_triage.behavior_authoring import (
    BehaviorSchemaError,
    contract_digest,
    evaluate_contract,
    validate_contract,
    validate_observations,
)


METRICS = ("horizontal_speed_mps", "command_speed_mps", "base_height_m", "up_axis_z")


def contract(*, trigger=None, deadline=1.0, hold=1.0, gap=0.6):
    return {
        "schema": "behavior-contract-v1",
        "title": "합성 행동 계약",
        "instruction": "트리거 뒤 수평 속도를 유지한다.",
        "trigger": trigger or {"kind": "time", "at_s": 1.0, "region": None, "object_id": None},
        "condition": {
            "metric": "horizontal_speed_mps", "op": "gte", "threshold": 1.0,
            "deadline_s": deadline, "hold_s": hold,
        },
        "max_gap_s": gap,
        "source": {"kind": "synthetic_test", "actual_sam_output": False},
    }


def observations(values, *, tracks=None, times=None):
    times = times or [index * 0.5 for index in range(len(values))]
    tracks = tracks or [[] for _ in values]
    samples = []
    for index, (t_s, value, sample_tracks) in enumerate(zip(times, values, tracks)):
        metrics = {metric: None for metric in METRICS}
        metrics["horizontal_speed_mps"] = value
        samples.append({"frame": index, "t_s": t_s, "values": metrics, "tracks": sample_tracks})
    return {
        "schema": "behavior-observations-v1", "clip_id": "synthetic-clip",
        "video_sha256": "a" * 64, "provenance": {"kind": "synthetic_test"}, "samples": samples,
    }


def track(object_id, center_x, center_y=0.5, size=0.1):
    half = size / 2
    return {"object_id": object_id, "bbox": [center_x - half, center_y - half, center_x + half, center_y + half]}


def test_time_trigger_pass_and_complete_view_fail():
    passing = evaluate_contract(contract(), observations([0, 0, 1, 1, 1, 0, 0]))
    assert passing["verdict"] == "PASS"
    assert passing["interval"] == {"start_s": 1.0, "end_s": 2.0}
    assert passing["stage"] == "draft"
    assert passing["approval_status"] == "unreviewed"

    failing = evaluate_contract(contract(), observations([0, 0, 0, 1, 0, 0, 0]))
    assert failing["verdict"] == "FAIL"
    assert failing["interval"] is None


def test_incomplete_suffix_missing_metric_and_gap_are_unknown():
    assert evaluate_contract(contract(), observations([0, 0, 0, 0]))["verdict"] == "UNKNOWN"
    missing = observations([0, 0, None, 0, 0, 0, 0])
    assert evaluate_contract(contract(), missing)["verdict"] == "UNKNOWN"
    gapped = observations([0, 0, 0, 0], times=[0.0, 1.0, 2.0, 3.0])
    assert evaluate_contract(contract(gap=0.6), gapped)["verdict"] == "UNKNOWN"
    endpoint_missing = observations([0, 0, 0, 0, None], times=[0.0, 0.5, 1.0, 1.5, 2.5])
    assert evaluate_contract(contract(deadline=0.5, hold=1.0), endpoint_missing)["verdict"] == "UNKNOWN"


def test_region_entry_rejects_unanchored_inside_but_allows_reacquired_outside_anchor():
    region_contract = contract(trigger={
        "kind": "region_entry", "at_s": None, "region": [0.4, 0.4, 0.6, 0.6], "object_id": "robot",
    })
    first_inside = observations([1, 1, 1], tracks=[[track("robot", 0.5)]] * 3)
    assert evaluate_contract(region_contract, first_inside)["verdict"] == "UNKNOWN"

    across_gap = observations(
        [1, 1, 1], times=[0.0, 0.5, 2.0],
        tracks=[[track("robot", 0.2)], [track("robot", 0.2)], [track("robot", 0.5)]],
    )
    assert evaluate_contract(region_contract, across_gap)["trigger_t_s"] is None

    reentry_after_gap = observations(
        [1, 1, 1, 1, 1], times=[0.0, 0.5, 2.0, 2.5, 3.0],
        tracks=[
            [track("robot", 0.2)], [track("robot", 0.2)], [track("robot", 0.5)],
            [track("robot", 0.2)], [track("robot", 0.5)],
        ],
    )
    reacquired = evaluate_contract(region_contract, reentry_after_gap)
    assert reacquired["trigger_t_s"] == 3.0
    assert any("new anchor" in warning and "physical identity" in warning for warning in reacquired["warnings"])

    mixed = observations(
        [1, 1, 1],
        tracks=[[track("robot", 0.2)], [track("other", 0.5)], [track("robot", 0.5)]],
    )
    assert evaluate_contract(region_contract, mixed)["trigger_t_s"] is None


def test_region_entry_includes_exact_boundary_and_uses_bbox_center_warning():
    spec = contract(trigger={
        "kind": "region_entry", "at_s": None, "region": [0.4, 0.4, 0.6, 0.6], "object_id": "robot",
    }, deadline=0.0, hold=0.5)
    bundle = observations(
        [0, 1, 1, 1],
        tracks=[[track("robot", 0.2)], [track("robot", 0.4)], [track("robot", 0.5)], [track("robot", 0.5)]],
    )
    result = evaluate_contract(spec, bundle)
    assert result["trigger_t_s"] == 0.5
    assert result["verdict"] == "PASS"
    assert any("bounding-box center" in warning and "SAM" in warning for warning in result["warnings"])


def test_rejected_capture_blocks_region_evidence_but_not_time_metrics():
    region_spec = contract(trigger={
        "kind": "region_entry", "at_s": None, "region": [0.4, 0.4, 0.6, 0.6], "object_id": "robot",
    }, deadline=0.0, hold=0.5)
    bundle = observations(
        [0, 1, 1],
        tracks=[[track("robot", 0.2)], [track("robot", 0.5)], [track("robot", 0.5)]],
    )
    bundle["provenance"]["capture_approved"] = False
    approval = {
        "contract_sha256": contract_digest(region_spec), "actor": "pytest", "scope": "automated_test",
    }

    rejected = evaluate_contract(region_spec, bundle, approval=approval)
    assert rejected["verdict"] == "UNKNOWN"
    assert rejected["trigger_t_s"] is None
    assert rejected["interval"] is None
    assert any("separate" in warning and "not promoted" in warning for warning in rejected["warnings"])

    time_bundle = observations([1, 1, 1])
    time_bundle["provenance"]["capture_approved"] = False
    assert evaluate_contract(contract(trigger={
        "kind": "time", "at_s": 0.0, "region": None, "object_id": None,
    }, deadline=0.0, hold=1.0), time_bundle)["verdict"] == "PASS"


def test_condition_never_uses_pretrigger_evidence():
    spec = contract(deadline=0.0, hold=1.0)
    bundle = observations([1, 1, 0, 1, 1], times=[0.0, 0.5, 1.0, 1.5, 2.0])
    result = evaluate_contract(spec, bundle)
    assert result["verdict"] == "FAIL"
    assert result["interval"] is None


def test_time_trigger_before_first_sample_cannot_prove_failure_even_within_max_gap():
    spec = contract(
        trigger={"kind": "time", "at_s": 0.0, "region": None, "object_id": None},
        deadline=0.01,
        hold=0.01,
        gap=0.05,
    )
    bundle = observations([0, 0], times=[0.04, 0.05])

    result = evaluate_contract(spec, bundle)

    assert result["verdict"] == "UNKNOWN"
    assert result["trigger_t_s"] == 0.0
    assert any("complete observation window" in warning for warning in result["warnings"])


@pytest.mark.parametrize("mutator", [
    lambda spec, bundle: spec.update(extra=True),
    lambda spec, bundle: spec["condition"].update(threshold=float("nan")),
    lambda spec, bundle: spec["condition"].update(threshold=True),
    lambda spec, bundle: spec["source"].update(exec="print('no')"),
    lambda spec, bundle: bundle["samples"][0].update(extra=True),
    lambda spec, bundle: bundle["samples"][0]["values"].update(horizontal_speed_mps=True),
    lambda spec, bundle: bundle["provenance"].update(script="payload"),
])
def test_closed_schema_rejects_nan_bool_extra_and_executable_fields(mutator):
    spec = contract()
    bundle = observations([0, 0, 0])
    mutator(spec, bundle)
    with pytest.raises(BehaviorSchemaError):
        evaluate_contract(spec, bundle)


def test_missing_metrics_normalize_to_null_but_unknown_keys_fail():
    bundle = observations([0, 0, 0])
    bundle["samples"][0]["values"] = {}
    normalized = validate_observations(bundle)
    assert normalized["samples"][0]["values"] == {metric: None for metric in METRICS}
    bundle["samples"][0]["values"]["reward"] = 1
    with pytest.raises(BehaviorSchemaError):
        validate_observations(bundle)


def test_approval_is_digest_bound_and_stage_is_truthful():
    spec = contract()
    digest = contract_digest(spec)
    approved = evaluate_contract(spec, observations([0, 0, 1, 1, 1, 0, 0]), approval={
        "contract_sha256": digest, "actor": "pytest", "scope": "automated_test",
    })
    assert approved["stage"] == "automated_test"
    assert approved["approval_status"] == "approved"
    assert any("GPU execution authority" in warning for warning in approved["warnings"])
    with pytest.raises(BehaviorSchemaError, match="digest"):
        evaluate_contract(spec, observations([0]), approval={
            "contract_sha256": "0" * 64, "actor": "reviewer", "scope": "local_semantic_review",
        })


def test_contract_digest_uses_normalized_canonical_form():
    spec = contract()
    normalized = validate_contract(deepcopy(spec))
    assert contract_digest(spec) == contract_digest(normalized)
