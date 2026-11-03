"""행동 계약과 오프라인 관측 묶음의 폐쇄형 스키마 검증."""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any


CONTRACT_SCHEMA = "behavior-contract-v1"
OBSERVATIONS_SCHEMA = "behavior-observations-v1"
METRICS = (
    "horizontal_speed_mps",
    "command_speed_mps",
    "base_height_m",
    "up_axis_z",
)
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_METADATA_KEYS = {
    "callable", "code", "command", "eval", "exec", "executable", "python", "script", "shell",
}
_METRIC_BOUNDS = {
    "horizontal_speed_mps": (0.0, 100.0),
    "command_speed_mps": (0.0, 100.0),
    "base_height_m": (0.0, 100.0),
    "up_axis_z": (-1.0, 1.0),
}


class BehaviorSchemaError(ValueError):
    """행동 계약 또는 관측 묶음이 폐쇄형 스키마를 어겼다."""


def _require_dict(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise BehaviorSchemaError(f"{label}은 객체여야 합니다")
    return value


def _exact_keys(value: dict, required: set[str], label: str, optional: set[str] | None = None) -> None:
    optional = optional or set()
    missing = required - value.keys()
    extra = value.keys() - required - optional
    if missing:
        raise BehaviorSchemaError(f"{label}에 필수 필드가 없습니다: {sorted(missing)}")
    if extra:
        raise BehaviorSchemaError(f"{label}에 허용되지 않은 필드가 있습니다: {sorted(extra)}")


def _text(value: Any, label: str, *, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise BehaviorSchemaError(f"{label}은 문자열이어야 합니다")
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise BehaviorSchemaError(f"{label}은 비어 있을 수 없습니다")
    if len(normalized) > maximum:
        raise BehaviorSchemaError(f"{label}이 너무 깁니다")
    return normalized


def _number(value: Any, label: str, *, minimum: float | None = None,
            maximum: float | None = None, strictly_positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BehaviorSchemaError(f"{label}은 숫자여야 합니다")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise BehaviorSchemaError(f"{label}은 유한한 숫자여야 합니다")
    if strictly_positive and normalized <= 0:
        raise BehaviorSchemaError(f"{label}은 0보다 커야 합니다")
    if minimum is not None and normalized < minimum:
        raise BehaviorSchemaError(f"{label}은 {minimum} 이상이어야 합니다")
    if maximum is not None and normalized > maximum:
        raise BehaviorSchemaError(f"{label}은 {maximum} 이하여야 합니다")
    return normalized


def _metadata(value: Any, label: str, *, depth: int = 0, budget: list[int] | None = None) -> Any:
    if budget is None:
        budget = [5000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 8:
        raise BehaviorSchemaError(f"{label} 메타데이터가 너무 큽니다")
    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str) and len(value) > 10_000:
            raise BehaviorSchemaError(f"{label} 문자열이 너무 깁니다")
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _number(value, label)
    if isinstance(value, list):
        return [_metadata(item, label, depth=depth + 1, budget=budget) for item in value]
    if isinstance(value, dict):
        normalized = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 200:
                raise BehaviorSchemaError(f"{label}의 키는 짧고 비어 있지 않은 문자열이어야 합니다")
            if key.casefold() in _FORBIDDEN_METADATA_KEYS:
                raise BehaviorSchemaError(f"{label}에는 실행 가능한 필드 {key!r}를 넣을 수 없습니다")
            normalized[key] = _metadata(item, f"{label}.{key}", depth=depth + 1, budget=budget)
        return normalized
    raise BehaviorSchemaError(f"{label}에는 JSON 메타데이터만 넣을 수 있습니다")


def _bbox(value: Any, label: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 4:
        raise BehaviorSchemaError(f"{label}은 좌표 4개여야 합니다")
    box = [_number(item, label, minimum=0.0, maximum=1.0) for item in value]
    if box[0] >= box[2] or box[1] >= box[3]:
        raise BehaviorSchemaError(f"{label}은 x1 < x2, y1 < y2여야 합니다")
    return box


def validate_contract(spec: dict) -> dict:
    """행동 계약을 검증하고 숫자와 문자열 표현을 정규화한다."""
    spec = _require_dict(spec, "contract")
    _exact_keys(spec, {"schema", "title", "instruction", "trigger", "condition", "max_gap_s", "source"},
                "contract")
    if spec["schema"] != CONTRACT_SCHEMA:
        raise BehaviorSchemaError(f"schema는 {CONTRACT_SCHEMA!r}여야 합니다")

    trigger = _require_dict(spec["trigger"], "trigger")
    _exact_keys(trigger, {"kind", "at_s", "region", "object_id"}, "trigger")
    kind = trigger["kind"]
    if kind not in {"time", "region_entry"}:
        raise BehaviorSchemaError("trigger.kind는 time 또는 region_entry여야 합니다")
    if kind == "time":
        if trigger["region"] is not None or trigger["object_id"] is not None:
            raise BehaviorSchemaError("time 트리거의 region과 object_id는 null이어야 합니다")
        normalized_trigger = {
            "kind": kind,
            "at_s": _number(trigger["at_s"], "trigger.at_s", minimum=0.0, maximum=1_000_000_000.0),
            "region": None,
            "object_id": None,
        }
    else:
        if trigger["at_s"] is not None:
            raise BehaviorSchemaError("region_entry 트리거의 at_s는 null이어야 합니다")
        normalized_trigger = {
            "kind": kind,
            "at_s": None,
            "region": _bbox(trigger["region"], "trigger.region"),
            "object_id": _text(trigger["object_id"], "trigger.object_id", maximum=200),
        }

    condition = _require_dict(spec["condition"], "condition")
    _exact_keys(condition, {"metric", "op", "threshold", "deadline_s", "hold_s"}, "condition")
    metric = condition["metric"]
    if metric not in METRICS:
        raise BehaviorSchemaError(f"condition.metric은 {list(METRICS)} 중 하나여야 합니다")
    if condition["op"] not in {"gte", "lte"}:
        raise BehaviorSchemaError("condition.op는 gte 또는 lte여야 합니다")
    low, high = _METRIC_BOUNDS[metric]
    normalized_condition = {
        "metric": metric,
        "op": condition["op"],
        "threshold": _number(condition["threshold"], "condition.threshold", minimum=low, maximum=high),
        "deadline_s": _number(condition["deadline_s"], "condition.deadline_s", minimum=0.0,
                              maximum=86_400.0),
        "hold_s": _number(condition["hold_s"], "condition.hold_s", maximum=86_400.0,
                          strictly_positive=True),
    }
    return {
        "schema": CONTRACT_SCHEMA,
        "title": _text(spec["title"], "title", maximum=200),
        "instruction": _text(spec["instruction"], "instruction", maximum=4000),
        "trigger": normalized_trigger,
        "condition": normalized_condition,
        "max_gap_s": _number(spec["max_gap_s"], "max_gap_s", maximum=86_400.0, strictly_positive=True),
        "source": _metadata(_require_dict(spec["source"], "source"), "source"),
    }


def validate_observations(bundle: dict) -> dict:
    """프레임 순서와 단위 범위를 포함해 오프라인 관측 묶음을 검증한다."""
    bundle = _require_dict(bundle, "observations")
    _exact_keys(bundle, {"schema", "clip_id", "video_sha256", "provenance", "samples"}, "observations")
    if bundle["schema"] != OBSERVATIONS_SCHEMA:
        raise BehaviorSchemaError(f"schema는 {OBSERVATIONS_SCHEMA!r}여야 합니다")
    digest = bundle["video_sha256"]
    if not isinstance(digest, str) or not _HEX_64.fullmatch(digest):
        raise BehaviorSchemaError("video_sha256은 소문자 64자리 SHA-256이어야 합니다")
    raw_samples = bundle["samples"]
    if not isinstance(raw_samples, list) or len(raw_samples) > 100_000:
        raise BehaviorSchemaError("samples는 최대 100000개인 배열이어야 합니다")

    samples = []
    previous_frame = -1
    previous_t = -math.inf
    for index, raw in enumerate(raw_samples):
        sample = _require_dict(raw, f"samples[{index}]")
        _exact_keys(sample, {"frame", "t_s", "values", "tracks"}, f"samples[{index}]")
        frame = sample["frame"]
        if isinstance(frame, bool) or not isinstance(frame, int) or frame < 0:
            raise BehaviorSchemaError(f"samples[{index}].frame은 0 이상의 정수여야 합니다")
        if frame <= previous_frame:
            raise BehaviorSchemaError("frame은 중복 없이 엄격히 증가해야 합니다")
        t_s = _number(sample["t_s"], f"samples[{index}].t_s", minimum=0.0,
                      maximum=1_000_000_000.0)
        if t_s <= previous_t:
            raise BehaviorSchemaError("t_s는 엄격히 증가해야 합니다")

        raw_values = _require_dict(sample["values"], f"samples[{index}].values")
        extra_metrics = raw_values.keys() - set(METRICS)
        if extra_metrics:
            raise BehaviorSchemaError(f"values에 허용되지 않은 지표가 있습니다: {sorted(extra_metrics)}")
        values = {}
        for metric in METRICS:
            value = raw_values.get(metric)
            if value is None:
                values[metric] = None
            else:
                low, high = _METRIC_BOUNDS[metric]
                values[metric] = _number(value, f"samples[{index}].values.{metric}", minimum=low, maximum=high)

        raw_tracks = sample["tracks"]
        if not isinstance(raw_tracks, list) or len(raw_tracks) > 16:
            raise BehaviorSchemaError(f"samples[{index}].tracks는 최대 16개인 배열이어야 합니다")
        tracks = []
        object_ids = set()
        for track_index, raw_track in enumerate(raw_tracks):
            track = _require_dict(raw_track, f"samples[{index}].tracks[{track_index}]")
            _exact_keys(track, {"object_id", "bbox"}, f"samples[{index}].tracks[{track_index}]")
            object_id = _text(track["object_id"], "track.object_id", maximum=200)
            if object_id in object_ids:
                raise BehaviorSchemaError(f"samples[{index}]에 object_id {object_id!r}가 중복됩니다")
            object_ids.add(object_id)
            tracks.append({"object_id": object_id, "bbox": _bbox(track["bbox"], "track.bbox")})

        samples.append({"frame": frame, "t_s": t_s, "values": values, "tracks": tracks})
        previous_frame = frame
        previous_t = t_s

    return {
        "schema": OBSERVATIONS_SCHEMA,
        "clip_id": _text(bundle["clip_id"], "clip_id", maximum=500),
        "video_sha256": digest,
        "provenance": _metadata(_require_dict(bundle["provenance"], "provenance"), "provenance"),
        "samples": samples,
    }


def _canonical_digest(value: dict) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def contract_digest(spec: dict) -> str:
    """검증하고 정규화한 계약의 canonical SHA-256을 반환한다."""
    return _canonical_digest(validate_contract(spec))


def observations_digest(bundle: dict) -> str:
    """검증하고 정규화한 관측 묶음의 canonical SHA-256을 반환한다."""
    return _canonical_digest(validate_observations(bundle))
