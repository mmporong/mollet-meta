"""기존 계단 자료를 Meta 응답과 결합한다. 호출 설정과 실제 완료를 구분한다."""
from __future__ import annotations

import base64
from copy import deepcopy
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
from pathlib import Path

from .adapters import build_sam_request, build_spark_request, import_sam_response, import_spark_response
from .client import MetaClient
from .datasets import ROOT, STAIRS_EXAMPLES, digest_file, example_paths, load_example_view
from .schema import validate_observations

PRICING_PATH = Path(__file__).with_name('meta-pricing.json')


def read_key_file(path: Path) -> str:
    """기존 저장소 밖의 키 또는 MODEL_API_KEY 한 줄을 읽는다. 파일은 실행하지 않는다."""
    target = path.expanduser().resolve(strict=True)
    if target == ROOT or ROOT in target.parents:
        raise ValueError('API 키 파일은 저장소 밖에 두세요.')
    if target.stat().st_size > 16384:
        raise ValueError('API 키 파일 크기를 확인하세요.')
    text = target.read_text(encoding='utf-8-sig').strip()
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    values = [line.partition('=')[2].strip().strip('\"\'') for line in lines if line.startswith('MODEL_API_KEY=')]
    if len(values) == 1:
        key = values[0]
    elif len(lines) == 1 and not values:
        key = lines[0]
    else:
        raise ValueError('키 파일에는 비밀 키 한 개 또는 MODEL_API_KEY 한 줄이 필요합니다.')
    if not key or len(key) > 4096 or not key.isascii() or any(char.isspace() for char in key):
        raise ValueError('API 키 파일의 형식을 확인하세요.')
    return key


def estimate_reservation(request: dict, frame_count: int) -> str:
    """공식 단가와 보수 입력 한도로 예약액을 계산한다. 실제 청구액은 별도다."""
    pricing = json.loads(PRICING_PATH.read_text(encoding='utf-8'))
    model = request['model']
    policy = pricing['reservation_policy']
    if model == 'muse-spark-1.3':
        bounds = pricing[model]
        byte_count = len(json.dumps(request, ensure_ascii=False, allow_nan=False).encode('utf-8'))
        input_bound = byte_count + policy['framing_reserve_tokens']
        output_bound = request.get('max_output_tokens', bounds['max_output_tokens'])
        amount = (Decimal(input_bound) * Decimal(bounds['input_per_million_usd'])
                  + Decimal(output_bound) * Decimal(bounds['output_per_million_usd'])) / Decimal(1000000)
    elif model == 'sam-3.1':
        bounds = pricing[model]
        amount = Decimal(max(frame_count, bounds['minimum_frames'])) * Decimal(bounds['video_per_thousand_frames_usd']) / Decimal(1000)
    else:
        raise ValueError('지원하지 않는 Meta 모델입니다.')
    return str((amount * Decimal(policy['multiplier'])).quantize(Decimal('0.000001'), rounding=ROUND_CEILING))


