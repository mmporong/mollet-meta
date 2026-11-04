#!/usr/bin/env python3
"""Build the English-only 180-second Mollet submission demo.

The renderer accepts a fresh English browser capture, verifies the saved historical
Spark and SAM evidence, and produces English UI, narration, and captions. It makes
no model request, training run, or GPU job.
"""
from __future__ import annotations

import argparse
import array
import base64
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import textwrap
import wave
from pathlib import Path

from PIL import Image, ImageDraw


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for entry in (str(SRC), str(HERE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)
LOCAL_FFMPEG = ROOT.parent / "ffmpeg"
if (LOCAL_FFMPEG / "ffmpeg.exe").is_file() and str(LOCAL_FFMPEG) not in os.environ.get("PATH", ""):
    os.environ["PATH"] = str(LOCAL_FFMPEG) + os.pathsep + os.environ.get("PATH", "")

import build_meta_behavior_demo as media  # noqa: E402
import build_meta_recovery_demo as recovery  # noqa: E402
from rl_triage.behavior_authoring.datasets import example_paths, load_example_view  # noqa: E402


DURATION = 180.0
SCENE_DURATION = 15.0
OUTPUT_SCHEMA = "meta-english-recovery-demo-v1"
VOICE_NAME = "Microsoft Zira Desktop"
HANGUL = re.compile(r"[\uac00-\ud7a3]")

ENGLISH_TITLES = {
    "title": "Turn desired robot behavior into a testable condition",
    "problem": "Put visual evidence and state verdicts on one timeline",
    "browser-replay": "Restore saved results in Chrome",
    "sam-motion": "SAM boxes cover 624 of 625 frames",
    "provider-result": "The original Spark condition passes for 0.5 seconds",
    "manual-eight": "A reviewer changes the hold to 8 seconds",
    "manual-roundtrip": "Reopen the edited condition without stale approval",
    "go2-workspace": "Apply the same evaluator to a Go2 recording",
    "go2-motion": "Compute the Go2 posture failure from recorded state",
    "go2-result": "The full-duration Go2 condition fails",
    "boundary": "Keep every claim inside the verified scope",
    "end": "Preserve source and verdict in one evidence lineage",
}
SCENES = tuple(
    (name, ENGLISH_TITLES[name], english)
    for name, _title, _korean, english in recovery.SCENES
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_english_text(label: str, value: str) -> None:
    if HANGUL.search(value):
        raise ValueError(f"{label} contains Korean text")


def source_commit() -> str:
    return media.run(["git", "rev-parse", "HEAD"], capture=True)


def make_motion(source: Path, start: float, duration: float, target: Path) -> None:
    media.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-stream_loop", "-1",
        "-i", str(source), "-ss", str(start), "-t", str(duration),
        "-vf", f"scale={media.WIDTH}:{media.HEIGHT}:force_original_aspect_ratio=decrease,"
               f"pad={media.WIDTH}:{media.HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=white,"
               f"fps={media.FPS},format=yuv420p",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(target),
    ])


def slide(path: Path, title: str, body: str, screenshot: Path | None,
          badges: list[tuple[str, str]]) -> None:
    assert_english_text("slide title", title)
    assert_english_text("slide body", body)
    image = Image.new("RGB", (media.WIDTH, media.HEIGHT), media.WHITE)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 16, media.HEIGHT), fill=media.BLUE)
    draw.text((60, 42), "MOLLET · SAVED META RECOVERY", font=media.font(19, bold=True), fill=media.BLUE)
    draw.text((60, 78), title, font=media.font(38, bold=True), fill=media.INK)
    y = 136
    for line in media.wrap(draw, body, media.font(22), 1150)[:3]:
        draw.text((60, y), line, font=media.font(22), fill=media.MUTED)
        y += 31
    if screenshot:
        source = Image.open(screenshot).convert("RGB")
        if "workspace" in screenshot.name:
            source = source.crop((20, 115, min(980, source.width), min(670, source.height)))
        source.thumbnail((1150, 405))
        x = (media.WIDTH - source.width) // 2
        top = 238
        image.paste(source, (x, top))
        draw.rounded_rectangle((x - 2, top - 2, x + source.width + 2, top + source.height + 2),
                               radius=8, outline=media.LINE, width=2)
    x = 60
    for label, value in badges:
        assert_english_text("badge", f"{label} {value}")
        width = 220
        draw.rounded_rectangle((x, 660, x + width, 705), radius=11, fill=media.PALE_BLUE)
        draw.text((x + 12, 671), f"{label}  {value}", font=media.font(16, bold=True), fill=media.BLUE)
        x += width + 12
    image.save(path)


