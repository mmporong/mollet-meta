"""폐쇄형 행동 계약을 저장된 시계열 관측에 대해 판정한다."""
from __future__ import annotations

from typing import Any

from .schema import (
    BehaviorSchemaError,
    _canonical_digest,
    contract_digest,
    validate_contract,
    validate_observations,
)


def _validate_approval(approval: dict | None, expected_digest: str) -> tuple[str, str, dict | None]:
    if approval is None:
        return "draft", "unreviewed", None
    if not isinstance(approval, dict):
        raise BehaviorSchemaError("approval은 객체여야 합니다")
    required = {"contract_sha256", "actor", "scope"}
    if set(approval) != required:
        raise BehaviorSchemaError("approval 필드는 contract_sha256, actor, scope만 허용됩니다")
    if approval["contract_sha256"] != expected_digest:
        raise BehaviorSchemaError("approval.contract_sha256이 현재 계약 digest와 일치하지 않습니다")
    actor = approval["actor"]
    if not isinstance(actor, str) or not actor.strip() or len(actor.strip()) > 200:
        raise BehaviorSchemaError("approval.actor는 비어 있지 않은 짧은 문자열이어야 합니다")
    scope = approval["scope"]
    if scope not in {"local_semantic_review", "automated_test"}:
        raise BehaviorSchemaError("approval.scope가 허용 범위를 벗어났습니다")
    normalized = {"contract_sha256": expected_digest, "actor": actor.strip(), "scope": scope}
    return ("reviewed" if scope == "local_semantic_review" else "automated_test"), "approved", normalized


def _track(sample: dict, object_id: str) -> dict | None:
    return next((track for track in sample["tracks"] if track["object_id"] == object_id), None)


def _inside(bbox: list[float], region: list[float]) -> bool:
    center_x = (bbox[0] + bbox[2]) / 2.0
    center_y = (bbox[1] + bbox[3]) / 2.0
    return region[0] <= center_x <= region[2] and region[1] <= center_y <= region[3]


def _find_trigger(spec: dict, samples: list[dict], warnings: list[str]) -> tuple[float | None, str | None]:
    """연속된 outside anchor 뒤 같은 ID가 inside가 된 첫 관측 시각을 찾는다.

    이는 전체 영상에서 대상이 물리적으로 처음 진입한 시각이 아니라, 현재 연속 관측
    구간에서 확인한 첫 진입이다. 가림 뒤에는 outside anchor를 다시 얻어야 한다.
    """
    trigger = spec["trigger"]
    if trigger["kind"] == "time":
        return trigger["at_s"], None

    object_id = trigger["object_id"]
    region = trigger["region"]
    max_gap = spec["max_gap_s"]
    previous_t: float | None = None
    previous_inside: bool | None = None
    continuity_lost = False
    saw_target = False
    for sample in samples:
        current = _track(sample, object_id)
        if current is None:
            continuity_lost = True
            previous_t = None
            previous_inside = None
            continue
        saw_target = True
        current_inside = _inside(current["bbox"], region)
        if previous_t is not None and sample["t_s"] - previous_t > max_gap:
            continuity_lost = True
            previous_inside = None
        if previous_inside is False and current_inside:
            warnings.append(
                "Region entry uses the selected object_id bounding-box center crossing the boundary. "
                "SAM masks were not interpreted."
            )
            if continuity_lost:
                warnings.append(
                    "After an initial inside observation or occlusion, a later outside observation of the same object_id became a new anchor. "
                    "This is the first observed entry after reacquisition and does not prove physical identity across the occlusion."
                )
            return sample["t_s"], None
        if previous_inside is None and current_inside:
            continuity_lost = True
        previous_t = sample["t_s"]
        previous_inside = current_inside

    warnings.append("Region entry uses the bounding-box center; SAM masks were not interpreted.")
    if continuity_lost or not saw_target:
        return (
            None,
            "Occlusion, observation gaps, or an initial inside observation prevent a confirmed entry time.",
        )
    return None, "No outside-to-inside entry was observed for the selected object_id."


def _satisfies(value: float, op: str, threshold: float) -> bool:
    return value >= threshold if op == "gte" else value <= threshold


