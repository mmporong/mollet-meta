"""Spark 영상 분석 요청과 제한된 가설 가져오기. 영상 원본을 바꾸지 않는다."""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json

from .adapters import SPARK_MODEL, _response_text_nonstream, _sse_events
from .datasets import example_paths
from .improvement import analysis_schema, validate_analysis, build_reflection_context
from .schema import validate_observations

MAX_VIDEO_BYTES = 8 * 1024 * 1024
MAX_HISTORY_ROUNDS = 3
OFFICIAL_VIDEO_DOCUMENTATION = 'https://dev.meta.ai/docs/video-understanding'


def provider_analysis_schema() -> dict:
    """공식 strict subset에서 미확인인 제한은 로컬 검증에 유지한다."""
    unsupported = {'minItems','maxItems','uniqueItems','exclusiveMinimum','pattern','minLength'}
    def narrow(value):
        if isinstance(value,dict):return {key:narrow(item) for key,item in value.items() if key not in unsupported}
        if isinstance(value,list):return [narrow(item) for item in value]
        return value
    return narrow(analysis_schema())


def build_video_analysis_request(instruction: str, bundle: dict, example_id: str,
                                 *, analysis: dict | None = None, history: list | None = None,
                                 external_evaluations: list | None = None) -> tuple[dict, dict]:
    """선택된 보관 영상만 인라인으로 구성한다. 외부 URL·임의 파일 입력은 받지 않는다."""
    bundle = validate_observations(bundle)
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 4000:
        raise ValueError('영상 분석 지시는 1~4000자여야 합니다.')
    if history is not None and (not isinstance(history, list) or len(history) > MAX_HISTORY_ROUNDS):
        raise ValueError('재검토 이력은 최대 3회입니다.')
    path = example_paths(example_id)[1]
    if path.stat().st_size > MAX_VIDEO_BYTES:
        raise ValueError('영상 분석 입력은 8MiB 이하의 등록 영상만 허용합니다.')
    raw = path.read_bytes()
    video_sha = hashlib.sha256(raw).hexdigest()
    if video_sha != bundle['video_sha256']:
        raise ValueError('선택 영상과 상태 기록의 SHA가 다릅니다.')
    samples = bundle['samples']
    # 원본 프레임과 시각의 대응을 보내고, 시각에서 프레임을 임의 추측하지 않는다.
    context = {'clip_id': bundle['clip_id'], 'video_sha256': video_sha,
               'provenance': deepcopy(bundle['provenance']), 'samples': deepcopy(samples)}
    reflection = None
    if analysis is not None:
        reflection = build_reflection_context(analysis, bundle, history or [])
    elif history:
        raise ValueError('재검토에는 이전 가설이 필요합니다.')
    if external_evaluations is not None and (not isinstance(external_evaluations,list) or len(external_evaluations)>3):
        raise ValueError('회복 평가 context는 최대 3개입니다.')
    prompt = (
        'Analyze the attached robot video together with its synchronized state trace. '
        'Return grounded failure hypotheses and suggested registered interventions, not a final causal diagnosis. '
        'Cite exact evidence_frames from the supplied table. Distinguish visible facts from conjectures. '
        'No hidden joint forces, ground-relative height, contact, or reward-code defects can be inferred from appearance alone. '
        'Use only the supplied behavior-contract-v1 signals and registered correction identifiers. '
        'A correction_id is a proposal, never authorization. Do not emit executable code, approval, or training results. '
        'Local checks on this same recording are not new experiments or evidence of recovery. '
        'Respond in Korean. Return exactly the requested JSON schema.\n'
        + json.dumps({'instruction': instruction, 'recorded_observations': context,
                      'previous_local_checks': reflection,
                      'imported_fixed_evaluations_unverified_execution': external_evaluations or []},
                     ensure_ascii=False, allow_nan=False)
    )
    request = {
        'model': SPARK_MODEL,
        'input': [{'role': 'user', 'content': [
            {'type': 'input_text', 'text': prompt},
            {'type': 'input_video', 'video_url': 'data:video/mp4;base64,' + base64.b64encode(raw).decode('ascii')},
        ]}],
        'text': {'format': {'type': 'json_schema', 'name': 'grounded_video_hypotheses',
                            'strict': True, 'schema': provider_analysis_schema()}},
        'max_output_tokens': 4096,
    }
    manifest = {'schema': 'meta-video-analysis-input-v1', 'example_id': example_id,
                'video_sha256': video_sha, 'video_bytes': len(raw), 'sample_count': len(samples),
                'source_trace_sha256': bundle['provenance'].get('source_trace_sha256'),
                'documentation': OFFICIAL_VIDEO_DOCUMENTATION,
                'input_kind': 'video_and_synchronized_state', 'sent': False,
                'capture_approved': bundle['provenance'].get('capture_approved', False),
                'new_training_executed': False}
    return request, manifest


def import_video_analysis(response: str | dict, bundle: dict) -> dict:
    """완료된 provider envelope만 가져온다. 가설 JSON만으로 호출 성공을 주장하지 않는다."""
    if isinstance(response, str):
        try:
            parsed = json.loads(response)
        except json.JSONDecodeError:
            completed = [e.get('response') for e in _sse_events(response)
                         if e.get('type') == 'response.completed']
            if len(completed) != 1:
                raise ValueError('완료된 Spark 영상 분석 응답이 하나 필요합니다.') from None
            parsed = completed[0]
    else:
        parsed = response
    if not isinstance(parsed, dict):
        raise ValueError('Spark 응답 객체가 필요합니다.')
    text = _response_text_nonstream(parsed, SPARK_MODEL)
    value = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('유한한 JSON만 허용합니다.')))
    return validate_analysis(value, bundle)
