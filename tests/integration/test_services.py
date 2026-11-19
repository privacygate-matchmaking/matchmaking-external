import json
import sqlite3
import sys
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from privacygate.errors import GateError

from ieum.core import Database, deny, iso, now
from ieum.external import create_app as external_app
from ieum.transfer import create_app as transfer_app

B='22222222-2222-4222-8222-222222222201'
C='22222222-2222-4222-8222-222222222202'
A='11111111-1111-4111-8111-111111111101'
P='11111111-1111-4111-8111-111111111103'
M='bbbbbbbb-0000-4000-8000-000000000001'
CONSENT='44444444-4444-4444-8444-444444444401'
PROFILE={'schema_version':2,'member_id':M,'display_name':'가상 회원','age_band':'30대','job_group':'연구','introduction':'합성 소개 자료'}


class FakeIdentity:
    def __init__(self):
        self.nonce=''
        self.refreshes=0
        self.fail_refresh=False

    def discovery(self):
        return {'authorization_endpoint':'https://id.test/auth','end_session_endpoint':'https://id.test/logout'}

    def service(self,client_id,secret):
        return 'SVC_EXTERNAL' if client_id=='external-api' else 'SVC_TRANSFER'

    def token(self,data,client_id,secret):
        if data['grant_type']=='refresh_token':
            self.refreshes+=1
            if self.fail_refresh: deny('AUTH_REQUIRED','refresh_rejected')
        return {'access_token':'MEMBER','id_token':'ID','refresh_token':'ROTATED' if self.refreshes else 'REFRESH','expires_in':300}

    def verify(self,token,audience,azp,nonce=None):
        if token=='ID' and azp=='external-web':
            if nonce is not None and nonce!=self.nonce: deny('AUTH_REQUIRED','nonce_mismatch')
            return {'sub':B,'azp':azp}
        tokens={
          'COUNSELOR':(A,'counselor','internal-web'), 'APPROVER':(P,'approver','internal-web'),
          'SELF':(A,'approver','internal-web'), 'ADMIN':(P,'data_admin','internal-web'),
          'MEMBER':(B,'member','external-web'), 'OTHER':(C,'member','external-web'),
          'SVC_EXTERNAL':(C,None,'external-api')}
        if token not in tokens or tokens[token][2]!=azp: deny('AUTH_REQUIRED','invalid_token')
        subject,role,client=tokens[token]
        return {'sub':subject,'azp':client,'realm_access':{'roles':[role] if role else []}}


def fixture_schema(path):
    # SQL equivalent of the pinned upstream transfer schema; SQLite is TEST-ONLY.
    conn=sqlite3.connect(path)
    conn.executescript('''
    CREATE TABLE transfer_files(file_id TEXT PRIMARY KEY,file_version INT NOT NULL,file_hash TEXT NOT NULL,uploaded_by TEXT NOT NULL,
      staging_key TEXT,verified_key TEXT,scan_status TEXT NOT NULL,scan_reason TEXT,export_banned BOOLEAN NOT NULL,created_at TEXT NOT NULL,UNIQUE(file_id,file_version));
    CREATE TABLE export_requests(request_id TEXT PRIMARY KEY,requester_id TEXT NOT NULL,file_id TEXT NOT NULL,file_version INT NOT NULL,
      file_hash TEXT NOT NULL,recipient_id TEXT NOT NULL,member_id TEXT NOT NULL,consent_id TEXT,purpose TEXT NOT NULL,
      request_status TEXT NOT NULL,expires_at TEXT NOT NULL,created_at TEXT NOT NULL,FOREIGN KEY(file_id,file_version) REFERENCES transfer_files(file_id,file_version));
    CREATE TABLE approvals(approval_id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL REFERENCES export_requests,requester_id TEXT NOT NULL,
      approver_id TEXT NOT NULL,approved_file_id TEXT NOT NULL,approved_file_version INT NOT NULL,approved_file_hash TEXT NOT NULL,
      expires_at TEXT NOT NULL,revoked_at TEXT,created_at TEXT NOT NULL,CHECK(approver_id<>requester_id));
    CREATE TABLE deliveries(delivery_id TEXT PRIMARY KEY,approval_id TEXT NOT NULL REFERENCES approvals,recipient_id TEXT NOT NULL,downloaded_at TEXT,created_at TEXT NOT NULL,download_count INT NOT NULL DEFAULT 0);
    CREATE TABLE download_tokens(token_hash TEXT PRIMARY KEY,delivery_id TEXT NOT NULL REFERENCES deliveries,recipient_id TEXT NOT NULL,created_at TEXT NOT NULL,expires_at TEXT NOT NULL,used_at TEXT);
    CREATE TABLE audit_log(id INTEGER PRIMARY KEY,ts TEXT NOT NULL,trace_id TEXT NOT NULL,actor TEXT,role TEXT,action TEXT NOT NULL,
      target_type TEXT,target_id TEXT,result TEXT NOT NULL,reason TEXT);
    ''')
    conn.close()