def save_tracking_overlays(folder: Path, samples: list[dict], condition: dict) -> None:
    folder.mkdir()
    title_face = media.font(25, bold=True)
    text_face = media.font(17)
    value_face = media.font(19, bold=True)
    object_face = media.font(14, bold=True)
    threshold = float(condition["threshold"])
    deadline = float(condition["deadline_s"])
    hold = float(condition["hold_s"])
    for sample in samples:
        image = Image.new("RGBA", (media.WIDTH, media.HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, media.WIDTH, 92), fill=(255, 255, 255, 238))
        draw.rectangle((0, 630, media.WIDTH, media.HEIGHT), fill=(255, 255, 255, 242))
        draw.rectangle((0, 0, 14, media.HEIGHT), fill=media.BLUE)
        draw.text((42, 15), "H1 stair ascent · saved SAM 3.1 response",
                  font=title_face, fill=media.INK)
        value = float(sample["values"]["up_axis_z"])
        draw.text((42, 55),
                  f"frame {sample['frame']:03d}  ·  t={float(sample['t_s']):.2f}s  ·  up_axis_z={value:.4f}",
                  font=text_face, fill=media.MUTED)
        for track in sample.get("tracks", []):
            x1, y1, x2, y2 = track["bbox"]
            box = (round(x1 * media.WIDTH), round(y1 * media.HEIGHT),
                   round(x2 * media.WIDTH), round(y2 * media.HEIGHT))
            draw.rectangle(box, outline=media.BLUE, width=5)
            label_y = max(98, box[1] - 31)
            draw.rounded_rectangle((box[0], label_y, box[0] + 130, label_y + 27),
                                   radius=5, fill=(8, 102, 217, 225))
            draw.text((box[0] + 7, label_y + 3), f"SAM object {track['object_id']}",
                      font=object_face, fill=media.WHITE)
        passed = value >= threshold
        draw.text((42, 650),
                  f"condition  up_axis_z >= {threshold:.1f}  ·  start within {deadline:.0f}s  ·  hold {hold:.1f}s",
                  font=text_face, fill=media.INK)
        draw.text((1085, 650), "PASS" if passed else "FAIL", font=value_face,
                  fill=media.GREEN if passed else media.RED)
        draw.text((42, 684), f"SAM tracks in this frame: {len(sample.get('tracks', []))}",
                  font=text_face, fill=media.MUTED)
        image.save(folder / f"frame-{int(sample['frame']):06d}.png")


def save_go2_overlay(path: Path, assessment: dict) -> None:
    image = Image.new("RGBA", (media.WIDTH, media.HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, media.WIDTH, 88), fill=(255, 255, 255, 238))
    draw.rectangle((0, 634, media.WIDTH, media.HEIGHT), fill=(255, 255, 255, 242))
    draw.rectangle((0, 0, 14, media.HEIGHT), fill=media.BLUE)
    draw.text((44, 19), "Go2 stair descent · recorded-state check",
              font=media.font(27, bold=True), fill=media.INK)
    draw.text((44, 55), "up_axis_z >= 0.8 · full 12.48 s · no new model diagnosis",
              font=media.font(16), fill=media.MUTED)
    accepted = bool(assessment["motion_accepted"])
    draw.text((44, 656),
              f"forward {assessment['root_dx_m']:+.3f} m   height {assessment['root_dz_m']:+.3f} m   min posture {assessment['min_up_axis_z']:.3f}",
              font=media.font(19), fill=media.INK)
    draw.text((1085, 656), "ACCEPTED" if accepted else "REJECTED",
              font=media.font(20, bold=True), fill=media.GREEN if accepted else media.RED)
    draw.rounded_rectangle((1005, 18, 1238, 64), radius=10, fill=media.PALE_BLUE,
                           outline=media.LINE, width=1)
    draw.text((1024, 29), "FRAME STATE CHECK", font=media.font(15, bold=True), fill=media.BLUE)
    image.save(path)


