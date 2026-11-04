"""실제 영상 가설·기록 검사·재검토 사례를 기존 US$1 원장 안에서 한 번 실행한다."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import urllib.error
import urllib.request


INSTRUCTION = (
    '첨부된 Go2 계단 하행 영상과 동기화된 상태 기록에서 관찰되는 행동 문제를 분석하세요. '
    '관측 가능한 서로 다른 가설을 최대 3개 제시하고, 각 가설에 근거 프레임과 상태 조건을 연결하세요. '
    '자세 유지와 진행을 구분하고, 영상으로 확인할 수 없는 보상 코드 결함은 확정하지 마세요. '
    '지표 단위와 관측 구간을 지키고 원본 범위 안에서 검사 가능한 조건을 만드세요. '
    '검사 결과가 제공되면 처음 주장과 모순되는 부분을 수정하거나 유지 이유를 설명하세요. '
    '검사 전에는 실제 검사 결과를 예측 사실로 쓰지 마세요.'
)


def write(path: Path, value: dict) -> None:
    with path.open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(value,stream,ensure_ascii=False,allow_nan=False,indent=2);stream.write('\n')


def request(port: int, route: str, value: dict | None=None) -> dict:
    raw=None if value is None else json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
    req=urllib.request.Request(f'http://127.0.0.1:{port}'+route,data=raw,
                               headers={'Content-Type':'application/json'},method='GET' if value is None else 'POST')
    try:
        with urllib.request.urlopen(req,timeout=230) as response:return json.load(response)
    except urllib.error.HTTPError as error:
        try:message=json.load(error).get('error','로컬 실행 실패')
        except Exception:message='로컬 실행 실패'
        raise RuntimeError(message) from None


def archive_response(out: Path, name: str, value: dict) -> dict:
    receipt=value['receipt']
    directory=Path(receipt['directory']).resolve(strict=True)
    # client가 보존한 redacted 응답만 사용한다. 키·전송 헤더를 읽지 않는다.
    source=directory/('response.sse' if receipt['content_type'].startswith('text/event-stream') else 'response.json')
    raw=source.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=receipt['response_sha256']:
        raise ValueError('저장 provider 응답 SHA가 다릅니다.')
    target=out/f'{name}-response.json'
    if source.suffix=='.sse':
        # Spark의 비스트리밍 경로가 바뀌면 발표용 parser를 임의로 추측하지 않는다.
        raise ValueError('Spark SSE 원문은 보존됐지만 현재 발표 녹화에는 JSON 응답이 필요합니다.')
    with target.open('xb') as stream:stream.write(raw)
    write(out/f'{name}-analysis.json',value['analysis'])
    write(out/f'{name}-session.json',value)
    return {'run_id':receipt['run_id'],'model':receipt['model'],'response_sha256':receipt['response_sha256'],
            'request_sha256':receipt['request_sha256'],'provider_request_id':receipt.get('provider_request_id'),
            'reserved_usd':receipt['reserved_usd'],'provider_authentication':value['provider_authentication'],
            'input_manifest':value['input_manifest'],'response_file':target.name}


def checks(port: int, session_id: str, count: int) -> dict:
    result=None
    for _ in range(min(3,count)):
        result=request(port,'/api/improvement/round',{'session_id':session_id})
    if result is None:raise ValueError('검사할 가설이 없습니다.')
    return result


def main() -> None:
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--port',type=int,default=7706);parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args()
    if not 1024<=args.port<=65535:raise ValueError('로컬 포트가 올바르지 않습니다.')
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    summary={'schema':'meta-actual-video-loop-presentation-v1','created_at':datetime.now(timezone.utc).isoformat(),
             'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
             'example_id':'go2-down-slow','instruction':INSTRUCTION,'actual_video_inference_executed':False,
             'actual_reflection_executed':False,'new_training_executed':False,'performance_improvement_measured':False,
             'model_quality_benchmark':False,'scope':'single_seen_development_case','pushed':False,'submitted':False}
    try:
        before=request(args.port,'/api/meta/status')['status'];summary['status_before']=before
        prepared=request(args.port,'/api/improvement/prepare',{'example_id':'go2-down-slow','instruction':INSTRUCTION})
        sid=prepared['session_id'];summary['session_id']=sid
        summary['input_manifest']=prepared['input_manifest']
        summary['prepared_request_sha256']=hashlib.sha256(json.dumps(prepared['request'],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        if args.prepare_only:
            summary['status']='prepared_not_sent';return
        if not before['key_configured'] or not before['credit_confirmed'] or not before.get('enabled'):
            summary['status']='blocked_missing_existing_key_or_credit_confirmation';return
        if not before.get('video_calls_enabled'):
            summary['status']='blocked_video_calls_locked';return
        first=request(args.port,'/api/improvement/analyze',{'session_id':sid})
        summary['first']=archive_response(out,'first',first);summary['actual_video_inference_executed']=True
        first_checks=checks(args.port,sid,len(first['analysis']['hypotheses']));write(out/'first-rounds.json',first_checks)
        summary['first_checks']=[{'hypothesis_id':r['selected_hypothesis_id'],'verdict':r['result']['verdict'],
                                 'interval':r['result'].get('interval')} for r in first_checks['history']]
        reflected=request(args.port,'/api/improvement/analyze',{'session_id':sid})
        summary['reflection']=archive_response(out,'reflection',reflected);summary['actual_reflection_executed']=True
        final_checks=checks(args.port,sid,len(reflected['analysis']['hypotheses']));write(out/'reflection-rounds.json',final_checks)
        summary['reflection_checks']=[{'hypothesis_id':r['selected_hypothesis_id'],'verdict':r['result']['verdict'],
                                      'interval':r['result'].get('interval')} for r in final_checks['history']]
        summary['analysis_changed']=first['analysis']!=reflected['analysis']
        summary['interpretation']='같은 저장 기록의 가설 재검토. 변경 여부는 모델 정확도·학습 개선 효과를 뜻하지 않음.'
        write(out/'final-session.json',request(args.port,'/api/improvement/export',{'session_id':sid}))
        summary['status']='completed_actual_video_and_reflection'
    except Exception as error:
        summary['status']='failed_preserved_no_automatic_retry';summary['error']=str(error)
        raise
    finally:
        try:
            after=request(args.port,'/api/meta/status')['status'];summary['status_after']=after
            if 'status_before' in summary:
                summary['new_generation_requests']=after['live_requests_started']-summary['status_before']['live_requests_started']
                summary['new_preflight_requests']=after.get('preflight_requests_started',0)-summary['status_before'].get('preflight_requests_started',0)
        except Exception:summary['status_after_available']=False
        write(out/'summary.json',summary)
        print(json.dumps({'out':str(out),'status':summary['status'],
                          'actual_video_inference_executed':summary['actual_video_inference_executed'],
                          'actual_reflection_executed':summary['actual_reflection_executed']},ensure_ascii=False))


if __name__=='__main__':main()
