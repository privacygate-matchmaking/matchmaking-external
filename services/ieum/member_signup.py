"""이음 회원가입 (로그인 전). 계정 생성·회원 저장·담당 배정은 internal-api 가 한다.

external-web 가입 폼 → external-api POST /api/signup (Origin 확인·요청 횟수 제한, 세션 없음)
  → transfer-api POST /api/internal/member-signups (external-api 서비스 토큰만)
  → internal-api POST /api/internal/member-signups (transfer-api 서비스 토큰만)

비밀번호는 요청 본문으로만 지나간다. 세 서비스 모두 감사 로그·응답·오류 메시지에 남기지 않는다.
"""
import threading
import time
from collections import deque
from datetime import date
from typing import Literal

import httpx
from fastapi import Depends, Request
from pydantic import Field

from .core import deny
from .identity import bearer
from .models import Strict

CONSENT_VERSION = '2026-11'


class Signup(Strict):
    """형식만 먼저 거른다. 나이·연락처 형식·중복 등 최종 판단은 internal-api 가 한다."""
    username: str = Field(min_length=4, max_length=20, pattern=r'^[a-z][a-z0-9_]{3,19}$')
    password: str = Field(min_length=12, max_length=64)
    name: str = Field(min_length=1, max_length=30)
    gender: Literal['남','여']
    birth_date: date
    contact_phone: str = Field(min_length=10, max_length=20)
    contact_email: str = Field(min_length=6, max_length=200)
    education_level: Literal['고졸','전문대졸','4년제 대졸','대학원졸']
    job_group: str = Field(min_length=1, max_length=40)
    height_cm: int = Field(ge=120, le=230)
    weight_kg: int = Field(ge=30, le=200)
    introduction: str = Field(min_length=1, max_length=300)
    privacy_consent: Literal[True]
    consent_version: Literal['2026-11']


class Limiter:
    """메모리 기반 요청 횟수 제한 (external-api 프로세스 하나 기준). 계정 대량 생성을 늦춘다."""
    def __init__(self, per_client=5, overall=60, window=600, clock=time.monotonic):
        self.per_client, self.overall, self.window, self.clock = per_client, overall, window, clock
        self.hits, self.all, self.lock = {}, deque(), threading.Lock()

    def allow(self, key):
        now = self.clock()
        with self.lock:
            while self.all and self.all[0] <= now-self.window:
                self.all.popleft()
            mine = self.hits.setdefault(key, deque())
            while mine and mine[0] <= now-self.window:
                mine.popleft()
            if not mine:
                self.hits.pop(key, None)
            if len(self.all) >= self.overall or len(mine) >= self.per_client:
                return False
            self.all.append(now)
            self.hits.setdefault(key, deque()).append(now)
            return True


def register_signup_bff(app, config, identity, upstream, audit, limiter=None):
    limiter = limiter or Limiter()

    def public_signup(request: Request):
        # 세션이 없으므로 CSRF 토큰 대신 Origin 을 확인한다 (같은 사이트의 가입 폼에서 온 요청만)
        if request.headers.get('Origin') != config['base']:
            deny('ACCESS_DENIED','origin_mismatch')
        # nginx 가 X-Forwarded-For 를 접속 주소로 덮어쓴다 (클라이언트가 보낸 값은 쓰지 않음)
        client = request.headers.get('X-Forwarded-For') or (request.client.host if request.client else '-')
        if not limiter.allow(client):
            deny('INVALID_STATE','signup_rate_limited','가입 요청이 많습니다. 잠시 후 다시 시도해 주세요.')
        return True

    setattr(public_signup,'__privacygate_policy__','public:signup')

    @app.post('/api/signup',status_code=201)
    def signup(body: Signup, request: Request, ok=Depends(public_signup)):
        headers = {'Authorization':'Bearer '+identity.service(config['service_id'],config['service_secret']),
                   'X-Trace-Id':request.state.trace_id}
        try:
            result = upstream.request('POST','/api/internal/member-signups',headers=headers,json=body.model_dump(mode='json'))
        except httpx.HTTPError:
            deny('INTERNAL_ERROR','transfer_unavailable')
        if result.status_code >= 400:
            try:
                error = result.json()['error']
                code, message = error['code'], error.get('message')
            except (ValueError,KeyError,TypeError):
                code, message = 'INTERNAL_ERROR', None
            if code not in ('ACCESS_DENIED','INVALID_STATE','INVALID_REQUEST'):
                code = 'INTERNAL_ERROR'
            if not isinstance(message,str) or len(message)>200 or '\n' in message or '\r' in message:
                message = None
            deny(code,'upstream_'+code.lower(),message)
        audit.record(request,'member.signup')
        return {'status':'registered'}


def register_signup_relay(app, identity, upstream, audit):
    def signup_client(request: Request):
        identity.verify(bearer(request),'transfer-api','external-api')
        return True

    setattr(signup_client,'__privacygate_policy__','service:external-api')

    @app.post('/api/internal/member-signups',status_code=201)
    def signup(body: Signup, request: Request, svc=Depends(signup_client)):
        upstream('POST','/api/internal/member-signups',json=body.model_dump(mode='json'))
        audit.record(request,'member.signup')
        return {'status':'registered'}
