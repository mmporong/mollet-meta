#!/usr/bin/env python3
"""인증된 Spark 영상 분석·재검토 근거로 180초 발표 영상을 만든다."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import build_meta_behavior_demo as media  # noqa: E402

DURATION = 180.0
SCENE_DURATION = 15.0
SCENES = (
    ("title", "META VIDEO RESEARCH LOOP", "영상 가설을 상태 기록으로 검사하고, 그 결과를 Spark에 다시 보내 다음 가설을 받는 순환입니다."),
    ("proof", "AUTHENTICATED PROVIDER EVIDENCE", "발표 근거는 인증된 HTTPS 완료 영수증 두 건과 원응답 해시로 묶었습니다. 화면 재생 중에는 새 모델 요청을 보내지 않습니다."),
    ("input", "ONE VIDEO, ONE LINEAGE", "1차 분석과 재검토는 전도가 기록된 같은 Go2 계단 하행 영상 해시와 동기화된 상태 기록을 사용합니다."),
    ("first", "1 · SPARK WATCHES THE VIDEO", "첫 Spark 응답은 근거 프레임, 실패 원인 후보, 검사 가능한 행동 조건을 제안합니다."),
    ("first-check", "2 · LOCAL CHECK", "제안 가설을 저장 상태 기록에서 검사해 통과, 위반, 판단 보류와 근거 구간을 남깁니다."),
    ("reflection", "3 · REFLECTION REQUEST", "1차 검사식과 실제 판정, 한계를 다음 요청에 함께 제공해 Spark가 같은 영상 근거를 재검토하게 합니다."),
    ("second", "4 · SPARK RECONSIDERS", "두 번째 실제 응답은 1차 검사식과 판정이 포함된 요청에 대한 재검토 결과입니다."),
    ("second-check", "5 · CHECK AGAIN", "재검토 가설도 같은 원본 상태에서 검사합니다. 모델 제안과 로컬 판정을 분리해 과도한 결론을 막습니다."),
    ("boundary", "WHAT THIS PROVES", "기존 수치 조건도 모델 호출 전에 자세 위반을 찾았습니다. 이번 근거는 Spark가 전도를 처음 발견했다는 비교가 아니라 영상 가설, 검사, 재검토의 순환 실행을 보여줍니다."),
    ("receipts", "REPRODUCIBLE RECEIPTS", "각 요청과 응답 해시, 실행 ID, 영상 해시를 남겨 발표 화면이 어느 provider 결과에서 왔는지 확인할 수 있습니다."),
    ("workflow", "VIDEO → HYPOTHESIS → CHECK → REFLECT", "Spark의 시각 추론과 결정론적 상태 검사를 결합해 실패 원인 탐색을 반복 가능한 연구 절차로 바꿉니다."),
    ("end", "MOLLET", "로봇 학습 실패 영상을 설명 가능한 가설과 다음 실험으로 연결합니다."),
)

ENGLISH_CAPTIONS = (
    "A video hypothesis is checked against recorded state, then returned to Spark for review.",
    "Two authenticated HTTPS receipts and raw-response hashes bind the presentation evidence. No model call occurs during playback.",
    "Both requests use the same recorded Go2 stair-descent video and synchronized state lineage.",
    "The first Spark response proposes a claim, evidence frames, and a locally checkable condition.",
    "The proposed condition is evaluated on recorded state as PASS, FAIL, or UNKNOWN with an evidence interval.",
    "The first check formula, observed verdict, and limitations are supplied in the review request.",
    "The second response is Spark's review of the same video with the first check and verdict included.",
    "The reviewed hypothesis is evaluated again on the same recorded state.",
    "A pre-model numeric baseline already detected posture failure. This is a loop execution, not a discovery or accuracy comparison.",
    "Request hashes, response hashes, provider IDs, run IDs, and the video hash preserve lineage.",
    "Spark video hypotheses and deterministic state checks remain separate evidence stages.",
    "Mollet connects robot-learning failure videos to inspectable hypotheses and next experiments.",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_evidence(folder: Path) -> dict:
    paths = {
        "session": folder / "final-session.json",
        "first": folder / "first-response.json",
        "reflection": folder / "reflection-response.json",
        "summary": folder / "summary.json",
        "first_analysis": folder / "first-analysis.json",
        "first_rounds": folder / "first-rounds.json",
        "reflection_analysis": folder / "reflection-analysis.json",
        "reflection_rounds": folder / "reflection-rounds.json",
        "baseline": folder / "baseline-before-model.json",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise ValueError("실제 근거 파일이 없습니다: " + ", ".join(missing))
    session = read_json(paths["session"])
    summary = read_json(paths["summary"])
    baseline = read_json(paths["baseline"])
    receipts = session.get("receipts")
    manifest = session.get("input_manifest") or {}
    if (summary.get("schema") != "meta-actual-video-loop-presentation-v1"
            or summary.get("example_id") != "go2-down-slow"
            or summary.get("status") != "completed_actual_video_and_reflection"
            or summary.get("actual_video_inference_executed") is not True
            or summary.get("actual_reflection_executed") is not True
            or summary.get("new_generation_requests") != 2
            or summary.get("new_training_executed") is not False
            or summary.get("performance_improvement_measured") is not False):
        raise ValueError("runner의 실제 Go2 영상 분석·재검토 완료 증거가 아닙니다")
    if (baseline.get("schema") != "meta-recorded-static-baseline-v1"
            or baseline.get("example_id") != "go2-down-slow"
            or baseline.get("result", {}).get("verdict") != "FAIL"
            or baseline.get("model_called") is not False
            or baseline.get("video_sha256") != manifest.get("video_sha256")
            or summary.get("static_baseline_verdict") != "FAIL"):
        raise ValueError("모델 호출 전 자세 FAIL 기준선이 없거나 최종 입력과 다릅니다")
    if session.get("schema") != "meta-research-session-v1":
        raise ValueError("final-session.json 스키마가 다릅니다")
    if session.get("example_id") != "go2-down-slow" or session.get("example_id") != summary.get("example_id"):
        raise ValueError("최종 세션이 선택한 Go2 전도 사례와 다릅니다")
    if (session.get("analysis_origin") != "authenticated_live_spark_video_response"
            or session.get("provider_authentication") != "authenticated_https_api"):
        raise ValueError("인증된 실제 Spark 영상 분석 세션이 아닙니다")
    if not manifest.get("sent") or not isinstance(manifest.get("video_sha256"), str):
        raise ValueError("전송 완료된 영상 manifest가 없습니다")
    if not isinstance(receipts, list) or len(receipts) < 2 or session.get("new_model_calls") != len(receipts):
        raise ValueError("1차 분석과 재검토의 provider 영수증 2건이 필요합니다")
    cycles = session.get("cycles")
    if (not isinstance(cycles, list) or not cycles or not cycles[0].get("history")
            or not session.get("analysis") or not session.get("history")):
        raise ValueError("1차 검사와 재검토 후 검사 이력이 모두 필요합니다")
    first_analysis = read_json(paths["first_analysis"])
    first_rounds = read_json(paths["first_rounds"])
    reflection_analysis = read_json(paths["reflection_analysis"])
    reflection_rounds = read_json(paths["reflection_rounds"])
    if (first_analysis != cycles[0].get("analysis")
            or first_rounds.get("history") != cycles[0].get("history")
            or reflection_analysis != session.get("analysis")
            or reflection_rounds.get("history") != session.get("history")):
        raise ValueError("분석·검사 파일이 최종 세션의 1차·재검토 이력과 다릅니다")
    response_paths = (paths["first"], paths["reflection"])
    run_ids = set()
    for index, (receipt, response_path) in enumerate(zip(receipts[:2], response_paths), start=1):
        if (receipt.get("status") != "completed_http" or receipt.get("response_sha256") != sha256(response_path)
                or receipt.get("context", {}).get("video_sha256") != manifest["video_sha256"]
                or receipt.get("context", {}).get("input_kind") != "video_and_synchronized_state"):
            raise ValueError(f"{index}차 원응답과 provider 영수증이 일치하지 않습니다")
        proof = summary.get("first" if index == 1 else "reflection") or {}
        if (not proof.get("provider_request_id") or proof.get("run_id") != receipt.get("run_id")
                or proof.get("request_sha256") != receipt.get("request_sha256")
                or proof.get("response_sha256") != receipt.get("response_sha256")
                or proof.get("provider_authentication") != "authenticated_https_api"):
            raise ValueError(f"{index}차 runner 요약의 provider ID·SHA 증거가 없습니다")
        run_dir = Path(receipt.get("directory", ""))
        receipt_path = run_dir / "receipt.json"
        request_path = run_dir / "request.json"
        stored_response = run_dir / ("response.json" if receipt.get("content_type") == "application/json" else "response.sse")
        if not receipt_path.is_file() or not request_path.is_file() or not stored_response.is_file():
            raise ValueError(f"{index}차 provider 실행 디렉터리의 원본 파일이 없습니다")
        stored_receipt = read_json(receipt_path)
        if (stored_receipt.get("run_id") != receipt.get("run_id")
                or stored_receipt.get("response_sha256") != receipt.get("response_sha256")
                or sha256(request_path) != receipt.get("request_sha256")
                or sha256(stored_response) != receipt.get("response_sha256")):
            raise ValueError(f"{index}차 provider 실행 원장의 해시가 일치하지 않습니다")
        run_ids.add(receipt.get("run_id"))
    if len(run_ids) != 2:
        raise ValueError("두 provider 실행 ID가 같아 재검토 실행을 증명하지 못합니다")
    evidence = {"session": session, "summary": summary, "baseline": baseline, "receipts": receipts[:2], "paths": paths,
                "first_analysis": first_analysis, "first_rounds": first_rounds,
                "reflection_analysis": reflection_analysis, "reflection_rounds": reflection_rounds}
    evidence["presentation_facts"] = presentation_facts(evidence)
    return evidence


def presentation_facts(evidence: dict) -> dict:
    """실제 첫 검사 대상과 판정을 발표용 최소 필드로 고른다."""
    result: dict[str, dict] = {}
    for name in ("first", "reflection"):
        analysis = evidence[f"{name}_analysis"]
        history = evidence[f"{name}_rounds"].get("history") or []
        if not history:
            raise ValueError(f"{name} 실제 검사 이력이 없습니다")
        checked = history[0]
        hypothesis_id = checked.get("selected_hypothesis_id")
        hypothesis = next((row for row in analysis.get("hypotheses", []) if row.get("id") == hypothesis_id), None)
        if hypothesis is None:
            raise ValueError(f"{name} 검사 대상 가설이 분석 결과에 없습니다")
        if not isinstance(hypothesis.get("claim"), str) or not hypothesis["claim"].strip():
            raise ValueError(f"{name} 검사 대상 가설 문구가 없습니다")
        verdict = (checked.get("result") or {}).get("verdict")
        if verdict not in {"PASS", "FAIL", "UNKNOWN"}:
            raise ValueError(f"{name} 실제 판정이 올바르지 않습니다")
        frames = hypothesis.get("evidence_frames")
        if not isinstance(frames, list) or not frames:
            raise ValueError(f"{name} 가설의 근거 프레임이 없습니다")
        result[name] = {"hypothesis_id": hypothesis_id, "claim": hypothesis.get("claim"),
                        "evidence_frames": frames, "verdict": verdict,
                        "reason": (checked.get("result") or {}).get("reason")}
    return result


def validate_capture(folder: Path, evidence: dict) -> dict:
    capture_path = folder / "capture.json"
    required = [folder / name for name in (
        "01-first-analysis.png", "02-first-check.png", "03-reflection-analysis.png",
        "04-reflection-check.png", "05-full-workbench.png",
    )]
    if not capture_path.is_file() or any(not path.is_file() for path in required):
        raise ValueError("브라우저 캡처 결과가 완전하지 않습니다")
    capture = read_json(capture_path)
    if (capture.get("schema") != "meta-actual-video-loop-capture-v1"
            or capture.get("capture_api_calls") != 0
            or capture.get("video_sha256") != evidence["session"]["input_manifest"]["video_sha256"]):
        raise ValueError("브라우저 캡처 manifest가 실제 근거와 다릅니다")
    expected = [row["run_id"] for row in evidence["receipts"]]
    if [row.get("run_id") for row in capture.get("provider_runs", [])] != expected:
        raise ValueError("브라우저 캡처의 provider 실행 ID가 근거와 다릅니다")
    return {"manifest": capture, "images": required}


def slide(path: Path, eyebrow: str, title: str, body: str, screenshot: Path | None, badges: list[tuple[str, str]]) -> None:
    image = Image.new("RGB", (media.WIDTH, media.HEIGHT), media.WHITE)
    draw = ImageDraw.Draw(image)
    draw.text((64, 48), eyebrow, font=media.font(20, bold=True), fill=media.BLUE)
    draw.text((64, 84), title, font=media.font(44, bold=True), fill=media.INK)
    lines = media.wrap(draw, body, media.font(25), 1120)
    y = 150
    for line in lines[:3]:
        draw.text((64, y), line, font=media.font(25), fill=media.MUTED)
        y += 36
    if screenshot:
        source = Image.open(screenshot).convert("RGB")
        source.thumbnail((1152, 390))
        x = (media.WIDTH - source.width) // 2
        image.paste(source, (x, 270))
        draw.rounded_rectangle((x - 2, 268, x + source.width + 2, 272 + source.height), radius=8, outline=(210, 222, 233), width=2)
    x = 64
    for label, value in badges:
        width = 195
        draw.rounded_rectangle((x, 662, x + width, 706), radius=12, fill=(238, 246, 255))
        draw.text((x + 12, 671), f"{label}  {value}", font=media.font(18, bold=True), fill=media.BLUE)
        x += width + 14
    image.save(path)


def result_slide(path: Path, title: str, facts: dict, stage: str) -> None:
    image = Image.new("RGB", (media.WIDTH, media.HEIGHT), media.WHITE)
    draw = ImageDraw.Draw(image)
    draw.text((64, 48), "ACTUAL SPARK RESPONSE + LOCAL STATE CHECK", font=media.font(20, bold=True), fill=media.BLUE)
    draw.text((64, 86), title, font=media.font(42, bold=True), fill=media.INK)
    draw.rounded_rectangle((64, 158, 1216, 430), radius=20, fill=(244, 248, 252), outline=(210, 222, 233), width=2)
    draw.text((94, 184), f"{stage} 가설", font=media.font(20, bold=True), fill=media.BLUE)
    y = 228
    for line in media.wrap(draw, str(facts["claim"]), media.font(31, bold=True), 1050)[:4]:
        draw.text((94, y), line, font=media.font(31, bold=True), fill=media.INK)
        y += 43
    frames = ", ".join(str(value) for value in facts["evidence_frames"][:8])
    draw.text((94, 386), f"근거 프레임  {frames}", font=media.font(23, bold=True), fill=media.MUTED)
    color = media.GREEN if facts["verdict"] == "PASS" else media.RED if facts["verdict"] == "FAIL" else media.AMBER
    draw.rounded_rectangle((64, 466, 430, 638), radius=20, fill=(249, 251, 253), outline=color, width=4)
    draw.text((94, 492), "저장 상태 검사", font=media.font(22, bold=True), fill=media.MUTED)
    draw.text((94, 540), facts["verdict"], font=media.font(54, bold=True), fill=color)
    draw.rounded_rectangle((462, 466, 1216, 638), radius=20, fill=(249, 251, 253), outline=(210, 222, 233), width=2)
    draw.text((490, 492), "판정 근거", font=media.font(22, bold=True), fill=media.MUTED)
    y = 532
    for line in media.wrap(draw, str(facts.get("reason") or "기록된 판정 사유 없음"), media.font(23), 680)[:3]:
        draw.text((490, y), line, font=media.font(23), fill=media.INK)
        y += 32
    image.save(path)


def subtitle_filter(srt: Path) -> str:
    escaped = str(srt.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    style = "FontName=Arial,FontSize=20,PrimaryColour=&H00263E53,Outline=0,Shadow=0,Alignment=2,MarginV=22"
    return f"pad=1280:840:0:0:color=white,subtitles=filename='{escaped}':force_style='{style}',format=yuv420p"


def burn_english_captions(source: Path, srt: Path, target: Path) -> None:
    media.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
               "-vf", subtitle_filter(srt), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-c:a", "copy", "-t", str(DURATION), "-movflags", "+faststart", str(target)])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    evidence = validate_evidence(args.evidence_dir.resolve())
    capture = validate_capture(args.capture_dir.resolve(), evidence)
    if args.validate_only:
        print(json.dumps({"status": "validated", "provider_runs": [r["run_id"] for r in evidence["receipts"]]}, ensure_ascii=False))
        return 0
    out = args.out.resolve()
    if out.exists():
        raise ValueError(f"새 출력 폴더가 필요합니다: {out}")
    for tool in ("ffmpeg", "ffprobe", "powershell"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"필요한 실행 파일이 없습니다: {tool}")
    build = out / "_build"
    slides = build / "slides"
    videos = build / "video"
    audios = build / "audio"
    for folder in (slides, videos, audios):
        folder.mkdir(parents=True, exist_ok=True)

    session = evidence["session"]
    receipts = evidence["receipts"]
    facts = evidence["presentation_facts"]
    shot_map = [None, None, capture["images"][4], None, None,
                capture["images"][1], None, None, capture["images"][4],
                None, capture["images"][3], capture["images"][4]]
    video_parts, audio_parts, narration_modes = [], [], []
    for index, ((name, title, narration), screenshot) in enumerate(zip(SCENES, shot_map)):
        badges = [("실제 호출", "2건"), ("렌더 호출", "0건"), ("GPU", "0건")]
        if name == "input": badges = [("example", session["example_id"]), ("video SHA", session["input_manifest"]["video_sha256"][:10]), ("입력", "video+state")]
        if name == "receipts": badges = [("run 1", receipts[0]["run_id"][:8]), ("run 2", receipts[1]["run_id"][:8]), ("status", "completed")]
        slide_path = slides / f"{index:02d}-{name}.png"
        if name in {"first", "first-check"}:
            result_slide(slide_path, title, facts["first"], "1차")
        elif name in {"second", "second-check"}:
            result_slide(slide_path, title, facts["reflection"], "재검토")
        else:
            slide(slide_path, "MOLLET · META GLOBAL AI DEVELOPER HACKATHON", title, narration, screenshot, badges)
        video_path = videos / f"{index:02d}-{name}.mp4"
        audio_path = audios / f"{index:02d}-{name}.wav"
        media.make_still(slide_path, SCENE_DURATION, video_path)
        narration_modes.append(media.make_audio(narration, SCENE_DURATION, audio_path, audios / f"{index:02d}-{name}-raw.wav"))
        video_parts.append(video_path)
        audio_parts.append(audio_path)

    video_list = build / "video-list.txt"
    audio_list = build / "audio-list.txt"
    video_list.write_text("".join(f"file '{p.as_posix()}'\n" for p in video_parts), encoding="utf-8")
    audio_list.write_text("".join(f"file '{p.as_posix()}'\n" for p in audio_parts), encoding="utf-8")
    joined_video, joined_audio = build / "joined.mp4", build / "joined.wav"
    media.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(video_list), "-c", "copy", str(joined_video)])
    media.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(audio_list), "-c", "copy", str(joined_audio)])
    final = out / "mollet-meta-loop-demo-180s.mp4"
    media.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(joined_video), "-i", str(joined_audio),
               "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
               "-t", str(DURATION), "-movflags", "+faststart", str(final)])

    srt_lines, english_lines, script_lines, current = [], [], ["# Mollet Meta 영상 연구 루프 3분 대본", ""], 0.0
    for index, ((name, title, narration), english) in enumerate(zip(SCENES, ENGLISH_CAPTIONS), start=1):
        end = current + SCENE_DURATION
        srt_lines.extend([str(index), f"{media.srt_timestamp(current)} --> {media.srt_timestamp(end)}", narration, ""])
        english_lines.extend([str(index), f"{media.srt_timestamp(current)} --> {media.srt_timestamp(end)}", english, ""])
        script_lines.extend([f"## {media.srt_timestamp(current)}–{media.srt_timestamp(end)} · {title}", "", narration, ""])
        current = end
    srt = out / "mollet-meta-loop-demo-ko.srt"
    english_srt = out / "mollet-meta-loop-demo-en.srt"
    script = out / "SCRIPT-ko.md"
    srt.write_text("\n".join(srt_lines), encoding="utf-8", newline="\n")
    english_srt.write_text("\n".join(english_lines), encoding="utf-8", newline="\n")
    script.write_text("\n".join(script_lines), encoding="utf-8", newline="\n")
    probe = media.probe(final)
    if abs(float(probe["format"]["duration"]) - DURATION) > 0.1:
        raise RuntimeError("최종 영상 길이가 180초와 다릅니다")
    video_stream = next(row for row in probe["streams"] if row["codec_type"] == "video")
    audio_stream = next(row for row in probe["streams"] if row["codec_type"] == "audio")
    if (video_stream.get("codec_name") != "h264" or video_stream.get("pix_fmt") != "yuv420p"
            or audio_stream.get("codec_name") != "aac"):
        raise RuntimeError("최종 영상 코덱이 발표 계약과 다릅니다")
    media.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(final), "-f", "null", "NUL"])
    english_video = out / "mollet-meta-loop-demo-180s-en-captioned.mp4"
    burn_english_captions(final, english_srt, english_video)
    english_probe = media.probe(english_video)
    english_stream = next(row for row in english_probe["streams"] if row["codec_type"] == "video")
    if (abs(float(english_probe["format"]["duration"]) - DURATION) > 0.1
            or english_stream.get("codec_name") != "h264" or english_stream.get("pix_fmt") != "yuv420p"
            or int(english_stream.get("width", 0)) != 1280 or int(english_stream.get("height", 0)) != 840):
        raise RuntimeError("영어 캡션 영상의 180초·1280x840 계약이 다릅니다")
    media.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(english_video), "-f", "null", "NUL"])
    manifest = {
        "schema": "meta_actual_video_loop_demo_v1",
        "scope": "authenticated_saved_provider_responses_and_local_ui_replay",
        "duration_s": DURATION,
        "output": {"file": final.name, "sha256": sha256(final), "probe": probe, "full_decode_exit": 0},
        "english_captioned_output": {"file": english_video.name, "sha256": sha256(english_video),
                                       "probe": english_probe, "full_decode_exit": 0},
        "evidence": {"session_sha256": sha256(evidence["paths"]["session"]), "provider_runs": [r["run_id"] for r in receipts]},
        "claims": {"actual_spark_video_requests": 2, "new_meta_calls_during_capture": 0, "new_meta_calls_during_render": 0,
                   "gpu_jobs": 0, "training_executed": False, "recovery_improvement_measured": False},
        "narration_modes": narration_modes,
        "presentation_facts": facts,
        "subtitles": {"ko": {"file": srt.name, "sha256": sha256(srt), "cues": 12},
                      "en": {"file": english_srt.name, "sha256": sha256(english_srt), "cues": 12}},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
