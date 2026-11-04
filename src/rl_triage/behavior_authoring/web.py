"""로컬 행동 조건 편집 화면과 명시적으로 설정한 Meta 요청을 처리한다."""
from __future__ import annotations

import json
import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .adapters import build_sam_request, build_spark_request, import_sam_response, import_spark_response
from .datasets import example_paths, list_examples, load_example_view, stairs_contract
from .evaluate import evaluate_contract
from .schema import contract_digest, validate_contract, validate_observations
from .live import LiveService
from .research import ResearchService
from .saved_run import load_saved_recovery

ASSETS = Path(__file__).with_name('web')
MAX_BODY = 12 * 1024 * 1024
_SERVICE_LOCK = threading.Lock()


def _json(raw: bytes) -> dict:
    value = json.loads(raw, parse_constant=lambda x: (_ for _ in ()).throw(ValueError('유한한 JSON만 허용합니다.')))
    if not isinstance(value, dict): raise ValueError('JSON 객체가 필요합니다.')
    return value


class Handler(BaseHTTPRequestHandler):
    server_version = 'MolletLocal/1.0'

    def log_message(self, format: str, *args) -> None:
        pass

    def _headers(self, status: int, content_type: str, length: int, extra: dict | None = None) -> None:
        self.send_response(status)
        for k,v in {
            'Content-Type':content_type, 'Content-Length':str(length),
            'X-Content-Type-Options':'nosniff', 'Cache-Control':'no-store',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            **(extra or {}),
        }.items(): self.send_header(k,v)
        self.end_headers()

    def _send(self, value: dict, status: int = 200) -> None:
        data=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
        self._headers(status,'application/json; charset=utf-8',len(data));self.wfile.write(data)

    def _meta(self) -> LiveService:
        with _SERVICE_LOCK:
            if not hasattr(self.server, 'meta_service'):
                self.server.meta_service = LiveService()
            return self.server.meta_service

    def _local(self) -> bool:
        port=self.server.server_port
        hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
        if self.headers.get('Host') not in hosts:
            self._send({'error':'로컬 호스트 요청만 허용합니다.'},403);return False
        origin=self.headers.get('Origin')
        if origin and origin not in {f'http://{h}' for h in hosts}:
            self._send({'error':'다른 웹사이트에서 보낸 요청은 허용하지 않습니다.'},403);return False
        return True

    def _research(self) -> ResearchService:
        live = self._meta()
        with _SERVICE_LOCK:
            if not hasattr(self.server, 'research_service'):
                self.server.research_service = ResearchService(live)
            return self.server.research_service

    def do_GET(self) -> None:
        if not self._local():return
        url=urlsplit(self.path);route=url.path
        try:
            if route=='/api/meta/status':
                self._send({'status':self._meta().status()});return
            if route=='/api/recovery/saved':
                if url.query: raise ValueError('저장 복구 경로에는 파일 경로나 URL 매개변수를 받지 않습니다.')
                self._send(load_saved_recovery());return
            if route=='/api/examples':
                self._send({'examples':list_examples(),'default_example':'h1-up','default_contract':stairs_contract(),
                            'model_api_calls':self._meta().status()['live_requests_started'],
                            'saved_recovery_available':True});return
            if route=='/api/example':
                example=parse_qs(url.query).get('id',['h1-up'])[0]
                self._send(load_example_view(example));return
            if route.startswith('/media/'):
                key=route.removeprefix('/media/');_,path,_=example_paths(key)
                size=path.stat().st_size;start=0;end=size-1;status=200;headers={'Accept-Ranges':'bytes'}
                raw_range=self.headers.get('Range')
                if raw_range:
                    match=re.fullmatch(r'bytes=(\d+)-(\d*)',raw_range)
                    if not match: self._headers(416,'video/mp4',0,{'Content-Range':f'bytes */{size}'});return
                    start=int(match[1]);end=min(int(match[2]) if match[2] else end,end)
                    if start>end or start>=size:self._headers(416,'video/mp4',0,{'Content-Range':f'bytes */{size}'});return
                    status=206;headers['Content-Range']=f'bytes {start}-{end}/{size}'
                self._headers(status,'video/mp4',end-start+1,headers)
                with path.open('rb') as stream:
                    stream.seek(start);remaining=end-start+1
                    while remaining:
                        chunk=stream.read(min(65536,remaining))
                        if not chunk:break
                        self.wfile.write(chunk);remaining-=len(chunk)
                return
            files={'/':'index.html','/app.js':'app.js','/improvement.js':'improvement.js','/style.css':'style.css'}
            if route not in files:self._send({'error':'없는 경로입니다.'},404);return
            path=ASSETS/files[route];data=path.read_bytes()
            mime=mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
            if path.suffix in {'.html','.js','.css'}:mime+='; charset=utf-8'
            self._headers(200,mime,len(data));self.wfile.write(data)
        except (ValueError,KeyError,TypeError) as exc:self._send({'error':str(exc)},400)
        except (BrokenPipeError,ConnectionResetError):pass
        except OSError:self._send({'error':'필요한 로컬 예제 파일을 읽을 수 없습니다. checkout에서 실행하세요.'},404)

    def do_POST(self) -> None:
        if not self._local():return
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':
            self._send({'error':'application/json이 필요합니다.'},415);return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=MAX_BODY:self._send({'error':'요청 크기를 확인하세요.'},413);return
            data=_json(self.rfile.read(length));route=urlsplit(self.path).path
            if route.startswith('/api/improvement/'):
                service = self._research()
                action = route.rsplit('/', 1)[-1]
                if action == 'prepare':
                    value = service.prepare(data['example_id'], data['instruction'], data.get('observations'))
                elif action == 'cancel':
                    value = service.cancel(data['session_id'])
                elif action == 'seed':
                    value = service.seed(data['example_id'],data['instruction'],data['contract'],data.get('observations'))
                elif action == 'import':
                    value = service.accept(data['session_id'], data['response'])
                elif action == 'analyze':
                    value = service.analyze(data['session_id'])
                elif action == 'round':
                    value = service.step(data['session_id'])
                elif action == 'reflect':
                    value = service.reflect(data['session_id'])
                elif action == 'export':
                    value = service.snapshot(data['session_id'])
                elif action == 'training':
                    from .triage_bridge import prepare_training
                    session = service.snapshot(data['session_id'])
                    # HTTP 입력으로 임의 작업 경로나 실행 권한을 받지 않는다.
                    _, bundle = self._meta()._input(session['example_id'], None)
                    value = prepare_training(session['analysis'],bundle,data['hypothesis_id'],
                                             change_id=data.get('change_id'),recovery_mode=data.get('recovery_mode','restart'))
                elif action == 'recovery':
                    value = service.recover(data['session_id'],data['hypothesis_id'],data['comparison'])
                else:
                    self._send({'error':'없는 분석 경로입니다.'},404);return
                self._send(value);return
            if route=='/api/meta/configure':
                self._send({'status':self._meta().configure(data)});return
            if route=='/api/meta/quote':
                self._send({'quote':self._meta().quote(data['model'],data['example_id'],data.get('instruction',''),data.get('observations'))});return
            if route in {'/api/meta/run/spark','/api/meta/run/sam'}:
                model=route.rsplit('/',1)[-1]
                instruction=data['instruction'] if model=='spark' else data['prompt']
                self._send(self._meta().run(model,data['example_id'],instruction,data.get('observations')));return
            if route=='/api/validate':self._send({'contract':validate_contract(data['contract'])});return
            if route=='/api/review':
                spec=validate_contract(data['contract'])
                self._send({'approval':{'contract_sha256':contract_digest(spec),'actor':'local_browser_review','scope':'local_semantic_review'}});return
            if route=='/api/evaluate':
                bundle=validate_observations(data['observations']);spec=validate_contract(data['contract'])
                result=evaluate_contract(spec,bundle,data.get('approval'))
                result['input_provenance']=bundle['provenance']
                result['model_called']=False
                self._send({'result':result});return
            if route=='/api/request/spark':
                bundle=validate_observations(data['observations'])
                self._send({'request':build_spark_request(data['instruction'],bundle),'sent':False});return
            if route=='/api/request/sam':
                self._send({'request':build_sam_request(data['prompt'],data['video_reference']),'sent':False});return
            if route=='/api/import/spark':
                self._send({'contract':import_spark_response(data['response'],data['observations'])});return
            if route=='/api/import/sam':
                self._send({'observations':import_sam_response(data['response'],data['observations'])});return
            self._send({'error':'없는 경로입니다.'},404)
        except (ValueError,KeyError,TypeError,OverflowError) as exc:
            self._send({'error':str(exc)},400)
        except (BrokenPipeError,ConnectionResetError):pass
        except OSError:self._send({'error':'모델 요청의 로컬 기록을 읽거나 저장하지 못했습니다. 자동 재시도하지 마세요.'},500)


def serve_local(port: int = 8784, *, meta_service: LiveService | None = None) -> None:
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    if meta_service is not None:
        server.meta_service = meta_service
    print(f'Mollet local: http://127.0.0.1:{server.server_port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
