import copy
import json

import pytest

from rl_triage.behavior_authoring.adapters import (
    AdapterError,
    build_sam_request,
    build_spark_request,
    import_sam_response,
    import_spark_response,
)
from rl_triage.behavior_authoring.evaluate import evaluate_contract
from rl_triage.behavior_authoring.schema import validate_observations


def observations():
    return {
        "schema": "behavior-observations-v1",
        "clip_id": "clip-a",
        "video_sha256": "a" * 64,
        "provenance": {"kind": "fixture", "nested": {"revision": 1}},
        "samples": [
            {
                "frame": 2,
                "t_s": 0.17,
                "values": {
                    "horizontal_speed_mps": 0.4,
                    "command_speed_mps": 0.5,
                    "base_height_m": 0.31,
                    "up_axis_z": 0.99,
                },
                "tracks": [],
            },
            {
                "frame": 7,
                "t_s": 0.91,
                "values": {
                    "horizontal_speed_mps": 0.2,
                    "command_speed_mps": 0.5,
                    "base_height_m": 0.30,
                    "up_axis_z": 0.97,
                },
                "tracks": [],
            },
            {
                "frame": 20,
                "t_s": 3.2,
                "values": {
                    "horizontal_speed_mps": 0.0,
                    "command_speed_mps": 0.0,
                    "base_height_m": 0.28,
                    "up_axis_z": 0.9,
                },
                "tracks": [],
            },
        ],
    }


def contract():
    return {
        "schema": "behavior-contract-v1",
        "title": "속도 복구",
        "instruction": "명령 속도의 80% 이상을 유지한다",
        "trigger": {"kind": "time", "at_s": 0.0, "region": None, "object_id": None},
        "condition": {
            "metric": "horizontal_speed_mps",
            "op": "gte",
            "threshold": 0.4,
            "deadline_s": 1.0,
            "hold_s": 0.2,
        },
        "max_gap_s": 0.25,
    }


def completed(model, text):
    return {
        "id": "resp_test",
        "model": model,
        "status": "completed",
        "output": [{
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
        }],
    }


def sse(model, deltas, done=None, completed_event=True):
    lines = []
    for delta in deltas:
        event = {
            "type": "response.output_text.delta",
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "delta": delta,
        }
        lines.append("data: " + json.dumps(event))
    done_text = "".join(deltas) if done is None else done
    lines.append("data: " + json.dumps({
        "type": "response.output_text.done",
        "item_id": "msg_1",
        "output_index": 0,
        "content_index": 0,
        "text": done_text,
    }))
    if completed_event:
        lines.append("data: " + json.dumps({
            "type": "response.completed",
            "response": {"model": model, "status": "completed"},
        }))
    lines.append("data: [DONE]")
    return "\n\n".join(lines) + "\n\n"


def test_spark_request_uses_strict_responses_schema_without_source():
    request = build_spark_request("속도를 검사해", observations())

    assert request["model"] == "muse-spark-1.3"
    assert "response_format" not in request
    output = request["text"]["format"]
    assert output["type"] == "json_schema"
    assert output["strict"] is True
    assert output["schema"]["additionalProperties"] is False
    assert set(output["schema"]["required"]) == set(output["schema"]["properties"])
    assert "source" not in output["schema"]["properties"]
    prompt = request["input"][0]["content"][0]["text"]
    assert "horizontal_speed_mps" in prompt
    assert "셸 명령" in prompt


def test_sam_request_supports_data_uri_and_provider_file_id():
    data_request = build_sam_request("사람을 추적", "data:video/mp4;base64,AA==")
    file_request = build_sam_request("사람을 추적", "file-video_123")

    assert data_request["input"][0]["content"][1] == {
        "type": "input_video",
        "video_url": "data:video/mp4;base64,AA==",
    }
    assert file_request["input"][0]["content"][1] == {
        "type": "input_video",
        "file_id": "file-video_123",
    }
    assert data_request["stream"] is True
    with pytest.raises(AdapterError):
        build_sam_request("사람", "https://example.invalid/video.mp4")
    with pytest.raises(AdapterError):
        build_sam_request("사람", "data:video/mp4;base64,not-base64!")


