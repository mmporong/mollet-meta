#!/usr/bin/env python3
"""저장된 실제 Spark·SAM 결과 복구를 보여 주는 180초 발표 영상을 만든다.

새 모델 호출, 학습, GPU 작업은 수행하지 않는다. Chrome 캡처와 고정된 로컬
provider 영수증을 검증한 뒤, 한국어 음성판과 영어 자막판을 만든다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for entry in (str(SRC), str(HERE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import build_meta_behavior_demo as media  # noqa: E402
from rl_triage.behavior_authoring.datasets import example_paths, load_example_view  # noqa: E402
from rl_triage.behavior_authoring.saved_run import KNOWN_RUNS, load_saved_recovery  # noqa: E402


DURATION = 180.0
SCENE_DURATION = 15.0
CAPTURE_SCHEMA = "meta-saved-recovery-capture-v1"
OUTPUT_SCHEMA = "meta-saved-recovery-demo-v1"
SCREENSHOTS = (
    "01-restored-workspace.png",
    "02-provider-result.png",
    "03-eight-second-workspace.png",
    "04-go2-failure-workspace.png",
    "05-go2-failure-result.png",
    "06-full-workbench.png",
)
DOWNLOADS = (
    "01-provider-contract.json",
    "02-manual-eight-second-contract.json",
    "03-manual-strict-condition.json",
    "04-go2-recorded-condition.json",
)

SCENES = (
    (
        "title", "원하는 로봇 행동을 검사 가능한 조건으로 바꿉니다",
        "로봇이 계단을 올랐다는 보상만으로는 자세가 언제 무너졌는지 알기 어렵습니다. Mollet은 보고 싶은 행동을 시간과 수치가 있는 조건으로 바꿉니다.",
        "A reward for climbing stairs does not explain when posture failed. Mollet turns the behavior we want to inspect into a condition with time and numeric thresholds.",
    ),
    (
        "problem", "영상 근거와 상태 판정을 같은 시간축에 놓습니다",
        "영상의 객체 위치는 프레임에 맞추고, 자세 조건은 동기화된 로봇 상태에서 계산합니다. 모델 응답과 로컬 판정의 출처를 섞지 않습니다.",
        "Object locations are aligned to video frames, while posture conditions are computed from synchronized robot state. Model output and local verdicts keep separate provenance.",
    ),
    (
        "browser-replay", "Chrome에서 저장 결과를 복구합니다",
        "복구 버튼은 API 키 없이 작동합니다. 화면은 H1 원본, Spark 조건, SAM 상자를 되살리고 과거 승인과 판정은 버립니다.",
        "The restore button works without an API key. It restores the H1 source, Spark condition, and SAM boxes while discarding the old approval and verdict.",
    ),
    (
        "sam-motion", "625프레임 중 624프레임에 상자가 있습니다",
        "과거 실제 이력은 Spark 한 건과 SAM 한 건입니다. 저장된 SAM 상자를 H1 시간축에 맞췄고, 624 대 625는 응답 범위이지 추적 정확도가 아닙니다.",
        "The historical record contains one Spark call and one SAM call. Saved SAM boxes are aligned to the H1 timeline; 624 of 625 is response coverage, not tracking accuracy.",
    ),
    (
        "provider-result", "Spark 원조건은 0.5초 구간을 통과합니다",
        "복구된 조건은 수직축 정렬 0.8 이상, 시작 기한 8초, 유지 0.5초입니다. 현재 원본에서 다시 계산한 근거 구간은 0초부터 0.5초입니다.",
        "The restored condition requires up-axis alignment of at least 0.8, an 8-second deadline, and a 0.5-second hold. Recalculation on the current source yields 0.0 to 0.5 seconds.",
    ),
    (
        "manual-eight", "사용자가 유지 시간을 8초로 바꿉니다",
        "유지 시간을 8초로 바꾸면 검토 상태가 초기화됩니다. 조건을 다시 확인한 결과 H1 상행은 8초 자세 유지도 통과했습니다.",
        "Changing the hold time to 8 seconds resets review state. After review, the H1 ascent also passes the full 8-second posture condition.",
    ),
    (
        "manual-roundtrip", "수정한 조건도 다시 열 수 있습니다",
        "8초 조건을 내보내고 다시 불러왔습니다. 사용자 조건은 모델 결과 권한을 얻지 않으며, 승인도 미검토 상태로 돌아갑니다.",
        "The 8-second condition is exported and loaded again. A user-authored condition gains no model authority, and approval returns to unreviewed.",
    ),
    (
        "go2-workspace", "같은 검사식을 Go2 기록에도 적용합니다",
        "Go2 계단 하행에는 수동으로 12.48초 전체 자세 조건을 적용했습니다. 이 조건은 저장 상태 기록을 읽으며 새 모델 진단으로 표시되지 않습니다.",
        "A manual full-duration 12.48-second posture condition is applied to the Go2 stair descent. It reads recorded state and is not presented as a new model diagnosis.",
    ),
    (
        "go2-motion", "Go2 자세 위반은 저장 상태에서 계산됩니다",
        "영상과 동기화된 상태에서 수직축 정렬이 기준 아래로 내려갑니다. 판정 근거는 로봇 상태이며 Spark나 SAM의 새 추론이 아닙니다.",
        "Synchronized state shows up-axis alignment falling below the threshold. The verdict comes from robot state, not a new Spark or SAM inference.",
    ),
    (
        "go2-result", "Go2 전체 구간 조건은 실패합니다",
        "12.48초 동안 0.8 이상을 유지하는 조건은 실패했습니다. 이 결과는 저장 기록의 수동 비교이며 정책 회복이나 성능 개선을 뜻하지 않습니다.",
        "The requirement to remain at or above 0.8 for 12.48 seconds fails. This is a manual comparison on saved data, not policy recovery or measured improvement.",
    ),
    (
        "boundary", "이 시연이 확인한 범위를 지킵니다",
        "현재 브라우저에서 저장 응답 복구와 로컬 재검사를 확인했습니다. 새 영상 추론, 재검토, 학습은 모두 0건이고 성능 향상은 측정하지 않았습니다.",
        "This demo verifies saved-response recovery and local reevaluation in the current browser. New video inference, reflection, and training are all zero, and performance improvement is unmeasured.",
    ),
    (
        "end", "원본과 판정을 한 파일로 이어갑니다",
        "영상 해시, 공급자 원문 해시, 조건, 상태값, 객체 위치를 한 계보로 보존합니다. Mollet은 저장 근거를 다시 열어 다음 검사를 이어 가는 작업 공간입니다.",
        "Video hash, provider-response hashes, condition, state values, and object boxes remain in one lineage. Mollet reopens saved evidence so the next check can continue.",
    ),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 객체가 필요합니다: {path}")
    return value


def current_commit() -> str:
    return media.run(["git", "rev-parse", "HEAD"], capture=True)


def source_commit_matches_workbench(commit: object) -> bool:
    """캡처 commit이 현재 HEAD의 조상이며 작업 화면 소스가 그대로인지 확인한다."""
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        return False
    paths = (
        "src/rl_triage/behavior_authoring",
        "tools/capture_meta_recovery.cjs",
    )
    try:
        subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=ROOT, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=ROOT, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "diff", "--quiet", commit, "--", *paths], cwd=ROOT, check=True)
    except subprocess.CalledProcessError:
        return False
    return True


def _index(rows: object, label: str) -> dict[str, dict]:
    if not isinstance(rows, list):
        raise ValueError(f"캡처 보고서의 {label} 목록이 없습니다")
    result: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("file"), str):
            raise ValueError(f"캡처 보고서의 {label} 항목이 올바르지 않습니다")
        result[row["file"]] = row
    return result


def _verified_files(folder: Path, report: dict, field: str, names: tuple[str, ...]) -> dict[str, Path]:
    indexed = _index(report.get(field), field)
    if any(name not in indexed for name in names):
        raise ValueError(f"캡처 보고서에 필요한 {field} 해시가 없습니다")
    paths: dict[str, Path] = {}
    for name in names:
        path = folder / name
        expected = indexed[name].get("sha256")
        if not path.is_file() or not isinstance(expected, str) or sha256(path) != expected:
            raise ValueError(f"캡처 파일의 SHA-256이 다릅니다: {name}")
        if field == "downloads" and indexed[name].get("bytes") != path.stat().st_size:
            raise ValueError(f"다운로드 파일 크기가 보고서와 다릅니다: {name}")
        paths[name] = path
    return paths


def _assert_result(result: dict, verdict: str, start: float | None, end: float | None) -> None:
    if result.get("verdict") != verdict:
        raise ValueError(f"저장 판정이 예상한 {verdict}가 아닙니다")
    interval = result.get("interval")
    if start is None:
        if interval is not None:
            raise ValueError("판정 구간이 없어야 합니다")
    elif not isinstance(interval, dict) or interval.get("start_s") != start or interval.get("end_s") != end:
        raise ValueError(f"판정 구간이 {start}초부터 {end}초가 아닙니다")


def validate_saved_evidence() -> dict:
    replay = load_saved_recovery()
    if (replay.get("available") is not True
            or replay.get("replay", {}).get("verification_level") != "local_provider_receipts_verified"
            or replay["replay"].get("historical_model_calls") != 2
            or replay["replay"].get("new_model_calls") != 0):
        raise ValueError("저장된 실제 Meta 결과의 로컬 provider 원문 검증이 완료되지 않았습니다")
    receipt_by_run = {row.get("run_id"): row for row in replay["replay"].get("receipts", [])}
    for expected in KNOWN_RUNS.values():
        receipt = receipt_by_run.get(expected["run_id"])
        if not receipt or any(receipt.get(key) != expected[key] for key in (
                "model", "provider_request_id", "response_id", "request_sha256", "response_sha256")):
            raise ValueError("복구 응답의 provider 영수증이 고정 실행 증거와 다릅니다")
    if (len(replay["observations"]["samples"]) != 625
            or sum(bool(row["tracks"]) for row in replay["observations"]["samples"]) != 624
            or replay["observations"]["provenance"].get("capture_approved") is not False
            or replay["observations"]["provenance"].get("model_ready") is not False):
        raise ValueError("복구 응답의 H1 원본 또는 SAM 응답 범위가 다릅니다")
    condition = replay["contract"]["condition"]
    if condition != {"metric": "up_axis_z", "op": "gte", "threshold": 0.8,
                     "deadline_s": 8.0, "hold_s": 0.5}:
        raise ValueError("복구된 Spark 원조건이 예상 값과 다릅니다")
    _assert_result(replay["result"], "PASS", 0.0, 0.5)
    if replay["result"].get("stage") != "draft" or replay["result"].get("approval_status") != "unreviewed":
        raise ValueError("복구 결과는 미검토 초안이어야 합니다")
    return replay


def validate_capture(folder: Path, replay: dict) -> dict:
    report_path = folder / "verification.json"
    if not report_path.is_file():
        raise ValueError("Chrome 캡처 verification.json이 없습니다")
    report = read_json(report_path)
    commit = report.get("source_commit")
    if (report.get("schema") != CAPTURE_SCHEMA or report.get("passed") is not True
            or not source_commit_matches_workbench(commit)
            or report.get("new_model_calls") != 0 or report.get("gpu_jobs") != 0
            or report.get("browser_closed") is not True
            or report.get("page_errors") != [] or report.get("blocked_provider_requests") != []):
        raise ValueError("Chrome 캡처의 스키마, source commit 또는 무호출 검증이 다릅니다")
    if (report.get("video_sha256") != replay["clip"]["video_sha256"]
            or report.get("replay", {}).get("verification_level") != "local_provider_receipts_verified"
            or report["replay"].get("new_model_calls") != 0):
        raise ValueError("Chrome 캡처가 검증된 저장 복구 응답과 연결되지 않았습니다")
    screenshots = _verified_files(folder, report, "screenshots", SCREENSHOTS)
    downloads = _verified_files(folder, report, "downloads", DOWNLOADS)
    exports = {name: read_json(path) for name, path in downloads.items()}

    provider = exports[DOWNLOADS[0]]
    if (provider.get("clip", {}).get("id") != "h1-up"
            or provider.get("clip", {}).get("video_sha256") != replay["clip"]["video_sha256"]
            or provider.get("contract", {}).get("condition") != replay["contract"]["condition"]
            or len(provider.get("observations", {}).get("samples", [])) != 625
            or sum(bool(row.get("tracks")) for row in provider["observations"]["samples"]) != 624
            or provider["observations"].get("provenance", {}).get("capture_approved") is not False
            or provider["observations"].get("provenance", {}).get("model_ready") is not False):
        raise ValueError("Chrome에서 내보낸 실제 provider 복구 파일이 원본과 다릅니다")
    _assert_result(provider.get("result", {}), "PASS", 0.0, 0.5)
    if report.get("provider_result") != provider.get("result"):
        raise ValueError("캡처 보고서의 provider_result가 다운로드와 다릅니다")

    manual = exports[DOWNLOADS[1]]
    manual_condition = manual.get("contract", {}).get("condition", {})
    if (manual_condition.get("threshold") != 0.8 or manual_condition.get("deadline_s") != 8.0
            or manual_condition.get("hold_s") != 8.0):
        raise ValueError("사용자 8초 조건이 캡처 다운로드와 다릅니다")
    _assert_result(manual.get("result", {}), "PASS", 0.0, 8.0)
    if report.get("manual_eight_second_result") != manual.get("result"):
        raise ValueError("캡처 보고서의 8초 판정이 다운로드와 다릅니다")

    strict = exports[DOWNLOADS[2]]
    strict_condition = strict.get("contract", {}).get("condition", {})
    if (strict_condition.get("threshold") != 1.0 or strict_condition.get("deadline_s") != 0.0
            or strict_condition.get("hold_s") != 8.0):
        raise ValueError("엄격 조건의 임계값·기한·유지 시간이 캡처 흐름과 다릅니다")
    _assert_result(strict.get("result", {}), "FAIL", None, None)

    go2 = exports[DOWNLOADS[3]]
    go2_condition = go2.get("contract", {}).get("condition", {})
    if (go2.get("clip", {}).get("id") != "go2-down-slow"
            or go2_condition.get("threshold") != 0.8 or go2_condition.get("deadline_s") != 0.0
            or go2_condition.get("hold_s") != 12.48
            or go2.get("contract", {}).get("source", {}).get("model_called") is not False):
        raise ValueError("Go2 수동 전체 구간 조건의 출처 또는 값이 다릅니다")
    if go2.get("result", {}).get("verdict") != "FAIL" or report.get("go2_result") != go2.get("result"):
        raise ValueError("Go2 수동 자세 조건의 실패 판정이 캡처 보고서와 다릅니다")

    recording_name = report.get("browser_video")
    recording = folder / "browser-video" / str(recording_name)
    if not recording_name or not recording.is_file() or recording.stat().st_size == 0:
        raise ValueError("Chrome 실브라우저 녹화 파일이 없습니다")
    recording_sha = sha256(recording)
    if (report.get("browser_video_sha256") != recording_sha
            or report.get("browser_video_bytes") != recording.stat().st_size):
        raise ValueError("Chrome 실브라우저 녹화의 SHA-256 또는 크기가 보고서와 다릅니다")
    return {
        "report": report, "report_path": report_path, "screenshots": screenshots,
        "downloads": downloads, "exports": exports, "browser_video": recording,
        "browser_video_sha256": recording_sha, "browser_video_bytes": recording.stat().st_size,
        "source_commit": commit,
    }


def slide(path: Path, title: str, body: str, screenshot: Path | None, badges: list[tuple[str, str]]) -> None:
    image = Image.new("RGB", (media.WIDTH, media.HEIGHT), media.WHITE)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 16, media.HEIGHT), fill=media.BLUE)
    draw.text((60, 42), "MOLLET · SAVED META RECOVERY", font=media.font(19, bold=True), fill=media.BLUE)
    draw.text((60, 78), title, font=media.font(40, bold=True), fill=media.INK)
    y = 136
    for line in media.wrap(draw, body, media.font(23), 1150)[:3]:
        draw.text((60, y), line, font=media.font(23), fill=media.MUTED)
        y += 32
    if screenshot:
        source = Image.open(screenshot).convert("RGB")
        if "workspace" in screenshot.name:
            source = source.crop((20, 125, min(770, source.width), min(555, source.height)))
        source.thumbnail((1150, 405))
        x = (media.WIDTH - source.width) // 2
        top = 242
        image.paste(source, (x, top))
        draw.rounded_rectangle((x - 2, top - 2, x + source.width + 2, top + source.height + 2),
                               radius=8, outline=media.LINE, width=2)
    x = 60
    for label, value in badges:
        width = 215
        draw.rounded_rectangle((x, 660, x + width, 705), radius=11, fill=media.PALE_BLUE)
        draw.text((x + 12, 671), f"{label}  {value}", font=media.font(17, bold=True), fill=media.BLUE)
        x += width + 12
    image.save(path)


def make_motion(source: Path, start: float, duration: float, target: Path) -> None:
    media.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-stream_loop", "-1",
        "-i", str(source), "-ss", str(start), "-t", str(duration),
        "-vf", f"scale={media.WIDTH}:{media.HEIGHT}:force_original_aspect_ratio=decrease,"
               f"pad={media.WIDTH}:{media.HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=white,"
               f"fps={media.FPS},format=yuv420p",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(target),
    ])


def make_sample_frames(video: Path, folder: Path) -> list[dict]:
    folder.mkdir()
    frames = []
    for index, at_s in enumerate((7.5, 37.5, 67.5, 97.5, 127.5, 172.5), start=1):
        target = folder / f"sample-{index:02d}-{at_s:05.1f}s.png"
        media.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(at_s),
                   "-i", str(video), "-frames:v", "1", str(target)])
        frames.append({"file": target.relative_to(folder.parent).as_posix(), "at_s": at_s,
                       "sha256": sha256(target)})
    return frames


def burn_english_captions(source: Path, target: Path, ass: Path) -> None:
    """자막 좌표계를 최종 영상과 맞춰 하단 120px에 영어를 배치한다."""
    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1280", "PlayResY: 840", "WrapStyle: 2", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Arial,24,&H003A2A17,&H003A2A17,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,0,0,1,0,0,2,50,50,15,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    def timestamp(seconds: int) -> str:
        return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}.00"
    for index, (_, _, _, english) in enumerate(SCENES):
        caption = r"\N".join(textwrap.wrap(english, width=90))
        lines.append(f"Dialogue: 0,{timestamp(index * 15)},{timestamp((index + 1) * 15)},Default,,0,0,0,,{caption}")
    ass.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    escaped = str(ass.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    media.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
               "-vf", f"pad=1280:840:0:0:color=white,ass=filename='{escaped}',format=yuv420p",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "copy", "-t", str(DURATION),
               "-movflags", "+faststart", str(target)])


def write_text_assets(out: Path) -> tuple[Path, Path, Path]:
    ko_lines: list[str] = []
    en_lines: list[str] = []
    script_lines = ["# Mollet 저장된 실제 Meta 결과 복구 3분 대본", ""]
    current = 0.0
    for index, (_, title, narration, english) in enumerate(SCENES, start=1):
        end = current + SCENE_DURATION
        span = f"{media.srt_timestamp(current)} --> {media.srt_timestamp(end)}"
        ko_lines.extend([str(index), span, narration, ""])
        en_lines.extend([str(index), span, english, ""])
        script_lines.extend([f"## {media.srt_timestamp(current)} ~ {media.srt_timestamp(end)} · {title}", "", narration, ""])
        current = end
    ko = out / "mollet-meta-recovery-demo-ko.srt"
    en = out / "mollet-meta-recovery-demo-en.srt"
    script = out / "SCRIPT-ko.md"
    ko.write_text("\n".join(ko_lines), encoding="utf-8", newline="\n")
    en.write_text("\n".join(en_lines), encoding="utf-8", newline="\n")
    script.write_text("\n".join(script_lines), encoding="utf-8", newline="\n")
    return ko, en, script


def build(capture: dict, replay: dict, out: Path) -> dict:
    for tool in ("ffmpeg", "ffprobe", "powershell"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"필요한 실행 파일이 없습니다: {tool}")
    if out.exists():
        raise ValueError(f"새 출력 폴더가 필요합니다: {out}")
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
        "boundary": shots["01-restored-workspace.png"], "end": shots["01-restored-workspace.png"],
    }
    media.save_tracking_overlays(overlays, replay["observations"]["samples"], replay["contract"]["condition"])
    go2 = load_example_view("go2-down-slow")
    go2_overlay = build_dir / "go2-overlay.png"
    media.save_overlay(go2_overlay, "Go2 계단 하행 · 저장 상태 수동 검사",
                       "수직축 정렬 0.8 이상 · 전체 12.48초 · 새 모델 진단 아님",
                       go2["recorded_assessment"], go2["state_available"])

    video_parts: list[Path] = []
    audio_parts: list[Path] = []
    narration_modes: list[str] = []
    for index, (name, title, narration, _english) in enumerate(SCENES):
        video = videos / f"{index:02d}-{name}.mp4"
        if name == "browser-replay":
            make_motion(capture["browser_video"], 0.0, SCENE_DURATION, video)
        elif name == "sam-motion":
            media.make_tracked_video(example_paths("h1-up")[1], overlays, SCENE_DURATION, video)
        elif name == "go2-motion":
            media.make_video(example_paths("go2-down-slow")[1], go2_overlay, SCENE_DURATION, video)
        else:
            still = slides / f"{index:02d}-{name}.png"
            badges = [("과거 호출", "2"), ("이번 호출", "0"), ("GPU", "0")]
            if name == "problem": badges = [("영상", "frames"), ("상태", "synchronized"), ("출처", "separate")]
            if name == "provider-result": badges = [("threshold", "0.8"), ("deadline", "8 s"), ("hold", "0.5 s")]
            if name == "manual-eight": badges = [("hold", "8 s"), ("판정", "PASS"), ("출처", "user")]
            if name == "go2-result": badges = [("duration", "12.48 s"), ("판정", "FAIL"), ("모델", "0")]
            slide(still, title, narration, shot_map.get(name), badges)
            media.make_still(still, SCENE_DURATION, video)
        raw_audio = audios / f"{index:02d}-{name}-raw.wav"
        audio = audios / f"{index:02d}-{name}.wav"
        narration_modes.append(media.make_audio(narration, SCENE_DURATION, audio, raw_audio))
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
    final = out / "mollet-meta-recovery-demo-180s-ko.mp4"
    media.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(joined_video),
               "-i", str(joined_audio), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
               "-c:a", "aac", "-b:a", "160k", "-t", str(DURATION), "-movflags", "+faststart", str(final)])

    ko_srt, en_srt, script = write_text_assets(out)
    probe = media.probe(final)
    video_stream = next(row for row in probe["streams"] if row["codec_type"] == "video")
    audio_stream = next(row for row in probe["streams"] if row["codec_type"] == "audio")
    if (abs(float(probe["format"]["duration"]) - DURATION) > 0.1
            or video_stream.get("codec_name") != "h264" or video_stream.get("pix_fmt") != "yuv420p"
            or int(video_stream.get("width", 0)) != 1280 or int(video_stream.get("height", 0)) != 720
            or audio_stream.get("codec_name") != "aac"):
        raise RuntimeError("한국어 영상의 180초·1280x720·H.264/AAC 계약이 다릅니다")
    media.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(final), "-f", "null", os.devnull])

    english = out / "mollet-meta-recovery-demo-180s-en-captioned.mp4"
    english_ass = out / "mollet-meta-recovery-demo-en.ass"
    burn_english_captions(final, english, english_ass)
    english_probe = media.probe(english)
    english_stream = next(row for row in english_probe["streams"] if row["codec_type"] == "video")
    if (abs(float(english_probe["format"]["duration"]) - DURATION) > 0.1
            or english_stream.get("codec_name") != "h264" or english_stream.get("pix_fmt") != "yuv420p"
            or int(english_stream.get("width", 0)) != 1280 or int(english_stream.get("height", 0)) != 840):
        raise RuntimeError("영어 자막 영상의 180초·1280x840 계약이 다릅니다")
    media.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(english), "-f", "null", os.devnull])
    samples = make_sample_frames(final, out / "sampleframes")

    manifest = {
        "schema": OUTPUT_SCHEMA,
        "scope": "current_chrome_capture_of_saved_historical_actual_meta_recovery",
        "source_commit": capture["source_commit"],
        "renderer_commit": current_commit(),
        "duration_s": DURATION,
        "scenes": [{"name": row[0], "start_s": index * SCENE_DURATION,
                    "end_s": (index + 1) * SCENE_DURATION} for index, row in enumerate(SCENES)],
        "output": {"file": final.name, "sha256": sha256(final), "probe": probe, "full_decode_exit": 0},
        "english_captioned_output": {"file": english.name, "sha256": sha256(english),
                                       "probe": english_probe, "full_decode_exit": 0},
        "capture": {
            "verification_file": capture["report_path"].name,
            "verification_sha256": sha256(capture["report_path"]),
            "browser_video_sha256": capture["browser_video_sha256"],
            "browser_video_bytes": capture["browser_video_bytes"],
            "screenshots": [{"file": name, "sha256": sha256(path)} for name, path in capture["screenshots"].items()],
            "downloads": [{"file": name, "sha256": sha256(path)} for name, path in capture["downloads"].items()],
        },
        "provider_evidence": {
            "verification_level": replay["replay"]["verification_level"],
            "historical_actual_calls": {"muse_spark_1_3": 1, "sam_3_1": 1},
            "receipts": replay["replay"]["receipts"],
            "video_sha256": replay["clip"]["video_sha256"],
        },
        "claims": {
            "new_model_calls_during_capture": 0, "new_model_calls_during_render": 0,
            "new_video_inference_calls": 0, "new_reflection_calls": 0, "new_training_jobs": 0,
            "gpu_jobs": 0, "sam_response_coverage": {"tracked_frames": 624, "total_frames": 625,
                                                       "tracking_accuracy_measured": False},
            "performance_improvement_measured": False,
            "go2_result_scope": "manual_recorded_state_check_not_new_model_diagnosis",
        },
        "results": {
            "saved_provider_condition": capture["report"]["provider_result"],
            "manual_eight_second_condition": capture["report"]["manual_eight_second_result"],
            "manual_go2_full_duration_condition": capture["report"]["go2_result"],
        },
        "subtitles": {
            "ko": {"file": ko_srt.name, "sha256": sha256(ko_srt), "cues": 12},
            "en": {"file": en_srt.name, "sha256": sha256(en_srt), "cues": 12},
            "en_ass": {"file": english_ass.name, "sha256": sha256(english_ass), "cues": 12,
                       "play_res_x": 1280, "play_res_y": 840, "font_size": 24},
        },
        "script": {"file": script.name, "sha256": sha256(script)},
        "narration_modes": narration_modes,
        "sampleframes": samples,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                                       encoding="utf-8", newline="\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="저장된 실제 Meta 결과 복구 180초 데모 빌더")
    parser.add_argument("--capture", type=Path, required=True, help="meta-saved-recovery-capture-v1 폴더")
    parser.add_argument("--output", type=Path, required=True, help="새 출력 폴더")
    parser.add_argument("--validate-only", action="store_true", help="캡처와 provider 증거만 검증")
    args = parser.parse_args()
    replay = validate_saved_evidence()
    capture = validate_capture(args.capture.resolve(), replay)
    if args.validate_only:
        result = {
            "status": "validated", "schema": CAPTURE_SCHEMA, "source_commit": capture["source_commit"],
            "verification_level": replay["replay"]["verification_level"],
            "historical_model_calls": 2, "new_model_calls": 0,
            "screenshots": len(capture["screenshots"]), "downloads": len(capture["downloads"]),
        }
        print(json.dumps(result, ensure_ascii=False))
        return 0
    manifest = build(capture, replay, args.output.resolve())
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
