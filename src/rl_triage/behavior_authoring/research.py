"""영상 가설·기록 검사·재검토를 같은 입력 해시에 연결하는 로컬 세션."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import threading
import time
import uuid

from .improvement import run_round, validate_analysis
from .schema import validate_contract
from .vision import build_video_analysis_request, import_video_analysis


class ResearchService:
    """사용자 파일의 history/승인을 실행 상태로 가져오지 않는다."""

    def __init__(self, live_service):
        self.live = live_service
        self.sessions: dict[str, dict] = {}
        self.lock = threading.RLock()

    def prepare(self, example_id: str, instruction: str, observations: dict | None = None) -> dict:
        _, bundle = self.live._input(example_id, observations)
        request, manifest = build_video_analysis_request(instruction, bundle, example_id)
        session_id = str(uuid.uuid4())
        with self.lock:
            expired = [key for key,value in self.sessions.items() if time.monotonic()-value['last_used']>7200]
            for key in expired:
                del self.sessions[key]
            if len(self.sessions) >= 32:
                prepared = next((key for key,value in self.sessions.items() if value['analysis'] is None), None)
                if prepared is None:
                    raise ValueError('활성 분석이 32개입니다. 사용하지 않는 분석을 닫고 다시 준비하세요.')
                del self.sessions[prepared]
            self.sessions[session_id] = {'example_id': example_id, 'instruction': instruction,
                                         'bundle': bundle, 'manifest': manifest, 'analysis': None,
                                         'history': [], 'cycles': [], 'provider_imports': 0,
                                         'external_evaluations': [],
                                         'last_used': time.monotonic(), 'preflight_calls': 0,
                                         'origin': 'prepared_video_request'}
        return {'session_id': session_id, 'request': request, 'input_manifest': manifest,
                'sent': False, 'additional_paid_calls_allowed': False}

    def _session(self, session_id: str) -> dict:
        if not isinstance(session_id, str) or session_id not in self.sessions:
            raise ValueError('이 서버에서 준비한 분석 세션을 선택하세요.')
        self.sessions[session_id]['last_used'] = time.monotonic()
        return self.sessions[session_id]

    def cancel(self, session_id: str) -> dict:
        with self.lock:
            self.sessions.pop(session_id, None)
        return {'closed': True}

    def accept(self, session_id: str, response: str | dict) -> dict:
        with self.lock:
            session = self._session(session_id)
            if session['provider_imports'] >= 3:
                raise ValueError('영상 재검토는 최대 3회입니다. 새 근거를 수집하세요.')
            analysis = import_video_analysis(response, session['bundle'])
            if session['analysis'] is not None:
                session['cycles'].append({'analysis': session['analysis'], 'history': session['history']})
            session['analysis'] = analysis
            session['history'] = []
            session['provider_imports'] += 1
            session['origin'] = 'unverified_imported_spark_video_response'
            return self._view(session_id, session)

    def analyze(self, session_id: str) -> dict:
        with self.lock:
            session = self._session(session_id)
            if session['provider_imports'] >= 3:
                raise ValueError('영상 분석·재검토는 최대 3회입니다.')
            before = self.live.status().get('preflight_requests_started',0)
            try:
                result = self.live.analyze_video(session['example_id'], session['instruction'], session['bundle'],
                                                 analysis=session['analysis'], history=session['history'],
                                                 external_evaluations=session['external_evaluations'])
            finally:
                session['preflight_calls'] += self.live.status().get('preflight_requests_started',0)-before
            if session['analysis'] is not None:
                session['cycles'].append({'analysis': session['analysis'], 'history': session['history']})
            session['analysis'] = result['analysis']
            session['manifest'] = result['input_manifest']
            session['history'] = []
            session['provider_imports'] += 1
            session['origin'] = 'authenticated_live_spark_video_response'
            session.setdefault('receipts', []).append(result['receipt'])
            return {**self._view(session_id, session), 'receipt': result['receipt'], 'status': result['status']}

    def seed(self, example_id: str, instruction: str, contract: dict, observations: dict | None = None) -> dict:
        """현재 사용자가 작성한 조건으로 무과금 경로를 확인한다. Spark 분석으로 표시하지 않는다."""
        prepared = self.prepare(example_id, instruction, observations)
        session_id = prepared['session_id']
        with self.lock:
            session = self._session(session_id)
            spec = deepcopy(validate_contract(contract))
            spec.pop('source', None)
            session['analysis'] = validate_analysis({
                'summary': '사용자가 작성한 행동 조건의 로컬 검사',
                'hypotheses': [{'id': 'manual-condition', 'claim': instruction,
                                 'evidence_frames': [session['bundle']['samples'][0]['frame']],
                                 'contract': spec, 'correction_id': 'none',
                                 'rationale': '현재 조건으로 저장 기록을 검사합니다. 원인·보상 교정을 추론하지 않았습니다.'}],
                'limitations': ['Spark 영상 분석 응답이 아닙니다.', '재학습·회복 효과를 측정하지 않았습니다.'],
            }, session['bundle'])
            session['origin'] = 'manual_condition_not_model_analysis'
            return self._view(session_id, session)

    def step(self, session_id: str) -> dict:
        with self.lock:
            session = self._session(session_id)
            if session['analysis'] is None:
                raise ValueError('완료된 영상 분석 응답을 먼저 가져오세요.')
            result = run_round(session['analysis'], session['bundle'], session['history'])
            session['history'] = result['history']
            return {**self._view(session_id, session), 'round_result': result}

    def reflect(self, session_id: str) -> dict:
        with self.lock:
            session = self._session(session_id)
            if session['analysis'] is None or not session['history']:
                raise ValueError('가설의 로컬 검사 결과가 있어야 재검토 요청을 만들 수 있습니다.')
            if session['provider_imports'] >= 3:
                raise ValueError('최대 3회 재검토에 도달했습니다. 새 근거가 필요합니다.')
            request, manifest = build_video_analysis_request(
                session['instruction'], session['bundle'], session['example_id'],
                analysis=session['analysis'], history=session['history'],
                external_evaluations=session['external_evaluations'])
            return {'session_id': session_id, 'request': request, 'input_manifest': manifest, 'sent': False}

    def recover(self, session_id: str, hypothesis_id: str, comparison: dict) -> dict:
        from .triage_bridge import compare_recovery
        with self.lock:
            session = self._session(session_id)
            hypotheses = (session['analysis'] or {}).get('hypotheses', [])
            hypothesis = next((h for h in hypotheses if h['id']==hypothesis_id), None)
            if hypothesis is None:
                raise ValueError('현재 세션의 가설을 선택하세요.')
            if comparison.get('intervention', {}).get('correction_id') != hypothesis['correction_id']:
                raise ValueError('선택한 가설과 실행 후 평가의 교정이 다릅니다.')
            if len(session['external_evaluations']) >= 3:
                raise ValueError('세션별 회복 평가 가져오기는 최대 3개입니다.')
            result = compare_recovery(comparison)
            session['external_evaluations'].append({'hypothesis_id': hypothesis_id, 'comparison': result,
                                                     'input_sha256': hashlib.sha256(
                                                         json.dumps(comparison,sort_keys=True,ensure_ascii=False,
                                                                                 allow_nan=False).encode()).hexdigest(),
                                                     'provider_authentication': 'unverified_saved_evaluation'})
            return {**self._view(session_id, session), 'recovery_comparison': result}

    def snapshot(self, session_id: str) -> dict:
        with self.lock:
            return self._view(session_id, self._session(session_id))

    @staticmethod
    def _view(session_id: str, session: dict) -> dict:
        return deepcopy({'schema': 'meta-research-session-v1', 'session_id': session_id,
                         'example_id': session['example_id'], 'input_manifest': session['manifest'],
                         'analysis': session['analysis'], 'history': session['history'], 'cycles': session['cycles'],
                         'analysis_origin': session['origin'],
                         'provider_authentication': ('authenticated_https_api' if session['origin']=='authenticated_live_spark_video_response'
                                                     else 'unverified_saved_response'),
                         'receipts': session.get('receipts', []),
                         'external_evaluations': session['external_evaluations'],
                         'provider_imports': session['provider_imports'], 'new_model_calls': len(session.get('receipts', [])),
                         'new_preflight_requests': session['preflight_calls'],
                         'new_external_api_requests': session['preflight_calls'] + len(session.get('receipts', [])),
                         'training_executed': False, 'recovery_improvement_measured': False})