def test_import_spark_nonstream_forces_trusted_source_fields():
    response = completed("muse-spark-1.3", json.dumps(contract(), ensure_ascii=False))
    result = import_spark_response(response, observations())

    assert result["source"]["kind"] == "imported_spark_response"
    assert result["source"]["provider_authentication"] == "unverified_saved_response"
    assert len(result["source"]["response_sha256"]) == 64


def test_import_spark_rejects_model_source_and_incomplete_response():
    model_contract = contract()
    model_contract["source"] = {"kind": "model_claim"}
    with pytest.raises(AdapterError):
        import_spark_response(
            completed("muse-spark-1.3", json.dumps(model_contract)), observations()
        )
    incomplete = completed("muse-spark-1.3", json.dumps(contract()))
    incomplete["status"] = "incomplete"
    with pytest.raises(AdapterError):
        import_spark_response(incomplete, observations())
    with pytest.raises(AdapterError):
        import_spark_response(completed("wrong-model", json.dumps(contract())), observations())


def test_import_spark_accepts_one_completed_sse_lane():
    raw = json.dumps(contract(), ensure_ascii=False)
    response = sse("muse-spark-1.3", [raw[:9], raw[9:33], raw[33:]])

    result = import_spark_response(response, observations())

    assert result["schema"] == "behavior-contract-v1"
    assert len(result["source"]["response_sha256"]) == 64


@pytest.mark.parametrize("response", [
    "data: [DONE]\n\n",
    sse("muse-spark-1.3", [json.dumps(contract())], completed_event=False),
    sse("muse-spark-1.3", [json.dumps(contract())], done="different"),
    "<2f>1<|box;x1=0;y1=0;x2=1;y2=1;w=2;h=2|><|mask;x=0;y=0;data=x|>",
])
def test_import_rejects_transport_only_incomplete_mismatch_and_raw_text(response):
    with pytest.raises(AdapterError):
        import_spark_response(response, observations())


def test_import_sam_normalizes_inclusive_boxes_and_preserves_real_times():
    text = (
        "<20f>9<|box;x1=8;y1=2;x2=9;y2=4;w=10;h=5;c=.8|>"
        "<|mask;x=0;y=0;c=.7;data=a;b,c[]{}!|>\n"
        "<2f>9<|box;x1=0;y1=0;x2=1;y2=1;w=10;h=5|>"
        "<|mask;x=0;y=0;data=payload;with;semicolons|>"
    )
    source = observations()
    result = import_sam_response(completed("sam-3.1", text), source)

    assert [row["frame"] for row in result["samples"]] == [2, 7, 20]
    assert [row["t_s"] for row in result["samples"]] == [0.17, 0.91, 3.2]
    assert result["samples"][0]["tracks"] == [{
        "object_id": "9", "bbox": [0.0, 0.0, 0.2, 0.4],
    }]
    assert result["samples"][1]["tracks"] == []
    assert result["samples"][2]["tracks"] == [{
        "object_id": "9", "bbox": [0.8, 0.4, 1.0, 1.0],
    }]
    provenance = result["provenance"]
    assert provenance["kind"] == "fixture"
    imported = provenance["sam_import"]
    assert imported["kind"] == "imported_api_response_unverified_provider"
    assert imported["video_binding"] == {
        "video_sha256": "a" * 64,
        "authentication": "claimed_binding_only",
        "selected_by": "caller_bundle",
    }
    assert imported["mask_handling"] == "raw_token_sha256_only_no_pixel_decode"
    assert set(imported["mask_token_sha256"]) == {"2:9", "20:9"}
    assert imported["input_provenance_snapshot"] == source["provenance"]
    assert len(imported["input_bundle_sha256"]) == 64


def test_import_sam_empty_completed_text_means_unknown_tracks():
    result = import_sam_response(completed("sam-3.1", ""), observations())
    assert all(sample["tracks"] == [] for sample in result["samples"])
    assert result["provenance"]["sam_import"]["source_dimensions_px"] is None


