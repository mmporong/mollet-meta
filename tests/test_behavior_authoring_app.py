"""로컬 UI와 CLI의 실제 공개 기록 경로를 검사한다. 모델 API는 사용하지 않는다."""
from __future__ import annotations

import copy
import http.client
import json
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest

from rl_triage.behavior_authoring.datasets import ROOT, default_contract, load_example
from rl_triage.behavior_authoring.evaluate import evaluate_contract
from rl_triage.behavior_authoring.web import Handler


@pytest.fixture(scope='module')
def server():
    instance=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=instance.serve_forever,daemon=True);thread.start()
    yield instance.server_port
    instance.shutdown();instance.server_close();thread.join(timeout=3)
    assert not thread.is_alive()


def request(port,path,body=None,headers=None,method=None):
    client=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
    payload=None if body is None else json.dumps(body)
    client.request(method or ('GET' if body is None else 'POST'),path,body=payload,headers={'Content-Type':'application/json',**(headers or {})})
    response=client.getresponse();data=response.read();status=response.status;result_headers=dict(response.getheaders());client.close()
    return status,data,result_headers


def test_recorded_state_demo_keeps_original_visual_deferral():
    spec=default_contract()
    a,b=load_example('reference'),load_example('case')
    assert len(a['samples'])==len(b['samples'])==625
    assert a['provenance']['capture_approved'] is False
    assert b['provenance']['model_ready'] is False
    assert evaluate_contract(spec,a)['verdict']=='PASS'
    assert evaluate_contract(spec,b)['verdict']=='FAIL'
    assert evaluate_contract(spec,a)['stage']=='draft'


def test_web_matches_core_and_review_is_digest_bound(server):
    bundle=load_example('reference');spec=default_contract()
    status,raw,_=request(server,'/api/review',{'contract':spec});assert status==200
    approval=json.loads(raw)['approval']
    status,raw,_=request(server,'/api/evaluate',{'contract':spec,'observations':bundle,'approval':approval})
    result=json.loads(raw)['result'];assert status==200
    assert result['verdict']==evaluate_contract(spec,bundle)['verdict']=='PASS'
    assert result['stage']=='reviewed' and result['model_called'] is False
    changed=copy.deepcopy(spec);changed['condition']['threshold']=.8
    assert request(server,'/api/evaluate',{'contract':changed,'observations':bundle,'approval':approval})[0]==400


def test_unapproved_video_never_proves_region_entry(server):
    bundle=load_example('case');bundle['samples'][0]['tracks']=[{'object_id':'1','bbox':[.01,.01,.1,.1]}]
    for row in bundle['samples'][1:]:row['tracks']=[{'object_id':'1','bbox':[.4,.4,.5,.5]}]
    spec=default_contract();spec['trigger']={'kind':'region_entry','at_s':None,'region':[.3,.3,.8,.8],'object_id':'1'}
    status,raw,_=request(server,'/api/evaluate',{'contract':spec,'observations':bundle})
    result=json.loads(raw)['result'];assert status==200
    assert result['verdict']=='UNKNOWN' and result['interval'] is None


def test_requests_are_exports_and_refusals_are_errors(server):
    status,raw,_=request(server,'/api/request/spark',{'instruction':'저장된 속도를 확인하세요.','observations':load_example('reference')})
    value=json.loads(raw);assert status==200 and value['sent'] is False
    assert value['request']['model']=='muse-spark-1.3' and 'format' in value['request']['text']
    status,_,_=request(server,'/api/import/spark',{'response':{'model':'muse-spark-1.3','status':'incomplete','output':[]},'observations':load_example('reference')})
    assert status==400


def test_loopback_origin_size_and_static_allowlist(server):
    assert request(server,'/api/examples',headers={'Host':'external.example'})[0]==403
    assert request(server,'/api/review',{'contract':default_contract()},headers={'Origin':'https://external.example'})[0]==403
    assert request(server,'/../../LICENSE')[0]==404
    assert request(server,'/api/example?id=../../LICENSE')[0]==400
    assert request(server,'/api/review',{},headers={'Content-Type':'text/plain'})[0]==415
    status,body,headers=request(server,'/')
    assert status==200 and b'<html lang="ko">' in body
    assert "frame-ancestors 'none'" in headers['Content-Security-Policy']


def test_video_ranges_are_real_file_bytes(server):
    status,body,headers=request(server,'/media/case',headers={'Range':'bytes=0-63'})
    assert status==206 and len(body)==64 and headers['Content-Range'].startswith('bytes 0-63/')
    path=ROOT/'evals/results/vision_transferred_pull_record_dev_20261027c_v1/f1/same_play.mp4'
    assert body==path.read_bytes()[:64]
    assert request(server,'/media/case',headers={'Range':'bytes=999999999-'})[0]==416


def test_cli_golden_path_and_write_once(tmp_path):
    out=tmp_path/'demo'
    run=subprocess.run([sys.executable,'-m','rl_triage.behavior_authoring','demo','--out',str(out)],capture_output=True,text=True)
    assert run.returncode==0,run.stderr
    summary=json.loads((out/'summary.json').read_text(encoding='utf-8'))
    assert summary['results']=={'reference':'PASS','case':'FAIL'} and summary['model_api_calls']==0
    again=subprocess.run([sys.executable,'-m','rl_triage.behavior_authoring','demo','--out',str(out)],capture_output=True,text=True)
    assert again.returncode==2
