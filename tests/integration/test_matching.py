import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from privacygate.errors import GateError

from ieum.core import Database, iso, now
from ieum.matching import Matching, Order, Proposal, Sharing, Person, Directory, synthetic_directory
from test_services import env, login, B, C

D='22222222-2222-4222-8222-222222222203'
E='22222222-2222-4222-8222-222222222204'


def schema(path):
    text=(Path(__file__).resolve().parents[2]/'infra/migrations/001_matching.sql').read_text(encoding='utf-8')
    text='\n'.join(line for line in text.splitlines() if not line.startswith('GRANT '))
    with sqlite3.connect(path) as db:db.executescript(text)
    meetings=(Path(__file__).resolve().parents[2]/'infra/migrations/002_meetings.sql').read_text(encoding='utf-8')
    meetings='\n'.join(line for line in meetings.splitlines() if not line.startswith('GRANT '))
    with sqlite3.connect(path) as db:db.executescript(meetings)


@pytest.fixture
def service(tmp_path):
    path=tmp_path/'matching.db';schema(path)
    clock=[datetime(2026,11,12,3,tzinfo=timezone.utc)]
    s=Matching(Database(str(path),True),clock=lambda:clock[0],test_payments=True)
    for u in (B,C,D,E):s.remember(synthetic_directory(u))
    s.test_clock=clock
    return s


def purchase(s,user=B,key=None):
    order=s.order(user,Order(plan_code='ieum_6m_5',idempotency_key=key or uuid4()))
    return s.confirm_test_order(user,order['order_id'])


def propose(s,user=B,peer=C,key=None):
    directory=synthetic_directory(user)
    target=next(x for x in directory.items if str(x.user_id)==peer)
    return s.propose(user,directory,Proposal(recipient_member_id=target.member_id,idempotency_key=key or uuid4()))


def reason(expected,fn):
    with pytest.raises(GateError) as e:fn()
    assert e.value.reason==expected


def test_free_member_cannot_send(service):
    reason('paid_membership_required',lambda:propose(service))


def test_price_is_server_fixed_and_extra_fields_rejected(service):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):Order(plan_code='ieum_6m_5',idempotency_key=uuid4(),amount_won=1)
    with pytest.raises(ValidationError):Order(plan_code='free',idempotency_key=uuid4())


def test_order_and_confirmation_are_idempotent(service):
    key=uuid4();a=service.order(B,Order(plan_code='ieum_6m_5',idempotency_key=key))
    b=service.order(B,Order(plan_code='ieum_6m_5',idempotency_key=key));assert a['order_id']==b['order_id']
    service.confirm_test_order(B,a['order_id']);service.confirm_test_order(B,a['order_id'])
    assert service.membership(B)['wallet']['available']==5
    assert service.membership(B)['plan']['amount_won']==3300000


def test_payment_is_own_only_and_default_disabled(service):
    o=service.order(B,Order(plan_code='ieum_6m_5',idempotency_key=uuid4()))
    reason('order_missing',lambda:service.confirm_test_order(C,o['order_id']))
    service.test_payments=False
    reason('test_payment_disabled',lambda:service.confirm_test_order(B,o['order_id']))


def test_order_expiry_and_membership_calendar_months(service):
    o=service.order(B,Order(plan_code='ieum_6m_5',idempotency_key=uuid4()))
    service.test_clock[0]+=timedelta(minutes=31)
    reason('order_expired',lambda:service.confirm_test_order(B,o['order_id']))
    service.test_clock[0]=datetime(2026,8,31,3,tzinfo=timezone.utc);purchase(service)
    assert service.membership(B)['wallet']['expires_at'].startswith('2027-02-28')


def test_match_reserve_accept_and_end_do_not_refund(service):
    purchase(service);m=propose(service)
    assert service.membership(B)['wallet']['reserved']==1
    service.decide(C,m['match_id'],'accept');service.decide(C,m['match_id'],'accept')
    assert service.membership(B)['wallet']['used']==1
    service.decide(B,m['match_id'],'end');service.decide(C,m['match_id'],'end')
    assert service.membership(B)['wallet']['available']==4