def synthesize_zira(text: str, target: Path) -> str:
    assert_english_text("narration", text)
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    escaped = str(target).replace("'", "''")
    command = (
        "$text=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "'));"
        "$voice=New-Object -ComObject SAPI.SpVoice;"
        "$candidate=@($voice.GetVoices() | Where-Object {$_.GetDescription() -like '*Microsoft Zira Desktop*'})[0];"
        "if(-not $candidate){throw 'Microsoft Zira Desktop voice is unavailable'};"
        "$voice.Voice=$candidate;$voice.Rate=0;"
        "$description=$candidate.GetDescription();"
        "$stream=New-Object -ComObject SAPI.SpFileStream;"
        "$stream.Open('" + escaped + "',3,$false);$voice.AudioOutputStream=$stream;"
        "[void]$voice.Speak($text);$stream.Close();Write-Output $description"
    )
    description = media.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                            capture=True).strip()
    if VOICE_NAME not in description or not target.is_file() or target.stat().st_size <= 44:
        raise RuntimeError("Microsoft Zira Desktop did not produce narration audio")
    return description


def wav_rms(path: Path) -> float:
    with wave.open(str(path), "rb") as stream:
        if stream.getsampwidth() != 2:
            raise RuntimeError("Narration WAV must use 16-bit PCM")
        values = array.array("h", stream.readframes(stream.getnframes()))
    if not values:
        return 0.0
    return math.sqrt(sum(value * value for value in values) / len(values))


def make_audio(text: str, target: Path, raw: Path) -> dict:
    voice = synthesize_zira(text, raw)
    raw_duration = media.probe_duration(raw)
    if not 1.0 <= raw_duration <= 25.0:
        raise RuntimeError(f"Unexpected narration duration: {raw_duration:.3f}s")
    available = SCENE_DURATION - 0.8
    speed = max(1.0, raw_duration / available)
    if speed > 1.35:
        raise RuntimeError(f"Narration would require excessive speed: {speed:.3f}x")
    filters = [] if speed == 1.0 else [media.atempo_chain(speed)]
    filters.extend(["adelay=300|300", "apad", "aresample=48000",
                    "aformat=sample_fmts=s16:channel_layouts=stereo"])
    media.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw),
               "-af", ",".join(filters), "-t", str(SCENE_DURATION),
               "-c:a", "pcm_s16le", str(target)])
    duration = media.probe_duration(target)
    rms = wav_rms(target)
    if abs(duration - SCENE_DURATION) > 0.03 or rms < 100.0:
        raise RuntimeError("Narration duration or RMS verification failed")
    return {"voice": voice, "language": "en-US", "raw_duration_s": raw_duration,
            "speed": speed, "duration_s": duration, "rms": rms}


def timestamp(seconds: int) -> str:
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}.00"


def write_text_assets(out: Path) -> tuple[Path, Path, Path]:
    srt_lines: list[str] = []
    ass_lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1280", "PlayResY: 840", "WrapStyle: 2", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Arial,24,&H003A2A17,&H003A2A17,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,0,0,1,0,0,2,50,50,15,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    script_lines = ["# Mollet English 180-second demo", ""]
    for index, (_name, title, narration) in enumerate(SCENES, start=1):
        start = (index - 1) * SCENE_DURATION
        end = index * SCENE_DURATION
        srt_lines.extend([str(index),
                          f"{media.srt_timestamp(start)} --> {media.srt_timestamp(end)}",
                          narration, ""])
        caption = r"\N".join(textwrap.wrap(narration, width=90))
        ass_lines.append(f"Dialogue: 0,{timestamp(int(start))},{timestamp(int(end))},Default,,0,0,0,,{caption}")
        script_lines.extend([f"## {media.srt_timestamp(start)}–{media.srt_timestamp(end)} · {title}",
                             "", narration, ""])
    srt = out / "mollet-meta-demo-en.srt"
    ass = out / "mollet-meta-demo-en.ass"
    script = out / "SCRIPT-en.md"
    srt.write_text("\n".join(srt_lines), encoding="utf-8", newline="\n")
    ass.write_text("\n".join(ass_lines) + "\n", encoding="utf-8", newline="\n")
    script.write_text("\n".join(script_lines), encoding="utf-8", newline="\n")
    for path in (srt, ass, script):
        assert_english_text(path.name, path.read_text(encoding="utf-8"))
    return srt, ass, script


def sample_frames(video: Path, folder: Path) -> list[dict]:
    folder.mkdir()
    rows = []
    for index, seconds in enumerate((7.5, 37.5, 67.5, 97.5, 127.5, 172.5), start=1):
        target = folder / f"sample-{index:02d}-{seconds:05.1f}s.png"
        media.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(seconds),
                   "-i", str(video), "-frames:v", "1", str(target)])
        rows.append({"file": target.relative_to(folder.parent).as_posix(), "at_s": seconds,
                     "sha256": sha256(target)})
    return rows


