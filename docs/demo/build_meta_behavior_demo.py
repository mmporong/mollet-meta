"""Meta 행동 조건 편집기의 3분 근거 영상과 검증 기록을 만든다.

저장된 계단 영상, 상태 기록, 판정 코드만 사용한다. 브라우저 화면이나 모델 응답을
합성하지 않으며 네트워크, GPU, 유료 API를 호출하지 않는다.

실행:
    C:/Python314/python.exe docs/demo/build_meta_behavior_demo.py
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from rl_triage.behavior_authoring.datasets import (  # noqa: E402
    example_paths,
    load_example_view,
    stairs_contract,
)
from rl_triage.behavior_authoring.evaluate import evaluate_contract  # noqa: E402


WIDTH, HEIGHT, FPS, DURATION = 1280, 720, 30, 180.0
FONT_REGULAR = Path("C:/Windows/Fonts/malgun.ttf")
FONT_BOLD = Path("C:/Windows/Fonts/malgunbd.ttf")
OUTPUT = ROOT / "submission" / "meta-behavior-20261103-v1"
LIVE_OUTPUT = ROOT / "submission" / "meta-behavior-20261104-live-v1"

WHITE = "#ffffff"
INK = "#172a3a"
MUTED = "#536778"
LINE = "#d8e1e8"
BLUE = "#0866d9"
PALE_BLUE = "#eff5fc"
GREEN = "#137349"
RED = "#af3434"
AMBER = "#95600b"


SCENES = [
    ("title", 15.0, "Mollet은 저장된 로봇 학습 기록에서 행동 조건을 만들고, 어느 구간이 조건을 만족했는지 확인하는 로컬 작업 공간입니다."),
    ("problem", 15.0, "보상 점수만 보면 계단을 제대로 오르는지, 자세가 무너졌는지 설명하기 어렵습니다. 그래서 보고 싶은 행동을 시간과 수치가 있는 검사식으로 바꿉니다."),
    ("contract", 15.0, "이번 조건은 영상 시작부터 8초 동안 몸체의 수직축 정렬이 0.8 이상인지 확인합니다. 조건을 충족한 연속 구간은 통과, 관측이 부족하면 판단 보류로 남깁니다."),
    ("h1-up", 15.0, "H1 이족 로봇의 계단 상행입니다. 영상 프레임 625개와 상태 기록을 맞춰 검사했고, 8초 자세 유지 조건을 충족했습니다."),
    ("h1-down", 15.0, "같은 방식으로 H1 계단 하행도 확인합니다. 수직축 정렬의 최솟값과 근거 시간은 저장된 상태 기록에서 계산했습니다."),
    ("go2-up-slow", 15.0, "Go2 상행 0.25미터 매초 기록은 자세 유지 조건을 통과했습니다. 하지만 전진과 높이 조건은 통과하지 못했습니다. 한 조건의 통과를 전체 성공으로 넓히지 않습니다."),
    ("go2-down-slow", 15.0, "같은 속도의 하행은 전진과 높이 조건을 통과했지만 말미에 뒤집혔습니다. 프레임 상태에서 자세 유지 조건은 실패했고, 원기록도 불수용입니다."),
    ("go2-up-fast", 15.0, "명령 속도를 0.75미터 매초로 바꾼 상행입니다. 같은 정책의 명령 비교이며 새 학습이나 회복 결과는 아닙니다. 이 기록은 수용 판정을 받았습니다."),
    ("go2-down-fast", 15.0, "0.75미터 매초 하행도 저장된 진행, 높이, 자세 요약에서 수용됐습니다. 이 두 빠른 기록은 공개 시계열이 없어 요약 범위에서만 비교합니다."),
    ("compare", 15.0, "영상과 판정 범위를 분리합니다. H1과 Go2 0.25 기록은 프레임 상태로 검사합니다. Go2 0.75 기록은 공개 요약만 보여주며, 없는 시계열은 보간하지 않습니다."),
    ("meta", 15.0, "Muse Spark 요청은 자연어를 제한된 행동 조건 스키마로 바꿉니다. SAM 요청은 영상의 객체 추적을 준비합니다. 현재는 요청 내보내기와 저장 응답 가져오기까지 구현됐고 실제 Meta 호출은 0회입니다."),
    ("end", 15.0, "조건, 원본 해시, 판정 근거를 한 파일로 내보낼 수 있습니다. 이 영상은 저장 원본과 계산 결과를 편집한 시연입니다. 모델 성능이나 새 정책 회복을 주장하지 않습니다."),
]

LIVE_SCENES = [
    ("live-title", 15.0, "Mollet은 로봇 영상과 상태 기록을 행동 조건으로 묶습니다. 이번 시연은 저장된 Meta 응답 두 건과 H1 계단 원본으로 만든 3분 검증 영상입니다."),
    ("live-evidence", 15.0, "Muse Spark 1.3과 SAM 3.1 응답은 모두 인증된 HTTPS 호출로 완료됐습니다. 영상 제작 중에는 새 모델 호출을 하지 않았습니다."),
    ("live-spark", 15.0, "Spark가 만든 조건은 시작 시점 0초, 수직축 정렬 0.8 이상, 8초 안에 시작, 0.5초 유지입니다. 저장된 응답 해시와 사용량도 함께 남겼습니다."),
    ("live-sam", 15.0, "SAM은 H1 영상 625프레임을 처리했고 624프레임에서 객체 상자를 반환했습니다. 상자는 저장 응답의 프레임 번호에 맞춰 원본 위에 표시합니다."),
    ("h1-live", 15.0, "지금 보이는 파란 상자가 SAM 응답에서 가져온 프레임별 위치입니다. 위쪽에는 시간과 자세 값, 아래쪽에는 조건과 추적 수를 표시했습니다."),
    ("live-verdict", 15.0, "검사는 0초부터 0.5초까지 조건이 이어진 것을 확인해 통과로 판정했습니다. 판정은 영상 추적과 상태 값을 섞지 않고 각각의 출처를 보존합니다."),
    ("live-export", 15.0, "내보낸 JSON에는 조건, 625개 관측값, SAM 추적 상자, 검토된 판정과 승인 범위가 함께 들어 있습니다. 다시 읽은 결과도 원본과 같았습니다."),
    ("contract", 15.0, "자세 값 전체 구간을 그리면 임계값 0.8과 실제 기록의 간격을 확인할 수 있습니다. 이번 통과 구간은 시작 직후 0.5초입니다."),
    ("compare", 15.0, "Go2 기록은 보조 비교로만 씁니다. 프레임 상태가 있는 기록과 요약만 있는 기록을 구분하고, 없는 시계열은 만들지 않습니다."),
    ("live-integrity", 15.0, "두 Meta 응답의 요청과 응답 해시, 원본 영상 해시를 기록했습니다. 렌더링 과정의 새 API 호출과 GPU 작업은 모두 0건입니다."),
    ("live-flow", 15.0, "흐름은 자연어 조건 생성, 영상 객체 추적, 상태 기반 판정, 근거 내보내기 순서입니다. 각 단계는 같은 원본 영상 해시로 연결됩니다."),
    ("live-end", 15.0, "이 영상은 저장된 Meta 응답과 로봇 원본을 편집한 시연입니다. 브라우저 캡처나 새 학습 결과가 아니며, 재현에 필요한 해시와 판정 구간을 남겼습니다."),
]


def run(command: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=capture,
    )
    return result.stdout.strip() if capture else ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_BOLD if bold else FONT_REGULAR), size)


def wrap(draw: ImageDraw.ImageDraw, text: str, face: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}".strip()
            if current and draw.textbbox((0, 0), candidate, font=face)[2] > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return lines


def write_lines(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    face: ImageFont.FreeTypeFont,
    fill: str,
    max_width: int,
    spacing: int = 14,
) -> int:
    x, y = xy
    for line in wrap(draw, text, face, max_width):
        draw.text((x, y), line, font=face, fill=fill)
        y += face.size + spacing
    return y


def base_slide(kicker: str, title: str, body: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 18, HEIGHT), fill=BLUE)
    draw.text((72, 52), kicker, font=font(20, bold=True), fill=BLUE)
    write_lines(draw, (72, 96), title, font(50, bold=True), INK, 1130, 10)
    write_lines(draw, (72, 245), body, font(27), MUTED, 1110, 16)
    draw.line((72, 650, 1208, 650), fill=LINE, width=2)
    draw.text((72, 670), "Mollet · 저장 원본과 계산 결과로 편집한 시연", font=font(15), fill=MUTED)
    return image, draw


def card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], label: str, value: str, color: str = INK) -> None:
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(box, radius=12, fill="#f4f7fa", outline=LINE, width=2)
    draw.text((x1 + 20, y1 + 18), label, font=font(17), fill=MUTED)
    draw.text((x1 + 20, y1 + 54), value, font=font(26, bold=True), fill=color)


def save_slide(path: Path, kicker: str, title: str, body: str, cards: list[tuple[str, str, str]] | None = None) -> None:
    image, draw = base_slide(kicker, title, body)
    if cards:
        y1, y2 = 450, 575
        gap = 16
        width = (1136 - gap * (len(cards) - 1)) // len(cards)
        for index, (label, value, color) in enumerate(cards):
            x1 = 72 + index * (width + gap)
            card(draw, (x1, y1, x1 + width, y2), label, value, color)
    image.save(path)


def save_contract_slide(path: Path, values: list[float], result: dict, condition: dict | None = None) -> None:
    body = "영상 시작부터 8초 동안 수직축 정렬이 0.8 이상인지 확인합니다."
    if condition is not None:
        body = (
            f"수직축 정렬이 {condition['threshold']:.1f} 이상인 구간을 "
            f"{condition['deadline_s']:.0f}초 안에 시작해 {condition['hold_s']:.1f}초 유지하는지 확인합니다."
        )
    image, draw = base_slide(
        "BEHAVIOR CONTRACT",
        "보고 싶은 행동을 검사식으로 바꿉니다",
        body,
    )
    left, top, right, bottom = 90, 330, 1190, 570
    draw.rounded_rectangle((left, top, right, bottom), radius=14, fill="#f8fafc", outline=LINE, width=2)
    low, high = min(min(values), 0.78), max(max(values), 1.01)
    for tick in (0.8, 0.9, 1.0):
        y = bottom - 30 - (tick - low) / (high - low) * (bottom - top - 55)
        draw.line((left + 55, y, right - 25, y), fill=LINE, width=1)
        draw.text((left + 8, y - 10), f"{tick:.1f}", font=font(14), fill=MUTED)
    points = []
    for index, value in enumerate(values):
        x = left + 55 + index / max(1, len(values) - 1) * (right - left - 80)
        y = bottom - 30 - (value - low) / (high - low) * (bottom - top - 55)
        points.append((x, y))
    draw.line(points, fill=BLUE, width=3)
    threshold_y = bottom - 30 - (0.8 - low) / (high - low) * (bottom - top - 55)
    draw.line((left + 55, threshold_y, right - 25, threshold_y), fill=AMBER, width=3)
    draw.text((980, 600), f"판정: {result['verdict']}", font=font(25, bold=True), fill=GREEN)
    image.save(path)


def save_compare_slide(path: Path, views: dict[str, dict]) -> None:
    image, draw = base_slide(
        "RECORDED COMPARISON",
        "같은 정책, 다른 명령 속도",
        "0.25 m/s 기록은 프레임 상태를 연결합니다. 0.75 m/s 기록은 공개 요약만 표시하며 시계열을 만들지 않습니다.",
    )
    rows = [
        ("상행 0.25", "go2-up-slow"),
        ("하행 0.25", "go2-down-slow"),
        ("상행 0.75", "go2-up-fast"),
        ("하행 0.75", "go2-down-fast"),
    ]
    y = 365
    draw.text((100, y), "기록", font=font(18, bold=True), fill=MUTED)
    draw.text((360, y), "전진 변화", font=font(18, bold=True), fill=MUTED)
    draw.text((570, y), "높이 변화", font=font(18, bold=True), fill=MUTED)
    draw.text((785, y), "최저 자세", font=font(18, bold=True), fill=MUTED)
    draw.text((1010, y), "원기록", font=font(18, bold=True), fill=MUTED)
    for label, key in rows:
        y += 52
        item = views[key]["recorded_assessment"]
        accepted = bool(item["motion_accepted"])
        draw.line((90, y - 12, 1190, y - 12), fill=LINE, width=1)
        draw.text((100, y), label, font=font(19), fill=INK)
        draw.text((360, y), f"{item['root_dx_m']:+.3f} m", font=font(19), fill=INK)
        draw.text((570, y), f"{item['root_dz_m']:+.3f} m", font=font(19), fill=INK)
        draw.text((785, y), f"{item['min_up_axis_z']:.3f}", font=font(19), fill=INK)
        draw.text((1010, y), "수용" if accepted else "불수용", font=font(19, bold=True), fill=GREEN if accepted else RED)
    image.save(path)


def save_overlay(path: Path, title: str, subtitle: str, assessment: dict, state_available: bool) -> None:
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 88), fill=(255, 255, 255, 238))
    draw.rectangle((0, 634, WIDTH, HEIGHT), fill=(255, 255, 255, 242))
    draw.rectangle((0, 0, 14, HEIGHT), fill=BLUE)
    draw.text((44, 19), title, font=font(27, bold=True), fill=INK)
    draw.text((44, 55), subtitle, font=font(16), fill=MUTED)
    accepted = bool(assessment["motion_accepted"])
    result = "수용" if accepted else "불수용"
    draw.text((44, 656), f"전진 {assessment['root_dx_m']:+.3f} m   높이 {assessment['root_dz_m']:+.3f} m   최저 자세 {assessment['min_up_axis_z']:.3f}", font=font(19), fill=INK)
    draw.text((1085, 656), result, font=font(24, bold=True), fill=GREEN if accepted else RED)
    state_text = "프레임 상태 판정" if state_available else "원기록 요약"
    draw.rounded_rectangle((1015, 18, 1238, 64), radius=10, fill=PALE_BLUE, outline=LINE, width=1)
    draw.text((1040, 29), state_text, font=font(17, bold=True), fill=BLUE)
    image.save(path)


def save_tracking_overlays(folder: Path, samples: list[dict], condition: dict) -> None:
    """저장된 SAM 상자를 원본 프레임 시간축에 맞춘 투명 오버레이로 만든다."""
    folder.mkdir(exist_ok=True)
    title_face = font(25, bold=True)
    text_face = font(17)
    value_face = font(19, bold=True)
    object_face = font(14, bold=True)
    threshold = float(condition["threshold"])
    deadline = float(condition["deadline_s"])
    hold = float(condition["hold_s"])
    for sample in samples:
        image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, WIDTH, 92), fill=(255, 255, 255, 238))
        draw.rectangle((0, 630, WIDTH, HEIGHT), fill=(255, 255, 255, 242))
        draw.rectangle((0, 0, 14, HEIGHT), fill=BLUE)
        draw.text((42, 15), "H1 계단 상행 · SAM 3.1 저장 응답", font=title_face, fill=INK)
        value = float(sample["values"]["up_axis_z"])
        draw.text(
            (42, 55),
            f"frame {sample['frame']:03d}  ·  t={float(sample['t_s']):.2f}s  ·  up_axis_z={value:.4f}",
            font=text_face,
            fill=MUTED,
        )
        for track in sample.get("tracks", []):
            x1, y1, x2, y2 = track["bbox"]
            box = (round(x1 * WIDTH), round(y1 * HEIGHT), round(x2 * WIDTH), round(y2 * HEIGHT))
            draw.rectangle(box, outline=BLUE, width=5)
            label_y = max(98, box[1] - 31)
            draw.rounded_rectangle((box[0], label_y, box[0] + 130, label_y + 27), radius=5, fill=(8, 102, 217, 225))
            draw.text((box[0] + 7, label_y + 3), f"SAM object {track['object_id']}", font=object_face, fill=WHITE)
        passed = value >= threshold
        draw.text(
            (42, 650),
            f"조건  up_axis_z >= {threshold:.1f}  ·  {deadline:.0f}초 안에 시작  ·  {hold:.1f}초 유지",
            font=text_face,
            fill=INK,
        )
        draw.text((1035, 650), "충족" if passed else "미충족", font=value_face, fill=GREEN if passed else RED)
        draw.text((42, 684), f"이 프레임의 SAM 추적 수: {len(sample.get('tracks', []))}", font=text_face, fill=MUTED)
        image.save(folder / f"frame-{int(sample['frame']):06d}.png")


def make_still(image: Path, duration: float, target: Path) -> None:
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-loop", "1", "-t", str(duration), "-i", str(image),
        "-vf", f"fps={FPS},format=yuv420p", "-an", "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "20", str(target),
    ])


def make_video(source: Path, overlay: Path, duration: float, target: Path) -> None:
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-loop", "1", "-i", str(overlay),
        "-filter_complex",
        f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=white,tpad=stop_mode=clone:stop_duration={duration}[base];[base][1:v]overlay=0:0:shortest=1,fps={FPS},format=yuv420p[v]",
        "-map", "[v]", "-t", str(duration), "-an", "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "20", str(target),
    ])


def make_tracked_video(source: Path, overlays: Path, duration: float, target: Path) -> None:
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-framerate", "50", "-start_number", "0",
        "-i", str(overlays / "frame-%06d.png"),
        "-filter_complex",
        f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=white,tpad=stop_mode=clone:stop_duration={duration}[base];"
        f"[1:v]tpad=stop_mode=clone:stop_duration={duration}[marks];"
        f"[base][marks]overlay=0:0:shortest=1,fps={FPS},format=yuv420p[v]",
        "-map", "[v]", "-t", str(duration), "-an", "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "20", str(target),
    ])


def tts(text: str, target: Path) -> bool:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    target_escaped = str(target).replace("'", "''")
    command = (
        "$text=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "'));"
        "$voice=New-Object -ComObject SAPI.SpVoice;"
        "$candidate=@($voice.GetVoices() | Where-Object {$_.GetDescription() -match 'Korean'})[0];"
        "if($candidate){$voice.Voice=$candidate};$voice.Rate=1;"
        "$stream=New-Object -ComObject SAPI.SpFileStream;"
        "$stream.Open('" + target_escaped + "',3,$false);"
        "$voice.AudioOutputStream=$stream;[void]$voice.Speak($text);$stream.Close()"
    )
    try:
        run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command])
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return target.is_file() and target.stat().st_size > 44


def probe_duration(path: Path) -> float:
    return float(run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ], capture=True))


def atempo_chain(speed: float) -> str:
    parts: list[float] = []
    while speed > 2.0:
        parts.append(2.0)
        speed /= 2.0
    while speed < 0.5:
        parts.append(0.5)
        speed /= 0.5
    parts.append(speed)
    return ",".join(f"atempo={value:.8f}" for value in parts)


def make_audio(text: str, duration: float, target: Path, raw: Path) -> str:
    spoken = tts(text, raw)
    if not spoken:
        run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
            "-i", "anullsrc=r=48000:cl=stereo", "-t", str(duration), "-c:a", "pcm_s16le", str(target),
        ])
        return "silent_fallback"
    speech_duration = probe_duration(raw)
    filters = []
    available = max(0.5, duration - 0.8)
    if speech_duration > available:
        filters.append(atempo_chain(speech_duration / available))
    filters.extend(["adelay=300|300", "apad", "aresample=48000", "aformat=sample_fmts=s16:channel_layouts=stereo"])
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw),
        "-af", ",".join(filters), "-t", str(duration), "-c:a", "pcm_s16le", str(target),
    ])
    return "sapi_korean"


def srt_timestamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def probe(path: Path) -> dict:
    return json.loads(run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,size:stream=index,codec_type,codec_name,width,height,r_frame_rate,pix_fmt,sample_rate,channels",
        "-of", "json", str(path),
    ], capture=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--live-evidence",
        type=Path,
        help="저장된 Meta 응답을 포함한 mollet-behavior-contract.json 경로",
    )
    parser.add_argument("--rebuild", action="store_true", help="이 빌더가 만든 같은 스키마의 출력만 다시 생성")
    args = parser.parse_args()
    live_mode = args.live_evidence is not None
    out = (args.out or (LIVE_OUTPUT if live_mode else OUTPUT)).resolve()
    scenes = LIVE_SCENES if live_mode else SCENES
    output_schema = "meta_behavior_demo_live_v1" if live_mode else "meta_behavior_demo_v1"
    if out.exists() and args.rebuild:
        manifest_path = out / "manifest.json"
        if out.parent != (ROOT / "submission").resolve() or not manifest_path.is_file():
            raise SystemExit(f"재생성할 출력 경로를 검증하지 못했습니다: {out}")
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if previous.get("schema") != output_schema:
            raise SystemExit(f"이 빌더의 출력이 아닙니다: {out}")
        shutil.rmtree(out)
    if out.exists():
        existing = [path for path in out.rglob("*") if path.is_file()]
        if existing:
            raise SystemExit(f"새 출력 경로가 필요합니다: {out}")
    build = out / "_build"
    build.mkdir(parents=True, exist_ok=True)

    if not FONT_REGULAR.is_file() or not FONT_BOLD.is_file():
        raise RuntimeError("맑은 고딕 글꼴을 찾지 못했습니다")
    for tool in ("ffmpeg", "ffprobe", "powershell"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"필요한 실행 파일이 없습니다: {tool}")
    if not math.isclose(sum(scene[1] for scene in scenes), DURATION):
        raise RuntimeError("장면 합계가 180초가 아닙니다")

    live_export = None
    live_verification = None
    if live_mode:
        evidence_path = args.live_evidence.resolve()
        verification_path = evidence_path.with_name("verification.json")
        if not evidence_path.is_file() or not verification_path.is_file():
            raise RuntimeError("live evidence와 같은 폴더의 verification.json이 필요합니다")
        live_export = json.loads(evidence_path.read_text(encoding="utf-8"))
        live_verification = json.loads(verification_path.read_text(encoding="utf-8"))
        if live_export.get("model_calls_in_this_app") != 2:
            raise RuntimeError("저장된 provider 호출 수가 2건이 아닙니다")
        if live_verification.get("tracked_sample_count") != 624 or live_verification.get("sample_count") != 625:
            raise RuntimeError("SAM 표본 수 검증값이 624/625와 다릅니다")
        if sha256(evidence_path) != live_verification.get("export_sha256"):
            raise RuntimeError("live evidence 해시가 verification.json과 다릅니다")

    keys = ["h1-up", "h1-down", "go2-up-slow", "go2-down-slow", "go2-up-fast", "go2-down-fast"]
    views = {key: load_example_view(key) for key in keys}
    contract = live_export["contract"] if live_mode else stairs_contract()
    state_results = {}
    for key, view in views.items():
        if view["observations"] is not None:
            state_results[key] = evaluate_contract(view["default_contract"], view["observations"])
    h1_results = {key: state_results[key] for key in ("h1-up", "h1-down")}
    live_samples = live_export["observations"]["samples"] if live_mode else None
    h1_values = [
        sample["values"]["up_axis_z"]
        for sample in (live_samples if live_mode else views["h1-up"]["observations"]["samples"])
    ]

    slides = build / "slides"
    overlays = build / "overlays"
    video_parts = build / "video"
    audio_parts = build / "audio"
    for folder in (slides, overlays, video_parts, audio_parts):
        folder.mkdir(exist_ok=True)

    if live_mode:
        spark_receipt = live_verification["receipts"]["spark"]
        sam_receipt = live_verification["receipts"]["sam"]
        spark_usage = live_verification["usage"]["spark"]
        condition = contract["condition"]
        reviewed = live_verification["reviewed_result"]
        save_slide(
            slides / "live-title.png", "META GLOBAL AI DEVELOPER HACKATHON 2026",
            "Mollet 행동 조건 편집기",
            "Muse Spark가 행동 조건을 만들고 SAM이 H1을 추적합니다. 상태 기록으로 판정한 뒤 조건과 근거를 한 파일로 내보냅니다.",
            [("Spark", "completed", GREEN), ("SAM", "624 / 625", GREEN), ("영상", "180초", BLUE)],
        )
        save_slide(
            slides / "live-evidence.png", "LIVE META EVIDENCE",
            "저장된 provider 응답 두 건",
            f"Spark response SHA256  {spark_receipt['response_sha256']}\nSAM response SHA256  {sam_receipt['response_sha256']}",
            [("기존 호출", "2건", GREEN), ("렌더 중 새 호출", "0건", BLUE), ("상태", "completed", GREEN)],
        )
        save_slide(
            slides / "live-spark.png", "MUSE SPARK 1.3",
            "자연어를 제한된 행동 조건으로 변환",
            f"시작 {contract['trigger']['at_s']:.0f}초 · up_axis_z >= {condition['threshold']:.1f} · {condition['deadline_s']:.0f}초 안에 시작 · {condition['hold_s']:.1f}초 유지\n응답 SHA256  {spark_receipt['response_sha256']}",
            [("입력 토큰", f"{spark_usage['input_tokens']:,}", BLUE), ("출력 토큰", f"{spark_usage['output_tokens']:,}", BLUE), ("합계", f"{spark_usage['total_tokens']:,}", BLUE)],
        )
        save_slide(
            slides / "live-sam.png", "SAM 3.1",
            "원본 H1 영상의 프레임별 객체 추적",
            f"625프레임을 처리했고 624프레임에 객체 상자가 있습니다.\n응답 SHA256  {sam_receipt['response_sha256']}",
            [("처리", "625 frames", BLUE), ("추적", "624 frames", GREEN), ("정확도 주장", "없음", AMBER)],
        )
        save_slide(
            slides / "live-verdict.png", "STATE EVALUATION",
            "조건을 충족한 연속 구간",
            reviewed["reason"],
            [("판정", reviewed["verdict"], GREEN), ("시작", f"{reviewed['interval']['start_s']:.1f}s", BLUE), ("종료", f"{reviewed['interval']['end_s']:.1f}s", BLUE)],
        )
        save_slide(
            slides / "live-export.png", "EVIDENCE EXPORT",
            "조건, 관측값, 추적 상자, 판정을 한 파일에 저장",
            f"내보내기 SHA256  {live_verification['export_sha256']}\n저장 후 다시 읽은 결과는 원본과 같습니다.",
            [("round trip", "PASS", GREEN), ("관측값", "625 samples", BLUE), ("검토", "approved", GREEN)],
        )
        save_slide(
            slides / "live-integrity.png", "PROVENANCE",
            "새 호출 없이 저장 근거만 렌더링",
            f"원본 영상 SHA256  {live_export['clip']['video_sha256']}\nSpark와 SAM은 같은 영상 해시로 묶여 있습니다.",
            [("새 API 호출", "0", GREEN), ("GPU 작업", "0", GREEN), ("provider 응답", "2", BLUE)],
        )
        save_slide(
            slides / "live-flow.png", "WORKFLOW",
            "조건 생성부터 근거 내보내기까지",
            "자연어 조건 생성  >  영상 객체 추적  >  상태 기반 판정  >  JSON 근거 내보내기",
            [("조건", "Spark", BLUE), ("추적", "SAM", BLUE), ("판정", "local", GREEN)],
        )
        save_slide(
            slides / "live-end.png", "SCOPE",
            "저장 응답과 원본을 편집한 시연",
            "브라우저 캡처, 새 학습, 정책 회복을 주장하지 않습니다. 응답 해시와 판정 구간으로 결과를 다시 확인할 수 있습니다.",
            [("원본", "H1 stairs", BLUE), ("판정", "0.0-0.5s", GREEN), ("형식", "evidence JSON", BLUE)],
        )
    else:
        save_slide(
            slides / "title.png", "META GLOBAL AI DEVELOPER HACKATHON 2026",
            "Mollet 행동 조건 편집기",
            "저장된 이족·사족 계단 기록에서 보고 싶은 행동을 검사 가능한 조건으로 만듭니다.",
            [("형식", "3분 근거 영상", BLUE), ("Meta 호출", "0회", AMBER), ("새 학습", "0회", AMBER)],
        )
        save_slide(
            slides / "problem.png", "PROBLEM",
            "보상 점수만으로는 행동을 설명하기 어렵습니다",
            "계단을 끝까지 이동했는지, 자세가 유지됐는지, 어느 구간에서 무너졌는지를 저장 기록에서 확인합니다.",
            [("입력", "원본 영상·상태", BLUE), ("조건", "시간·지표·임계값", BLUE), ("결과", "통과·위반·보류", BLUE)],
        )
    save_contract_slide(
        slides / "contract.png",
        h1_values,
        live_export["result"] if live_mode else h1_results["h1-up"],
        contract["condition"] if live_mode else None,
    )
    save_compare_slide(slides / "compare.png", views)
    if not live_mode:
        save_slide(
            slides / "meta.png", "META TOOLS",
            "요청을 만들고 저장 응답을 연결합니다",
            "Muse Spark 1.3은 자연어 조건 요청을 만듭니다. SAM 3.1은 영상 객체 추적 요청을 만듭니다. 유료 API 금지를 유지해 실제 호출과 응답은 없습니다.",
            [("요청 생성", "구현", GREEN), ("응답 가져오기", "구현", GREEN), ("실제 호출", "미실행", AMBER)],
        )
        save_slide(
            slides / "end.png", "EVIDENCE EXPORT",
            "조건과 근거를 함께 남깁니다",
            "영상 해시, 원기록 요약, 조건, 판정 구간을 JSON으로 내보냅니다. 이 시연은 모델 성능이나 새 정책 회복을 주장하지 않습니다.",
            [("H1", "상태 판정", GREEN), ("Go2", "요약 표시", BLUE), ("검증", "원본 해시 결합", GREEN)],
        )

    labels = {
        "h1-up": ("H1 이족 · 계단 상행", "625프레임 상태 판정 · 자세 유지 PASS"),
        "h1-down": ("H1 이족 · 계단 하행", "625프레임 상태 판정 · 자세 유지 PASS"),
        "go2-up-slow": ("Go2 사족 · 계단 상행 · 0.25 m/s", "자세 PASS · 진행·높이 미통과 · 원기록 불수용"),
        "go2-down-slow": ("Go2 사족 · 계단 하행 · 0.25 m/s", "자세 FAIL · 뒤집힘 확인 · 원기록 불수용"),
        "go2-up-fast": ("Go2 사족 · 계단 상행 · 0.75 m/s", "같은 정책의 명령 비교 · 원기록 수용"),
        "go2-down-fast": ("Go2 사족 · 계단 하행 · 0.75 m/s", "같은 정책의 명령 비교 · 원기록 수용"),
    }
    for key in keys:
        save_overlay(
            overlays / f"{key}.png", *labels[key], views[key]["recorded_assessment"], views[key]["state_available"]
        )

    if live_mode:
        tracking_overlays = build / "tracking-overlays"
        save_tracking_overlays(tracking_overlays, live_samples, contract["condition"])
        slide_files = {
            name: slides / f"{name}.png"
            for name in (
                "live-title", "live-evidence", "live-spark", "live-sam", "live-verdict",
                "live-export", "contract", "compare", "live-integrity", "live-flow", "live-end",
            )
        }
    else:
        slide_files = {
            "title": slides / "title.png", "problem": slides / "problem.png",
            "contract": slides / "contract.png", "compare": slides / "compare.png",
            "meta": slides / "meta.png", "end": slides / "end.png",
        }
    video_list: list[Path] = []
    audio_list: list[Path] = []
    narration_modes: list[str] = []
    for index, (name, duration, narration) in enumerate(scenes):
        video_target = video_parts / f"{index:02d}-{name}.mp4"
        if name in slide_files:
            make_still(slide_files[name], duration, video_target)
        elif live_mode and name == "h1-live":
            make_tracked_video(example_paths("h1-up")[1], tracking_overlays, duration, video_target)
        else:
            make_video(example_paths(name)[1], overlays / f"{name}.png", duration, video_target)
        video_list.append(video_target)
        audio_target = audio_parts / f"{index:02d}-{name}.wav"
        raw_target = audio_parts / f"{index:02d}-{name}-raw.wav"
        narration_modes.append(make_audio(narration, duration, audio_target, raw_target))
        audio_list.append(audio_target)

    video_concat = build / "video-list.txt"
    audio_concat = build / "audio-list.txt"
    video_concat.write_text("".join(f"file '{path.as_posix()}'\n" for path in video_list), encoding="utf-8")
    audio_concat.write_text("".join(f"file '{path.as_posix()}'\n" for path in audio_list), encoding="utf-8")
    joined_video = build / "joined-video.mp4"
    joined_audio = build / "joined-audio.wav"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(video_concat), "-c", "copy", str(joined_video)])
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(audio_concat), "-c", "copy", str(joined_audio)])

    final_video = out / "mollet-meta-behavior-demo-3min.mp4"
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(joined_video), "-i", str(joined_audio),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
        "-t", str(DURATION), "-movflags", "+faststart", str(final_video),
    ])

    srt_lines: list[str] = []
    script_lines = ["# Mollet 3분 데모 대본", "", "이 영상은 저장 원본과 계산 결과를 편집한 시연입니다.", ""]
    current = 0.0
    for index, (name, duration, narration) in enumerate(scenes, start=1):
        end = current + duration
        srt_lines.extend([str(index), f"{srt_timestamp(current)} --> {srt_timestamp(end)}", narration, ""])
        script_lines.extend([f"## {srt_timestamp(current)} - {srt_timestamp(end)} · {name}", "", narration, ""])
        current = end
    srt_path = out / "mollet-meta-behavior-demo-ko.srt"
    script_path = out / "SCRIPT-ko.md"
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8", newline="\n")
    script_path.write_text("\n".join(script_lines), encoding="utf-8", newline="\n")

    final_probe = probe(final_video)
    video_stream = next(stream for stream in final_probe["streams"] if stream["codec_type"] == "video")
    audio_stream = next(stream for stream in final_probe["streams"] if stream["codec_type"] == "audio")
    final_duration = float(final_probe["format"]["duration"])
    if abs(final_duration - DURATION) > 0.1:
        raise RuntimeError(f"최종 영상 길이가 180초와 다릅니다: {final_duration}")
    if (video_stream.get("codec_name") != "h264" or video_stream.get("pix_fmt") != "yuv420p"
            or int(video_stream.get("width", 0)) != WIDTH or int(video_stream.get("height", 0)) != HEIGHT
            or audio_stream.get("codec_name") != "aac"):
        raise RuntimeError("최종 영상의 코덱·해상도·픽셀 형식이 계약과 다릅니다")
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(final_video), "-f", "null", "NUL"])

    verification_dir = out / "verification-frames"
    verification_dir.mkdir()
    verification_frames = []
    frame_times = (5, 20, 35, 50, 67, 82, 97, 112, 127, 157, 172) if live_mode else (5, 36, 50, 95, 126, 156)
    for seconds in frame_times:
        frame_path = verification_dir / f"frame-{seconds:03d}s.png"
        run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(seconds),
            "-i", str(final_video), "-frames:v", "1", "-update", "1", str(frame_path),
        ])
        verification_frames.append({
            "time_s": seconds,
            "file": frame_path.relative_to(out).as_posix(),
            "sha256": sha256(frame_path),
        })

    sources = {}
    for key in keys:
        _, source_video, metadata = example_paths(key)
        sources[key] = {
            "video": source_video.relative_to(ROOT).as_posix(),
            "video_sha256": sha256(source_video),
            "metadata": metadata.relative_to(ROOT).as_posix(),
            "metadata_sha256": sha256(metadata),
            "state_available": views[key]["state_available"],
            "recorded_assessment": views[key]["recorded_assessment"],
            "condition_result": state_results.get(key),
            "state_provenance": views[key]["provenance"],
            "presentation_edit": {
                "source_clip_duration_s": 12.5,
                "segment_duration_s": 15.0,
                "freeze_last_frame_s": 2.5,
                "speed_changed": False,
            },
        }
    manifest = {
        "schema": output_schema,
        "title": "Mollet 행동 조건 편집기 3분 데모",
        "scope": "stored_source_and_computed_evidence_edit_not_browser_capture",
        "duration_contract_s": DURATION,
        "output": {
            "file": final_video.name,
            "sha256": sha256(final_video),
            "bytes": final_video.stat().st_size,
            "probe": final_probe,
            "full_decode_exit": 0,
        },
        "subtitles": {"file": srt_path.name, "sha256": sha256(srt_path), "cues": len(scenes)},
        "script": {"file": script_path.name, "sha256": sha256(script_path)},
        "builder": {"file": "docs/demo/build_meta_behavior_demo.py", "sha256": sha256(Path(__file__))},
        "behavior_contract": contract,
        "state_results": state_results,
        "sources": sources,
        "narration_modes": narration_modes,
        "claims": {
            "browser_capture": False,
            "new_meta_model_calls_during_render": 0,
            "existing_provider_responses": 2 if live_mode else 0,
            "paid_api_calls_during_render": 0,
            "gpu_jobs": 0,
            "new_training": 0,
            "new_recovery_claim": False,
            "spark_status": (
                "completed_provider_response_reused"
                if live_mode else "request_builder_and_saved_response_import_only"
            ),
            "sam_status": (
                "completed_provider_response_reused_624_of_625_bbox_frames"
                if live_mode else "request_builder_and_saved_response_import_only"
            ),
            "go2_state_status": "0p25_state_backed_0p75_summary_only_no_synthetic_time_series",
        },
        "verification_frames": verification_frames,
    }
    if live_mode:
        manifest["behavior_contract"] = live_export["contract"]
        manifest["state_results"]["h1-up-live-reviewed"] = live_export["result"]
        manifest["live_evidence"] = {
            "export_file": args.live_evidence.resolve().relative_to(ROOT).as_posix(),
            "export_sha256": sha256(args.live_evidence.resolve()),
            "verification_file": args.live_evidence.resolve().with_name("verification.json").relative_to(ROOT).as_posix(),
            "verification_sha256": sha256(args.live_evidence.resolve().with_name("verification.json")),
            "spark": {
                "model": live_verification["receipts"]["spark"]["model"],
                "status": live_verification["provider_completed"]["spark"],
                "response_sha256": live_verification["receipts"]["spark"]["response_sha256"],
                "usage": live_verification["usage"]["spark"],
            },
            "sam": {
                "model": live_verification["receipts"]["sam"]["model"],
                "status": live_verification["provider_completed"]["sam"],
                "response_sha256": live_verification["receipts"]["sam"]["response_sha256"],
                "video_frames_processed": live_verification["usage"]["sam"]["video_frames_processed"],
                "sample_count": live_verification["sample_count"],
                "tracked_sample_count": live_verification["tracked_sample_count"],
                "tracking_accuracy_measured": live_verification["tracking_accuracy_measured"],
            },
            "export_roundtrip_equal": live_verification["export_roundtrip_equal"],
            "source_files_sha256": live_verification["source_files_sha256"],
        }
    else:
        manifest["claims"]["meta_model_calls"] = 0
        manifest["claims"]["paid_api_calls"] = 0
    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    shutil.rmtree(build)
    print(json.dumps({
        "video": str(final_video), "duration_s": final_duration,
        "sha256": manifest["output"]["sha256"], "bytes": final_video.stat().st_size,
        "audio": audio_stream, "h1_results": {key: value["verdict"] for key, value in h1_results.items()},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
