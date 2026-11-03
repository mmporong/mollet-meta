"""저장된 Meta 모델 응답을 제한된 행동 계약과 관측값으로 가져온다.

이 모듈은 요청 본문만 만들며 네트워크 전송 기능을 제공하지 않는다.
"""

from __future__ import annotations

import base64
import binascii
from copy import deepcopy
import hashlib
import json
import math
import re

from .schema import validate_contract, validate_observations


SPARK_MODEL = "muse-spark-1.3"
SAM_MODEL = "sam-3.1"
SIGNALS = (
    "horizontal_speed_mps",
    "command_speed_mps",
    "base_height_m",
    "up_axis_z",
)


class AdapterError(ValueError):
    """저장 응답 또는 요청 재료가 계약을 만족하지 않을 때 발생한다."""


_CONTRACT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "title", "instruction", "trigger", "condition", "max_gap_s"],
    "properties": {
        "schema": {"type": "string", "const": "behavior-contract-v1"},
        "title": {"type": "string"},
        "instruction": {"type": "string"},
        "trigger": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "at_s", "region", "object_id"],
            "properties": {
                "kind": {"type": "string", "enum": ["time", "region_entry"]},
                "at_s": {"type": ["number", "null"]},
                "region": {
                    "anyOf": [
                        {"type": "null"},
                        {
                            "type": "array",
                            "items": {"type": "number", "minimum": 0, "maximum": 1},
                            "minItems": 4,
                            "maxItems": 4,
                        },
                    ]
                },
                "object_id": {"type": ["string", "null"]},
            },
        },
        "condition": {
            "type": "object",
            "additionalProperties": False,
            "required": ["metric", "op", "threshold", "deadline_s", "hold_s"],
            "properties": {
                "metric": {"type": "string", "enum": list(SIGNALS)},
                "op": {"type": "string", "enum": ["gte", "lte"]},
                "threshold": {"type": "number"},
                "deadline_s": {"type": "number", "minimum": 0},
                "hold_s": {"type": "number", "exclusiveMinimum": 0},
            },
        },
        "max_gap_s": {"type": "number", "exclusiveMinimum": 0},
    },
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _nonempty_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdapterError(f"{label}은 비어 있지 않은 문자열이어야 합니다")
    return value.strip()


def build_spark_request(instruction: str, bundle: dict) -> dict:
    """Spark Responses API에 저장할 요청 본문을 만든다. 전송은 수행하지 않는다."""
    instruction = _nonempty_text(instruction, "instruction")
    validate_observations(bundle)
    prompt = (
        "다음 관측 묶음과 사용자 지시를 behavior-contract-v1 JSON 하나로 변환하세요. "
        f"사용 가능한 신호는 {', '.join(SIGNALS)}뿐입니다. "
        "조건 DSL은 metric/op/threshold/deadline_s/hold_s와 time 또는 region_entry trigger만 허용합니다. "
        "셸 명령, 코드, 함수 호출, 승인 또는 실행 지시는 만들지 마세요. source 필드는 출력하지 마세요.\n"
        f"사용자 지시: {instruction}\n"
        f"관측 묶음: {json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
    )
    return {
        "model": SPARK_MODEL,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "behavior_contract",
                "strict": True,
                "schema": deepcopy(_CONTRACT_SCHEMA),
            }
        },
    }


def build_sam_request(prompt: str, video_reference: str) -> dict:
    """SAM Responses API 요청 본문을 만든다. data URI와 업로드 파일 ID만 받는다."""
    prompt = _nonempty_text(prompt, "prompt")
    video_reference = _nonempty_text(video_reference, "video_reference")
    if video_reference.startswith("data:video/mp4;base64,"):
        encoded = video_reference.partition(",")[2]
        try:
            base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise AdapterError("video_reference의 MP4 data URI가 올바른 base64가 아닙니다") from exc
        video = {"type": "input_video", "video_url": video_reference}
    elif re.fullmatch(r"file[-_][A-Za-z0-9._:-]{1,250}", video_reference):
        video = {"type": "input_video", "file_id": video_reference}
    else:
        raise AdapterError("video_reference는 MP4 data URI 또는 provider file_id여야 합니다")
    return {
        "model": SAM_MODEL,
        "input": [{
            "role": "user",
            "content": [{"type": "input_text", "text": prompt}, video],
        }],
        "stream": True,
    }


def _check_model(model: object, expected: str) -> None:
    if model is not None and model != expected:
        raise AdapterError(f"응답 모델이 {expected!r}가 아닙니다")


