#!/usr/bin/env python3
"""Build the allowlisted, credential-free Mollet public demo package."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUTPUT = ROOT / "deployment" / "meta-public-demo"
WEB = SRC / "rl_triage" / "behavior_authoring" / "web"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from rl_triage.behavior_authoring.datasets import example_paths, list_examples, load_example_view, stairs_contract  # noqa: E402
from rl_triage.behavior_authoring.saved_run import load_saved_recovery  # noqa: E402

EXAMPLE_NAMES = {
    "h1-up": "H1 biped · stair ascent", "h1-down": "H1 biped · stair descent",
    "go2-up-slow": "Go2 quadruped · stair ascent · 0.25 m/s",
    "go2-down-slow": "Go2 quadruped · stair descent · 0.25 m/s",
    "go2-up-fast": "Go2 quadruped · stair ascent · 0.75 m/s",
    "go2-down-fast": "Go2 quadruped · stair descent · 0.75 m/s",
}
EXAMPLE_CAPTIONS = {
    "h1-up": "Render-synchronized H1 stair-ascent video and state trace. The time condition is evaluated from saved state; video approval remains separate.",
    "h1-down": "Render-synchronized H1 stair-descent video and state trace. The time condition is evaluated from saved state; video approval remains separate.",
    "go2-up-slow": "Recorded Go2 stair ascent at 0.25 m/s with a source-bound frame-level state trace.",
    "go2-down-slow": "Recorded Go2 stair descent at 0.25 m/s with a source-bound frame-level state trace.",
    "go2-up-fast": "Recorded Go2 stair ascent at 0.75 m/s. This public record contains summary evidence only, so no time-series verdict is created.",
    "go2-down-fast": "Recorded Go2 stair descent at 0.75 m/s. This public record contains summary evidence only, so no time-series verdict is created.",
}
RESULT_TEXT = {
    "마감 시점 안에 조건을 시작해 필요한 유지 시간 동안 연속으로 충족했습니다.":
        "The condition began before the deadline and remained satisfied for the required hold time.",
}


def _english_contract(contract: dict) -> dict:
    value = json.loads(json.dumps(contract))
    value["title"] = "Maintain upright posture on stairs"
    value["instruction"] = "Check whether upright alignment remains at or above 0.8 for the required interval from the start of the clip."
    return value


def _english_view(example_id: str, value: dict) -> dict:
    value["name"] = EXAMPLE_NAMES[example_id]
    value["caption"] = EXAMPLE_CAPTIONS[example_id]
    value["default_contract"] = _english_contract(value["default_contract"])
    return value


def _json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n",
                    encoding="utf-8", newline="\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _commit() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


def build() -> dict:
    public, data, runtime = OUTPUT / "public", OUTPUT / "data", OUTPUT / "runtime"
    for generated in (public, data, runtime / "__pycache__", OUTPUT / "api" / "__pycache__"):
        if generated.exists():
            shutil.rmtree(generated)
    for folder in (public, data, runtime):
        folder.mkdir(parents=True, exist_ok=True)

    for name in ("index.html", "app.js", "improvement.js", "style.css"):
        shutil.copyfile(WEB / name, public / name)
    index = public / "index.html"
    html = index.read_text(encoding="utf-8")
    marker = "</header>"
    if marker not in html:
        raise RuntimeError("The workbench header marker is missing.")
    html = html.replace(
        marker,
        marker + ("\n<div class=\"public-banner\" role=\"status\"><strong>Public evidence demo</strong> "
                  "Saved Spark and SAM results only · no API key · no new model calls · no training</div>"),
        1,
    )
    index.write_text(html, encoding="utf-8", newline="\n")
    with (public / "style.css").open("a", encoding="utf-8", newline="\n") as stream:
        stream.write("\n/* The public evidence deployment has no provider or training controls. */\n"
                     ".connections{display:none!important}\n"
                     ".public-banner{padding:10px 4%;background:#eff5fc;border-bottom:1px solid #d8e1e8;"
                     "color:#172a3a;font-size:13px;text-align:center}.public-banner strong{color:#0866d9}\n")
    shutil.copyfile(SRC / "rl_triage" / "behavior_authoring" / "schema.py", runtime / "schema.py")
    shutil.copyfile(SRC / "rl_triage" / "behavior_authoring" / "evaluate.py", runtime / "evaluate.py")

    examples = list_examples()
    for item in examples:
        item["name"] = EXAMPLE_NAMES[item["id"]]
    _json(data / "examples.json", {
        "examples": examples, "default_example": "h1-up", "default_contract": _english_contract(stairs_contract()),
        "model_api_calls": 0, "saved_recovery_available": True, "public_read_only": True,
    })
    commit = _commit()
    media: dict[str, dict] = {}
    for item in examples:
        example_id = item["id"]
        value = _english_view(example_id, load_example_view(example_id))
        value["video_url"] = f"/media/{example_id}"
        _json(data / f"example-{example_id}.json", value)
        _, video_path, _ = example_paths(example_id)
        relative = video_path.relative_to(ROOT).as_posix()
        media[example_id] = {
            "url": f"https://cdn.jsdelivr.net/gh/mmporong/mollet-meta@{commit}/{relative}",
            "sha256": _sha256(video_path), "bytes": video_path.stat().st_size, "source_commit": commit,
        }
    _json(data / "media.json", media)

    recovery = load_saved_recovery(runs_root=OUTPUT / ".no-private-provider-files")
    if recovery["replay"]["verification_level"] != "repository_evidence_verified":
        raise RuntimeError("Public recovery must use repository evidence, not private provider files.")
    recovery["clip"] = _english_view("h1-up", recovery["clip"])
    recovery["result"]["reason"] = RESULT_TEXT.get(recovery["result"]["reason"], recovery["result"]["reason"])
    recovery["replay"]["notice"] = "Replaying verified saved responses. This public demo makes no new model calls."
    _json(data / "recovery.json", recovery)

    forbidden = {"credentials.dpapi", ".env", ".env.local", "prepared-form.private.json"}
    leaked = [path for path in OUTPUT.rglob("*") if path.is_file() and path.name in forbidden]
    if leaked:
        raise RuntimeError(f"Credential or private files entered the package: {leaked}")
    files = [path for path in OUTPUT.rglob("*") if path.is_file()]
    report = {
        "schema": "mollet-public-demo-build-v1", "source_commit": commit,
        "files": len(files), "bytes": sum(path.stat().st_size for path in files), "examples": len(examples),
        "video_delivery": "commit_pinned_jsdelivr_redirect",
        "provider_evidence": "repository_evidence_verified", "live_model_endpoints": False,
        "credential_files": 0, "new_model_calls": 0, "gpu_jobs": 0, "training_jobs": 0,
    }
    _json(OUTPUT / "build-report.json", report)
    return report


def main() -> int:
    print(json.dumps(build(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
