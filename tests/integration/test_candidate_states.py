"""소개 후보 분류: 진행 중·성사된 상대는 숨기고, 종료·거절·취소된 상대는 '매칭 취소'로 맨 뒤에. 직원 관계 종료."""
from uuid import uuid4

from test_matching import lab          # noqa: F401
from test_services import env, login, headers, B, C          # noqa: F401


def setup(lab):
    me=login(lab);e=lab['e'];h={'X-CSRF-Token':me['csrf_token'],'Origin':'https://testserver'}
    o=e.post('/api/me/orders',headers=h,json={'plan_code':'ieum_6m_5','idempotency_key':str(uuid4())}).json()
    e.post(f'/api/me/orders/{o["order_id"]}/test-confirm',headers=h,json={})
    return e,h


def ids(e):
    return [(i['member_id'],i['category']) for i in e.get('/api/me/candidates').json()['items']]


def test_active_pair_hidden_and_closed_pair_moves_last(lab):
    e,h=setup(lab);s=lab['transfer'].state.matching
    before=ids(e);first=before[0][0]
    assert all(c=='new' for _,c in before) and len(before)>1
    mid=e.post('/api/me/matches',headers=h,json={'recipient_member_id':first,'idempotency_key':str(uuid4())}).json()['match_id']
    assert first not in [m for m,_ in ids(e)]                 # 신청 대기 중 → 숨김
    s.decide(C,mid,'accept')
    assert first not in [m for m,_ in ids(e)]                 # 성사 → 숨김
    s.decide(B,mid,'end')
    after=ids(e)
    assert after[-1]==(first,'closed') and len(after)==len(before)   # 종료 → 맨 뒤, 매칭 취소
    item=e.get('/api/me/candidates').json()['items'][-1]
    assert item['last_status']=='ended' and item['retry_at'] is None


def test_rejected_pair_shows_retry_date(lab):
    e,h=setup(lab);s=lab['transfer'].state.matching
    first=ids(e)[0][0]
    mid=e.post('/api/me/matches',headers=h,json={'recipient_member_id':first,'idempotency_key':str(uuid4())}).json()['match_id']
    s.decide(C,mid,'reject')
    item=e.get('/api/me/candidates').json()['items'][-1]
    assert item['member_id']==first and item['last_status']=='rejected' and item['retry_at']


def test_staff_can_end_match_and_both_members_are_notified(lab):
    e,h=setup(lab);s=lab['transfer'].state.matching;t=lab['t']
    first=ids(e)[0][0]
    mid=e.post('/api/me/matches',headers=h,json={'recipient_member_id':first,'idempotency_key':str(uuid4())}).json()['match_id']
    assert t.post(f'/api/matching/requests/{mid}/end',headers=headers(),json={}).status_code==403   # 범위 밖
    lab['state']['staff_scope_allowed']=True
    assert t.post(f'/api/matching/requests/{mid}/end',headers=headers(),json={}).status_code==409   # 성사 전에는 종료 불가
    s.decide(C,mid,'accept')
    r=t.post(f'/api/matching/requests/{mid}/end',headers=headers(),json={})
    assert r.status_code==200 and r.json()=={'match_id':mid,'status':'ended'}
    assert lab['state']['staff_scope_body']['action']=='end'
    ev=lab['state']['events'][-1]
    assert ev['kind']=='match_ended_by_staff' and set(ev['user_ids'])=={B,C}
    assert t.post(f'/api/matching/requests/{mid}/end',headers=headers('APPROVER'),json={}).status_code==403
    assert ids(e)[-1]==(first,'closed')