@pytest.mark.parametrize('action,actor',[('reject',C),('cancel',B)])
def test_reject_and_cancel_release_once(service,action,actor):
    purchase(service);m=propose(service)
    service.decide(actor,m['match_id'],action);service.decide(actor,m['match_id'],action)
    assert service.membership(B)['wallet']['available']==5
    with service.database.connection() as db:
        assert db.execute("SELECT count(*) AS n FROM matching_credit_events WHERE event_type='release'").fetchone()['n']==1


def test_timeout_releases_once(service):
    purchase(service);m=propose(service);service.test_clock[0]+=timedelta(hours=72)
    assert service.membership(B)['wallet']['available']==5
    assert service.list(B)['items'][0]['status']=='expired'
    reason('match_transition_invalid',lambda:service.decide(C,m['match_id'],'accept'))


def test_expired_membership_cannot_send(service):
    purchase(service);service.test_clock[0]+=timedelta(days=190)
    reason('paid_membership_required',lambda:propose(service))


def test_ownership_and_direction(service):
    purchase(service);m=propose(service)
    reason('match_missing',lambda:service.decide(D,m['match_id'],'accept'))
    reason('recipient_only',lambda:service.decide(B,m['match_id'],'accept'))
    reason('sender_only',lambda:service.decide(C,m['match_id'],'cancel'))


def test_duplicate_and_rejection_cooldown(service):
    purchase(service);m=propose(service)
    reason('duplicate_pair',lambda:propose(service))
    service.decide(C,m['match_id'],'reject')
    reason('rejection_cooldown',lambda:propose(service))
    service.test_clock[0]+=timedelta(days=30)
    assert propose(service)['status']=='pending'


def test_daily_limit_counts_cancelled_requests(service):
    purchase(service)
    for peer in (C,D,E):
        m=propose(service,peer=peer);service.decide(B,m['match_id'],'cancel')
    reason('daily_limit',lambda:propose(service))
    service.test_clock[0]+=timedelta(days=1)
    assert propose(service)['status']=='pending'


def test_pending_limit_across_korean_day_boundary(service):
    purchase(service)
    for peer in (C,D,E):propose(service,peer=peer)
    service.test_clock[0]+=timedelta(days=1)
    d=synthetic_directory(B)
    extra=Person(member_id=uuid4(),user_id=uuid4(),pseudo_id='M-F01',age_band='30대',job_group='교육',introduction='합성',eligible=True)
    d.items.append(extra);service.remember(d)
    reason('pending_limit',lambda:service.propose(B,d,Proposal(recipient_member_id=extra.member_id,idempotency_key=uuid4())))


def test_concurrent_duplicate_requests_reserve_only_once(service):
    purchase(service)
    def send(_):
        try:return propose(service)['match_id']
        except GateError:return None
    with ThreadPoolExecutor(max_workers=5) as pool:results=list(pool.map(send,range(5)))
    assert sum(x is not None for x in results)==1
    assert service.membership(B)['wallet']['reserved']==1


def test_idempotent_request_and_credit_exhaustion(service):
    purchase(service);key=uuid4();first=propose(service,key=key)
    assert propose(service,key=key)['match_id']==first['match_id']
    reason('idempotency_conflict',lambda:propose(service,peer=D,key=key))
    service.decide(C,first['match_id'],'accept');service.decide(B,first['match_id'],'end')
    for _ in range(4):
        service.test_clock[0]+=timedelta(days=1)
        m=propose(service);service.decide(C,m['match_id'],'accept');service.decide(B,m['match_id'],'end')
    assert service.membership(B)['wallet']['available']==0
    reason('credits_exhausted',lambda:propose(service,peer=D))


def test_matching_is_not_implicit_consent_and_grants_are_directional(service):
    purchase(service);m=propose(service);service.decide(C,m['match_id'],'accept')
    reason('peer_consent_required',lambda:service.disclosure(B,m['match_id']))
    service.share(C,m['match_id'],Sharing(scopes=['employment'],expires_at=service.clock()+timedelta(days=1)))
    assert service.disclosure(B,m['match_id'])['scopes']==['employment']
    reason('peer_consent_required',lambda:service.disclosure(C,m['match_id']))
    reason('match_missing',lambda:service.share(D,m['match_id'],Sharing(scopes=['contact'],expires_at=service.clock()+timedelta(days=1))))


