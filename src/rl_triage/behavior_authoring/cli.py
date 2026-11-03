"""네트워크·GPU 없이 저장 기록과 조건을 다루는 진입점."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .adapters import build_sam_request, build_spark_request, import_sam_response, import_spark_response
from .datasets import default_contract, load_example
from .evaluate import evaluate_contract
from .schema import contract_digest


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f'비유한 값: {value}')))


def write_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def main() -> int:
    parser = argparse.ArgumentParser(description='Mollet 행동 조건 편집기: 로컬 기록과 저장 모델 응답만 사용')
    sub = parser.add_subparsers(dest='command', required=True)
    serve = sub.add_parser('serve'); serve.add_argument('--port', type=int, default=8784)
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
    args = parser.parse_args()
    try:
        if args.command == 'serve':
            from .web import serve_local
            serve_local(args.port)
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