def _response_text_nonstream(response: dict, expected_model: str) -> str:
    _check_model(response.get("model"), expected_model)
    if response.get("status") != "completed":
        raise AdapterError("provider 응답의 최종 status가 completed가 아닙니다")
    output = response.get("output")
    if not isinstance(output, list):
        raise AdapterError("provider 응답 output이 배열이 아닙니다")
    texts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            raise AdapterError("provider 응답 output 항목이 객체가 아닙니다")
        content = item.get("content", [])
        if not isinstance(content, list):
            raise AdapterError("provider 응답 content가 배열이 아닙니다")
        for part in content:
            if not isinstance(part, dict):
                raise AdapterError("provider 응답 content 항목이 객체가 아닙니다")
            if part.get("type") == "refusal" or "refusal" in part:
                raise AdapterError("거절 응답은 가져올 수 없습니다")
            if part.get("type") == "output_text":
                if item.get("type") != "message" or item.get("role") != "assistant":
                    raise AdapterError("output_text는 assistant message에만 있어야 합니다")
                if not isinstance(part.get("text"), str):
                    raise AdapterError("output_text.text가 문자열이 아닙니다")
                texts.append(part["text"])
    if len(texts) != 1:
        raise AdapterError("provider 응답에는 output_text가 정확히 하나 있어야 합니다")
    return texts[0]


def _sse_events(raw: str) -> list[dict]:
    events: list[dict] = []
    data_lines: list[str] = []

    def flush() -> None:
        if not data_lines:
            return
        payload = "\n".join(data_lines)
        data_lines.clear()
        if payload == "[DONE]":
            return
        try:
            event = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise AdapterError("저장된 SSE data가 JSON이 아닙니다") from exc
        if not isinstance(event, dict):
            raise AdapterError("저장된 SSE event는 JSON 객체여야 합니다")
        events.append(event)

    for line in raw.splitlines():
        if line == "":
            flush()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
        elif line.startswith(":") or line.startswith(("event:", "id:", "retry:")):
            continue
        elif line.strip():
            raise AdapterError("저장된 문자열은 JSON 또는 올바른 SSE여야 합니다")
    flush()
    if not events:
        raise AdapterError("SSE에 provider event가 없습니다")
    return events


def _lane(event: dict) -> tuple[object, object, object]:
    return (event.get("item_id"), event.get("output_index"), event.get("content_index"))


def _response_text_sse(raw: str, expected_model: str) -> str:
    deltas: dict[tuple[object, object, object], list[str]] = {}
    done: dict[tuple[object, object, object], str] = {}
    completed = False
    final_response = None
    for event in _sse_events(raw):
        event_type = event.get("type")
        if not isinstance(event_type, str):
            raise AdapterError("SSE event type이 없습니다")
        nested_response = event.get("response")
        if nested_response is not None:
            if not isinstance(nested_response, dict):
                raise AdapterError("SSE event의 response가 객체가 아닙니다")
            _check_model(nested_response.get("model"), expected_model)
        if "refusal" in event_type or event_type in {"response.failed", "response.incomplete", "error"}:
            raise AdapterError("실패·미완료·거절 SSE는 가져올 수 없습니다")
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if not isinstance(delta, str):
                raise AdapterError("output_text delta가 문자열이 아닙니다")
            deltas.setdefault(_lane(event), []).append(delta)
        elif event_type == "response.output_text.done":
            text = event.get("text")
            if not isinstance(text, str):
                raise AdapterError("output_text done.text가 문자열이 아닙니다")
            key = _lane(event)
            if key in done and done[key] != text:
                raise AdapterError("같은 output_text 레인의 완료 값이 서로 다릅니다")
            done[key] = text
        elif event_type == "response.content_part.done":
            part = event.get("part")
            if isinstance(part, dict) and part.get("type") == "refusal":
                raise AdapterError("거절 SSE는 가져올 수 없습니다")
            if isinstance(part, dict) and part.get("type") == "output_text":
                text = part.get("text")
                if not isinstance(text, str):
                    raise AdapterError("완료된 output_text가 문자열이 아닙니다")
                key = _lane(event)
                if key in done and done[key] != text:
                    raise AdapterError("output_text 완료 값이 서로 다릅니다")
                done[key] = text
        elif event_type == "response.completed":
            if nested_response is None or nested_response.get("status") != "completed":
                raise AdapterError("completed event의 최종 응답 상태가 completed가 아닙니다")
            completed = True
            final_response = nested_response
    if not completed:
        raise AdapterError("response.completed SSE event가 없습니다")
    lanes = set(deltas) | set(done)
    if expected_model == SAM_MODEL and not lanes and final_response is not None:
        # SAM의 탐지 대상 0개 응답은 delta 없이 완료된 빈 output_text를 반환할 수 있다.
        if _response_text_nonstream(final_response, expected_model) == "":
            return ""
    if len(lanes) != 1 or len(done) != 1:
        raise AdapterError("완료된 output_text 레인이 정확히 하나여야 합니다")
    key = next(iter(lanes))
    if key in deltas and "".join(deltas[key]) != done[key]:
        raise AdapterError("output_text delta 결합값과 done.text가 다릅니다")
    return done[key]


