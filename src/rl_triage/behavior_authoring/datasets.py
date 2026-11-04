"""공개 보관 기록을 별도 행동 조건 편집기의 관측으로 읽는다."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ARCHIVE = Path('evals/results/vision_transferred_pull_record_dev_20261027c_v1')
EXAMPLES = {
    'reference': ('none', 'Recorded baseline-policy run'),
    'case': ('f1', 'Recorded reward-defect-policy run'),
}
H1_ARCHIVE = Path('docs/media/stairs_h1_s172_20261030_v1')
GO2_ARCHIVE = Path('evals/results/go2_stairs_command_contrast_s200_20261101_v1')
GO2_STATE_ARCHIVE = Path('evals/results/stairs_motion_integrity_20261101_v1/sources')
GO2_STATE_SOURCES = {'go2-up-slow': 'go2-up', 'go2-down-slow': 'go2-down'}
STAIRS_EXAMPLES = {
    'h1-up': ('h1', 'biped-stairs-up', 'Biped H1 · stairs up'),
    'h1-down': ('h1', 'biped-stairs-down', 'Biped H1 · stairs down'),
    'go2-up-slow': ('go2', 'quadruped-stairs-up-step-0p05-cmd-0p25', 'Quadruped Go2 · stairs up · 0.25 m/s'),
    'go2-down-slow': ('go2', 'quadruped-stairs-down-step-0p05-cmd-0p25', 'Quadruped Go2 · stairs down · 0.25 m/s'),
    'go2-up-fast': ('go2', 'quadruped-stairs-up-step-0p05-cmd-0p75', 'Quadruped Go2 · stairs up · 0.75 m/s'),
    'go2-down-fast': ('go2', 'quadruped-stairs-down-step-0p05-cmd-0p75', 'Quadruped Go2 · stairs down · 0.75 m/s'),
}


def digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def example_paths(example_id: str, root: Path = ROOT) -> tuple[Path, Path, Path]:
    if example_id in STAIRS_EXAMPLES:
        robot, clip, _ = STAIRS_EXAMPLES[example_id]
        if robot == 'h1':
            base = root / H1_ARCHIVE / clip
            return base / 'state-trace.jsonl', base / 'video/rl-video-step-0.mp4', base / 'manifest.json'
        base = root / GO2_ARCHIVE
        return base / 'cases.json', base / 'media' / f'{clip}.mp4', base / 'media.json'
    if example_id not in EXAMPLES:
        raise ValueError('알 수 없는 예제입니다.')
    base = root / ARCHIVE / EXAMPLES[example_id][0]
    return base / 'evidence/raw_trace.json', base / 'same_play.mp4', base / 'result.json'


def list_examples(root: Path = ROOT) -> list[dict]:
    return [dict(id=key, name=value[2], video_url=f'/media/{key}',
                 state_available=value[0] == 'h1' or (key in GO2_STATE_SOURCES
                    and (root / GO2_STATE_ARCHIVE / GO2_STATE_SOURCES[key] / 'state-trace.jsonl').is_file()),
                 available=all(p.is_file() for p in example_paths(key, root)))
            for key, value in STAIRS_EXAMPLES.items()]


def load_example(example_id: str, root: Path = ROOT) -> dict:
    if example_id in STAIRS_EXAMPLES:
        bundle = load_example_view(example_id, root)['observations']
        if bundle is None:
            raise ValueError('이 사족 예제에는 프레임별 상태 기록이 없습니다. 요약 수치로 시간 판정을 만들지 않습니다.')
        return bundle
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


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def _up_axis(row: dict) -> float:
    w, x, y, z = row['root_quat_w']
    norm_sq = w*w + x*x + y*y + z*z
    if not math.isfinite(norm_sq) or norm_sq <= 0:
        raise ValueError('상태 기록의 자세 quaternion이 유효하지 않습니다.')
    return max(-1.0, min(1.0, 1.0 - 2.0*(x*x + y*y)/norm_sq))


def _state_samples(rows: list[dict], command_speed: float, fps: float) -> list[dict]:
    if (len(rows) != 626 or fps != 50
            or any(row['frame'] != i or not math.isclose(row['sim_time_s'], i/fps, abs_tol=1e-9)
                   for i, row in enumerate(rows))):
        raise ValueError('계단 상태 원문의 프레임 수 또는 시간 순서가 다릅니다.')
    # 초기 행 0은 캡처 전이다. 영상 frame 0 / PTS 0초를 step 이후 행 1에 연결한다.
    return [{
        'frame': index, 't_s': index/fps,
        'values': {'horizontal_speed_mps': math.hypot(*row['root_lin_vel_w'][:2]),
                   'command_speed_mps': command_speed, 'base_height_m': row['root_pos_w'][2],
                   'up_axis_z': _up_axis(row)}, 'tracks': [],
    } for index, row in enumerate(rows[1:])]


def stairs_contract() -> dict:
    spec = default_contract()
    spec.update(title='Maintain posture during stair playback', instruction='Check whether upright alignment stays at or above 0.8 for eight seconds from the start of the video.')
    spec['trigger']['at_s'] = 0.0
    spec['condition'].update(metric='up_axis_z', threshold=0.8, deadline_s=0.0, hold_s=8.0)
    return spec


def load_example_view(example_id: str, root: Path = ROOT) -> dict:
    """영상과 원기록 요약을 제공하며, 없는 상태 시계열은 None으로 남긴다."""
    from .schema import validate_observations
    if example_id not in STAIRS_EXAMPLES:
        bundle = load_example(example_id, root)
        return {'id': example_id, 'name': EXAMPLES[example_id][1], 'observations': bundle,
                'video_url': f'/media/{example_id}', 'video_sha256': bundle['video_sha256'],
                'state_available': True, 'frame_width': 640, 'frame_height': 480,
                'provenance': bundle['provenance'], 'recorded_assessment': None,
                'caption': 'Rendering approval is pending. This run is retained only as a CLI regression example.',
                'default_contract': default_contract()}
    robot, clip, name = STAIRS_EXAMPLES[example_id]
    trace_path, video_path, metadata_path = example_paths(example_id, root)
    video_sha = digest_file(video_path)
    observations = None
    if robot == 'h1':
        manifest = _read(metadata_path)
        receipt_path = video_path.with_suffix('.mp4.exact-frames.json')
        receipt = _read(receipt_path)
        source_path = root / H1_ARCHIVE / 'source_manifest.json'
        source_clip = next(row for row in _read(source_path)['clips'] if row['clip_id'] == clip)
        trace_sha = digest_file(trace_path)
        if (video_sha != manifest['video']['sha256'] or video_sha != receipt['video_sha256']
                or video_sha != source_clip['video_sha256']
                or trace_sha != manifest['state_trace']['sha256'] or trace_sha != source_clip['trace_sha256']
                or digest_file(metadata_path) != source_clip['manifest_sha256']
                or digest_file(receipt_path) != source_clip['exact_frames_sha256']):
            raise ValueError('이족 계단 영상·상태·인코더 기록의 해시 결합이 다릅니다.')
        rows = [json.loads(line) for line in trace_path.read_text(encoding='utf-8').splitlines()]
        fps, frames = receipt['fps'], receipt['frames_written']
        contract = manifest['record_video_contract']
        if (fps != 50 or frames != 625 or receipt['frames_received'] != frames or len(rows) != frames + 1
                or contract['step_trigger'] != 'step_id == 0' or contract['executed_steps'] != frames
                or contract['frames_before_close'] != frames or manifest['render_sync']['calls'] != frames
                or manifest['render_sync']['physics_state_unchanged'] is not True
                or any(row['frame'] != i or not math.isclose(row['sim_time_s'], i/fps, abs_tol=1e-9)
                       for i, row in enumerate(rows))):
            raise ValueError('이족 계단의 625 영상 프레임과 626 상태 행의 시간 결합이 다릅니다.')
        command = manifest['terrain']['command']
        command_speed = math.hypot(command['lin_vel_x'], command['lin_vel_y'])
        samples = _state_samples(rows, command_speed, fps)
        provenance = {
            'kind': 'recorded_h1_stairs_playback', 'source_path': metadata_path.relative_to(root).as_posix(),
            'source_sha256': digest_file(metadata_path), 'source_trace': trace_path.relative_to(root).as_posix(),
            'source_trace_sha256': trace_sha, 'source_manifest_sha256': digest_file(source_path),
            'exact_frames_sha256': digest_file(receipt_path), 'video_path': video_path.relative_to(root).as_posix(),
            'frame_width': receipt['width'], 'frame_height': receipt['height'],
            'capture_approved': False, 'model_ready': False, 'playback_render_synced': True,
            'visual_evidence_status': 'render_synced_playback_not_model_input_approval',
            'time_basis': 'video_frame_index / encoder_fps', 'state_frame_offset': 1,
            'sim_time_offset_s': 1/fps, 'motion_frame': 'world', 'height_frame': 'world_z_not_ground_clearance',
            'scope': 'stored_stairs_condition_demo_only_not_recovery_or_generalization',
            'new_model_api_calls': 0, 'new_training_jobs': 0,
        }
        observations = validate_observations({'schema': 'behavior-observations-v1', 'clip_id': example_id,
                                             'video_sha256': video_sha, 'samples': samples, 'provenance': provenance})
        assessment = {**manifest['assessment'], 'command_speed_mps': command_speed,
                      'min_up_axis_z': min(_up_axis(row) for row in rows),
                      'motion_accepted': manifest['assessment']['outcome'] == 'completed'}
        caption = 'This H1 stair video is synchronized with the recorded render state. Time conditions use stored measurements; approval for model input is separate.'
        width, height = receipt['width'], receipt['height']
    else:
        archive_manifest = _read(root / GO2_ARCHIVE / 'manifest.json')
        registered_files = {item['path']: item['sha256'] for item in archive_manifest['files']}
        if (digest_file(trace_path) != registered_files.get('cases.json')
                or digest_file(metadata_path) != registered_files.get('media.json')):
            raise ValueError('사족 계단 결과 요약의 공개 manifest 해시가 다릅니다.')
        row = next(row for row in _read(trace_path)['rows'] if row['condition_id'] == clip)
        media = next(row for row in _read(metadata_path)['items'] if row['condition_id'] == clip)
        if video_sha != row['video_sha256'] or video_sha != media['sha256']:
            raise ValueError('사족 계단 영상과 결과 요약의 해시가 다릅니다.')
        assessment = {key: row[key] for key in ('root_dx_m', 'root_dz_m', 'motion_accepted',
                      'progress_condition_pass', 'height_condition_pass', 'termination_seen')}
        assessment.update(command_speed_mps=row['command_x_m_s'], min_up_axis_z=row['body_up_world_z']['min'])
        provenance = {'kind': 'recorded_go2_command_contrast_summary',
                      'source_path': trace_path.relative_to(root).as_posix(), 'source_sha256': digest_file(trace_path),
                      'source_media_sha256': digest_file(metadata_path), 'source_trace_sha256': row['trace_sha256'],
                      'video_path': video_path.relative_to(root).as_posix(),
                      'raw_state_trace_available': False, 'capture_approved': False, 'model_ready': False,
                      'scope': 'same_policy_command_contrast_only_not_retraining_or_recovery',
                      'new_model_api_calls': 0, 'new_training_jobs': 0}
        caption = 'This source compares command speeds under the same policy. It has no frame-level state, so Mollet shows summary values and withholds a time-based verdict.'
        width, height = 1280, 720
        if example_id in GO2_STATE_SOURCES:
            state_path = root / GO2_STATE_ARCHIVE / GO2_STATE_SOURCES[example_id] / 'state-trace.jsonl'
            if state_path.is_file():
                # contrast exporter는 같은 row에 검증한 영상·상태 SHA를 보존한다.
                # 별도 감사 폴더의 원문은 그 상태 SHA로 읽으며, 옛 영상 manifest는 재사용하지 않는다.
                if digest_file(state_path) != row['trace_sha256']:
                    raise ValueError('사족 영상 결과에 등록된 상태 원문의 SHA가 다릅니다.')
                rows = [json.loads(line) for line in state_path.read_text(encoding='utf-8').splitlines()]
                samples = _state_samples(rows, row['command_x_m_s'], 50)
                measured = {'root_dx_m': rows[-1]['root_pos_w'][0] - rows[0]['root_pos_w'][0],
                            'root_dz_m': rows[-1]['root_pos_w'][2] - rows[0]['root_pos_w'][2],
                            'min_up_axis_z': min(_up_axis(item) for item in rows)}
                if any(not math.isclose(measured[key], assessment[key], abs_tol=1e-8) for key in measured):
                    raise ValueError('사족 상태 원문에서 계산한 수치와 공개 요약이 다릅니다.')
                provenance.update(
                    kind='recorded_go2_stairs_state_sha_join', source_trace=state_path.relative_to(root).as_posix(),
                    raw_state_trace_available=True, binding_kind='public_contrast_row_video_and_trace_sha256',
                    frame_width=width, frame_height=height, time_basis='video_frame_index / encoder_fps',
                    state_frame_offset=1, sim_time_offset_s=0.02, motion_frame='world',
                    height_frame='world_z_not_ground_clearance')
                observations = validate_observations({'schema': 'behavior-observations-v1', 'clip_id': example_id,
                    'video_sha256': video_sha, 'samples': samples, 'provenance': provenance})
                caption = 'The Go2 0.25 m/s source is joined to a raw state trace whose SHA matches the public result. Review the posture condition separately from the original progress verdict.'
    contract = stairs_contract()
    if robot == 'go2':
        contract.update(title='Maintain posture across the stair run', instruction='Check whether upright alignment remains at or above 0.8 throughout the 12.48-second state window.')
        contract['condition']['hold_s'] = 12.48
    return {'id': example_id, 'name': name, 'observations': observations, 'video_url': f'/media/{example_id}',
            'video_sha256': video_sha, 'frame_width': width, 'frame_height': height,
            'state_available': observations is not None, 'recorded_assessment': assessment,
            'provenance': provenance, 'caption': caption, 'default_contract': contract}


def default_contract() -> dict:
    return {
        'schema': 'behavior-contract-v1', 'title': 'Maintain horizontal speed after command',
        'instruction': 'Starting at 3 seconds, check whether horizontal speed reaches at least 0.4 m/s within one second and stays there for at least one second.',
        'trigger': {'kind': 'time', 'at_s': 3.0, 'region': None, 'object_id': None},
        'condition': {'metric': 'horizontal_speed_mps', 'op': 'gte', 'threshold': 0.4, 'deadline_s': 1.0, 'hold_s': 1.0},
        'max_gap_s': 0.05,
        'source': {'kind': 'manual_example_contract', 'model_called': False, 'scope': 'development_demonstration'},
    }