@pytest.fixture
def env(tmp_path):
    ident=FakeIdentity()
    state={'allowed':True,'consent_expires':iso(now()+timedelta(days=10)),'requests':[]}
    def upstream(req):
        state['requests'].append(req)
        if req.headers.get('Authorization')!='Bearer SVC_TRANSFER':
            return httpx.Response(401,json={'error':{'code':'AUTH_REQUIRED'}})
        if req.url.path=='/api/internal/matching-release-checks':
            body=json.loads(req.content)
            if not state.get('release_assigned',False):
                return httpx.Response(403,json={'error':{'code':'ACCESS_DENIED'}})
            if state.get('release_review_fail'):
                return httpx.Response(409,json={'error':{'code':'INVALID_STATE'}})
            fields=[{'scope':s,'label':s,'value':state.get('release_value','REVIEWED-TEXT'),
                     'review_status':'reviewed','reviewed_at':'2026-01-01T00:00:00Z'} for s in body['scopes']]
            for f in fields:
                if f['scope'] in ('photo','documents') and state.get('release_attachment'):
                    f['attachments']=[state['release_attachment']]
            return httpx.Response(200,json={'member_id':body['member_id'],'fields':fields})
        if req.url.path=='/api/internal/disclosure-checks':
            return httpx.Response(200,json={'allowed':state['allowed'],'expires_at':state['consent_expires'],
              'member_id':state.get('disclosure_member_id',M)})
        if req.url.path=='/api/internal/member-scope-checks':
            state.setdefault('scope_checks',[]).append((json.loads(req.content),req.headers.get('X-User-Token')))
            return httpx.Response(200,json={'allowed':req.headers.get('X-User-Token') in state.get('scope_allowed_tokens',('COUNSELOR','ADMIN'))})
        if req.url.path=='/api/internal/matching-staff-checks':
            if state.get('staff_scope_unavailable'):return httpx.Response(501,json={})
            state['staff_scope_body']=json.loads(req.content)
            return httpx.Response(200,json={'allowed':state.get('staff_scope_allowed',False)
                and req.headers.get('X-User-Token')=='COUNSELOR',
                'assigned_user_ids':state.get('staff_scope_assigned',[B,C])})
        if state.get('matching') and req.url.path=='/api/internal/matching-directory':
            from ieum.matching import synthetic_directory
            subject={'MEMBER':B,'OTHER':C}.get(req.headers.get('X-User-Token'))
            if not subject:return httpx.Response(403,json={'error':{'code':'ACCESS_DENIED'}})
            return httpx.Response(200,json=synthetic_directory(subject).model_dump(mode='json'))
        if state.get('matching') and req.url.path=='/api/internal/matching-disclosures':
            body=json.loads(req.content);state['disclosure_body']=body
            return httpx.Response(200,json={'member_id':body['member_id'],'fields':[
              {'scope':'employment','label':'재직','value':'합성 연구직','review_status':'reviewed','reviewed_at':iso(now())},
              {'scope':'contact','label':'연락처','value':'SYNTHETIC-CONTACT','review_status':'reviewed','reviewed_at':iso(now())}]})
        if req.url.path=='/api/internal/security-events':
            if state.get('security_down'):return httpx.Response(500,json={'error':{'code':'INTERNAL_ERROR'}})
            state.setdefault('security_events',[]).append({**json.loads(req.content),'user_token':req.headers.get('X-User-Token')})
            return httpx.Response(200,json={'incident_id':str(uuid4()),'locked_until':None,'newly_locked':False})
        if req.url.path=='/api/internal/member-notification-events':
            if state.get('events_fail'):return httpx.Response(500,json={'error':{'code':'INTERNAL_ERROR'}})
            state.setdefault('events',[]).append({**json.loads(req.content),'user_token':req.headers.get('X-User-Token')})
            return httpx.Response(200,json={'created':1})
        if req.url.path=='/api/internal/member-signups':
            state.setdefault('signups',[]).append({'body':json.loads(req.content),'user_token':req.headers.get('X-User-Token')})
            if state.get('signup_error'):return httpx.Response(state['signup_error'][0],json={'error':state['signup_error'][1]})
            return httpx.Response(201,json={'status':'registered','username':json.loads(req.content)['username']})
        if req.headers.get('X-User-Token')!='MEMBER':
            return httpx.Response(403,json={'error':{'code':'ACCESS_DENIED'}})
        if req.url.path.startswith('/api/internal/my-notifications'):
            state.setdefault('notification_calls',[]).append((req.method,req.url.path,req.content.decode() or None))
            if req.method=='GET':return httpx.Response(200,json={'unread':1,'items':[{'notification_id':CONSENT,'kind':'member_match_requested','title':'새 매칭 신청','body':'b','link_view':'matches','created_at':iso(now()),'read':False}]})
            return httpx.Response(200,json={'updated':1,'unread':0})
        if req.url.path=='/api/internal/my-photo':
            state['photo_which']=req.url.params.get('which')
            kind=state.get('photo_kind','jpeg')
            if kind=='missing':return httpx.Response(404,json={'error':{'code':'NOT_FOUND','message':'표시할 사진이 없습니다.'}})
            if kind=='html':return httpx.Response(200,content=b'<script>alert(1)</script>',headers={'content-type':'image/jpeg'})
            return httpx.Response(200,content=b'\xff\xd8\xff\xe0SYNTHETIC-JPEG',headers={'content-type':'image/jpeg'})
        if req.url.path=='/api/internal/my-status':
            if state.get('status_unavailable'):return httpx.Response(501,json={})
            return httpx.Response(200,json=state.get('member_status',{'status':'active','message':None}))
        if req.url.path=='/api/internal/my-profile':
            if req.method=='PATCH':state['profile_body']=json.loads(req.content)
            return httpx.Response(200,json={'height_cm':175,'weight_kg':70,'contact_phone':'010-****-5678','contact_email':None,'photo':{'latest':None,'approved_at':None}})
        if req.url.path=='/api/internal/my-withdrawal':
            state['withdrawal_body']=json.loads(req.content)
            state['member_status']={'status':'withdraw_pending','by':'self','can_cancel':True,
                                    'message':'탈퇴 신청한 계정입니다.','withdraw_at':iso(now()+timedelta(days=7))}
            return httpx.Response(200,json=state['member_status'])
        if req.url.path=='/api/internal/my-withdrawal/cancel':
            if state.get('member_status',{}).get('by')=='staff':
                return httpx.Response(403,json={'error':{'code':'ACCESS_DENIED','message':'직원 처리 탈퇴는 철회할 수 없습니다.'}})
            state['member_status']={'status':'active','message':None}
            return httpx.Response(200,json={'status':'active'})
        if req.url.path=='/api/internal/my-consents':
            if req.method=='POST':return httpx.Response(201,json={'consent_id':CONSENT,**json.loads(req.content)})
            return httpx.Response(200,json={'items':[],'counterparts':[{'counterpart_member_id':M,'counterpart_pseudo_id':'M-001'}]})
        if req.url.path.endswith('/revoke'):
            return httpx.Response(200,json={'consent_id':CONSENT,'revoked_at':iso(now())})
        if req.url.path=='/api/evidence-files':
            return httpx.Response(201,json={'file_id':str(uuid4()),'status':'submitted','created_at':iso(now()),'delete_after':iso(now()+timedelta(days=7))})
        if req.url.path=='/api/internal/my-evidence-files':
            return httpx.Response(200,json={'items':[]})
        return httpx.Response(501,json={'status':'not_implemented'})
    path=tmp_path/'transfer.sqlite3'; fixture_schema(path)
    config={'database':str(path),'discovery':'mock','service_id':'transfer-api','secret':'test-only',
       'internal':'http://internal','staging':str(tmp_path/'staging'),'verified':str(tmp_path/'verified'),
       'audit':str(tmp_path/'transfer.jsonl'),'mode':'enforce'}
    t=transfer_app(config,ident,Database(str(path),True),httpx.Client(base_url='http://internal',transport=httpx.MockTransport(upstream)))
    with TestClient(t) as tc:
        ecfg={'discovery':'mock','client_id':'external-web','client_secret':'test-only','service_id':'external-api',
          'service_secret':'test-only','base':'https://testserver','transfer':'http://transfer',
          'sessions':str(tmp_path/'sessions.sqlite3'),'key':Fernet.generate_key(),'audit':str(tmp_path/'external.jsonl'),'mode':'enforce','secure':True}
        e=external_app(ecfg,ident,tc)
        with TestClient(e,base_url='https://testserver') as ec:
            yield {'t':tc,'e':ec,'external':e,'transfer':t,'cfg':config,'id':ident,'state':state,'root':tmp_path,'ecfg':ecfg}


