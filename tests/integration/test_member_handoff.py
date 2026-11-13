"""New internal contract alongside unchanged paid matching and download routes."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_services import env, login, headers, approved


def enable_status(env):
    env['cfg']['member_status_check']=True
    env['ecfg']['member_status_check']=True


@pytest.mark.parametrize('kind',['education','employment'])
def test_evidence_kind_survives_both_relays(env,kind):
    user=login(env)
    response=env['e'].post('/api/me/evidence-files',data={'kind':kind},
        files={'file':('proof.pdf',b'%PDF-1.4\nsynthetic')},headers={'Origin':'https://testserver','X-CSRF-Token':user['csrf_token']})
    assert response.status_code==201
    forwarded=[r for r in env['state']['requests'] if r.url.path=='/api/evidence-files'][-1]
    assert b'name="kind"' in forwarded.content and kind.encode() in forwarded.content
    assert forwarded.headers['X-User-Token']=='MEMBER'
    assert len(forwarded.headers['X-Trace-Id'])==32


def test_unknown_evidence_kind_is_rejected_before_forwarding(env):
    user=login(env)
    count=len(env['state']['requests'])
    response=env['e'].post('/api/me/evidence-files',data={'kind':'identity'},
        files={'file':('proof.pdf',b'%PDF-test')},headers={'Origin':'https://testserver','X-CSRF-Token':user['csrf_token']})
    assert response.status_code==422 and len(env['state']['requests'])==count


def test_old_evidence_clients_default_to_education(env):
    user=login(env)
    assert env['e'].post('/api/me/evidence-files',files={'file':('proof.pdf',b'%PDF-test')},
        headers={'Origin':'https://testserver','X-CSRF-Token':user['csrf_token']}).status_code==201
    assert b'education' in env['state']['requests'][-1].content


def test_withdrawal_requires_session_csrf_and_bounded_reason(env):
    enable_status(env)
    assert env['e'].get('/api/me/status').status_code==401
    assert env['t'].get('/api/internal/member-status',headers=headers('MEMBER')).status_code==401
    user=login(env)
    assert env['e'].post('/api/me/withdrawal',json={}).status_code==403
    csrf={'Origin':'https://testserver','X-CSRF-Token':user['csrf_token']}
    assert env['e'].post('/api/me/withdrawal',json={'reason':'x'*201},headers=csrf).status_code==422
    assert env['e'].post('/api/me/withdrawal',json={'user_id':'someone'},headers=csrf).status_code==422


def test_self_withdrawal_notice_and_cancel_restore_service(env):
    enable_status(env)
    user=login(env);csrf={'Origin':'https://testserver','X-CSRF-Token':user['csrf_token']}
    secret_reason='PRIVATE-WITHDRAWAL-REASON'
    assert env['e'].post('/api/me/withdrawal',json={'reason':secret_reason},headers=csrf).status_code==200
    assert env['state']['withdrawal_body']=={'reason':secret_reason}
    status=env['e'].get('/api/me/status').json()
    assert status['status']=='withdraw_pending' and status['can_cancel'] is True
    assert env['e'].get('/api/me/deliveries').status_code==403
    assert env['e'].get('/api/me/membership').status_code==403
    assert env['e'].post('/api/me/withdrawal/cancel',json={},headers=csrf).status_code==200
    assert env['e'].get('/api/me/deliveries').status_code==200
    assert secret_reason not in (env['root']/'transfer.jsonl').read_text()
    assert secret_reason not in (env['root']/'external.jsonl').read_text()


@pytest.mark.parametrize('status',['withdraw_pending','withdrawn','not_registered'])
def test_inactive_member_cannot_use_old_endpoints(env,status):
    delivery,_=approved(env)
    enable_status(env);user=login(env)
    env['state']['member_status']={'status':status,'by':'staff','can_cancel':True,'message':'계정 안내'}
    result=env['e'].get('/api/me/status').json()
    assert result['status']==status and result['can_cancel'] is False
    for path in ('/api/me/deliveries','/api/me/evidence-files','/api/me/consents','/api/me/matches','/api/me/membership',
                 f'/api/me/deliveries/{delivery["delivery_id"]}/download'):
        assert env['e'].get(path).status_code==403,path
    assert env['e'].post('/api/me/withdrawal/cancel',json={},headers={'Origin':'https://testserver','X-CSRF-Token':user['csrf_token']}).status_code==403


def test_status_failure_does_not_allow_member_or_destroy_browser_session(env):
    enable_status(env);login(env)
    env['state']['status_unavailable']=True
    assert env['e'].get('/api/me/status').status_code==500
    assert env['e'].get('/api/me/deliveries').status_code==500
    assert env['e'].get('/api/me').status_code==200


def test_existing_mode_does_not_require_new_internal_status_api(env):
    env['state']['status_unavailable']=True
    me=login(env)
    assert me['member_status_check'] is False
    assert env['e'].get('/api/me/deliveries').status_code==200
    assert env['e'].post('/api/me/withdrawal',json={},
        headers={'Origin':'https://testserver','X-CSRF-Token':me['csrf_token']}).status_code==409


def test_status_does_not_echo_unexpected_internal_fields(env):
    login(env)
    env['state']['member_status']={'status':'active','member_code':'A-003','name':'PRIVATE-NAME','can_cancel':True}
    response=env['e'].get('/api/me/status')
    assert response.status_code==200
    assert 'member_code' not in response.json() and 'PRIVATE-NAME' not in response.text
    assert response.json()['can_cancel'] is False


@pytest.mark.parametrize('base_value',[None,'owner-provided-secret'])
def test_initialization_preserves_keys_and_never_edits_base_env(tmp_path,base_value):
    root=tmp_path/'external';scripts=root/'scripts';scripts.mkdir(parents=True)
    script=scripts/'initialize.py'
    script.write_bytes((Path(__file__).parents[2]/'scripts/initialize.py').read_bytes())
    base=tmp_path/'privacygate/.env';base.parent.mkdir()
    original='KC_TEST_PASSWORD=local-test\n'+('INTERNAL_ADMIN_CLIENT_SECRET='+base_value+'\n' if base_value else '')
    base.write_text(original)
    environment=dict(os.environ);environment.pop('INTERNAL_ADMIN_CLIENT_SECRET',None)
    first=subprocess.run([sys.executable,str(script)],capture_output=True,text=True,env=environment,check=True)
    key=(root/'.local/secrets/session.key').read_bytes()
    private=(root/'.local/internal-admin.env').read_text()
    second=subprocess.run([sys.executable,str(script)],capture_output=True,text=True,env=environment,check=True)
    assert (root/'.local/secrets/session.key').read_bytes()==key
    assert (root/'.local/internal-admin.env').read_text()==private
    assert base.read_text()==original
    assert key.decode() not in first.stdout+second.stdout
    if base_value:assert base_value not in private+first.stdout+second.stdout
    else:assert private.partition('=')[2].strip() not in first.stdout+second.stdout