def test_sam_import_preserves_capture_deferral_and_expected_dimensions():
    bundle = observations()
    bundle["provenance"].update({
        "capture_approved": False,
        "model_ready": False,
        "frame_width": 640,
        "frame_height": 480,
    })
    bundle = validate_observations(bundle)
    assert bundle["provenance"]["frame_width"] == 640.0
    text = (
        "<2f>9<|box;x1=0;y1=0;x2=63;y2=95;w=640;h=480|><|mask;x=0;y=0;data=a|>\n"
        "<7f>9<|box;x1=576;y1=0;x2=639;y2=95;w=640;h=480|><|mask;x=0;y=0;data=b|>"
    )
    imported = import_sam_response(completed("sam-3.1", text), bundle)
    region_spec = {
        "schema": "behavior-contract-v1",
        "title": "영역 진입 뒤 속도",
        "instruction": "객체가 오른쪽 영역에 들어온 뒤 속도를 유지한다",
        "trigger": {
            "kind": "region_entry",
            "at_s": None,
            "region": [0.5, 0.0, 1.0, 1.0],
            "object_id": "9",
        },
        "condition": {
            "metric": "horizontal_speed_mps",
            "op": "gte",
            "threshold": 0.1,
            "deadline_s": 1.0,
            "hold_s": 0.1,
        },
        "max_gap_s": 1.0,
        "source": {"kind": "synthetic_fixture_declared"},
    }

    assert imported["provenance"]["capture_approved"] is False
    assert imported["provenance"]["model_ready"] is False
    assert imported["provenance"]["frame_width"] == 640.0
    assert imported["provenance"]["frame_height"] == 480.0
    assert evaluate_contract(region_spec, imported)["verdict"] == "UNKNOWN"


def test_sam_import_rejects_dimensions_that_disagree_with_original_provenance():
    bundle = observations()
    bundle["provenance"].update({
        "capture_approved": False,
        "model_ready": False,
        "frame_width": 640,
        "frame_height": 480,
    })
    text = "<2f>1<|box;x1=0;y1=0;x2=1;y2=1;w=10;h=10|><|mask;x=0;y=0;data=x|>"

    with pytest.raises(AdapterError, match="원본 provenance와 다릅니다"):
        import_sam_response(completed("sam-3.1", text), bundle)


@pytest.mark.parametrize("text", [
    (
        "<2f>1<|box;x1=0;y1=0;x2=1;y2=1;w=10;h=10|><|mask;x=0;y=0;data=x|>\n"
        "<2f>2<|box;x1=0;y1=0;x2=1;y2=1;w=10;h=10|><|mask;x=0;y=0;data=y|>"
    ),
    (
        "<2f>1<|box;x1=0;y1=0;x2=1;y2=1;w=10;h=10|><|mask;x=0;y=0;data=x|>,"
        "1<|box;x1=2;y1=2;x2=3;y2=3;w=10;h=10|><|mask;x=0;y=0;data=y|>"
    ),
    (
        "<2f>1<|box;x1=0;y1=0;x2=10;y2=1;w=10;h=10|>"
        "<|mask;x=0;y=0;data=x|>"
    ),
    (
        "<2f>1<|box;x1=0;y1=0;x2=1;y2=1;w=10;h=10|><|mask;x=0;y=0;data=x|>\n"
        "<7f>1<|box;x1=0;y1=0;x2=1;y2=1;w=11;h=10|><|mask;x=0;y=0;data=x|>"
    ),
    "<3f>1<|box;x1=0;y1=0;x2=1;y2=1;w=10;h=10|><|mask;x=0;y=0;data=x|>",
])
def test_import_sam_rejects_duplicate_bad_bounds_dimensions_and_unknown_frames(text):
    with pytest.raises(AdapterError):
        import_sam_response(completed("sam-3.1", text), observations())


def test_import_sam_rejects_more_than_sixteen_objects():
    objects = []
    for object_id in range(17):
        objects.append(
            f"{object_id}<|box;x1=0;y1=0;x2=0;y2=0;w=2;h=2|>"
            f"<|mask;x=0;y=0;data={object_id}|>"
        )
    text = "<2f>" + ",".join(objects)

    with pytest.raises(AdapterError):
        import_sam_response(completed("sam-3.1", text), observations())


def test_import_does_not_mutate_caller_bundle():
    bundle = observations()
    before = copy.deepcopy(bundle)
    text = "<2f>1<|box;x1=0;y1=0;x2=0;y2=0;w=2;h=2|><|mask;x=0;y=0;data=x|>"

    import_sam_response(completed("sam-3.1", text), bundle)

    assert bundle == before
