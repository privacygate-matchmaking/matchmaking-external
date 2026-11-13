from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from pydantic import ValidationError

from ieum.core import iso, now
from ieum.meetings import Availability, MeetingOption, MeetingOptions, Meetings
from ieum.matching import synthetic_directory
from test_matching import service, lab, purchase, propose, reason, B, C, D
from test_services import A, env, login, headers


def allow(pair,action):return {B,C}


def options(timestamp,count=1):
    return MeetingOptions(options=[MeetingOption(starts_at=timestamp+timedelta(days=i+2),
        venue=f'합성 카페 {i+1}',address='시험시 시험구 1번길') for i in range(count)])


@pytest.fixture
def meeting(service):
    purchase(service);match=propose(service);service.decide(C,match['match_id'],'accept')
    meetings=Meetings(service)
    batch=meetings.propose(match['match_id'],A,options(service.clock(),2),allow)
    return meetings,service,match['match_id'],batch['items'][0]['proposal_id']


def test_both_members_and_staff_confirmation_are_separate(meeting):
    meetings,s,mid,pid=meeting
    reason('both_members_required',lambda:meetings.confirm(mid,pid,A,allow))
    meetings.reply(mid,pid,B,Availability(availability='available'))
    reason('both_members_required',lambda:meetings.confirm(mid,pid,A,allow))
    meetings.reply(mid,pid,C,Availability(availability='available'))
    row=meetings.list(mid,B)['items'][0]
    assert row['status']=='proposed' and row['both_available']
    confirmed=meetings.confirm(mid,pid,A,allow)
    assert confirmed['status']=='confirmed' and confirmed['confirmed_by']==A
    assert meetings.confirm(mid,pid,A,allow)['confirmed_at']==confirmed['confirmed_at']
    assert [x['status'] for x in meetings.list(mid,B)['items']].count('confirmed')==1


def test_private_notes_are_not_shared_with_peer_or_audit(meeting):
    meetings,s,mid,pid=meeting
    meetings.reply(mid,pid,B,Availability(availability='change_requested',private_note='PRIVATE-B-NOTE'))
    assert meetings.list(mid,B)['items'][0]['my_reply']['private_note']=='PRIVATE-B-NOTE'
    assert 'PRIVATE-B-NOTE' not in str(meetings.list(mid,C))
    staff=meetings.list(mid,authorize=allow)
    assert staff['items'][0]['replies'][0]['private_note']=='PRIVATE-B-NOTE'
    other_staff=meetings.list(mid,authorize=lambda pair,action:{C})
    assert 'PRIVATE-B-NOTE' not in str(other_staff)
    assert other_staff['items'][0]['replies'][0]['availability']=='change_requested'


def test_new_round_invalidates_old_replies_and_changed_details_need_new_ids(meeting):
    meetings,s,mid,pid=meeting
    for user in (B,C):meetings.reply(mid,pid,user,Availability(availability='available'))
    new=meetings.propose(mid,A,options(s.clock()+timedelta(days=1)),allow)['items'][0]
    assert new['proposal_id']!=pid and new['both_available'] is False
    reason('meeting_not_open',lambda:meetings.confirm(mid,pid,A,allow))
    reason('meeting_not_open',lambda:meetings.reply(mid,pid,B,Availability(availability='available')))
    reason('both_members_required',lambda:meetings.confirm(mid,new['proposal_id'],A,allow))


def test_change_request_invalidates_confirmation_without_additional_credits(meeting):
    meetings,s,mid,pid=meeting
    for user in (B,C):meetings.reply(mid,pid,user,Availability(availability='available'))
    meetings.confirm(mid,pid,A,allow)
    wallet=s.membership(B)['wallet']
    meetings.reply(mid,pid,B,Availability(availability='change_requested',private_note='다른 시간 희망'))
    row=meetings.list(mid,B)['items'][0]
    assert row['status']=='proposed' and row['confirmed_at'] is None
    reason('both_members_required',lambda:meetings.confirm(mid,pid,A,allow))
    meetings.cancel(mid,pid,allow)
    assert s.membership(B)['wallet']==wallet


def test_unrelated_member_ended_match_and_past_date_cannot_reply(meeting):
    meetings,s,mid,pid=meeting
    reason('match_missing',lambda:meetings.list(mid,D))
    reason('match_missing',lambda:meetings.reply(mid,pid,D,Availability(availability='available')))
    s.test_clock[0]+=timedelta(days=4)
    reason('meeting_not_open',lambda:meetings.reply(mid,pid,B,Availability(availability='available')))
    reason('meeting_not_open',lambda:meetings.confirm(mid,pid,A,allow))
    s.decide(B,mid,'end')
    reason('active_match_required',lambda:meetings.list(mid,B))


