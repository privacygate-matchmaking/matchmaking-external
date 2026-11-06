import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from privacygate.errors import GateError
from privacygate.session_rules import csrf_problem

from .core import MAX_FILE, Audit, application, deny, required
from .identity import Identity, principal
from .sessions import Sessions


def create_app(config=None, identity=None, upstream=None):
    config = config or {
        'discovery':required('OIDC_DISCOVERY_URL'), 'client_id':os.getenv('OIDC_CLIENT_ID','external-web'),
        'client_secret':required('OIDC_CLIENT_SECRET'), 'service_id':os.getenv('SERVICE_CLIENT_ID','external-api'),
        'service_secret':required('SERVICE_CLIENT_SECRET'), 'base':required('APP_BASE_URL').rstrip('/'),
        'transfer':os.getenv('TRANSFER_API_URL','http://transfer-api:8000'),
        'sessions':os.getenv('SESSION_DB_PATH','/data/external-session/sessions.sqlite3'),
        'key':Path(os.getenv('SESSION_KEY_FILE','/run/secrets/external_session_key')).read_bytes().strip(),
        'audit':os.getenv('AUDIT_LOG_PATH','/var/log/matchmaking/external-api.json'),
        'mode':os.getenv('GATE_MODE','enforce'), 'secure':True,
    }
    if config['mode'] not in ('enforce','baseline'):
        raise RuntimeError('Invalid GATE_MODE')
    identity = identity or Identity(config['discovery'])
    upstream = upstream or httpx.Client(base_url=config['transfer'], timeout=15, trust_env=False)
    sessions = Sessions(config['sessions'], config['key'])
    audit = Audit('external-api', config['audit'])
    app = application('external-api', audit)

    def current(request: Request):
        sid = request.cookies.get('external_session','')
        row = sessions.get(sid)
        tokens = row['tokens']
        if tokens['access_expires_at'] <= time.time()+30:
            with sessions.refresh_lock:
                row = sessions.get(sid)
                tokens = row['tokens']
                if tokens['access_expires_at'] <= time.time()+30:
                    try:
                        if not tokens.get('refresh_token'):
                            deny('AUTH_REQUIRED','refresh_missing')
                        fresh = identity.token({'grant_type':'refresh_token','refresh_token':tokens['refresh_token']}, config['client_id'], config['client_secret'])
                        claims = identity.verify(fresh['access_token'],'transfer-api','external-web')
                        user = principal(claims)
                        if user['role'] != 'member' or user['user_id'] != row['user_id']:
                            deny('AUTH_REQUIRED','refresh_identity_changed')
                        if fresh.get('id_token'):
                            id_claims=identity.verify(fresh['id_token'], config['client_id'], 'external-web')
                            if id_claims['sub']!=row['user_id']:
                                deny('AUTH_REQUIRED','refresh_identity_changed')
                        tokens = sessions.replace(sid,tokens,fresh)
                    except GateError as exc:
                        if exc.status in (401,403):
                            sessions.delete(sid)
                        raise
        claims = identity.verify(tokens['access_token'],'transfer-api','external-web')
        user = principal(claims)
        if user['role'] != 'member' or user['user_id'] != row['user_id']:
            sessions.delete(sid)
            deny('AUTH_REQUIRED','not_member')
        request.state.principal = user
        request.state.session = row
        request.state.tokens = tokens
        if request.method not in ('GET','HEAD','OPTIONS'):
            problem = csrf_problem(row['csrf_token'], request.headers.get('X-CSRF-Token'), request.headers.get('Origin'), config['base'])
            if problem:
                deny('ACCESS_DENIED',problem)
        return user

    @app.get('/auth/login')
    def login(request: Request):
        state, nonce, binding, verifier = [secrets.token_urlsafe(32) for _ in range(4)]
        sessions.flow(state, {'nonce':nonce,'binding':hashlib.sha256(binding.encode()).hexdigest(),'verifier':verifier})
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        params = {'response_type':'code','client_id':config['client_id'],'redirect_uri':config['base']+'/auth/callback',
                  'scope':'openid','state':state,'nonce':nonce,'code_challenge':challenge,'code_challenge_method':'S256'}
        response = RedirectResponse(identity.discovery()['authorization_endpoint']+'?'+urlencode(params),302)
        response.set_cookie('external_login',binding,max_age=600,path='/auth',httponly=True,secure=config['secure'],samesite='lax')
        return response

    @app.get('/auth/callback')
    def callback(request: Request, code: str | None=None, state: str | None=None, error: str | None=None):
        try:
            if not state or len(state)>200 or not code or len(code)>4096 or error:
                deny('AUTH_REQUIRED','login_failed')
            flow = sessions.consume(state)
            binding = request.cookies.get('external_login','')
            if not binding or not secrets.compare_digest(hashlib.sha256(binding.encode()).hexdigest(),flow['binding']):
                deny('AUTH_REQUIRED','browser_mismatch')
            tokens = identity.token({'grant_type':'authorization_code','code':code,'code_verifier':flow['verifier'],
                                     'redirect_uri':config['base']+'/auth/callback'},config['client_id'],config['client_secret'])
            id_claims = identity.verify(tokens.get('id_token',''),config['client_id'],'external-web',flow['nonce'])
            claims = identity.verify(tokens['access_token'],'transfer-api','external-web')
            user = principal(claims)
            if user['role'] != 'member' or user['user_id'] != id_claims['sub']:
                deny('ACCESS_DENIED','not_member')
            sessions.delete(request.cookies.get('external_session',''))
            sid = sessions.create(user,tokens)
            request.state.principal = user
            audit.record(request,'login')
            response = RedirectResponse('/',302)
            response.set_cookie('external_session',sid,max_age=28800,path='/',httponly=True,secure=config['secure'],samesite='strict')
        except GateError:
            audit.record(request,'login','deny','login_failed')
            response = RedirectResponse('/?login=failed',302)
        response.delete_cookie('external_login',path='/auth')
        return response

    @app.get('/api/me')
    def me(request: Request, user=Depends(current)):
        return {**user,'csrf_token':request.state.session['csrf_token'],'gate_mode':config['mode']}

    @app.post('/auth/logout')
    def logout(request: Request, user=Depends(current)):
        tokens = request.state.tokens
        sessions.delete(request.cookies.get('external_session',''))
        response = JSONResponse({'logout_url':identity.discovery()['end_session_endpoint']+'?'+urlencode({
            'id_token_hint':tokens['id_token'],'post_logout_redirect_uri':config['base']+'/'})})
        response.delete_cookie('external_session',path='/',secure=config['secure'],httponly=True,samesite='strict')
        audit.record(request,'logout')
        return response

    def relay(request, method, path, **kwargs):
        headers = {'Authorization':'Bearer '+identity.service(config['service_id'],config['service_secret']),
                   'X-User-Token':request.state.tokens['access_token'],'X-Trace-Id':request.state.trace_id}
        try:
            result = upstream.request(method,path,headers=headers,**kwargs)
        except httpx.HTTPError:
            deny('INTERNAL_ERROR','transfer_unavailable')
        if result.status_code >= 400:
            try:
                code = result.json()['error']['code']
            except (ValueError,KeyError,TypeError):
                code = 'INTERNAL_ERROR'
            if code not in ('AUTH_REQUIRED','ACCESS_DENIED','APPROVAL_MISMATCH','APPROVAL_EXPIRED','CONSENT_INVALID','INVALID_STATE','NOT_FOUND','INVALID_REQUEST'):
                code = 'INTERNAL_ERROR'
            deny(code,'upstream_'+code.lower())
        return result

    @app.get('/api/me/deliveries')
    def deliveries(request: Request, user=Depends(current)):
        return relay(request,'POST','/api/internal/delivery-queries',json={}).json()

    @app.get('/api/me/deliveries/{delivery_id}/download')
    def download(delivery_id: UUID, request: Request, file_id: UUID | None=None, user=Depends(current)):
        result=relay(request,'GET',f'/api/internal/deliveries/{delivery_id}/download',params={'file_id':str(file_id)} if file_id else {})
        audit.record(request,'delivery.download',target=str(delivery_id))
        extension='json' if result.headers.get('content-type','').startswith('application/json') else 'csv'
        return Response(result.content,media_type=result.headers.get('content-type','application/octet-stream'),
                        headers={'Content-Disposition':f'attachment; filename="ieum-material.{extension}"'})

    @app.get('/api/me/evidence-files')
    def evidence_list(request: Request, user=Depends(current)):
        return relay(request,'GET','/api/internal/evidence-submissions').json()

    @app.post('/api/me/evidence-files',status_code=201)
    async def evidence(request: Request, file: UploadFile=File(...), user=Depends(current)):
        body=await file.read(MAX_FILE+1)
        await file.close()
        if not body or len(body)>MAX_FILE or not body.startswith(b'%PDF-'):
            deny('INVALID_REQUEST','invalid_evidence')
        result=relay(request,'POST','/api/internal/evidence-submissions',files={'file':('evidence.pdf',body,'application/pdf')})
        audit.record(request,'evidence.submit')
        return result.json()

    @app.get('/api/me/consents')
    def consents(request: Request, user=Depends(current)):
        return relay(request,'GET','/api/internal/member-consents').json()

    @app.post('/api/me/consents',status_code=201)
    async def consent_create(request: Request, user=Depends(current)):
        from .models import Consent
        from pydantic import ValidationError
        try:
            body=Consent.model_validate(await request.json())
        except (ValueError,ValidationError):
            deny('INVALID_REQUEST','invalid_consent')
        result=relay(request,'POST','/api/internal/member-consents',json=body.model_dump(mode='json'))
        audit.record(request,'consent.create')
        return result.json()

    @app.post('/api/me/consents/{consent_id}/revoke')
    def consent_revoke(consent_id: UUID, request: Request, user=Depends(current)):
        result=relay(request,'POST',f'/api/internal/member-consents/{consent_id}/revoke',json={})
        audit.record(request,'consent.revoke')
        return result.json()

    setattr(current,'__privacygate_policy__','roles:member')
    from privacygate.web import assert_all_routes_guarded
    assert_all_routes_guarded(app)
    app.state.sessions, app.state.identity, app.state.current = sessions,identity,current
    return app