def headers(token='COUNSELOR'):
    return {'Authorization':'Bearer '+token}


def login(env):
    result=env['e'].get('/auth/login',follow_redirects=False)
    assert result.status_code==302
    q=parse_qs(urlsplit(result.headers['location']).query)
    env['id'].nonce=q['nonce'][0]
    callback=env['e'].get('/auth/callback',params={'state':q['state'][0],'code':'test-code'},follow_redirects=False)
    assert callback.status_code==302 and callback.headers['location']=='/'
    return env['e'].get('/api/me').json()


def upload(env,content=None):
    result=env['t'].post('/api/transfer-files',files={'file':('intro.json',content or json.dumps(PROFILE).encode())},headers=headers())
    assert result.status_code==201,result.text
    return result.json()


def request(env):
    f=upload(env)
    result=env['t'].post('/api/export-requests',headers=headers(),json={'file_id':f['file_id'],'member_id':M,'recipient_id':B,
      'consent_id':CONSENT,'purpose':'introduction','expires_at':iso(now()+timedelta(days=1))})
    assert result.status_code==201,result.text
    return result.json(),f


def approved(env):
    r,f=request(env)
    result=env['t'].post(f'/api/export-requests/{r["request_id"]}/approve',headers=headers('APPROVER'),json={})
    assert result.status_code==200,result.text
    return result.json(),f


