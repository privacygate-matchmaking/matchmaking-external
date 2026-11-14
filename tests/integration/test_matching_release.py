import json
from datetime import timedelta
from uuid import uuid4

import pytest

from ieum.core import now
from ieum.matching import Order, Proposal, Sharing, synthetic_directory
from test_services import env, headers, B, C
from test_matching import schema


@pytest.fixture
def release_env(env):
    schema(env['root']/'transfer.sqlite3')
    service=env['transfer'].state.matching
    service.test_payments=True
    directory=synthetic_directory(B)
    service.remember(directory)
    order=service.order(B,Order(plan_code='ieum_6m_5',idempotency_key=uuid4()))
    service.confirm_test_order(B,order['order_id'])
    pair=service.propose(B,directory,Proposal(recipient_member_id=directory.items[0].member_id,idempotency_key=uuid4()))
    service.decide(C,pair['match_id'],'accept')
    grant=service.share(B,pair['match_id'],Sharing(scopes=['education'],expires_at=now()+timedelta(hours=1)))
    env['state'].update(release_assigned=True,staff_scope_allowed=True)
    return env,service,pair,grant


def prepare(fixture,scopes=None):
    env,_,_,grant=fixture
    return env['t'].post('/api/matching/releases',headers=headers(),json={'grant_id':grant['grant_id'],'scopes':scopes or ['education']})


def approved(fixture):
    env,_,_,_=fixture
    response=prepare(fixture);assert response.status_code==201,response.text
    record=response.json()
    approval=env['t'].post(f'/api/export-requests/{record["request_id"]}/approve',headers=headers('APPROVER'),json={})
    assert approval.status_code==200,approval.text
    return record,approval.json()


def download(fixture,delivery):
    return fixture[0]['t'].get(f'/api/internal/deliveries/{delivery["delivery_id"]}/download',headers={
        'Authorization':'Bearer SVC_EXTERNAL','X-User-Token':'OTHER'})


def test_matching_release_is_scoped_separate_approval_and_download(release_env):
    env,_,_,grant=release_env
    response=prepare(release_env);assert response.status_code==201,response.text
    record=response.json()
    detail=env['t'].get(f'/api/export-requests/{record["request_id"]}',headers=headers('APPROVER')).json()
    assert detail['consent_id']==grant['grant_id'] and detail['purpose']=='matching_introduction'
    assert detail['preview']['schema_version']==3
    assert detail['preview']['scopes']==['education']
    assert 'display_name' not in detail['preview'] and 'contact' not in detail['preview']['scopes']
    recipient={'Authorization':'Bearer SVC_EXTERNAL','X-User-Token':'OTHER'}
    assert env['t'].post('/api/internal/delivery-queries',json={},headers=recipient).json()['items']==[]
    result=env['t'].post(f'/api/export-requests/{record["request_id"]}/approve',headers=headers('APPROVER'),json={})
    assert result.status_code==200,result.text
    received=download(release_env,result.json());assert received.status_code==200,received.text
    assert received.json()==detail['preview']


@pytest.mark.parametrize('fault',['unassigned','unsupported','not_granted','missing_grant','review'])
def test_new_release_rejects_before_file_creation(release_env,fault):
    env,_,_,grant=release_env
    body={'grant_id':grant['grant_id'],'scopes':['education']}
    if fault=='unassigned':env['state']['release_assigned']=False
    if fault=='unsupported':body['scopes']=['contact']
    if fault=='not_granted':body['scopes']=['employment']
    if fault=='missing_grant':body['grant_id']=str(uuid4())
    if fault=='review':env['state']['release_review_fail']=True
    r=env['t'].post('/api/matching/releases',headers=headers(),json=body)
    assert r.status_code in (403,409,422),r.text
    assert list((env['root']/'verified').iterdir())==[]


@pytest.mark.parametrize('change',['revoke','replace','end','expired','review','assignment'])
def test_consent_change_blocks_approved_download(release_env,change):
    env,service,pair,grant=release_env
    _,delivery=approved(release_env)
    if change=='revoke':service.revoke(B,pair['match_id'])
    if change=='replace':service.share(B,pair['match_id'],Sharing(scopes=['profile'],expires_at=now()+timedelta(hours=1)))
    if change=='end':service.decide(B,pair['match_id'],'end')
    if change=='expired':
        with env['transfer'].state.database.connection() as db:db.execute('UPDATE matching_grants SET expires_at=%s WHERE grant_id=%s',(now()-timedelta(seconds=1),grant['grant_id']))
    if change=='review':env['state']['release_value']='CHANGED-REVIEW'
    if change=='assignment':env['state']['release_assigned']=False
    r=download(release_env,delivery);assert r.status_code in (403,409),r.text
    assert 'REVIEWED-TEXT' not in r.text


def test_revoke_before_approval_never_creates_delivery(release_env):
    env,service,pair,_=release_env
    record=prepare(release_env).json();service.revoke(B,pair['match_id'])
    r=env['t'].post(f'/api/export-requests/{record["request_id"]}/approve',headers=headers('APPROVER'),json={})
    assert r.status_code==403,r.text
    with env['transfer'].state.database.connection() as db:
        assert db.execute('SELECT count(*) AS n FROM deliveries').fetchone()['n']==0


def test_staff_overview_includes_pending_and_consent_metadata(release_env):
    env,service,pair,grant=release_env
    response=env['t'].get('/api/matching/overview',headers=headers())
    assert response.status_code==200,response.text
    item=response.json()['items'][0]
    assert item['grants'][0]['grant_id']==grant['grant_id'] and item['grants'][0]['can_prepare']
    assert 'REVIEWED-TEXT' not in response.text
    service.decide(B,pair['match_id'],'end')
    assert env['t'].get('/api/matching/overview',headers=headers()).json()['items'][0]['status']=='ended'
    env['state']['staff_scope_allowed']=False
    assert env['t'].get('/api/matching/overview',headers=headers()).json()['items']==[]


def test_member_and_approver_cannot_prepare_or_list_staff_data(release_env):
    env,_,_,grant=release_env
    for token in ('MEMBER','APPROVER'):
        assert env['t'].get('/api/matching/overview',headers=headers(token)).status_code in (401,403)
        assert env['t'].post('/api/matching/releases',headers=headers(token),json={'grant_id':grant['grant_id'],'scopes':['education']}).status_code in (401,403)
