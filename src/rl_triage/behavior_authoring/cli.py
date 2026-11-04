"""저장 기록과 명시적으로 설정한 Meta 요청을 다루는 진입점."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .adapters import build_sam_request, build_spark_request, import_sam_response, import_spark_response
from .datasets import STAIRS_EXAMPLES, default_contract, load_example
from .evaluate import evaluate_contract
from .schema import contract_digest


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f'비유한 값: {value}')))


def write_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def _meta_options(parser: argparse.ArgumentParser, *, serve: bool = False) -> None:
    prefix = '--meta-' if serve else '--'
    parser.add_argument(prefix+'key-file', dest='meta_key_file', type=Path, help='저장소 밖의 기존 API 키 파일. 키를 명령행 값으로 받지 않음')
    parser.add_argument(prefix+'credit-confirmed', dest='meta_credit_confirmed', action='store_true',default=None,help='Billing에서 크레딧·결제수단을 확인한 경우만 지정')
    parser.add_argument(prefix+'remaining-credit-usd', dest='meta_remaining_credit_usd')
    parser.add_argument(prefix+'budget-usd', dest='meta_budget_usd')
    parser.add_argument('--enable-video-calls', action='store_true', help='영상 분석과 토큰 계수의 외부 요청을 허용하는 서버/단일 호출 전용 옵션. 별도 승인 없이 사용하지 않음')
    parser.add_argument('--video-input-sha256',help='추가 영상 입력 허용을 받은 원본 SHA-256. 원본 승인 필드는 변경하지 않음')


def _meta_service(args: argparse.Namespace):
    from .client import MetaClient
    from .live import LiveService, read_key_file
    key = read_key_file(args.meta_key_file) if args.meta_key_file else None
    return LiveService(MetaClient(api_key=key, credit_confirmed=args.meta_credit_confirmed,
                                 remaining_credit_usd=args.meta_remaining_credit_usd, budget_usd=args.meta_budget_usd,
                                 persist_credentials=os.name=='nt'),
                       video_calls_enabled=args.enable_video_calls,video_input_sha256=args.video_input_sha256)


def main() -> int:
    parser = argparse.ArgumentParser(description='Mollet 행동 조건 편집기: 로컬 기록과 설정 후 Meta 모델 요청')
    sub = parser.add_subparsers(dest='command', required=True)
    serve = sub.add_parser('serve'); serve.add_argument('--port', type=int, default=8784)
    _meta_options(serve, serve=True)
    serve.add_argument('--attach-meta-port',type=int,help='기존 loopback 서버의 메모리 키를 유지하면서 새 화면 사용. 추가 영상 호출은 잠금')
    live = sub.add_parser('call', help='크레딧·키·예산 설정 후 Meta 요청 1회. 자동 재시도 없음')
    live.add_argument('--model', choices=['spark','sam','vision'], required=True)
    live.add_argument('--example', choices=list(STAIRS_EXAMPLES), default='h1-up')
    live.add_argument('--instruction', required=True, help='Spark 행동 지시 또는 SAM 객체 이름')
    _meta_options(live)
    status = sub.add_parser('meta-status', help='키 값을 노출하지 않고 로컬 호출 설정 확인')
    _meta_options(status)
    demo = sub.add_parser('demo'); demo.add_argument('--out', type=Path, required=True)
    evaluate = sub.add_parser('evaluate')
    for name in ('spec', 'observations', 'out'): evaluate.add_argument('--'+name, type=Path, required=True)
    evaluate.add_argument('--approval', type=Path)
    request = sub.add_parser('request')
    request.add_argument('--model', choices=['spark', 'sam'], required=True)
    request.add_argument('--example', choices=['reference', 'case'], default='reference')
    request.add_argument('--instruction', default=default_contract()['instruction'])
    request.add_argument('--video-reference', help='SAM provider file_id 또는 MP4 data URI. 파일 업로드는 수행하지 않음')
    request.add_argument('--out', type=Path, required=True)
    imported = sub.add_parser('import-response')
    imported.add_argument('--model', choices=['spark', 'sam'], required=True)
    imported.add_argument('--response', type=Path, required=True)
    imported.add_argument('--observations', type=Path, required=True)
    imported.add_argument('--out', type=Path, required=True)
    training = sub.add_parser('prepare-training', help='Meta 가설을 기존 교정 소비용 사전등록으로 연결. 승인·학습 실행 없음')
    training.add_argument('--analysis',type=Path,required=True)
    training.add_argument('--observations',type=Path,required=True)
    training.add_argument('--hypothesis',required=True)
    training.add_argument('--change-id')
    training.add_argument('--workspace',type=Path)
    training.add_argument('--recovery-mode',choices=['restart','resume'],default='restart')
    training.add_argument('--out',type=Path,required=True)
    recovery = sub.add_parser('compare-recovery',help='기존 고정 평가 판정기로 저장 평가 비교')
    recovery.add_argument('--input',type=Path,required=True)
    recovery.add_argument('--out',type=Path,required=True)
    correction = sub.add_parser('prepare-correction',help='확인 probe와 Meta 교정이 일치할 때 기존 원장에 GPU 예산 0의 미승인 제안 작성')
    correction.add_argument('--analysis',type=Path,required=True)
    correction.add_argument('--observations',type=Path,required=True)
    correction.add_argument('--hypothesis',required=True)
    correction.add_argument('--case-id',required=True)
    correction.add_argument('--loops',type=Path,required=True)
    correction.add_argument('--source-report',type=Path,required=True)
    correction.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    try:
        if args.command == 'serve':
            from .web import serve_local
            if args.attach_meta_port:
                from .attached import AttachedLiveService
                if args.attach_meta_port==args.port:raise ValueError('서버 포트와 기존 연결 포트가 같을 수 없습니다.')
                service=AttachedLiveService(args.attach_meta_port)
                service.status()
            else:service=_meta_service(args)
            serve_local(args.port, meta_service=service)
        elif args.command == 'meta-status':
            print(json.dumps(_meta_service(args).status(), ensure_ascii=False))
        elif args.command == 'call':
            service = _meta_service(args)
            value = (service.analyze_video(args.example,args.instruction) if args.model=='vision'
                     else service.run(args.model,args.example,args.instruction))
            print(json.dumps({'receipt':value['receipt'],'status':value['status']},ensure_ascii=False))
        elif args.command == 'demo':
            if args.out.exists(): raise ValueError('새 결과 폴더를 지정하세요. 기존 결과는 덮어쓰지 않습니다.')
            args.out.mkdir(parents=True)
            spec = default_contract(); write_new(args.out/'contract.json', spec)
            results = {}
            for example in ('reference', 'case'):
                bundle = load_example(example)
                write_new(args.out/f'{example}-observations.json', bundle)
                result = evaluate_contract(spec, bundle, {'contract_sha256': contract_digest(spec), 'actor': 'automated_demo', 'scope': 'automated_test'})
                result.update({'input_provenance': bundle['provenance'], 'model_called': False, 'interpretation': '예제 조건의 저장 수치 판정. 기존 회복 판정·영상 동기화 승인·모델 성능을 뜻하지 않음'})
                write_new(args.out/f'{example}-result.json', result)
                results[example] = result['verdict']
            write_new(args.out/'summary.json', {'schema':'behavior-authoring-demo-v1', 'results':results, 'model_api_calls':0, 'gpu_jobs':0, 'source_kind':'recorded_development_state', 'visual_input_status':'deferred_archive_not_model_approved', 'generalization_claim':False})
            print(json.dumps({'out':str(args.out), 'results':results, 'model_api_calls':0}, ensure_ascii=False))
        elif args.command == 'prepare-training':
            from .triage_bridge import prepare_training
            if args.out.exists():
                raise ValueError('새 출력 파일을 지정하세요.')
            result = prepare_training(read_json(args.analysis),read_json(args.observations),args.hypothesis,
                                      change_id=args.change_id,workspace=args.workspace,recovery_mode=args.recovery_mode)
            write_new(args.out,result)
            print(json.dumps({'out':str(args.out),'status':result['status'],
                              'consumer_ready':result['consumer_ready'],'training_started':False},ensure_ascii=False))
        elif args.command == 'compare-recovery':
            from .triage_bridge import compare_recovery
            result=compare_recovery(read_json(args.input))
            write_new(args.out,result)
            print(json.dumps(result,ensure_ascii=False))
        elif args.command == 'prepare-correction':
            from .triage_bridge import prepare_registered_correction
            if args.out.exists():raise ValueError('새 출력 파일을 지정하세요.')
            result=prepare_registered_correction(read_json(args.analysis),read_json(args.observations),args.hypothesis,
                                                 case_id=args.case_id,loops=args.loops,source_report=read_json(args.source_report))
            write_new(args.out,result)
            print(json.dumps({'out':str(args.out),'decision':result['decision'],'training_started':False},ensure_ascii=False))
        elif args.command == 'evaluate':
            result = evaluate_contract(read_json(args.spec), read_json(args.observations), read_json(args.approval) if args.approval else None)
            write_new(args.out, result); print(result['verdict'])
        elif args.command == 'request':
            bundle = load_example(args.example)
            if args.model == 'spark': body = build_spark_request(args.instruction, bundle)
            else:
                if not args.video_reference: raise ValueError('SAM 요청에는 --video-reference가 필요합니다. 자동 업로드는 하지 않습니다.')
                body = build_sam_request(args.instruction, args.video_reference)
            write_new(args.out, body); print('요청 파일 저장 완료. API 전송은 수행하지 않았습니다.')
        else:
            bundle = read_json(args.observations)
            response = args.response.read_text(encoding='utf-8')
            value = import_spark_response(response, bundle) if args.model=='spark' else import_sam_response(response, bundle)
            write_new(args.out, value); print('저장 응답을 가져왔습니다. 공급자 인증·영상 결합의 실증을 뜻하지 않습니다.')
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f'오류: {exc}\n')
    return 0