def test_normal_full_bff_transfer_file_flow(env):
    d,f=approved(env); user=login(env)
    assert user['role']=='member' and 'access_token' not in user
    result=env['e'].get('/api/me/deliveries').json()
    assert len(result['items'])==1
    response=env['e'].get(f'/api/me/deliveries/{d["delivery_id"]}/download')
    assert response.status_code==200 and response.json()==PROFILE
    assert response.headers['cache-control']=='no-store'
    assert response.headers['content-disposition'].endswith('.json"')
    assert env['e'].get('/api/me/deliveries').json()['items'][0]['downloaded_at']


@pytest.mark.parametrize('mode',['enforce','baseline'])
def test_same_file_substitution_request_comparison(env,mode):
    d,_=approved(env); private=upload(env,b'name,phone\nSYNTHETIC,010-0000-0000')
    assert private['export_banned'] and private['scan_status']=='blocked'
    env['cfg']['mode']=mode;login(env)
    response=env['e'].get(f'/api/me/deliveries/{d["delivery_id"]}/download',params={'file_id':private['file_id']})
    assert response.status_code==(200 if mode=='baseline' else 403)
    assert (b'SYNTHETIC' in response.content)==(mode=='baseline')
    if mode=='baseline':
        assert response.headers['content-disposition'].endswith('.csv"')


@pytest.mark.parametrize('mode',['enforce','baseline'])
@pytest.mark.parametrize('fault',['expired','revoked','consent','hash','version','scan','banned'])
def test_baseline_preserves_all_other_controls(env,mode,fault):
    d,f=approved(env);private=upload(env,b'SYNTHETIC,private')
    env['cfg']['mode']=mode
    with env['transfer'].state.database.connection() as db:
        if fault=='expired':db.execute('UPDATE approvals SET expires_at=%s',(now()-timedelta(seconds=1),))
        if fault=='revoked':db.execute('UPDATE approvals SET revoked_at=%s',(now(),))
        if fault=='consent':env['state']['allowed']=False
        if fault=='hash':(Path(env['cfg']['verified'])/f['file_id']).write_bytes(b'changed')
        if fault=='version':db.execute('UPDATE approvals SET approved_file_version=2')
        if fault=='scan':db.execute("UPDATE transfer_files SET scan_status='blocked' WHERE file_id=%s",(f['file_id'],))
        if fault=='banned':db.execute('UPDATE transfer_files SET export_banned=%s WHERE file_id=%s',(True,f['file_id']))
    result=env['t'].get(f'/api/internal/deliveries/{d["delivery_id"]}/download',params={'file_id':private['file_id']},
       headers={**headers('SVC_EXTERNAL'),'X-User-Token':'MEMBER'})
    assert result.status_code==403 and b'SYNTHETIC' not in result.content


@pytest.mark.parametrize('token',['COUNSELOR','ADMIN','MEMBER'])
def test_approval_role_boundaries(env,token):
    r,_=request(env)
    result=env['t'].post(f'/api/export-requests/{r["request_id"]}/approve',json={},headers=headers(token))
    assert result.status_code in (401,403)


def test_self_and_duplicate_approval(env):
    r,_=request(env);path=f'/api/export-requests/{r["request_id"]}/approve'
    assert env['t'].post(path,json={},headers=headers('SELF')).status_code==403
    assert env['t'].post(path,json={},headers=headers('APPROVER')).status_code==200
    assert env['t'].post(path,json={},headers=headers('APPROVER')).status_code==409


def test_other_member_and_forged_header(env):
    d,_=approved(env)
    result=env['t'].get(f'/api/internal/deliveries/{d["delivery_id"]}/download',headers={**headers('SVC_EXTERNAL'),'X-User-Token':'OTHER','X-Actor-ID':B})
    assert result.status_code==404
    assert env['t'].get(f'/api/internal/deliveries/{d["delivery_id"]}/download',headers={'X-Actor-ID':B}).status_code==401