def _saved_response(response: dict | str, expected_model: str) -> tuple[str, str]:
    if isinstance(response, dict):
        raw = _canonical_bytes(response)
        return _response_text_nonstream(response, expected_model), _sha256(raw)
    if not isinstance(response, str):
        raise AdapterError("response는 저장된 JSON 객체 또는 JSON/SSE 문자열이어야 합니다")
    digest = _sha256(response.encode("utf-8"))
    stripped = response.strip()
    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise AdapterError("저장된 response JSON을 해석할 수 없습니다") from exc
        if not isinstance(parsed, dict):
            raise AdapterError("저장된 response JSON은 객체여야 합니다")
        return _response_text_nonstream(parsed, expected_model), digest
    if "data:" not in stripped:
        raise AdapterError("원시 모델 텍스트는 provider 완료 응답으로 간주하지 않습니다")
    return _response_text_sse(response, expected_model), digest


def import_spark_response(response: dict | str, bundle: dict) -> dict:
    """완료된 저장 Spark 응답에서 행동 계약 하나를 가져온다."""
    validate_observations(bundle)
    text, response_sha = _saved_response(response, SPARK_MODEL)
    try:
        contract = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AdapterError("Spark output_text는 엄격한 JSON 객체여야 합니다") from exc
    if not isinstance(contract, dict):
        raise AdapterError("Spark output_text는 JSON 객체여야 합니다")
    forbidden = {"source", "approval", "execution", "execute", "command"} & set(contract)
    if forbidden:
        raise AdapterError(f"Spark 출력에 허용되지 않은 필드가 있습니다: {sorted(forbidden)}")
    contract["source"] = {
        "kind": "imported_spark_response",
        "response_sha256": response_sha,
        "provider_authentication": "unverified_saved_response",
    }
    return validate_contract(contract)


_FRAME_RE = re.compile(r"^<(\d+)f>(.*)$", re.DOTALL)
_OBJECT_RE = re.compile(r"(\d+)<\|box;")
_NUMBER_RE = r"-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


def _parse_box(body: str) -> tuple[int, int, int, int, int, int]:
    pattern = re.compile(
        rf"^x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)"
        rf"(?:;c=({_NUMBER_RE}))?$"
    )
    match = pattern.fullmatch(body)
    if not match:
        raise AdapterError("SAM box token 형식이 올바르지 않습니다")
    x1, y1, x2, y2, width, height = map(int, match.groups()[:6])
    confidence = match.group(7)
    if confidence is not None and not math.isfinite(float(confidence)):
        raise AdapterError("SAM box confidence가 유한수가 아닙니다")
    if width <= 0 or height <= 0 or x1 > x2 or y1 > y2 or x2 >= width or y2 >= height:
        raise AdapterError("SAM box 좌표·크기가 영상 경계를 벗어났습니다")
    return x1, y1, x2, y2, width, height


def _parse_mask(body: str) -> str:
    match = re.fullmatch(rf"x=0;y=0(?:;c={_NUMBER_RE})?;data=(.+)", body, re.DOTALL)
    if not match or not match.group(1) or any(not char.isprintable() for char in match.group(1)):
        raise AdapterError("SAM mask token 형식이 올바르지 않습니다")
    return _sha256(("<|mask;" + body + "|>").encode("utf-8"))


