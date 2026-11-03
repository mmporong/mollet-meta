"""공개 보관 기록을 별도 행동 조건 편집기의 관측으로 읽는다."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ARCHIVE = Path('evals/results/vision_transferred_pull_record_dev_20261027c_v1')
EXAMPLES = {
    'reference': ('none', '기준 정책의 저장 기록'),
    'case': ('f1', '보상 결함 정책의 저장 기록'),
}


def digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def example_paths(example_id: str, root: Path = ROOT) -> tuple[Path, Path, Path]:
    if example_id not in EXAMPLES:
        raise ValueError('알 수 없는 예제입니다.')
    base = root / ARCHIVE / EXAMPLES[example_id][0]
    return base / 'evidence/raw_trace.json', base / 'same_play.mp4', base / 'result.json'


def list_examples(root: Path = ROOT) -> list[dict]:
    return [dict(id=key, name=value[1], video_url=f'/media/{key}',
                 available=all(p.is_file() for p in example_paths(key, root)))
            for key, value in EXAMPLES.items()]


def load_example(example_id: str, root: Path = ROOT) -> dict:
    from .schema import validate_observations
    trace_path, video_path, result_path = example_paths(example_id, root)
    trace = json.loads(trace_path.read_text(encoding='utf-8'))
    result = json.loads(result_path.read_text(encoding='utf-8'))
    if trace.get('schema') != 'vision_play_raw_trace_v1':
        raise ValueError('지원하는 공개 상태 기록 형식이 아닙니다.')
    video_sha = digest_file(video_path)
    if video_sha != trace.get('video_sha256'):
        raise ValueError('영상과 상태 기록의 해시가 다릅니다.')
    samples = []
    for row in trace['records']:
        state = row['state']
        motion, command = state['motion'], state['command']
        samples.append({
            'frame': row['frame_index'], 't_s': row['actual_timestamp_s'],
            'values': {
                'horizontal_speed_mps': math.hypot(motion[0], motion[1]),
                'command_speed_mps': math.hypot(command[0], command[1]),
                'base_height_m': state['pose']['base_height_m'],
                'up_axis_z': state['pose']['up_axis_z'],
            }, 'tracks': [],
        })
    if len(samples) != trace['frame_count']:
        raise ValueError('기록의 프레임 수가 일치하지 않습니다.')
    bundle = {
        'schema': 'behavior-observations-v1', 'clip_id': example_id,
        'video_sha256': video_sha, 'samples': samples,
        'provenance': {
            'kind': 'recorded_rl_development_archive',
            'source_trace': trace_path.relative_to(root).as_posix(),
            'source_trace_sha256': digest_file(trace_path),
            'source_result_sha256': digest_file(result_path),
            'video_path': video_path.relative_to(root).as_posix(),
            'capture_approved': result.get('capture_approved', False),
            'model_ready': result.get('model_ready', False),
            'used_for_original_decision': result.get('used_for_decision', False),
            'visual_evidence_status': 'deferred_archive_not_model_approved',
            'state_field_source': 'vision_play_worker_v1.state_snapshot: root_lin_vel_b[:2], command, base_height, projected_gravity',
            'time_basis': trace['time_basis'],
            'frame_width': trace['profile']['resolution']['width'],
            'frame_height': trace['profile']['resolution']['height'],
            'new_model_api_calls': 0, 'new_training_jobs': 0,
            'scope': 'stored_state_condition_demo_only_not_recovery_or_generalization',
        },
    }
    return validate_observations(bundle)


def default_contract() -> dict:
    return {
        'schema': 'behavior-contract-v1', 'title': '명령 이후 수평 속도 유지',
        'instruction': '3초부터 1초 안에 수평 속도가 0.4 m/s 이상이 되어 1초 이상 유지되는지 확인합니다.',
        'trigger': {'kind': 'time', 'at_s': 3.0, 'region': None, 'object_id': None},
        'condition': {'metric': 'horizontal_speed_mps', 'op': 'gte', 'threshold': 0.4, 'deadline_s': 1.0, 'hold_s': 1.0},
        'max_gap_s': 0.05,
        'source': {'kind': 'manual_example_contract', 'model_called': False, 'scope': 'development_demonstration'},
    }