def _evaluate_condition(spec: dict, samples: list[dict], trigger_t: float) -> tuple[str, str, dict | None, int, list[str]]:
    condition = spec["condition"]
    deadline_t = trigger_t + condition["deadline_s"]
    window_end = deadline_t + condition["hold_s"]
    max_gap = spec["max_gap_s"]
    warnings: list[str] = []
    relevant = [sample for sample in samples if sample["t_s"] >= trigger_t]
    examined = sum(1 for sample in relevant if sample["t_s"] <= window_end)
    if not relevant:
        return "UNKNOWN", "No observation samples are available after the trigger.", None, 0, warnings

    run_start: float | None = None
    previous_t: float | None = None
    for sample in relevant:
        t_s = sample["t_s"]
        value = sample["values"][condition["metric"]]
        if previous_t is not None and t_s - previous_t > max_gap:
            run_start = None
        if value is None:
            run_start = None
        elif _satisfies(value, condition["op"], condition["threshold"]):
            if run_start is None:
                run_start = t_s
            if run_start <= deadline_t and t_s - run_start >= condition["hold_s"]:
                return (
                    "PASS",
                    "The condition started before the deadline and remained satisfied for the required hold time.",
                    {"start_s": run_start, "end_s": t_s},
                    examined,
                    warnings,
                )
        else:
            run_start = None
        previous_t = t_s
        if t_s > window_end and run_start is None:
            break

    first_t = relevant[0]["t_s"]
    # FAIL은 트리거 순간부터 기회 구간 전체가 관측돼야 한다. 이산 표본의
    # 허용 간격은 관측된 두 표본 사이에만 적용하며, 트리거 이전 값으로
    # 첫 표본 전 구간을 메우지 않는다. PASS는 위의 존재 증명으로 독립한다.
    coverage_uncertain = first_t > trigger_t
    previous_t = None
    has_endpoint = False
    for sample in relevant:
        t_s = sample["t_s"]
        if previous_t is not None and previous_t < window_end and t_s - previous_t > max_gap:
            coverage_uncertain = True
        if t_s <= window_end and sample["values"][condition["metric"]] is None:
            coverage_uncertain = True
        if t_s >= window_end:
            if sample["values"][condition["metric"]] is None:
                coverage_uncertain = True
            has_endpoint = True
            break
        previous_t = t_s
    if not has_endpoint:
        coverage_uncertain = True
    if coverage_uncertain:
        warnings.append("The complete observation window required for a FAIL verdict is not continuously covered.")
        return "UNKNOWN", "A short video, observation gap, or missing metric prevents a confirmed violation.", None, examined, warnings
    return "FAIL", "The full observation window is covered, but no satisfying interval starts before the deadline and reaches the required hold time.", None, examined, warnings


def evaluate_contract(spec: dict, bundle: dict, approval: dict | None = None) -> dict[str, Any]:
    """계약을 평가한다. 승인 기록이 없어도 계산하지만 결과 단계는 draft다."""
    normalized_spec = validate_contract(spec)
    normalized_bundle = validate_observations(bundle)
    spec_sha = contract_digest(normalized_spec)
    observations_sha = _canonical_digest(normalized_bundle)
    stage, approval_status, normalized_approval = _validate_approval(approval, spec_sha)
    warnings: list[str] = []
    if normalized_approval is not None:
        warnings.append("The approval record marks local semantic review or an automated test; it is not human authentication or GPU execution authority.")

    capture_rejected = (
        normalized_spec["trigger"]["kind"] == "region_entry"
        and normalized_bundle["provenance"].get("capture_approved") is False
    )
    if capture_rejected:
        warnings.append(
            "Source-video capture approval is separate from user condition review. Visual evidence explicitly marked unapproved "
            "was not promoted by a model import or tracking result."
        )
        trigger_t = None
        verdict = "UNKNOWN"
        reason = "The source video is not approved for visual evidence, so region entry cannot be confirmed."
        interval = None
        examined = len(normalized_bundle["samples"])
    else:
        trigger_t, trigger_error = _find_trigger(normalized_spec, normalized_bundle["samples"], warnings)
    if not capture_rejected and trigger_t is None:
        verdict, reason, interval, examined = "UNKNOWN", trigger_error, None, len(normalized_bundle["samples"])
    elif not capture_rejected:
        verdict, reason, interval, examined, condition_warnings = _evaluate_condition(
            normalized_spec, normalized_bundle["samples"], trigger_t
        )
        warnings.extend(condition_warnings)
    result = {
        "stage": stage,
        "approval_status": approval_status,
        "verdict": verdict,
        "reason": reason,
        "trigger_t_s": trigger_t,
        "interval": interval,
        "examined_samples": examined,
        "warnings": warnings,
        "spec_sha256": spec_sha,
        "observations_sha256": observations_sha,
    }
    if normalized_approval is not None:
        result["approval"] = normalized_approval
    return result