def test_invalid_options_do_not_replace_current_round(meeting):
    meetings,s,mid,pid=meeting
    for payload in (
        MeetingOptions(options=[MeetingOption(starts_at=s.clock()-timedelta(days=1),venue='카페',address='주소')]),
        MeetingOptions(options=[MeetingOption(starts_at=s.clock()+timedelta(days=181),venue='카페',address='주소')]),
        MeetingOptions(options=[MeetingOption(starts_at=s.clock()+timedelta(days=1),venue='  ',address='주소')]),
    ):
        reason('meeting_option_invalid',lambda:meetings.propose(mid,A,payload,allow))
    assert meetings.list(mid,B)['items'][0]['status']=='proposed'
    with pytest.raises(ValidationError):Availability(availability='available',private_note='x'*301)
    with pytest.raises(ValidationError):Availability(availability='available',user_id=D)


def test_concurrent_confirm_and_change_never_keep_invalid_confirmation(meeting):
    meetings,s,mid,pid=meeting
    for user in (B,C):meetings.reply(mid,pid,user,Availability(availability='available'))
    def confirm():
        try:meetings.confirm(mid,pid,A,allow)
        except Exception as exc:
            from privacygate.errors import GateError
            if not isinstance(exc,GateError):raise
            assert exc.reason=='both_members_required'
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda fn:fn(),[confirm,lambda:meetings.reply(mid,pid,B,Availability(availability='change_requested'))]))
    row=meetings.list(mid,B)['items'][0]
    assert row['status']=='proposed' and row['confirmed_at'] is None and not row['both_available']


@pytest.fixture
def web_meeting(lab):
    s=lab['transfer'].state.matching
    s.remember(synthetic_directory(B))
    purchase(s);match=propose(s);s.decide(C,match['match_id'],'accept')
    lab['state']['staff_scope_allowed']=True
    mid=match['match_id']
    response=lab['t'].post(f'/api/matching/requests/{mid}/meetings',headers=headers(),
        json=options(now()).model_dump(mode='json'))
    assert response.status_code==201,response.text
    return lab,mid,response.json()['items'][0]['proposal_id']


def test_real_relay_and_bff_schedule_flow(web_meeting):
    lab,mid,pid=web_meeting;me=login(lab);e=lab['e'];t=lab['t']
    csrf={'Origin':'https://testserver','X-CSRF-Token':me['csrf_token']}
    path=f'/api/me/matches/{mid}/meetings/{pid}/reply'
    assert e.post(path,json={'availability':'available'}).status_code==403
    assert e.post(path,headers=csrf,json={'availability':'available','user_id':C}).status_code==422
    assert e.post(path,headers=csrf,json={'availability':'available','private_note':'PRIVATE-API-NOTE'}).status_code==200
    assert t.post(f'/api/matching/requests/{mid}/meetings/{pid}/confirm',headers=headers(),json={}).status_code==409
    t.post(f'/api/internal/matching/requests/{mid}/meetings/{pid}/reply',
        headers={'Authorization':'Bearer SVC_EXTERNAL','X-User-Token':'OTHER'},json={'availability':'available'})
    result=t.post(f'/api/matching/requests/{mid}/meetings/{pid}/confirm',headers=headers(),json={})
    assert result.status_code==200,result.text
    assert e.get(f'/api/me/matches/{mid}/meetings').json()['items'][0]['status']=='confirmed'
    lab['state']['staff_scope_assigned']=[C]
    staff_view=t.get(f'/api/matching/requests/{mid}/meetings',headers=headers())
    assert staff_view.status_code==200 and 'PRIVATE-API-NOTE' not in staff_view.text
    for name in ('transfer.jsonl','external.jsonl'):
        assert 'PRIVATE-API-NOTE' not in (lab['root']/name).read_text()


def test_staff_scope_is_checked_on_every_operation_and_cannot_be_faked(web_meeting):
    lab,mid,pid=web_meeting;t=lab['t'];path=f'/api/matching/requests/{mid}/meetings'
    assert lab['state']['staff_scope_body']['participant_user_ids']==[B,C]
    assert t.get(path,headers=headers('MEMBER')).status_code==401
    assert t.get(path,headers=headers('APPROVER')).status_code==403
    lab['state']['staff_scope_allowed']=False
    assert t.get(path,headers=headers()).status_code==403
    assert t.get('/api/matching/requests',headers=headers()).json()['items']==[]
    assert t.post(path,headers=headers(),json=options(now()).model_dump(mode='json')).status_code==403
    assert t.post(path+f'/{pid}/confirm',headers=headers(),json={}).status_code==403
    lab['state']['staff_scope_unavailable']=True
    assert t.get(path,headers=headers()).status_code==500