def test_csrf_and_logout(env):
    me=login(env);sid=env['e'].cookies.get('external_session')
    assert env['e'].post('/auth/logout',json={}).status_code==403
    result=env['e'].post('/auth/logout',json={},headers={'X-CSRF-Token':me['csrf_token'],'Origin':'https://testserver'})
    assert result.status_code==200 and 'logout_url' in result.json()
    env['e'].cookies.set('external_session',sid)
    assert env['e'].get('/api/me').status_code==401


def test_token_refresh_rotation_and_absolute_expiry(env):
    login(env);sessions=env['external'].state.sessions;sid=env['e'].cookies.get('external_session')
    row=sessions.get(sid);absolute=row['absolute_expires_at']
    tokens=row['tokens'];tokens['access_expires_at']=0
    with sessions.db() as db:db.execute('UPDATE sessions SET payload=?',(sessions.encode(tokens),))
    assert env['e'].get('/api/me').status_code==200
    updated=sessions.get(sid)
    assert updated['tokens']['refresh_token']=='ROTATED' and updated['absolute_expires_at']==absolute
    assert env['id'].refreshes==1


def test_refresh_failure_invalidates_session(env):
    login(env);sessions=env['external'].state.sessions;sid=env['e'].cookies.get('external_session')
    row=sessions.get(sid);tokens=row['tokens'];tokens['access_expires_at']=0
    with sessions.db() as db:db.execute('UPDATE sessions SET payload=?',(sessions.encode(tokens),))
    env['id'].fail_refresh=True
    assert env['e'].get('/api/me').status_code==401
    with sessions.db() as db:assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==0


def test_session_tokens_encrypted_and_restart_persistent(env):
    login(env)
    contents=Path(env['ecfg']['sessions']).read_bytes()
    assert b'REFRESH' not in contents and b'ROTATED' not in contents
    from ieum.sessions import Sessions
    reopened=Sessions(env['ecfg']['sessions'],env['ecfg']['key'])
    assert reopened.get(env['e'].cookies.get('external_session'))['user_id']==B


def test_pkce_state_and_browser_binding(env):
    response=env['e'].get('/auth/login',follow_redirects=False)
    q=parse_qs(urlsplit(response.headers['location']).query)
    assert q['code_challenge_method']==['S256']
    env['id'].nonce=q['nonce'][0]
    env['e'].cookies.clear()
    r=env['e'].get('/auth/callback',params={'state':q['state'][0],'code':'code'},follow_redirects=False)
    assert 'failed' in r.headers['location']
    r=env['e'].get('/auth/callback',params={'state':q['state'][0],'code':'code'},follow_redirects=False)
    assert 'failed' in r.headers['location']


def test_evidence_and_consent_relay(env):
    me=login(env);h={'X-CSRF-Token':me['csrf_token'],'Origin':'https://testserver'}
    result=env['e'].post('/api/me/evidence-files',files={'file':('e.pdf',b'%PDF-1.4\nsynthetic')},headers=h)
    assert result.status_code==201
    assert env['e'].get('/api/me/evidence-files').json()=={'items':[]}
    assert env['e'].get('/api/me/consents').status_code==200
    body={'counterpart_member_id':M,'item':'profile','purpose':'introduction','expires_at':iso(now()+timedelta(days=1))}
    assert env['e'].post('/api/me/consents',json=body,headers=h).status_code==201
    assert env['e'].post(f'/api/me/consents/{CONSENT}/revoke',json={},headers=h).status_code==200
    body['user_id']=C
    assert env['e'].post('/api/me/consents',json=body,headers=h).status_code==422


def test_audit_no_sensitive_values(env):
    d,f=approved(env);login(env)
    env['e'].get(f'/api/me/deliveries/{d["delivery_id"]}/download',params={'file_id':str(uuid4())})
    data=''.join(p.read_text(encoding='utf-8') for p in env['root'].glob('*.jsonl'))
    for value in ['SVC_EXTERNAL','REFRESH','test-only','010-0000','합성 소개 자료','external_session']:
        assert value not in data
    assert 'requested_file_not_approved' in data


def test_downstream_unauthorized_preserves_member_session(env):
    login(env)
    verify=env['id'].verify
    def reject_service(token,*args,**kwargs):
        if token=='SVC_EXTERNAL':
            deny('AUTH_REQUIRED','invalid_token')
        return verify(token,*args,**kwargs)
    env['id'].verify=reject_service
    response=env['e'].get('/api/me/deliveries')
    assert response.status_code==500
    assert response.json()['error']['code']=='INTERNAL_ERROR'
    assert env['e'].get('/api/me').status_code==200


