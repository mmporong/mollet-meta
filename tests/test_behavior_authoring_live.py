"""가짜 전송으로 실제 요청 경로를 검증한다. 외부 API는 호출하지 않는다."""
from __future__ import annotations

from copy import deepcopy
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading

import pytest

from rl_triage.behavior_authoring.client import MetaClient
from rl_triage.behavior_authoring.datasets import load_example_view, stairs_contract
from rl_triage.behavior_authoring.live import LiveService, estimate_reservation, read_key_file
from rl_triage.behavior_authoring.web import Handler


def completed(model, text):
    return {'id':'resp_test_only','model':model,'status':'completed','output':[
        {'type':'message','role':'assistant','content':[{'type':'output_text','text':text}]}]}


def make_service(tmp_path, response, calls):
    def transport(raw, key):
        assert key=='fixture-key-not-a-credential'
        calls.append(json.loads(raw))
        return response.encode('utf-8'),'text/event-stream' if response.startswith('data:') else 'application/json','request_test_only'
    return LiveService(MetaClient(api_key='fixture-key-not-a-credential',credit_confirmed=True,
        remaining_credit_usd='150',budget_usd='1',state_dir=tmp_path,transport=transport))


def test_live_spark_binds_selected_source_and_saves_validated_result(tmp_path):
    spec=stairs_contract();spec.pop('source');calls=[]
    service=make_service(tmp_path,json.dumps(completed('muse-spark-1.3',json.dumps(spec))),calls)
    result=service.run('spark','h1-up','자세 유지 조건을 작성하세요.')
    assert result['contract']['source']['kind']=='live_meta_spark_response'
    assert result['contract']['source']['model_called'] is True
    assert calls[0]['model']=='muse-spark-1.3' and calls[0]['max_output_tokens']==4096
    assert calls[0]['text']['format']['type']=='json_schema'
    assert result['status']['live_requests_started']==1
    assert Path(result['receipt']['result_file']).is_file()
    for p in tmp_path.rglob('*'):
        if p.is_file() and p.suffix in {'.json','.sse'}:
            assert 'fixture-key-not-a-credential' not in p.read_text(encoding='utf-8')


def test_live_sam_uses_selected_video_and_preserves_capture_deferral(tmp_path):
    text='<0f>1<|box;x1=10;y1=10;x2=100;y2=100;w=1280;h=720|><|mask;x=0;y=0;data=opaque|>'
    calls=[];service=make_service(tmp_path,json.dumps(completed('sam-3.1',text)),calls)
    result=service.run('sam','go2-down-slow','robot')
    content=calls[0]['input'][0]['content']
    assert content[1]['video_url'].startswith('data:video/mp4;base64,')
    assert result['observations']['samples'][0]['tracks'][0]['object_id']=='1'
    assert result['observations']['provenance']['capture_approved'] is False
    assert result['observations']['provenance']['sam_import']['model_called'] is True


def test_live_sam_zero_matches_are_completed_empty_tracks(tmp_path):
    calls=[];raw='data: '+json.dumps({'type':'response.completed','response':completed('sam-3.1','')})+'\n\n'
    result=make_service(tmp_path,raw,calls).run('sam','h1-up','absent object')
    assert len(calls)==1
    assert all(not row['tracks'] for row in result['observations']['samples'])


def test_live_rejects_changed_state_and_summary_only_before_any_send(tmp_path):
    calls=[];service=make_service(tmp_path,'{}',calls)
    modified=deepcopy(load_example_view('h1-up')['observations'])
    modified['samples'][0]['values']['up_axis_z']=0
    with pytest.raises(ValueError,match='상태 값'):
        service.run('spark','h1-up','조건',modified)
    with pytest.raises(ValueError,match='프레임별 상태'):
        service.run('sam','go2-up-fast','robot')
    assert not calls


def test_invalid_provider_result_keeps_receipt_without_faking_success(tmp_path):
    calls=[];service=make_service(tmp_path,json.dumps({'id':'test','status':'incomplete','output':[]}),calls)
    with pytest.raises(ValueError,match='결합 검증에 실패'):
        service.run('spark','h1-up','조건')
    assert len(calls)==1 and len(list(tmp_path.rglob('import-error.json')))==1


def test_key_files_are_read_not_executed_and_repo_keys_are_rejected(tmp_path,monkeypatch):
    import rl_triage.behavior_authoring.live as live
    repo=tmp_path/'repo';repo.mkdir();monkeypatch.setattr(live,'ROOT',repo)
    outside=tmp_path/'existing.env';outside.write_text('MODEL_API_KEY="fixture-value"\n',encoding='utf-8')
    assert read_key_file(outside)=='fixture-value'
    inside=repo/'key.txt';inside.write_text('fixture-value',encoding='utf-8')
    with pytest.raises(ValueError,match='저장소 밖'):
        read_key_file(inside)
    outside.write_text('fixture-value\nsecond-line',encoding='utf-8')
    with pytest.raises(ValueError):read_key_file(outside)


def test_reservations_cover_recorded_video_and_spark_output_limit():
    from decimal import Decimal
    sam=Decimal(estimate_reservation({'model':'sam-3.1'},625))
    spark=Decimal(estimate_reservation({'model':'muse-spark-1.3','input':'x','max_output_tokens':4096},625))
    assert sam==Decimal('.250000')
    assert spark>Decimal('4096')*Decimal('4.25')/Decimal('1000000')


def test_http_configuration_call_and_origin_guards(tmp_path):
    calls=[];spec=stairs_contract();spec.pop('source')
    service=make_service(tmp_path,json.dumps(completed('muse-spark-1.3',json.dumps(spec))),calls)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.meta_service=service
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request(path,body=None,origin=None):
        c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
        headers={'Content-Type':'application/json'}
        if origin:headers['Origin']=origin
        c.request('GET' if body is None else 'POST',path,body=json.dumps(body) if body is not None else None,headers=headers)
        response=c.getresponse();raw=response.read();status=response.status;c.close()
        return status,json.loads(raw)
    try:
        status,value=request('/api/meta/status');assert status==200 and value['status']['key_configured']
        assert 'fixture-key-not-a-credential' not in json.dumps(value)
        assert request('/api/meta/configure',{'credit_confirmed':False},'https://other.example')[0]==403
        status,value=request('/api/meta/run/spark',{'example_id':'h1-up','instruction':'조건'})
        assert status==200 and value['contract']['source']['model_called'] is True
        assert len(calls)==1
        assert request('/api/meta/configure',{'credit_confirmed':False})[0]==200
        assert request('/api/meta/run/spark',{'example_id':'h1-up','instruction':'조건'})[0]==400
        assert len(calls)==1
        assert request('/api/meta/configure',{'arbitrary_file':'key'})[0]==400
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
        assert not thread.is_alive()
