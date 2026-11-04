"""영상 분석·다회 검사·기존 호출 경계의 CPU 통합 회귀."""
from copy import deepcopy
import base64
import http.client
import json
from pathlib import Path
import threading
import runpy
from http.server import ThreadingHTTPServer

import pytest

from rl_triage.behavior_authoring.client import MetaClient, MetaAPIError
from rl_triage.behavior_authoring.datasets import load_example_view, stairs_contract, example_paths
from rl_triage.behavior_authoring.live import LiveService
from rl_triage.behavior_authoring.research import ResearchService
from rl_triage.behavior_authoring.vision import build_video_analysis_request, import_video_analysis
from rl_triage.behavior_authoring.web import Handler


def hypothesis_analysis():
    contract = stairs_contract(); contract.pop('source')
    contract['condition'].update(metric='up_axis_z',threshold=.8,deadline_s=8,hold_s=.5)
    return {'summary':'영상 근거와 상태 수치 대조', 'hypotheses':[
        {'id':'posture', 'claim':'자세가 유지되는지 검사', 'evidence_frames':[0,350],
         'contract':contract, 'correction_id':'none','rationale':'원인 확정 전 자세 조건을 검사합니다.'},
        {'id':'reward', 'claim':'보상 좌표계 결함 가능성은 코드 검사 필요', 'evidence_frames':[100],
         'contract':{**deepcopy(contract),'condition':{**contract['condition'],'threshold':1.0}},
         'correction_id':'restore_body_frame_reward','rationale':'영상만으로 코드 결함을 확정하지 않습니다.'}],
        'limitations':['저장된 개발 기록이며 새 인과 실험이 아닙니다.']}


def envelope(value):
    return {'model':'muse-spark-1.3','status':'completed','output':[
        {'type':'message','role':'assistant','content':[{'type':'output_text','text':json.dumps(value)}]}]}


def service(tmp_path, *, enabled=False, count=50000):
    calls=[]
    def transport(raw,key):
        calls.append(('generate',json.loads(raw)))
        return json.dumps(envelope(hypothesis_analysis())).encode(), 'application/json', 'fixture-response'
    def counter(raw,key):
        calls.append(('count',json.loads(raw)))
        return json.dumps({'input_tokens':count}).encode(), 'application/json', 'fixture-count'
    client=MetaClient(api_key='fixture-secret',credit_confirmed=True,remaining_credit_usd='150',budget_usd='1',
                      state_dir=tmp_path,transport=transport,token_transport=counter)
    return LiveService(client,video_calls_enabled=enabled,
                       video_input_sha256=load_example_view('h1-up')['observations']['video_sha256'] if enabled else None),calls


def test_video_payload_is_actual_registered_mp4_and_still_unsent():
    bundle=load_example_view('h1-up')['observations']
    request,manifest=build_video_analysis_request('계단에서 자세 실패 원인을 찾아주세요',bundle,'h1-up')
    content=request['input'][0]['content']
    assert content[1]['type']=='input_video'
    assert base64.b64decode(content[1]['video_url'].split(',',1)[1])==example_paths('h1-up')[1].read_bytes()
    assert request['text']['format']['schema']['additionalProperties'] is False
    assert manifest['video_sha256']==bundle['video_sha256'] and manifest['sent'] is False
    assert manifest['capture_approved'] is False
    changed=deepcopy(bundle);changed['video_sha256']='f'*64
    with pytest.raises(ValueError,match='SHA'):
        build_video_analysis_request('실패 분석',changed,'h1-up')


def test_video_import_rejects_incomplete_invented_frames_and_authority():
    bundle=load_example_view('h1-up')['observations']
    assert len(import_video_analysis(envelope(hypothesis_analysis()),bundle)['hypotheses'])==2
    incomplete=envelope(hypothesis_analysis());incomplete['status']='incomplete'
    with pytest.raises(ValueError):import_video_analysis(incomplete,bundle)
    invalid=hypothesis_analysis();invalid['hypotheses'][0]['evidence_frames']=[999999]
    with pytest.raises(ValueError):import_video_analysis(envelope(invalid),bundle)
    invalid=hypothesis_analysis();invalid['approval']=True
    with pytest.raises(ValueError):import_video_analysis(envelope(invalid),bundle)