@pytest.mark.parametrize('change',['revoke','expiry','end'])
def test_grant_revoke_expiry_end_block_next_read(service,change):
    purchase(service);m=propose(service);service.decide(C,m['match_id'],'accept')
    service.share(C,m['match_id'],Sharing(scopes=['profile'],expires_at=service.clock()+timedelta(hours=1)))
    if change=='revoke':service.revoke(C,m['match_id'])
    if change=='expiry':service.test_clock[0]+=timedelta(hours=1)
    if change=='end':service.decide(B,m['match_id'],'end')
    with pytest.raises(GateError):service.disclosure(B,m['match_id'])


@pytest.fixture
def lab(env):
    env['state']['matching']=True
    schema(env['root']/'transfer.sqlite3')
    env['transfer'].state.matching.test_payments=True
    return env


def test_real_bff_matching_wallet_flow_csrf_and_field_filter(lab):
    me=login(lab);e=lab['e'];h={'X-CSRF-Token':me['csrf_token'],'Origin':'https://testserver'}
    candidates=e.get('/api/me/candidates').json();peer=candidates['items'][0]
    bad=e.post('/api/me/orders',json={'plan_code':'ieum_6m_5','idempotency_key':str(uuid4())})
    assert bad.status_code==403
    o=e.post('/api/me/orders',headers=h,json={'plan_code':'ieum_6m_5','idempotency_key':str(uuid4())}).json()
    assert e.post(f'/api/me/orders/{o["order_id"]}/test-confirm',headers=h,json={}).status_code==200
    m=e.post('/api/me/matches',headers=h,json={'recipient_member_id':peer['member_id'],'idempotency_key':str(uuid4())})
    assert m.status_code==201,m.text
    mid=m.json()['match_id'];s=lab['transfer'].state.matching
    s.decide(C,mid,'accept')
    s.share(C,mid,Sharing(scopes=['employment'],expires_at=now()+timedelta(days=1)))
    result=e.get(f'/api/me/matches/{mid}/profile')
    assert result.status_code==200,result.text
    assert result.json()['fields']==[] and result.json()['approval_required'] is True     # 동의만으로는 값이 보이지 않음
    assert result.json()['consented_scopes']==['employment']
    assert 'SYNTHETIC-CONTACT' not in result.text and '합성 연구직' not in result.text
    assert e.get('/api/me').status_code==200


def test_unimplemented_directory_stays_fail_closed(env):
    login(env)
    result=env['e'].get('/api/me/candidates')
    assert result.status_code==500
    assert '아직 구현' in result.json()['error']['message']
    assert env['e'].get('/api/me').status_code==200


def test_profile_shows_no_values_before_approval_and_tracks_contact_conditions(lab):
    from uuid import uuid4 as new_id
    me=login(lab);e=lab['e'];h={'X-CSRF-Token':me['csrf_token'],'Origin':'https://testserver'}
    peer=e.get('/api/me/candidates').json()['items'][0]
    o=e.post('/api/me/orders',headers=h,json={'plan_code':'ieum_6m_5','idempotency_key':str(uuid4())}).json()
    e.post(f'/api/me/orders/{o["order_id"]}/test-confirm',headers=h,json={})
    mid=e.post('/api/me/matches',headers=h,json={'recipient_member_id':peer['member_id'],'idempotency_key':str(uuid4())}).json()['match_id']
    s=lab['transfer'].state.matching;s.decide(C,mid,'accept')
    s.share(C,mid,Sharing(scopes=['employment','contact','name'],expires_at=now()+timedelta(days=1)))
    s.share(B,mid,Sharing(scopes=['contact'],expires_at=now()+timedelta(days=1)))
    view=e.get(f'/api/me/matches/{mid}/profile').json()
    assert view['fields']==[] and view['contact_pending'] is True and 'SYNTHETIC-CONTACT' not in str(view)
    with lab['transfer'].state.database.connection() as db:
        db.execute("""INSERT INTO matching_meeting_proposals (proposal_id,round_id,match_id,proposed_by,starts_at,venue,address,status,created_at,confirmed_at,confirmed_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'confirmed',%s,%s,%s)""",(str(new_id()),str(new_id()),mid,B,now()+timedelta(days=2),'카페','서울',now(),now(),'staff'))
    view=e.get(f'/api/me/matches/{mid}/profile').json()
    assert view['fields']==[] and view['contact_pending'] is False                     # 조건 충족이어도 승인 전엔 값 없음