def test_invalid_files_and_unknown_fields(env):
    assert upload(env,json.dumps({**PROFILE,'contact':'010'}).encode())['scan_status']=='blocked'
    result=env['t'].post('/api/transfer-files',files={'file':('too-big',b'x'*2097153)},headers=headers())
    assert result.status_code==422
    assert env['e'].get('/api/me').status_code==401
    assert env['e'].get('/api/admin/member-exports').status_code==404


def test_body_limit_with_no_content_length(env):
    result=env['e'].post('/auth/logout',content=iter([b'x'*1048576]*4))
    assert result.status_code==422


def test_all_api_routes_have_declared_policy(env):
    from privacygate.web import route_policies
    for app in (env['external'],env['transfer']):
        for (method,path),policies in route_policies(app).items():
            if path.startswith('/api'):
                assert policies,(method,path)


@pytest.mark.parametrize('field',['last_seen_at','absolute_expires_at'])
def test_session_expiry(env,field):
    login(env)
    with env['external'].state.sessions.db() as db:
        db.execute(f'UPDATE sessions SET {field}=0')
    assert env['e'].get('/api/me').status_code==401


def test_wrong_origin_and_oversized_metadata(env):
    me=login(env)
    assert env['e'].post('/auth/logout',headers={'Origin':'https://evil.test','X-CSRF-Token':me['csrf_token']},json={}).status_code==403


def test_reject_and_revoke_workflow(env):
    r,_=request(env)
    assert env['t'].post(f'/api/export-requests/{r["request_id"]}/reject',json={},headers=headers('APPROVER')).status_code==200
    assert env['t'].post(f'/api/export-requests/{r["request_id"]}/approve',json={},headers=headers('APPROVER')).status_code==409
    d,_=approved(env)
    assert env['t'].post(f'/api/approvals/{d["approval_id"]}/revoke',json={},headers=headers('APPROVER')).status_code==200
    login(env)
    assert env['e'].get(f'/api/me/deliveries/{d["delivery_id"]}/download').status_code==403


@pytest.mark.parametrize('stage',['request','approve','download'])
def test_disclosure_uuid_mismatch_is_rejected_at_each_stage(env,stage):
    if stage=='request':
        f=upload(env)
        env['state']['disclosure_member_id']=str(uuid4())
        result=env['t'].post('/api/export-requests',headers=headers(),json={
            'file_id':f['file_id'],'member_id':M,'recipient_id':B,'consent_id':CONSENT,
            'purpose':'introduction','expires_at':iso(now()+timedelta(days=1))})
    elif stage=='approve':
        r,_=request(env);env['state']['disclosure_member_id']=str(uuid4())
        result=env['t'].post(f'/api/export-requests/{r["request_id"]}/approve',headers=headers('APPROVER'),json={})
    else:
        d,_=approved(env);env['state']['disclosure_member_id']=str(uuid4())
        result=env['t'].get(f'/api/internal/deliveries/{d["delivery_id"]}/download',headers={**headers('SVC_EXTERNAL'),'X-User-Token':'MEMBER'})
    assert result.status_code==403 and '합성 소개 자료' not in result.text


def test_display_name_is_not_subject_identity(env):
    profile={**PROFILE,'display_name':'다른 표시 이름'}
    f=upload(env,json.dumps(profile,ensure_ascii=False).encode())
    r=env['t'].post('/api/export-requests',headers=headers(),json={
        'file_id':f['file_id'],'member_id':M,'recipient_id':B,'consent_id':CONSENT,
        'purpose':'introduction','expires_at':iso(now()+timedelta(days=1))})
    assert r.status_code==201


def test_same_name_with_wrong_uuid_cannot_be_released(env):
    f=upload(env,json.dumps({**PROFILE,'member_id':str(uuid4())}).encode())
    r=env['t'].post('/api/export-requests',headers=headers(),json={
        'file_id':f['file_id'],'member_id':M,'recipient_id':B,'consent_id':CONSENT,
        'purpose':'introduction','expires_at':iso(now()+timedelta(days=1))})
    assert r.status_code==403


@pytest.mark.parametrize('bad',[{'schema_version':True},{'member_id':'not-a-uuid'},{'extra':'bad'}])
def test_profile_v2_rejects_malformed_files(env,bad):
    f=upload(env,json.dumps({**PROFILE,**bad}).encode())
    assert f['scan_status']=='blocked' and f['export_banned']


def test_legacy_name_only_file_is_not_automatically_trusted(env):
    f=upload(env,json.dumps({'member_pseudo_id':'M-001','age_band':'30대','job_group':'연구','introduction':'가상 자료'}).encode())
    assert f['scan_status']=='blocked' and f['export_banned']