def validate_english_capture(capture: dict) -> None:
    report = capture["report"]
    if report.get("ui_language") != "en" or report.get("visible_korean_text") not in ([], None):
        raise ValueError("Capture does not prove an English-only workbench")
    if report.get("english_ui_text_check_passed") is not True:
        raise ValueError("Capture is missing the English UI text check")


def build(capture: dict, replay: dict, out: Path) -> dict:
    if out.exists():
        raise ValueError(f"Output directory already exists: {out}")
    for tool in ("ffmpeg", "ffprobe", "powershell"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"Required executable is unavailable: {tool}")
    validate_english_capture(capture)
    for _name, title, narration in SCENES:
        assert_english_text("scene", f"{title}\n{narration}")

    build_dir = out / "_build"
    slides = build_dir / "slides"
    videos = build_dir / "video"
    audios = build_dir / "audio"
    overlays = build_dir / "tracking-overlays"
    for folder in (slides, videos, audios):
        folder.mkdir(parents=True, exist_ok=True)

    shots = capture["screenshots"]
    shot_map = {
        "title": shots["01-restored-workspace.png"], "problem": shots["01-restored-workspace.png"],
        "provider-result": shots["02-provider-result.png"],
        "manual-eight": shots["03-eight-second-workspace.png"],
        "manual-roundtrip": shots["03-eight-second-workspace.png"],
        "go2-workspace": shots["04-go2-failure-workspace.png"],
        "go2-result": shots["05-go2-failure-result.png"],
        "boundary": shots["06-full-workbench.png"], "end": shots["06-full-workbench.png"],
    }
    save_tracking_overlays(overlays, replay["observations"]["samples"], replay["contract"]["condition"])
    go2 = load_example_view("go2-down-slow")
    go2_overlay = build_dir / "go2-overlay.png"
    save_go2_overlay(go2_overlay, go2["recorded_assessment"])

    video_parts: list[Path] = []
    audio_parts: list[Path] = []
    audio_proof: list[dict] = []
    for index, (name, title, narration) in enumerate(SCENES):
        video = videos / f"{index:02d}-{name}.mp4"
        if name == "browser-replay":
            make_motion(capture["browser_video"], 0.0, SCENE_DURATION, video)
        elif name == "sam-motion":
            media.make_tracked_video(example_paths("h1-up")[1], overlays, SCENE_DURATION, video)
        elif name == "go2-motion":
            media.make_video(example_paths("go2-down-slow")[1], go2_overlay, SCENE_DURATION, video)
        else:
            still = slides / f"{index:02d}-{name}.png"
            badges = [("historical calls", "2"), ("new calls", "0"), ("GPU", "0")]
            if name == "problem":
                badges = [("video", "frames"), ("state", "synchronized"), ("provenance", "separate")]
            elif name == "provider-result":
                badges = [("threshold", "0.8"), ("deadline", "8 s"), ("hold", "0.5 s")]
            elif name == "manual-eight":
                badges = [("hold", "8 s"), ("verdict", "PASS"), ("source", "user")]
            elif name == "go2-result":
                badges = [("duration", "12.48 s"), ("verdict", "FAIL"), ("model calls", "0")]
            slide(still, title, narration, shot_map.get(name), badges)
            media.make_still(still, SCENE_DURATION, video)
        raw = audios / f"{index:02d}-{name}-raw.wav"
        audio = audios / f"{index:02d}-{name}.wav"
        proof = make_audio(narration, audio, raw)
        proof["scene"] = name
        audio_proof.append(proof)
        video_parts.append(video)
        audio_parts.append(audio)

    video_list = build_dir / "video-list.txt"
    audio_list = build_dir / "audio-list.txt"
    video_list.write_text("".join(f"file '{path.as_posix()}'\n" for path in video_parts), encoding="utf-8")
    audio_list.write_text("".join(f"file '{path.as_posix()}'\n" for path in audio_parts), encoding="utf-8")
    joined_video = build_dir / "joined.mp4"
    joined_audio = build_dir / "joined.wav"
    media.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
               "-i", str(video_list), "-c", "copy", str(joined_video)])
    media.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
               "-i", str(audio_list), "-c", "copy", str(joined_audio)])

    srt, ass, script = write_text_assets(out)
    final = out / "mollet-meta-demo-180s-en.mp4"
    escaped_ass = str(ass.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    media.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(joined_video),
               "-i", str(joined_audio), "-filter_complex",
               f"[0:v]pad=1280:840:0:0:color=white,ass=filename='{escaped_ass}',format=yuv420p[v]",
               "-map", "[v]", "-map", "1:a:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-c:a", "aac", "-b:a", "160k", "-t", str(DURATION), "-movflags", "+faststart", str(final)])
    probe = media.probe(final)
    video_stream = next(row for row in probe["streams"] if row["codec_type"] == "video")
    audio_stream = next(row for row in probe["streams"] if row["codec_type"] == "audio")
    if (abs(float(probe["format"]["duration"]) - DURATION) > 0.1
            or video_stream.get("codec_name") != "h264" or video_stream.get("pix_fmt") != "yuv420p"
            or int(video_stream.get("width", 0)) != 1280 or int(video_stream.get("height", 0)) != 840
            or audio_stream.get("codec_name") != "aac"):
        raise RuntimeError("Final media contract verification failed")
    media.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(final),
               "-f", "null", os.devnull])
    frames = sample_frames(final, out / "sampleframes")
    manifest = {
        "schema": OUTPUT_SCHEMA,
        "scope": "english_ui_voice_and_captions_for_saved_historical_meta_recovery",
        "language": {"ui": "en", "narration": "en-US", "captions": "en"},
        "source_commit": capture["source_commit"], "renderer_commit": source_commit(),
        "duration_s": DURATION,
        "scenes": [{"name": row[0], "start_s": index * SCENE_DURATION,
                    "end_s": (index + 1) * SCENE_DURATION}
                   for index, row in enumerate(SCENES)],
        "output": {"file": final.name, "sha256": sha256(final), "bytes": final.stat().st_size,
                   "probe": probe, "full_decode_exit": 0},
        "voice": {"required": VOICE_NAME, "language": "en-US", "scenes": audio_proof,
                  "silent_fallback_allowed": False},
        "captions": {"srt": {"file": srt.name, "sha256": sha256(srt), "cues": 12},
                     "ass": {"file": ass.name, "sha256": sha256(ass), "cues": 12,
                             "play_res_x": 1280, "play_res_y": 840, "font_size": 24,
                             "bottom_panel_px": 120}},
        "script": {"file": script.name, "sha256": sha256(script)},
        "capture": {"schema": capture["report"]["schema"],
                    "verification_sha256": sha256(capture["report_path"]),
                    "ui_language": capture["report"]["ui_language"],
                    "browser_video_sha256": capture["browser_video_sha256"],
                    "screenshots": [{"file": name, "sha256": sha256(path)}
                                    for name, path in capture["screenshots"].items()]},
        "provider_evidence": {"verification_level": replay["replay"]["verification_level"],
                              "historical_actual_calls": {"muse_spark_1_3": 1, "sam_3_1": 1},
                              "video_sha256": replay["clip"]["video_sha256"]},
        "claims": {"new_model_calls": 0, "new_video_inference_calls": 0,
                   "new_reflection_calls": 0, "new_training_jobs": 0, "gpu_jobs": 0,
                   "sam_response_coverage": {"tracked_frames": 624, "total_frames": 625,
                                             "tracking_accuracy_measured": False},
                   "performance_improvement_measured": False,
                   "go2_result_scope": "manual_recorded_state_check_not_new_model_diagnosis"},
        "results": {"saved_provider_condition": capture["report"]["provider_result"],
                    "manual_eight_second_condition": capture["report"]["manual_eight_second_result"],
                    "manual_go2_full_duration_condition": capture["report"]["go2_result"]},
        "sampleframes": frames,
        "english_text_smoke": {"scene_text": True, "subtitle_assets": True,
                               "capture_report": True, "hangul_matches": 0},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2,
                                                   allow_nan=False) + "\n",
                                       encoding="utf-8", newline="\n")
    shutil.rmtree(build_dir)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the English-only 180-second Mollet demo")
    parser.add_argument("--capture", type=Path, required=True,
                        help="fresh meta-saved-recovery-capture-v1 folder with English UI proof")
    parser.add_argument("--out", type=Path, required=True, help="new output directory")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    replay = recovery.validate_saved_evidence()
    capture = recovery.validate_capture(args.capture.resolve(), replay)
    validate_english_capture(capture)
    if args.validate_only:
        print(json.dumps({"status": "validated", "ui_language": "en",
                          "historical_model_calls": 2, "new_model_calls": 0,
                          "source_commit": capture["source_commit"]}))
        return 0
    manifest = build(capture, replay, args.out.resolve())
    print(json.dumps({"status": "built", "output": manifest["output"],
                      "voice": VOICE_NAME, "language": manifest["language"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
