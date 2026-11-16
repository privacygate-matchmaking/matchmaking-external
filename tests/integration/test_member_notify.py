"""회원 알림: 중계는 사건 종류와 대상 회원만 내부에 알리고(회원 토큰 없음), 실패해도 업무는 진행된다."""
from uuid import uuid4

from test_matching import lab, purchase, propose          # noqa: F401  (fixtures)
from test_meetings import web_meeting, options             # noqa: F401
from test_services import env, login, headers, B, C, CONSENT


def kinds(env):
    return [(e['kind'],tuple(e['user_ids'])) for e in env['state'].get('events',[])]


def test_matching_events_notify_the_other_member(lab):
    s=lab['transfer'].state.matching
    me=login(lab);e=lab['e'];h={'X-CSRF-Token':me['csrf_token'],'Origin':'https://testserver'}
    o=e.post('/api/me/orders',headers=h,json={'plan_code':'ieum_6m_5','idempotency_key':str(uuid4())}).json()
    e.post(f'/api/me/orders/{o["order_id"]}/test-confirm',headers=h,json={})
    peer=e.get('/api/me/candidates').json()['items'][0]
    m=e.post('/api/me/matches',headers=h,json={'recipient_member_id':peer['member_id'],'idempotency_key':str(uuid4())}).json()
    assert kinds(lab)==[('match_requested',(C,))]
    t=lab['t']
    r=t.post(f'/api/internal/matching/requests/{m["match_id"]}/decision',headers={'Authorization':'Bearer SVC_EXTERNAL','X-User-Token':'OTHER'},json={'action':'accept'})
    assert r.status_code==200,r.text
    assert kinds(lab)[-1]==('match_accepted',(B,))
    # 같은 결정을 다시 보내도 알림은 한 번만
    t.post(f'/api/internal/matching/requests/{m["match_id"]}/decision',headers={'Authorization':'Bearer SVC_EXTERNAL','X-User-Token':'OTHER'},json={'action':'accept'})
    assert [k for k,_ in kinds(lab)].count('match_accepted')==1
    e.post(f'/api/me/matches/{m["match_id"]}/decision',headers=h,json={'action':'end'})
    assert kinds(lab)[-1]==('match_ended',(C,))
    assert all(ev['user_token'] is None for ev in lab['state']['events'])
    assert all(set(ev)=={'user_ids','kind','user_token'} for ev in lab['state']['events'])   # 문구는 보내지 않음


def test_meeting_events(web_meeting):
    lab,mid,pid=web_meeting;t=lab['t'];me=login(lab)
    assert kinds(lab)[-1]==('meeting_proposed',(C,))
    t.post(f'/api/internal/matching/requests/{mid}/meetings/{pid}/reply',
        headers={'Authorization':'Bearer SVC_EXTERNAL','X-User-Token':'OTHER'},json={'availability':'available'})
    assert kinds(lab)[-1]==('meeting_accepted',(B,))
    assert t.post(f'/api/matching/requests/{mid}/meetings/{pid}/confirm',headers=headers(),json={}).status_code==200
    assert kinds(lab)[-1]==('meeting_confirmed',(B,C))
    assert t.post(f'/api/matching/requests/{mid}/meetings/{pid}/cancel',headers=headers(),json={}).status_code==200
    assert kinds(lab)[-1]==('meeting_cancelled',(B,C))
    t.post(f'/api/matching/requests/{mid}/meetings/{pid}/cancel',headers=headers(),json={})
    assert [k for k,_ in kinds(lab)].count('meeting_cancelled')==1


def test_notification_failure_does_not_block_business(lab):
    lab['state']['events_fail']=True
    me=login(lab);e=lab['e'];h={'X-CSRF-Token':me['csrf_token'],'Origin':'https://testserver'}
    purchase(lab['transfer'].state.matching)
    peer=e.get('/api/me/candidates').json()['items'][0]
    m=e.post('/api/me/matches',headers=h,json={'recipient_member_id':peer['member_id'],'idempotency_key':str(uuid4())})
    assert m.status_code==201,m.text
    assert 'notify_failed:match_requested' in (lab['root']/'transfer.jsonl').read_text()


def test_member_notification_bff_and_relay(env):
    me=login(env);e=env['e'];h={'X-CSRF-Token':me['csrf_token'],'Origin':'https://testserver'}
    data=e.get('/api/me/notifications').json()
    assert data['unread']==1 and data['items'][0]['link_view']=='matches'
    assert e.post('/api/me/notifications/read',json={'all':True}).status_code==403          # CSRF 필요
    assert e.post('/api/me/notifications/read',headers=h,json={'ids':[CONSENT]}).json()=={'updated':1,'unread':0}
    assert e.post('/api/me/notifications/delete',headers=h,json={'all':True}).status_code==200
    assert e.post('/api/me/notifications/read',headers=h,json={'ids':[CONSENT],'user_id':C}).status_code==422
    calls=env['state']['notification_calls']
    assert [c[1] for c in calls]==['/api/internal/my-notifications','/api/internal/my-notifications/read','/api/internal/my-notifications/delete']
    # 중계 경로는 회원 토큰 필수
    assert env['t'].get('/api/internal/member-notifications',headers={'Authorization':'Bearer SVC_EXTERNAL'}).status_code==401