def _parse_sam_text(text: str, valid_frames: set[int]) -> tuple[dict[int, list[dict]], dict[str, str], tuple[int, int] | None]:
    tracks: dict[int, list[dict]] = {}
    mask_hashes: dict[str, str] = {}
    dimensions: tuple[int, int] | None = None
    if not text:
        return tracks, mask_hashes, dimensions
    for line in text.splitlines():
        if not line:
            raise AdapterError("SAM output_text에 빈 프레임 행이 있습니다")
        frame_match = _FRAME_RE.fullmatch(line)
        if not frame_match:
            raise AdapterError("SAM 프레임 토큰 형식이 올바르지 않습니다")
        frame = int(frame_match.group(1))
        if frame not in valid_frames:
            raise AdapterError("SAM 응답 프레임이 관측 묶음에 없습니다")
        if frame in tracks:
            raise AdapterError("SAM 응답에 중복 프레임이 있습니다")
        rest = frame_match.group(2)
        if not rest:
            raise AdapterError("SAM 프레임 행에 객체가 없습니다")
        rows: list[dict] = []
        seen_objects: set[str] = set()
        pos = 0
        while pos < len(rest):
            object_match = _OBJECT_RE.match(rest, pos)
            if not object_match:
                raise AdapterError("SAM 객체 토큰 형식이 올바르지 않습니다")
            object_id = object_match.group(1)
            if object_id in seen_objects:
                raise AdapterError("같은 프레임에 중복 object_id가 있습니다")
            seen_objects.add(object_id)
            box_start = object_match.end()
            separator = rest.find("|><|mask;", box_start)
            if separator < 0:
                raise AdapterError("SAM box 뒤에 mask token이 없습니다")
            x1, y1, x2, y2, width, height = _parse_box(rest[box_start:separator])
            current_dimensions = (width, height)
            if dimensions is None:
                dimensions = current_dimensions
            elif dimensions != current_dimensions:
                raise AdapterError("SAM 응답의 프레임 크기가 서로 다릅니다")
            mask_start = separator + len("|><|mask;")
            mask_end = rest.find("|>", mask_start)
            if mask_end < 0:
                raise AdapterError("SAM mask token이 끝나지 않았습니다")
            mask_hash = _parse_mask(rest[mask_start:mask_end])
            mask_hashes[f"{frame}:{object_id}"] = mask_hash
            rows.append({
                "object_id": object_id,
                "bbox": [x1 / width, y1 / height, (x2 + 1) / width, (y2 + 1) / height],
            })
            pos = mask_end + 2
            if pos < len(rest):
                if rest[pos] != ",":
                    raise AdapterError("SAM 객체 토큰 사이는 쉼표로 구분해야 합니다")
                pos += 1
                if pos == len(rest):
                    raise AdapterError("SAM 프레임 행 끝에 불필요한 쉼표가 있습니다")
        if len(rows) > 16:
            raise AdapterError("프레임당 SAM 객체는 16개를 넘을 수 없습니다")
        tracks[frame] = rows
    return tracks, mask_hashes, dimensions


def import_sam_response(response: dict | str, bundle: dict) -> dict:
    """완료된 저장 SAM 응답의 box만 관측 묶음에 결합한다."""
    validate_observations(bundle)
    text, response_sha = _saved_response(response, SAM_MODEL)
    samples = bundle["samples"]
    valid_frames = {sample["frame"] for sample in samples}
    parsed, mask_hashes, dimensions = _parse_sam_text(text, valid_frames)
    input_snapshot = deepcopy(bundle)
    original_provenance = deepcopy(bundle["provenance"])
    expected_width = original_provenance.get("frame_width")
    expected_height = original_provenance.get("frame_height")
    # provenance 숫자는 최종 스키마 검증에서 float로 정규화된다. 크기 비교에만
    # 정수 의미를 확인해 int로 바꾸며 반환 표현은 스키마 정규화 결과를 따른다.
    if (expected_width is None) != (expected_height is None):
        raise AdapterError("원본 provenance의 frame_width와 frame_height는 함께 있어야 합니다")
    if expected_width is not None:
        if (
            isinstance(expected_width, bool)
            or not isinstance(expected_width, (int, float))
            or not math.isfinite(expected_width)
            or expected_width <= 0
            or not float(expected_width).is_integer()
            or isinstance(expected_height, bool)
            or not isinstance(expected_height, (int, float))
            or not math.isfinite(expected_height)
            or expected_height <= 0
            or not float(expected_height).is_integer()
        ):
            raise AdapterError("원본 provenance의 프레임 크기는 양의 정수여야 합니다")
        expected_dimensions = (int(expected_width), int(expected_height))
        if dimensions is not None and dimensions != expected_dimensions:
            raise AdapterError("SAM 응답의 프레임 크기가 원본 provenance와 다릅니다")
    if "sam_import" in original_provenance:
        raise AdapterError("원본 provenance에 예약된 sam_import 필드가 이미 있습니다")
    result = deepcopy(bundle)
    result["samples"] = []
    for sample in sorted(samples, key=lambda row: row["frame"]):
        updated = deepcopy(sample)
        updated["tracks"] = deepcopy(parsed.get(sample["frame"], []))
        result["samples"].append(updated)
    original_provenance["sam_import"] = {
        "kind": "imported_api_response_unverified_provider",
        "model_requested": SAM_MODEL,
        "response_sha256": response_sha,
        "provider_authentication": "unverified_saved_response",
        "video_binding": {
            "video_sha256": bundle["video_sha256"],
            "authentication": "claimed_binding_only",
            "selected_by": "caller_bundle",
        },
        "input_bundle_sha256": _sha256(_canonical_bytes(input_snapshot)),
        "input_provenance_snapshot": deepcopy(bundle["provenance"]),
        "sam_object_id_scope": "single_response_visible_track_only",
        "mask_handling": "raw_token_sha256_only_no_pixel_decode",
        "mask_token_sha256": mask_hashes,
        "source_dimensions_px": None if dimensions is None else list(dimensions),
    }
    result["provenance"] = original_provenance
    return validate_observations(result)


__all__ = [
    "build_spark_request",
    "build_sam_request",
    "import_spark_response",
    "import_sam_response",
]