def test_complete_import_local_checks_reflection_and_duplicate_stop(tmp_path):
    live,calls=service(tmp_path);research=ResearchService(live)
    prepared=research.prepare('h1-up','영상 분석')
    session_id=prepared['session_id']
    research.accept(session_id,envelope(hypothesis_analysis()))
    first=research.step(session_id);second=research.step(session_id);third=research.step(session_id)
    assert len(first['history'])==1 and len(second['history'])==2
    assert third['history']==second['history'] and not third['round_result']['experiments']
    reflected=research.reflect(session_id)
    prompt=reflected['request']['input'][0]['content'][0]['text']
    assert 'previous_local_checks' in prompt and 'outcomes' in prompt
    assert 'local_recorded_check_not_causal_experiment' in prompt
    assert research.snapshot(session_id)['new_model_calls']==0 and not calls
    exported=research.snapshot(session_id);exported['history'].clear()
    assert len(research.snapshot(session_id)['history'])==2
    with pytest.raises(ValueError):research.step('invented-session')


def test_video_calls_default_lock_and_media_token_preflight(tmp_path):
    live,calls=service(tmp_path/'locked')
    with pytest.raises(ValueError,match='잠겨'):live.analyze_video('h1-up','분석')
    assert not calls
    live.video_calls_enabled=True
    with pytest.raises(ValueError,match='입력 허용'):live.analyze_video('h1-up','분석')
    assert not calls
    live,calls=service(tmp_path/'enabled',enabled=True)
    result=live.analyze_video('h1-up','분석')
    assert [kind for kind,_ in calls]==['count','generate']
    assert calls[0][1]['input']==calls[1][1]['input']
    assert calls[0][1]['text']==calls[1][1]['text']
    assert result['provider_authentication']=='authenticated_https_api'
    assert result['input_manifest']['sent'] is True
    assert result['receipt']['context']['counted_input_units']==50000
    live,calls=service(tmp_path/'invalid-count',enabled=True,count=True)
    with pytest.raises(ValueError):live.analyze_video('h1-up','분석')
    assert [kind for kind,_ in calls]==['count']
    live,calls=service(tmp_path/'too-expensive',enabled=True,count=1000000)
    with pytest.raises(ValueError):live.analyze_video('h1-up','분석')
    assert [kind for kind,_ in calls]==['count']


def test_manual_path_is_not_spark_inference_and_http_origin_stays_local(tmp_path):
    live,calls=service(tmp_path)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.meta_service=live
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request(path,value=None,origin=None):
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=20)
        headers={'Content-Type':'application/json'}
        if origin:headers['Origin']=origin
        connection.request('POST' if value is not None else 'GET',path,json.dumps(value) if value is not None else None,headers)
        result=connection.getresponse();raw=result.read();status=result.status;connection.close()
        return status,raw
    try:
        assert request('/improvement.js')[0]==200
        payload={'example_id':'h1-up','instruction':'자세 검사','contract':stairs_contract()}
        assert request('/api/improvement/seed',payload,'https://external.example')[0]==403
        status,raw=request('/api/improvement/seed',payload)
        value=json.loads(raw);assert status==200 and value['analysis_origin']=='manual_condition_not_model_analysis'
        status,raw=request('/api/improvement/round',{'session_id':value['session_id']})
        assert status==200 and len(json.loads(raw)['history'])==1
        assert request('/api/improvement/analyze',{'session_id':value['session_id']})[0]==400
        assert not calls
    finally:
        server.shutdown();server.server_close();thread.join(3)
        assert not thread.is_alive()