# ---------- 사후 대응: 공격 ② 보안 사건 알림 · 총관리자 자료 회수 ----------
def test_mismatch_is_reported_to_internal_with_member_token(env):
    d,_=approved(env); private=upload(env,b'name,phone\nSYNTHETIC,010-0000-0000')
    login(env)
    r=env['e'].get(f'/api/me/deliveries/{d["delivery_id"]}/download',params={'file_id':private['file_id']})
    assert r.status_code==403 and r.json()['error']['code']=='APPROVAL_MISMATCH'
    events=env['state']['security_events']
    assert events==[{'kind':'approval_mismatch','reason':'requested_file_not_approved','blocked':True,
                     'delivery_id':d['delivery_id'],'user_token':'MEMBER'}]
    log=(env['root']/'transfer.jsonl').read_text()
    assert 'security.report' in log and 'SYNTHETIC' not in log


def test_baseline_swap_is_reported_as_not_blocked(env):
    d,_=approved(env); private=upload(env,b'name,phone\nSYNTHETIC,010-0000-0000')
    env['cfg']['mode']='baseline';login(env)
    r=env['e'].get(f'/api/me/deliveries/{d["delivery_id"]}/download',params={'file_id':private['file_id']})
    assert r.status_code==200
    assert [e['blocked'] for e in env['state']['security_events']]==[False]


def test_normal_download_and_integrity_faults_are_not_reported(env):
    d,f=approved(env);login(env)
    assert env['e'].get(f'/api/me/deliveries/{d["delivery_id"]}/download').status_code==200
    (Path(env['cfg']['verified'])/f['file_id']).write_bytes(b'changed')       # 시스템 쪽 무결성 문제
    assert env['e'].get(f'/api/me/deliveries/{d["delivery_id"]}/download').status_code==403
    assert 'security_events' not in env['state']


def test_report_failure_keeps_block(env):
    d,_=approved(env); private=upload(env,b'SYNTHETIC,private')
    env['state']['security_down']=True;login(env)
    r=env['e'].get(f'/api/me/deliveries/{d["delivery_id"]}/download',params={'file_id':private['file_id']})
    assert r.status_code==403 and b'SYNTHETIC' not in r.content
    assert 'security_report_failed' in (env['root']/'transfer.jsonl').read_text()


def test_locked_or_suspended_member_is_blocked(env):
    env['cfg']['member_status_check']=True
    d,_=approved(env);login(env)
    for status in ('locked','suspended'):
        env['state']['member_status']={'status':status,'message':'보안 점검 중인 계정입니다.'}
        r=env['e'].get(f'/api/me/deliveries/{d["delivery_id"]}/download')
        assert r.status_code==403 and '보안 점검' in r.json()['error']['message']
        assert env['e'].get('/api/me/status').json()['status']==status


@pytest.mark.parametrize('token',['COUNSELOR','APPROVER','MEMBER'])
def test_revoke_deliveries_is_admin_only(env,token):
    approved(env)
    r=env['t'].post(f'/api/security/recipients/{B}/revoke-deliveries',headers=headers(token),json={})
    assert r.status_code in (401,403)


def test_admin_revokes_all_deliveries_of_member(env):
    d,_=approved(env);d2,_=approved(env)
    r=env['t'].post(f'/api/security/recipients/{B}/revoke-deliveries',headers=headers('ADMIN'),json={})
    assert r.status_code==200 and r.json()['revoked']==2
    login(env)
    assert {i['request_status'] for i in env['e'].get('/api/me/deliveries').json()['items']}=={'revoked'}
    assert env['e'].get(f'/api/me/deliveries/{d["delivery_id"]}/download').status_code==403
    again=env['t'].post(f'/api/security/recipients/{B}/revoke-deliveries',headers=headers('ADMIN'),json={})
    assert again.json()['revoked']==0


# ---------- 안전한 반출: 일회용 다운로드 토큰 · 다운로드 횟수 제한 · 공통 감사 형식 ----------
def csrf(env):
    return {'X-CSRF-Token':env['e'].get('/api/me').json()['csrf_token'],'Origin':'https://testserver'}


def token_for(env,delivery_id):
    r=env['e'].post(f'/api/me/deliveries/{delivery_id}/download-token',headers=csrf(env))
    assert r.status_code==201,r.text
    return r.json()


def secure(env,limit=3):
    env['cfg']['download_tokens']=True;env['cfg']['max_downloads']=limit


