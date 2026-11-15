"""Member proposed meetings, counterpart availability and explicit staff approval.

Members arrange the time and place between themselves (up to three options per round).
The proposer is recorded as available for each option; once the counterpart accepts the
same option, the assigned counselor approves (confirm) or rejects (cancel) it."""
from datetime import timedelta
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from .core import deny, iso, plain, stamp, uid
from .matching import Input


class MeetingOption(Input):
    starts_at: AwareDatetime
    venue: str = Field(min_length=1,max_length=120)
    address: str = Field(min_length=1,max_length=240)


class MeetingOptions(Input):
    options: list[MeetingOption] = Field(min_length=1,max_length=3)


class Availability(Input):
    availability: Literal['available','change_requested']
    private_note: str = Field(default='',max_length=300)


class Meetings:
    def __init__(self,matching):
        self.matching=matching

    def active_match(self,db,match_id,user_id=None):
        if user_id is None:
            raw=db.execute('SELECT * FROM matching_requests WHERE match_id=%s',(str(match_id),)).fetchone()
            if not raw:deny('NOT_FOUND','match_missing')
            row=plain(raw)
        else:row=self.matching.owned(db,match_id,user_id)
        if row['status']!='matched':
            deny('INVALID_STATE','active_match_required','매칭이 성립한 경우에만 일정을 조율할 수 있습니다.')
        return row

    def proposal(self,db,match_id,proposal_id):
        raw=db.execute('SELECT * FROM matching_meeting_proposals WHERE proposal_id=%s AND match_id=%s',
                       (str(proposal_id),str(match_id))).fetchone()
        if not raw:deny('NOT_FOUND','meeting_missing')
        return plain(raw)

    def serialize(self,db,row,pair,t,user_id=None,staff=False,note_user_ids=None):
        replies=[plain(r) for r in db.execute('SELECT * FROM matching_meeting_replies WHERE proposal_id=%s',
                                            (row['proposal_id'],)).fetchall()]
        own=next((r for r in replies if r['user_id']==user_id),None)
        both=all(any(r['user_id']==who and r['availability']=='available' for r in replies)
                 for who in (pair['sender_id'],pair['recipient_id']))
        result={k:row[k] for k in ('proposal_id','round_id','match_id','starts_at','venue','address','status','created_at','confirmed_at')}
        result.update(both_available=both,can_reply=row['status'] in ('proposed','confirmed') and stamp(row['starts_at'])>t)
        if staff:
            allowed=set(note_user_ids or ())
            replies=[{k:v for k,v in reply.items() if k!='private_note' or reply['user_id'] in allowed} for reply in replies]
            result.update(proposed_by=row['proposed_by'],confirmed_by=row['confirmed_by'],replies=replies)
        else:
            result['my_reply']={k:own[k] for k in ('availability','private_note','updated_at')} if own else None
            result['proposed_by_me']=row['proposed_by']==user_id
        return result

    def list(self,match_id,user_id=None,authorize=None):
        with self.matching.transaction() as (db,t):
            pair=self.active_match(db,match_id,user_id)
            allowed=authorize(pair,'read') if authorize else None
            rows=db.execute('SELECT * FROM matching_meeting_proposals WHERE match_id=%s ORDER BY created_at DESC,starts_at',
                            (str(match_id),)).fetchall()
            return {'match_id':str(match_id),'items':[self.serialize(db,plain(r),pair,t,user_id,authorize is not None,allowed) for r in rows]}

    def propose(self,match_id,user_id,body):
        """A matched member proposes up to three options. A new round supersedes open options."""
        with self.matching.transaction() as (db,t):
            pair=self.active_match(db,match_id,user_id)
            if db.execute("SELECT 1 FROM matching_meeting_proposals WHERE match_id=%s AND status='confirmed'",(str(match_id),)).fetchone():
                deny('INVALID_STATE','meeting_already_confirmed','확정된 일정이 있습니다. 변경이 필요하면 확정 일정에서 변경 요청을 먼저 보내주세요.')
            # Once the counterpart accepted an option it waits for staff approval; it can no longer be edited.
            for raw in db.execute("SELECT * FROM matching_meeting_proposals WHERE match_id=%s AND status='proposed'",(str(match_id),)).fetchall():
                if self.serialize(db,plain(raw),pair,t,user_id)['both_available']:
                    deny('INVALID_STATE','meeting_already_accepted','상대가 이미 수락한 일정이 있어 수정할 수 없습니다. 담당 직원 승인을 기다리거나 수락 취소 후 다시 제안해주세요.')
            unique=set()
            for item in body.options:
                if not t<item.starts_at<=t+timedelta(days=180) or not item.venue.strip() or not item.address.strip():
                    deny('INVALID_REQUEST','meeting_option_invalid','일정은 현재 이후 180일 이내이며 장소와 주소가 필요합니다.')
                key=(item.starts_at,item.venue.strip(),item.address.strip())
                if key in unique:deny('INVALID_REQUEST','duplicate_meeting_option')
                unique.add(key)
            # A new round uses new IDs. Old replies cannot approve new details.
            db.execute("UPDATE matching_meeting_proposals SET status='superseded' WHERE match_id=%s AND status='proposed'",(str(match_id),))
            group=uid();items=[]
            for item in body.options:
                proposal_id=uid()
                db.execute('''INSERT INTO matching_meeting_proposals
                    (proposal_id,round_id,match_id,proposed_by,starts_at,venue,address,status,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'proposed',%s)''',
                    (proposal_id,group,str(match_id),user_id,item.starts_at,item.venue.strip(),item.address.strip(),t))
                # Proposing an option means the proposer can attend it.
                db.execute('''INSERT INTO matching_meeting_replies(proposal_id,user_id,availability,private_note,updated_at)
                    VALUES (%s,%s,'available','',%s)''',(proposal_id,user_id,t))
                items.append(self.serialize(db,self.proposal(db,match_id,proposal_id),pair,t,user_id))
            return {'match_id':str(match_id),'round_id':group,'items':items}

    def reply(self,match_id,proposal_id,user_id,body):
        with self.matching.transaction() as (db,t):
            pair=self.active_match(db,match_id,user_id);row=self.proposal(db,match_id,proposal_id)
            if row['status'] not in ('proposed','confirmed') or stamp(row['starts_at'])<=t:
                deny('INVALID_STATE','meeting_not_open','응답할 수 있는 일정이 아닙니다. 최신 일정을 확인해주세요.')
            if row['status']=='confirmed':
                if body.availability!='change_requested':deny('INVALID_STATE','meeting_already_confirmed')
                # A change request invalidates confirmation immediately, without a fee.
                db.execute("UPDATE matching_meeting_proposals SET status='proposed',confirmed_at=NULL,confirmed_by=NULL WHERE proposal_id=%s",(str(proposal_id),))
            if body.availability=='available':
                # A member accepts at most one option: accepting another withdraws the earlier acceptance.
                db.execute('''UPDATE matching_meeting_replies SET availability='change_requested',updated_at=%s
                    WHERE user_id=%s AND availability='available' AND proposal_id IN
                    (SELECT proposal_id FROM matching_meeting_proposals WHERE match_id=%s AND status='proposed'
                     AND proposal_id<>%s AND proposed_by<>%s)''',(t,user_id,str(match_id),str(proposal_id),user_id))
            db.execute('''INSERT INTO matching_meeting_replies(proposal_id,user_id,availability,private_note,updated_at)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT(proposal_id,user_id) DO UPDATE SET
                availability=excluded.availability,private_note=excluded.private_note,updated_at=excluded.updated_at''',
                (str(proposal_id),user_id,body.availability,body.private_note.strip(),t))
            return self.serialize(db,self.proposal(db,match_id,proposal_id),pair,t,user_id)

    def confirm(self,match_id,proposal_id,staff_id,authorize):
        with self.matching.transaction() as (db,t):
            pair=self.active_match(db,match_id);allowed=authorize(pair,'confirm');row=self.proposal(db,match_id,proposal_id)
            if stamp(row['starts_at'])<=t or row['status'] not in ('proposed','confirmed'):
                deny('INVALID_STATE','meeting_not_open')
            result=self.serialize(db,row,pair,t,staff=True,note_user_ids=allowed)
            if not result['both_available']:
                deny('INVALID_STATE','both_members_required','두 회원이 같은 일정에 합의한 뒤 승인할 수 있습니다.')
            if row['status']=='confirmed':return result
            db.execute("UPDATE matching_meeting_proposals SET status='superseded' WHERE match_id=%s AND status='proposed' AND proposal_id<>%s",(str(match_id),str(proposal_id)))
            db.execute("UPDATE matching_meeting_proposals SET status='confirmed',confirmed_at=%s,confirmed_by=%s WHERE proposal_id=%s",(t,staff_id,str(proposal_id)))
            return self.serialize(db,self.proposal(db,match_id,proposal_id),pair,t,staff=True,note_user_ids=allowed)

    def cancel(self,match_id,proposal_id,authorize):
        with self.matching.transaction() as (db,t):
            pair=self.active_match(db,match_id);allowed=authorize(pair,'cancel');row=self.proposal(db,match_id,proposal_id)
            if row['status']=='cancelled':return self.serialize(db,row,pair,t,staff=True,note_user_ids=allowed)
            if row['status'] not in ('proposed','confirmed'):deny('INVALID_STATE','meeting_not_open')
            db.execute("UPDATE matching_meeting_proposals SET status='cancelled',confirmed_at=NULL,confirmed_by=NULL WHERE proposal_id=%s",(str(proposal_id),))
            return self.serialize(db,self.proposal(db,match_id,proposal_id),pair,t,staff=True,note_user_ids=allowed)
