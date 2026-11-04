"""발표 실행기의 준비·호출 차단·provider 스키마 계약을 확인한다."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from rl_triage.behavior_authoring.vision import provider_analysis_schema
from rl_triage.behavior_authoring.improvement import analysis_schema


def module():
    path=Path(__file__).resolve().parents[1]/'tools/run_meta_presentation_case.py'
    spec=importlib.util.spec_from_file_location('presentation_case_test',path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


def test_provider_subset_keeps_application_validation_strict():
    rendered=json.dumps(provider_analysis_schema())
    assert 'additionalProperties' in rendered and 'maxItems' not in rendered and 'uniqueItems' not in rendered
    assert 'maxItems' in json.dumps(analysis_schema())


def test_missing_key_blocks_all_paid_paths_and_preserves_record(tmp_path,monkeypatch):
    runner=module();routes=[]
    def request(port,route,value=None):
        routes.append(route)
        if route=='/api/meta/status':return {'status':{'key_configured':False,'credit_confirmed':False,'enabled':False,
                                                     'live_requests_started':2,'preflight_requests_started':0}}
        if route=='/api/improvement/prepare':return {'session_id':'test-local-session','request':{'model':'muse-spark-1.3'},
                                                    'input_manifest':{'video_sha256':'a'*64}}
        pytest.fail('No paid request may run without credentials')
    monkeypatch.setattr(runner,'request',request)
    out=tmp_path/'new';monkeypatch.setattr(sys,'argv',['case','--out',str(out)])
    runner.main();summary=json.loads((out/'summary.json').read_text(encoding='utf-8'))
    assert summary['status']=='blocked_missing_existing_key_or_credit_confirmation'
    assert summary['new_generation_requests']==0 and not summary['actual_reflection_executed']
    assert not any('/analyze' in path for path in routes)


def test_response_archive_requires_actual_matching_provider_bytes(tmp_path):
    runner=module();source=tmp_path/'private';source.mkdir()
    (source/'response.json').write_text('{}',encoding='utf-8')
    receipt={'directory':str(source),'content_type':'application/json','response_sha256':'f'*64}
    with pytest.raises(ValueError,match='SHA'):runner.archive_response(tmp_path,'first',{'receipt':receipt})


def test_static_baseline_preserves_known_posture_failure_without_model_claim():
    baseline=module().baseline_recorded_case()
    assert baseline['result']['verdict']=='FAIL'
    assert baseline['min_up_axis_z']<0 and baseline['first_below_threshold_s'] is not None
    assert baseline['model_called'] is False and baseline['model_quality_benchmark'] is False
