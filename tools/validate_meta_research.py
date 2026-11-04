"""외부 호출 없이 실제 MP4·Chrome·교정 소비 경로를 검증하고 새 결과 폴더에 보존한다."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from rl_triage.behavior_authoring.client import MetaClient
from rl_triage.behavior_authoring.live import LiveService
from rl_triage.behavior_authoring.web import Handler
from rl_triage.behavior_authoring.triage_bridge import compare_recovery,prepare_training
from rl_triage.behavior_authoring.research import ResearchService
from rl_triage.behavior_authoring.datasets import load_example_view
from rl_triage import behavior_oracle as oracle


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    fixture=runpy.run_path(str(ROOT/'tests/test_meta_video_research.py'))
    bridge=runpy.run_path(str(ROOT/'tests/test_meta_triage_bridge.py'))
    report={'scope':'CPU_integration_and_synthetic_provider_browser_validation_not_live_model_quality',
            'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'additional_meta_requests':0,'gpu_jobs':0,'training_executed':False,'performance_improvement_measured':False}
    def forbidden(*_):raise AssertionError('External transport must not run')
    try:
        with tempfile.TemporaryDirectory(prefix='mollet-research-') as scratch:
            temp=Path(scratch)
            live=LiveService(MetaClient(state_dir=temp/'state',transport=forbidden,token_transport=forbidden))
            response=fixture['envelope'](fixture['hypothesis_analysis']())
            (temp/'response.json').write_text(json.dumps(response,ensure_ascii=False),encoding='utf-8')
            reference=bridge['_report']()
            contract=oracle.register_contract(reference,[],{'min_survival_fraction':.9,'max_fall_rate':.05,
                                                            'max_lin_vel_rmse_mps':.5,'max_yaw_rate_rmse_radps':.5})
            comparison=bridge['_comparison'](bridge['_report']('before',lin=.8),bridge['_report']('after'),reference,contract)
            (temp/'recovery.json').write_text(json.dumps(comparison,ensure_ascii=False),encoding='utf-8')
            result=compare_recovery(comparison)
            (args.out/'synthetic-recovery-comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            research=ResearchService(live);prepared=research.prepare('h1-up','프레임 근거로 실패 가설 검토')
            sid=prepared['session_id'];research.accept(sid,response);research.step(sid);research.step(sid)
            report['recorded_h1_checks']=len(research.snapshot(sid)['history'])
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.meta_service=live
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                execution=subprocess.run(['node',str(ROOT/'tools/validate_meta_research_browser.cjs'),
                                          f'http://127.0.0.1:{server.server_port}',str(args.out.resolve()),str(temp)],
                                         cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=120)
                (args.out/'browser-stdout.txt').write_text(execution.stdout+execution.stderr,encoding='utf-8')
                if execution.returncode:raise RuntimeError(execution.stderr)
            finally:
                server.shutdown();server.server_close();thread.join(3)
                report['test_server_closed']=not thread.is_alive()
            report['passed']=True
    except Exception as error:
        report['passed']=False;report['error']=str(error);raise
    finally:
        (args.out/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