def test_token_preflight_is_counted_cached_and_budget_failure_sends_nothing(tmp_path):
    live,calls=service(tmp_path/'cached',enabled=True)
    bundle=load_example_view('h1-up')['observations']
    request,_=build_video_analysis_request('분석',bundle,'h1-up')
    assert live.client.count_input_tokens(request)==50000
    assert live.client.count_input_tokens(request)==50000
    status=live.status()
    assert status['preflight_requests_started']==1 and status['total_external_requests_started']==1
    assert len(calls)==1
    reloaded=MetaClient(api_key='fixture-secret',credit_confirmed=True,remaining_credit_usd='150',
                        state_dir=tmp_path/'cached',token_transport=lambda *_:pytest.fail('cached count must not send'))
    assert reloaded.count_input_tokens(request)==50000
    live.client.configure(budget_usd='.01')
    with pytest.raises(ValueError,match='최소 예산'):live.analyze_video('h1-up','분석')
    assert len(calls)==1


def test_prepared_sessions_can_be_reclaimed_and_cancelled_without_restart(tmp_path):
    live,_=service(tmp_path);research=ResearchService(live)
    first=research.prepare('h1-up','분석')['session_id']
    for _ in range(32):last=research.prepare('h1-up','분석')['session_id']
    assert len(research.sessions)==32 and first not in research.sessions
    assert research.cancel(last)=={'closed':True}
    assert last not in research.sessions


def test_live_session_manifest_and_all_external_requests_are_reported(tmp_path):
    live,calls=service(tmp_path,enabled=True);research=ResearchService(live)
    sid=research.prepare('h1-up','영상 분석')['session_id']
    value=research.analyze(sid)
    assert value['input_manifest']['sent'] is True
    assert value['provider_authentication']=='authenticated_https_api'
    assert value['new_external_api_requests']==2 and value['new_preflight_requests']==1
    assert [kind for kind,_ in calls]==['count','generate']


def test_recovery_source_text_preserves_fixed_eval_hash_and_enters_reflection(tmp_path):
    from rl_triage import behavior_oracle as oracle
    helpers=runpy.run_path(str(Path(__file__).with_name('test_meta_triage_bridge.py')))
    reference=helpers['_report']()
    contract=oracle.register_contract(reference,[],{'min_survival_fraction':.9,'max_fall_rate':.05,
                                                   'max_lin_vel_rmse_mps':.5,'max_yaw_rate_rmse_radps':.5})
    comparison=helpers['_comparison'](helpers['_report']('before',lin=.8),helpers['_report']('after'),reference,contract)
    live,calls=service(tmp_path);research=ResearchService(live)
    sid=research.prepare('h1-up','영상 분석')['session_id'];research.accept(sid,envelope(hypothesis_analysis()));research.step(sid)
    result=research.recover(sid,'reward',json.dumps(comparison))
    assert result['recovery_comparison']['result']['outcome']=='recovered'
    prompt=research.reflect(sid)['request']['input'][0]['content'][0]['text']
    assert 'recovered' in prompt and 'unverified_saved_evaluation' in prompt
    with pytest.raises(ValueError,match='교정이 다릅니다'):research.recover(sid,'posture',json.dumps(comparison))
    assert not calls


def test_attached_server_keeps_existing_key_without_exporting_it(tmp_path):
    from rl_triage.behavior_authoring.attached import AttachedLiveService
    live,calls=service(tmp_path)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.meta_service=live
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        attached=AttachedLiveService(server.server_port)
        assert attached.status()['key_configured'] is True
        assert attached.status()['video_calls_enabled'] is False
        assert 'fixture-secret' not in json.dumps(attached.status())
        assert attached.configure({'budget_usd':'.5'})['budget_usd']=='0.50'
        assert live.status()['key_configured'] is True
        assert ResearchService(attached).prepare('h1-up','분석')['sent'] is False
        with pytest.raises(ValueError,match='잠겨'):attached.analyze_video('h1-up','분석')
        with pytest.raises(ValueError):attached._request('arbitrary-path')
        assert not calls
    finally:
        server.shutdown();server.server_close();thread.join(3)