class LiveService:
    def __init__(self, client: MetaClient | None = None, *, video_calls_enabled: bool = False,
                 video_input_sha256: str | None = None):
        self.client = client if client is not None else MetaClient()
        self.video_calls_enabled = video_calls_enabled
        self.video_input_sha256 = video_input_sha256

    def status(self) -> dict:
        return {**self.client.status(), 'video_calls_enabled': self.video_calls_enabled}

    def analyze_video(self, example_id: str, instruction: str, observations: dict | None = None,
                      *, analysis: dict | None = None, history: list | None = None,
                      external_evaluations: list | None = None) -> dict:
        """허용된 서버에서만 영상 분석을 단일 전송한다. 후속 라운드는 자동 호출하지 않는다."""
        from .vision import build_video_analysis_request, import_video_analysis
        if not self.video_calls_enabled:
            raise ValueError('추가 영상 모델 호출은 잠겨 있습니다. 현재 작업은 요청 준비·저장 응답 가져오기만 허용합니다.')
        _, bundle = self._input(example_id, observations)
        if not (bundle['provenance'].get('capture_approved') is True and bundle['provenance'].get('model_ready') is True):
            if self.video_input_sha256 != bundle['video_sha256']:
                raise ValueError('이 영상 SHA에 결합된 입력 허용이 없습니다. 토큰 계수와 영상 전송을 수행하지 않았습니다.')
        request, manifest = build_video_analysis_request(instruction, bundle, example_id,
                                                        analysis=analysis, history=history,
                                                        external_evaluations=external_evaluations)
        # 영상 토큰은 파일 바이트로 추정하지 않는다. 공식 media-aware 계수 뒤 출력
        # 상한과 스키마/framing 여유를 더해 기존 예약 배수를 적용한다.
        pricing = json.loads(PRICING_PATH.read_text(encoding='utf-8'))
        rates = pricing['muse-spark-1.3']
        policy = pricing['reservation_policy']
        minimum = (Decimal(request['max_output_tokens']) * Decimal(rates['output_per_million_usd'])
                   * Decimal(policy['multiplier']) / Decimal(1000000))
        if Decimal(self.client.status()['available_budget_usd']) < minimum:
            raise ValueError('출력 예약 최소 예산이 부족해 토큰 계수를 보내지 않았습니다.')
        count = self.client.count_input_tokens(request)
        cost = ((Decimal(count + policy['framing_reserve_tokens']) * Decimal(rates['input_per_million_usd'])
                 + Decimal(request['max_output_tokens']) * Decimal(rates['output_per_million_usd']))
                * Decimal(policy['multiplier']) / Decimal(1000000))
        sent = self.client.send(request, estimated_cost_usd=cost.quantize(Decimal('0.000001'), rounding=ROUND_CEILING), context={
            'example_id': example_id, 'video_sha256': manifest['video_sha256'],
            'source_trace_sha256': manifest['source_trace_sha256'],
            'input_kind': 'video_and_synchronized_state',
            'counted_input_units': count,
            'reservation_scope': 'counted_media_input_plus_output_bound_not_provider_billing_cap',
        })
        value = import_video_analysis(sent['response'], bundle)
        receipt = sent['receipt']
        manifest = {**manifest, 'sent': True, 'run_id': receipt['run_id'],
                    'request_sha256': receipt['request_sha256'],
                    'input_authorization': 'sha_bound_operator_opt_in' if self.video_input_sha256==bundle['video_sha256']
                                           else 'source_capture_and_model_approval'}
        target = Path(receipt['directory']) / 'video-analysis.json'
        with target.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write('\n')
        return {'analysis': value, 'receipt': receipt, 'input_manifest': manifest,
                'status': self.status(), 'provider_authentication': 'authenticated_https_api'}

    def configure(self, values: dict) -> dict:
        allowed = {'api_key', 'credit_confirmed', 'remaining_credit_usd', 'budget_usd'}
        if set(values) - allowed:
            raise ValueError('지원하지 않는 Meta 설정 항목입니다.')
        return self.client.configure(**values)

    def _input(self, example_id: str, supplied: dict | None) -> tuple[dict, dict]:
        if example_id not in STAIRS_EXAMPLES:
            raise ValueError('현재 계단 예제 중 하나를 선택하세요.')
        view = load_example_view(example_id)
        if view['observations'] is None:
            raise ValueError('이 예제는 프레임별 상태가 없어 현재 모델 결합 경로에서 사용할 수 없습니다.')
        base = deepcopy(view['observations'])
        if supplied is not None:
            incoming = validate_observations(supplied)
            if (incoming['clip_id'] != base['clip_id'] or incoming['video_sha256'] != base['video_sha256']
                    or len(incoming['samples']) != len(base['samples'])):
                raise ValueError('선택 영상과 입력 관측 묶음이 다릅니다.')
            for current, original in zip(incoming['samples'], base['samples']):
                if any(current[key] != original[key] for key in ('frame', 't_s', 'values')):
                    raise ValueError('기존 계단의 프레임·시각·상태 값을 바꿀 수 없습니다.')
                original['tracks'] = deepcopy(current['tracks'])
        # 호출자가 보낸 provenance 대신 원본 출처와 승인 보류를 유지한다.
        return view, validate_observations(base)

    def quote(self, model: str, example_id: str, instruction: str = '', observations: dict | None = None) -> dict:
        _, bundle = self._input(example_id, observations)
        if model == 'spark':
            request = build_spark_request(instruction, bundle)
            request['max_output_tokens'] = 4096
        elif model == 'sam':
            request = {'model': 'sam-3.1'}
        else:
            raise ValueError('지원하지 않는 Meta 모델입니다.')
        return {'model': request['model'], 'reserved_usd': estimate_reservation(request, len(bundle['samples'])),
                'pricing_checked_on': json.loads(PRICING_PATH.read_text(encoding='utf-8'))['checked_on']}

    def run(self, model: str, example_id: str, instruction: str, observations: dict | None = None) -> dict:
        view, bundle = self._input(example_id, observations)
        if model == 'spark':
            request = build_spark_request(instruction, bundle)
            request['max_output_tokens'] = 4096
        elif model == 'sam':
            # 1회 요청의 inline 영상만 보내며 공급자 파일 저장소에 업로드하지 않는다.
            path = example_paths(example_id)[1]
            if digest_file(path) != bundle['video_sha256']:
                raise ValueError('요청 직전 선택 영상의 SHA가 달라졌습니다.')
            reference = 'data:video/mp4;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')
            request = build_sam_request(instruction, reference)
        else:
            raise ValueError('지원하지 않는 Meta 모델입니다.')
        cost = estimate_reservation(request, len(bundle['samples']))
        sent = self.client.send(request, estimated_cost_usd=cost, context={
            'example_id': example_id, 'video_sha256': bundle['video_sha256'],
            'source_trace_sha256': bundle['provenance'].get('source_trace_sha256'),
            'source_name': view['name'], 'pricing_sha256': digest_file(PRICING_PATH),
            'reservation_scope': 'conservative_app_reservation_not_actual_provider_charge',
        })
        receipt = sent['receipt']
        try:
            if model == 'spark':
                contract = import_spark_response(sent['response'], bundle)
                contract['source'].update(kind='live_meta_spark_response', provider_authentication='authenticated_https_api',
                                          run_id=receipt['run_id'], model_called=True)
                output = {'contract': contract}
                file_name = 'contract.json'
            else:
                # 새 SAM 응답은 기본 원문에 결합해 이전 sam_import와 중복하지 않는다.
                base = view['observations']
                imported = import_sam_response(sent['response'], base)
                imported['provenance']['sam_import'].update(
                    kind='live_meta_sam_response', provider_authentication='authenticated_https_api',
                    run_id=receipt['run_id'], model_called=True)
                imported = validate_observations(imported)
                output = {'observations': imported}
                file_name = 'observations.json'
            output_path = Path(receipt['directory']) / file_name
            with output_path.open('x', encoding='utf-8', newline='\n') as stream:
                json.dump(output, stream, ensure_ascii=False, allow_nan=False, indent=2)
                stream.write('\n')
            receipt = {**receipt, 'import_status': 'validated', 'result_file': str(output_path),
                       'result_sha256': hashlib.sha256(output_path.read_bytes()).hexdigest()}
        except (ValueError, KeyError, TypeError) as exc:
            failure = {'import_status': 'rejected', 'reason': 'provider 응답이 조건·관측 스키마를 충족하지 않았습니다.'}
            with (Path(receipt['directory']) / 'import-error.json').open('x', encoding='utf-8', newline='\n') as stream:
                json.dump(failure, stream, ensure_ascii=False)
            raise ValueError(f"Meta 응답을 저장했지만 결합 검증에 실패했습니다. 실행 ID: {receipt['run_id']}") from exc
        return {**output, 'receipt': receipt, 'status': self.client.status()}
