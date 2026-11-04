"""실행 중인 로컬 Meta 연결을 재시작하지 않고 새 화면에서 재사용한다."""
from __future__ import annotations

import http.client
import json

from .live import LiveService


class AttachedLiveService(LiveService):
    """고정 loopback 포트에 기존 API 작업만 전달한다. 비밀키를 조회하지 않는다."""

    def __init__(self, port: int):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError('기존 Meta 연결의 로컬 포트를 확인하세요.')
        self.port = port
        self.video_calls_enabled = False
        self.video_input_sha256 = None

    def _request(self, route: str, body: dict | None = None) -> dict:
        if route not in {'status','configure','quote','run/spark','run/sam'}:
            raise ValueError('기존 연결에서 지원하지 않는 작업입니다.')
        connection = http.client.HTTPConnection('127.0.0.1',self.port,timeout=190)
        try:
            connection.request('GET' if body is None else 'POST','/api/meta/'+route,
                               body=None if body is None else json.dumps(body,ensure_ascii=False).encode('utf-8'),
                               headers={'Content-Type':'application/json'})
            response = connection.getresponse()
            raw = response.read(32*1024*1024+1)
            if len(raw)>32*1024*1024 or response.status!=200:
                raise ValueError('기존 로컬 Meta 연결이 요청을 완료하지 못했습니다.')
            return json.loads(raw)
        except (OSError,http.client.HTTPException,json.JSONDecodeError):
            raise ValueError('기존 로컬 Meta 서버에 연결하지 못했습니다.') from None
        finally:
            connection.close()

    def status(self) -> dict:
        return {**self._request('status')['status'],'video_calls_enabled':False,'attached_local_port':self.port}

    def configure(self, values: dict) -> dict:
        if set(values)-{'api_key','credit_confirmed','remaining_credit_usd','budget_usd'}:
            raise ValueError('지원하지 않는 연결 설정입니다.')
        self._request('configure',values)
        return self.status()

    def quote(self, model: str, example_id: str, instruction: str='', observations: dict | None=None) -> dict:
        return self._request('quote',{'model':model,'example_id':example_id,'instruction':instruction,
                                      'observations':observations})['quote']

    def run(self, model: str, example_id: str, instruction: str, observations: dict | None=None) -> dict:
        if model not in {'spark','sam'}:
            raise ValueError('지원하지 않는 Meta 모델입니다.')
        return self._request('run/'+model,{'example_id':example_id,'instruction':instruction,
                                          'prompt':instruction,'observations':observations})
