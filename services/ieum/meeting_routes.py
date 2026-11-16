from uuid import UUID

from fastapi import Body, Depends, Request
from privacygate.errors import GateError

from .core import deny, plain
from .identity import bearer
from .meetings import Availability, MeetingOptions, Meetings
from .models import Empty


def register_meetings(app,matching,member,counselor,upstream,config,audit):
    meetings=Meetings(matching)
    from .member_notify import notifier
    notify=notifier(upstream,audit)

    def state(match_id,proposal_id=None):
        with matching.transaction() as (db,t):
            m=db.execute('SELECT sender_id,recipient_id FROM matching_requests WHERE match_id=%s',(str(match_id),)).fetchone()
            s=db.execute('SELECT status FROM matching_meeting_proposals WHERE proposal_id=%s',(str(proposal_id),)).fetchone() if proposal_id else None
        return ([str(m['sender_id']),str(m['recipient_id'])] if m else []),(s['status'] if s else None)

    def others(match_id,user_id):
        return [who for who in state(match_id)[0] if who!=user_id]

    def authorization(request,user):
        def check(pair,action):
            if config.get('matching_directory_mode')=='synthetic':
                allowed=user['user_id']=='11111111-1111-4111-8111-111111111101' and all(
                    who in {f'22222222-2222-4222-8222-222222222{suffix}' for suffix in ('201','202','203','204')}
                    for who in (pair['sender_id'],pair['recipient_id']))
                assigned=[pair['sender_id'],pair['recipient_id']] if allowed else []
            else:
                request.state.user_token=bearer(request)
                result=upstream('POST','/api/internal/matching-staff-checks',request,json={
                    'participant_user_ids':[pair['sender_id'],pair['recipient_id']],
                    'purpose':'meeting_schedule','action':action}).json()
                if not isinstance(result,dict) or type(result.get('allowed')) is not bool:deny('INTERNAL_ERROR','invalid_staff_scope_response')
                allowed=result['allowed']
                assigned=result.get('assigned_user_ids',[])
                if not isinstance(assigned,list) or any(not isinstance(who,str) or who not in (pair['sender_id'],pair['recipient_id']) for who in assigned):
                    deny('INTERNAL_ERROR','invalid_staff_scope_response')
            if not allowed:deny('ACCESS_DENIED','meeting_staff_not_assigned','담당 회원의 일정만 관리할 수 있습니다.')
            return set(assigned)
        return check

    def record(request,action,target):
        audit.record(request,action,target=str(target),target_type='meeting')

    @app.get('/api/matching/requests')
    def staff_matches(request: Request,user=Depends(counselor)):
        result=[]
        with matching.transaction() as (db,t):
            rows=db.execute("SELECT * FROM matching_requests WHERE status='matched' ORDER BY decided_at DESC LIMIT 100").fetchall()
            for raw in rows:
                row=plain(raw)
                try:authorization(request,user)(row,'read')
                except GateError as exc:
                    if exc.code.value=='ACCESS_DENIED':continue
                    raise
                result.append({k:row[k] for k in ('match_id','sender_id','recipient_id','status')})
        return {'items':result,'data_source':config.get('matching_directory_mode','internal')}

    @app.get('/api/matching/requests/{match_id}/meetings')
    def staff_list(match_id: UUID,request: Request,user=Depends(counselor)):
        return meetings.list(match_id,authorize=authorization(request,user))

    @app.post('/api/matching/requests/{match_id}/meetings/{proposal_id}/confirm')
    def confirm(match_id: UUID,proposal_id: UUID,request: Request,body: Empty=Body(...),user=Depends(counselor)):
        before=state(match_id,proposal_id)[1]
        result=meetings.confirm(match_id,proposal_id,user['user_id'],authorization(request,user))
        record(request,'meeting.confirm',proposal_id)
        both,after=state(match_id,proposal_id)
        if after=='confirmed' and before!='confirmed':notify(request,both,'meeting_confirmed')
        return result

    @app.post('/api/matching/requests/{match_id}/meetings/{proposal_id}/cancel')
    def cancel(match_id: UUID,proposal_id: UUID,request: Request,body: Empty=Body(...),user=Depends(counselor)):
        before=state(match_id,proposal_id)[1]
        result=meetings.cancel(match_id,proposal_id,authorization(request,user))
        record(request,'meeting.cancel',proposal_id)
        both,after=state(match_id,proposal_id)
        if after=='cancelled' and before!='cancelled':notify(request,both,'meeting_cancelled')
        return result

    @app.post('/api/matching/requests/{match_id}/end')
    def staff_end(match_id: UUID,request: Request,body: Empty=Body(...),user=Depends(counselor)):
        """담당 직원(두 회원 중 한쪽 담당이면 가능)의 관계 종료. 두 회원 모두에게 알린다."""
        result=matching.staff_end(match_id,authorization(request,user))
        audit.record(request,'matching.staff_end',target=str(match_id),target_type='match')
        both=[result.pop(k) for k in ('sender_id','recipient_id') if k in result]
        if both:notify(request,both,'match_ended_by_staff')
        return result

    @app.get('/api/internal/matching/requests/{match_id}/meetings')
    def member_list(match_id: UUID,request: Request,user=Depends(member)):
        return meetings.list(match_id,user['user_id'])

    @app.post('/api/internal/matching/requests/{match_id}/meetings',status_code=201)
    def member_propose(match_id: UUID,body: MeetingOptions,request: Request,user=Depends(member)):
        result=meetings.propose(match_id,user['user_id'],body)
        record(request,'meeting.propose',result['round_id'])
        notify(request,others(match_id,user['user_id']),'meeting_proposed')
        return result

    @app.post('/api/internal/matching/requests/{match_id}/meetings/{proposal_id}/reply')
    def reply(match_id: UUID,proposal_id: UUID,body: Availability,request: Request,user=Depends(member)):
        result=meetings.reply(match_id,proposal_id,user['user_id'],body)
        record(request,'meeting.reply',proposal_id)
        notify(request,others(match_id,user['user_id']),'meeting_accepted' if body.availability=='available' else 'meeting_change_requested')
        return result

    app.state.meetings=meetings