def test_download_requires_one_time_token(env):
    secure(env);d,f=approved(env);login(env)
    url=f'/api/me/deliveries/{d["delivery_id"]}/download'
    r=env['e'].get(url)
    assert r.status_code==403 and r.json()['error']['code']=='ACCESS_DENIED'
    t=token_for(env,d['delivery_id'])
    assert t['required'] and t['expires_in']==60 and len(t['token'])>=40
    ok=env['e'].get(url,headers={'X-Download-Token':t['token']})
    assert ok.status_code==200 and json.loads(ok.content)['member_id']==M
    again=env['e'].get(url,headers={'X-Download-Token':t['token']})          # 재사용 차단
    assert again.status_code==403 and '이미 사용한' in again.json()['error']['message']
    log=(env['root']/'transfer.jsonl').read_text()
    assert 'download_token_required' in log and 'download_token_used' in log and t['token'] not in log


def test_token_is_bound_to_delivery_and_expires(env):
    secure(env);d1,_=approved(env);d2,_=approved(env);login(env)
    t=token_for(env,d1['delivery_id'])
    other=env['e'].get(f'/api/me/deliveries/{d2["delivery_id"]}/download',headers={'X-Download-Token':t['token']})
    assert other.status_code==403
    env['cfg']['download_token_ttl']=-1
    stale=token_for(env,d1['delivery_id'])
    r=env['e'].get(f'/api/me/deliveries/{d1["delivery_id"]}/download',headers={'X-Download-Token':stale['token']})
    assert r.status_code==403 and '만료' in r.json()['error']['message']


def test_new_token_replaces_old_and_revoke_drops_tokens(env):
    secure(env);d,_=approved(env);login(env)
    url=f'/api/me/deliveries/{d["delivery_id"]}/download'
    first=token_for(env,d['delivery_id']);second=token_for(env,d['delivery_id'])
    assert env['e'].get(url,headers={'X-Download-Token':first['token']}).status_code==403
    pending=token_for(env,d['delivery_id'])
    env['t'].post(f'/api/approvals/{d["approval_id"]}/revoke',headers=headers('APPROVER'),json={})
    assert env['e'].get(url,headers={'X-Download-Token':pending['token']}).status_code==403
    assert env['e'].post(f'/api/me/deliveries/{d["delivery_id"]}/download-token',headers=csrf(env)).status_code==403   # 회수 후 발급도 거부


def test_download_limit(env):
    secure(env,limit=2);d,_=approved(env);login(env)
    url=f'/api/me/deliveries/{d["delivery_id"]}/download'
    for _ in range(2):
        assert env['e'].get(url,headers={'X-Download-Token':token_for(env,d['delivery_id'])['token']}).status_code==200
    item=env['e'].get('/api/me/deliveries').json()['items'][0]
    assert item['download_count']==2 and item['download_limit']==2
    r=env['e'].post(f'/api/me/deliveries/{d["delivery_id"]}/download-token',headers=csrf(env))
    assert r.status_code==403 and '최대 2회' in r.json()['error']['message']
    assert 'download_limit_reached' in (env['root']/'transfer.jsonl').read_text()


def test_attack2_still_reported_as_mismatch_with_tokens(env):
    secure(env);d,_=approved(env);private=upload(env,b'name,phone\nSYNTHETIC,010-0000-0000');login(env)
    r=env['e'].get(f'/api/me/deliveries/{d["delivery_id"]}/download',params={'file_id':private['file_id']})
    assert r.status_code==403 and r.json()['error']['code']=='APPROVAL_MISMATCH'
    assert env['state']['security_events'][0]['reason']=='requested_file_not_approved'


def test_baseline_skips_token_and_limit(env):
    secure(env,limit=1);d,_=approved(env);env['cfg']['mode']='baseline';login(env)
    url=f'/api/me/deliveries/{d["delivery_id"]}/download'
    assert env['e'].get(url).status_code==200 and env['e'].get(url).status_code==200
    assert env['e'].post(f'/api/me/deliveries/{d["delivery_id"]}/download-token',headers=csrf(env)).json()['required'] is False


def test_audit_lines_have_standard_fields_and_chain(env):
    from privacygate.audit import POLICY_VERSION, verify_chain
    secure(env);d,f=approved(env);login(env)
    env['e'].get(f'/api/me/deliveries/{d["delivery_id"]}/download',headers={'X-Download-Token':token_for(env,d['delivery_id'])['token']})
    lines=(env['root']/'transfer.jsonl').read_text().splitlines()
    assert verify_chain(lines)==[]
    dl=[json.loads(l) for l in lines if '"delivery.download"' in l][-1]
    assert dl['policy_version']==POLICY_VERSION and dl['owner_id']==M and dl['recipient_id']==B
    assert dl['approval_id']==d['approval_id'] and dl['file_id']==f['file_id'] and dl['file_hash']==f['file_hash']
    req=[json.loads(l) for l in lines if '"release.approved"' in l][-1]
    assert req['consent_id'] and req['file_hash']==f['file_hash']
