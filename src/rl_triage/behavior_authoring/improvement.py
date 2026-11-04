"""Muse 제안과 저장 기록 검사를 분리하는 제한된 개선 가설 루프."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re

from .adapters import _CONTRACT_SCHEMA
from .evaluate import evaluate_contract
from .schema import BehaviorSchemaError, validate_contract, validate_observations


ANALYSIS_SCHEMA = "meta-improvement-analysis-v1"
ROUND_SCHEMA = "meta-improvement-round-v1"
CORRECTIONS = ("none", "restore_body_frame_reward", "restore_reward_family_scope")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise BehaviorSchemaError(f"{label}은 비어 있지 않은 {maximum}자 이하 문자열이어야 합니다")
    return value.strip()


def _exact(value: object, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise BehaviorSchemaError(f"{label} 필드는 {sorted(keys)}만 허용됩니다")
    return value


def analysis_schema() -> dict:
    """Spark 구조화 출력에 사용하는 폐쇄형 개선 분석 스키마를 반환한다."""
    contract = deepcopy(_CONTRACT_SCHEMA)
    hypothesis = {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "claim", "evidence_frames", "contract", "correction_id", "rationale"],
        "properties": {
            "id": {"type": "string", "pattern": _ID.pattern},
            "claim": {"type": "string", "minLength": 1},
            "evidence_frames": {
                "type": "array", "items": {"type": "integer", "minimum": 0},
                "minItems": 1, "maxItems": 64, "uniqueItems": True,
            },
            "contract": contract,
            "correction_id": {"type": "string", "enum": list(CORRECTIONS)},
            "rationale": {"type": "string", "minLength": 1},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "hypotheses", "limitations"],
        "properties": {
            "summary": {"type": "string", "minLength": 1},
            "hypotheses": {"type": "array", "items": hypothesis, "minItems": 1, "maxItems": 4},
            "limitations": {
                "type": "array", "items": {"type": "string", "minLength": 1},
                "maxItems": 16,
            },
        },
    }


def validate_analysis(value: dict, bundle: dict) -> dict:
    """모델 분석을 원본 프레임과 행동 계약에 묶고 실행 권한을 배제한다."""
    observations = validate_observations(bundle)
    value = _exact(value, {"summary", "hypotheses", "limitations"}, "analysis")
    raw_hypotheses = value["hypotheses"]
    if not isinstance(raw_hypotheses, list) or not 1 <= len(raw_hypotheses) <= 4:
        raise BehaviorSchemaError("hypotheses는 1~4개여야 합니다")
    limitations = value["limitations"]
    if not isinstance(limitations, list) or len(limitations) > 16:
        raise BehaviorSchemaError("limitations는 최대 16개인 배열이어야 합니다")
    normalized_limitations = [_text(item, f"limitations[{index}]", 1000)
                              for index, item in enumerate(limitations)]
    available_frames = {sample["frame"] for sample in observations["samples"]}
    ids: set[str] = set()
    hypotheses = []
    for index, raw in enumerate(raw_hypotheses):
        item = _exact(raw, {"id", "claim", "evidence_frames", "contract", "correction_id", "rationale"},
                      f"hypotheses[{index}]")
        identifier = item["id"]
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
            raise BehaviorSchemaError(f"hypotheses[{index}].id 형식이 올바르지 않습니다")
        if identifier in ids:
            raise BehaviorSchemaError("hypothesis id는 중복될 수 없습니다")
        ids.add(identifier)
        frames = item["evidence_frames"]
        if (not isinstance(frames, list) or not 1 <= len(frames) <= 64
                or any(isinstance(frame, bool) or not isinstance(frame, int) for frame in frames)
                or len(set(frames)) != len(frames)):
            raise BehaviorSchemaError("evidence_frames는 중복 없는 프레임 번호 배열이어야 합니다")
        if any(frame not in available_frames for frame in frames):
            raise BehaviorSchemaError("evidence_frames가 현재 원본 관측 프레임과 일치하지 않습니다")
        correction = item["correction_id"]
        if correction not in CORRECTIONS:
            raise BehaviorSchemaError("등록되지 않은 correction_id입니다")
        raw_contract = item["contract"]
        if not isinstance(raw_contract, dict) or "source" in raw_contract:
            raise BehaviorSchemaError("모델 contract에는 source나 실행 권한을 넣을 수 없습니다")
        contract = validate_contract({
            **deepcopy(raw_contract),
            "source": {
                "kind": "meta_visual_hypothesis_proposal",
                "provider_authentication": "unverified_analysis_input",
                "model_called": False,
                "execution_authorized": False,
            },
        })
        hypotheses.append({
            "id": identifier,
            "claim": _text(item["claim"], f"hypotheses[{index}].claim", 2000),
            "evidence_frames": list(frames),
            "contract": contract,
            "correction_id": correction,
            "rationale": _text(item["rationale"], f"hypotheses[{index}].rationale", 4000),
        })
    return {
        "schema": ANALYSIS_SCHEMA,
        "summary": _text(value["summary"], "summary", 4000),
        "hypotheses": hypotheses,
        "limitations": normalized_limitations,
    }


def _entropy(hypothesis: dict, observations: dict) -> float:
    condition = hypothesis["contract"]["condition"]
    values = [sample["values"][condition["metric"]] for sample in observations["samples"]]
    known = [value for value in values if value is not None]
    if not known:
        return 0.0
    positives = sum(value >= condition["threshold"] if condition["op"] == "gte"
                    else value <= condition["threshold"] for value in known)
    if positives in {0, len(known)}:
        return 0.0
    probability = positives / len(known)
    return -(probability * math.log2(probability) + (1 - probability) * math.log2(1 - probability))


def _validate_history(history: list[dict], analysis: dict, observations: dict, maximum: int) -> list[dict]:
    if not isinstance(history, list) or len(history) > maximum:
        raise BehaviorSchemaError("history가 라운드 상한을 초과했습니다")
    analysis_sha = _digest(analysis)
    observations_sha = _digest(observations)
    hypotheses = {item["id"]: item for item in analysis["hypotheses"]}
    normalized = []
    selected: set[str] = set()
    for index, raw in enumerate(history):
        entry = _exact(raw, {"round", "analysis_sha256", "observations_sha256", "selected_hypothesis_id",
                            "result", "information_gain_bits"}, f"history[{index}]")
        if entry["round"] != index + 1:
            raise BehaviorSchemaError("history round가 연속적이지 않습니다")
        if entry["analysis_sha256"] != analysis_sha or entry["observations_sha256"] != observations_sha:
            raise BehaviorSchemaError("history가 현재 분석과 관측에 묶여 있지 않습니다")
        identifier = entry["selected_hypothesis_id"]
        if identifier not in hypotheses or identifier in selected:
            raise BehaviorSchemaError("history의 가설 선택이 유효하지 않습니다")
        selected.add(identifier)
        expected = evaluate_contract(hypotheses[identifier]["contract"], observations)
        expected["evidence_scope"] = "local_recorded_check_not_causal_experiment"
        if entry["result"] != expected:
            raise BehaviorSchemaError("history 판정이 현재 원본 재계산과 일치하지 않습니다")
        entropy = _entropy(hypotheses[identifier], observations)
        gain = entry["information_gain_bits"]
        if isinstance(gain, bool) or not isinstance(gain, (int, float)) or not math.isfinite(gain):
            raise BehaviorSchemaError("history information_gain_bits가 유효하지 않습니다")
        if abs(float(gain) - entropy) > 1e-12:
            raise BehaviorSchemaError("history 정보량이 현재 관측 재계산과 일치하지 않습니다")
        normalized.append(deepcopy(entry))
    return normalized


def run_round(analysis: dict, bundle: dict, history: list[dict] | None = None, *, max_rounds: int = 3) -> dict:
    """저장 기록에서 정보량이 큰 미검사 가설 하나만 계산한다."""
    if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or not 1 <= max_rounds <= 3:
        raise BehaviorSchemaError("max_rounds는 1~3의 정수여야 합니다")
    observations = validate_observations(bundle)
    normalized_analysis = validate_analysis(_strip_analysis_schema(analysis), observations)
    normalized_history = _validate_history(history or [], normalized_analysis, observations, max_rounds)
    tested = {entry["selected_hypothesis_id"] for entry in normalized_history}
    available = [item for item in normalized_analysis["hypotheses"] if item["id"] not in tested]
    analysis_sha = _digest(normalized_analysis)
    observations_sha = _digest(observations)
    if len(normalized_history) >= max_rounds:
        return _round_result(normalized_analysis, normalized_history, None, "round_budget_reached", True)
    if not available:
        return _round_result(normalized_analysis, normalized_history, None, "all_hypotheses_checked", True)
    selected = sorted(available, key=lambda item: (-_entropy(item, observations), item["id"]))[0]
    gain = _entropy(selected, observations)
    result = evaluate_contract(selected["contract"], observations)
    result["evidence_scope"] = "local_recorded_check_not_causal_experiment"
    entry = {
        "round": len(normalized_history) + 1,
        "analysis_sha256": analysis_sha,
        "observations_sha256": observations_sha,
        "selected_hypothesis_id": selected["id"],
        "result": result,
        "information_gain_bits": gain,
    }
    updated = normalized_history + [entry]
    stop = "all_hypotheses_checked" if len(updated) == len(normalized_analysis["hypotheses"]) else "recorded_check_complete"
    if len(updated) >= max_rounds and stop != "all_hypotheses_checked":
        stop = "round_budget_reached"
    return _round_result(normalized_analysis, updated, selected["id"], stop, True)


def _strip_analysis_schema(value: dict) -> dict:
    if isinstance(value, dict) and set(value) == {"schema", "summary", "hypotheses", "limitations"}:
        if value["schema"] != ANALYSIS_SCHEMA:
            raise BehaviorSchemaError("analysis schema가 올바르지 않습니다")
        stripped = deepcopy(value)
        stripped.pop("schema")
        if not isinstance(stripped["hypotheses"], list):
            raise BehaviorSchemaError("정규화된 analysis의 hypotheses는 배열이어야 합니다")
        for hypothesis in stripped["hypotheses"]:
            if not isinstance(hypothesis, dict) or not isinstance(hypothesis.get("contract"), dict):
                raise BehaviorSchemaError("정규화된 analysis의 hypothesis 형식이 올바르지 않습니다")
            source = hypothesis["contract"].pop("source", None)
            if source != {
                "kind": "meta_visual_hypothesis_proposal",
                "provider_authentication": "unverified_analysis_input",
                "model_called": False,
                "execution_authorized": False,
            }:
                raise BehaviorSchemaError("정규화된 analysis의 출처 또는 실행 권한이 변조됐습니다")
        return stripped
    return value


def _round_result(analysis: dict, history: list[dict], selected: str | None,
                  stop_reason: str, requires_new_evidence: bool) -> dict:
    return {
        "schema": ROUND_SCHEMA,
        "round": len(history),
        "analysis": analysis,
        "selected_hypothesis_id": selected,
        "experiments": ([] if selected is None else [{
            "hypothesis_id": selected,
            "result": history[-1]["result"],
            "information_gain_bits": history[-1]["information_gain_bits"],
        }]),
        "stop_reason": stop_reason,
        "requires_new_evidence": requires_new_evidence,
        "history": history,
    }


def build_reflection_context(analysis: dict, bundle: dict, history: list[dict]) -> dict:
    """다음 Spark 검토에 필요한 판정과 불확실성만 노출한다."""
    observations = validate_observations(bundle)
    normalized_analysis = validate_analysis(_strip_analysis_schema(analysis), observations)
    normalized_history = _validate_history(history, normalized_analysis, observations, 3)
    outcomes = [{
        "hypothesis_id": entry["selected_hypothesis_id"],
        "verdict": entry["result"]["verdict"],
        "reason": entry["result"]["reason"],
        "information_gain_bits": entry["information_gain_bits"],
        "evidence_scope": entry["result"]["evidence_scope"],
    } for entry in normalized_history]
    return {
        "schema": "meta-improvement-reflection-context-v1",
        "analysis_sha256": _digest(normalized_analysis),
        "observations_sha256": _digest(observations),
        "summary": normalized_analysis["summary"],
        "hypotheses": [{
            "id": item["id"], "claim": item["claim"], "correction_id": item["correction_id"],
            "evidence_frames": item["evidence_frames"],
        } for item in normalized_analysis["hypotheses"]],
        "outcomes": outcomes,
        "uncertainty": normalized_analysis["limitations"] + [
            "저장 기록 검사는 인과 실험이나 재학습 성능 측정이 아닙니다.",
            "후속 결론에는 새 실행 근거와 별도 승인 절차가 필요합니다.",
        ],
    }
