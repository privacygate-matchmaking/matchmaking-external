"""Member-only matching routes on the relay. Internal originals are never returned."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import Body, Depends, Request
import base64
import binascii
import hashlib

from pydantic import AwareDatetime, Field, ValidationError, model_validator

from .core import deny, iso, now, stamp
from .matching import Matching, Directory, Input, Order, Proposal, Decision, Sharing, SCOPES, Scope, public, synthetic_directory
from .models import Empty


class Attachment(Input):
    """검토·마스킹 사본·검토된 사진 (반출 자료 전용). 형식·해시·파일 머리를 모두 확인한다."""
    name: str=Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9._-]+$')
    label: str=Field(min_length=1,max_length=80)
    media_type: Literal['image/png','image/jpeg']
    sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    data_base64: str=Field(min_length=4,max_length=700_000)

    @model_validator(mode='after')
    def check(self):
        try:blob=base64.b64decode(self.data_base64,validate=True)
        except (ValueError,binascii.Error):raise ValueError('attachment_base64')
        magic={'image/png':b'\x89PNG\r\n\x1a\n','image/jpeg':b'\xff\xd8\xff'}[self.media_type]
        if not blob.startswith(magic) or hashlib.sha256(blob).hexdigest()!=self.sha256:
            raise ValueError('attachment_mismatch')
        return self


class ResultField(Input):
    scope: Scope
    label: str=Field(min_length=1,max_length=80)
    value: str=Field(min_length=1,max_length=300)
    review_status: Literal['reviewed']
    reviewed_at: AwareDatetime
    attachments: list[Attachment]|None=Field(default=None,max_length=5)


class Result(Input):
    member_id: UUID
    fields: list[ResultField]=Field(max_length=10)


# 화면 열람 없이 직원 요청 + 승인자 승인 자료로만 제공하는 항목
RELEASE_ONLY=SCOPES   # 모든 항목: 직원 요청 + 승인자 승인 자료로만 상대에게 표시


def register_matching(app,config,database,member,upstream,audit):
    mode=config.get('matching_directory_mode','internal')
    if mode not in ('internal','synthetic'):raise RuntimeError('Invalid MATCHING_DIRECTORY_MODE')
    service=Matching(database,test_payments=config.get('test_payments',False))
    from .member_notify import notifier
    notify=notifier(upstream,audit)

    def pair(match_id):
        with service.transaction() as (db,t):
            row=db.execute('SELECT sender_id,recipient_id,status FROM matching_requests WHERE match_id=%s',(str(match_id),)).fetchone()
        return {k:str(row[k]) for k in ('sender_id','recipient_id','status')} if row else None

    def directory(request,user):
        if mode=='synthetic':result=synthetic_directory(user['user_id'])
        else:
            try:
                result=Directory.model_validate(upstream('POST','/api/internal/matching-directory',request,json={}).json())
            except (ValidationError,ValueError):
                deny('INTERNAL_ERROR','matching_directory_invalid','매칭 프로필 응답 형식을 확인할 수 없습니다.')
        ids=[str(x.member_id) for x in [result.self,*result.items]]
        users=[str(x.user_id) for x in [result.self,*result.items]]
        if str(result.self.user_id)!=user['user_id'] or len(set(ids))!=len(ids) or len(set(users))!=len(users):
            deny('INTERNAL_ERROR','matching_directory_identity_invalid')
        service.remember(result)
        return result

    def check_peer(directory_result,context):
        if not directory_result.self.eligible:
            deny('ACCESS_DENIED','member_not_eligible','회원 검토 상태를 확인해주세요.')
        if not any(str(p.member_id)==context['member_id'] and p.eligible for p in directory_result.items):
            deny('ACCESS_DENIED','peer_not_eligible','현재 공개 가능한 상대가 아닙니다.')

    def record(request,action,target=None):
        audit.record(request,action,target=str(target) if target else None,target_type='match')

    @app.get('/api/internal/matching/candidates')
    def candidates(request: Request,user=Depends(member)):
        d=directory(request,user)
        peers=[p for p in d.items if p.eligible]
        history=service.pair_history(user['user_id'],[str(p.user_id) for p in peers])
        fresh,closed=[],[]
        for p in peers:
            h=history.get(str(p.user_id))
            if h and h['status'] in ('pending','matched'):continue      # 진행 중·성사된 상대는 후보에서 뺀다
            item={**public(p),'category':'closed' if h else 'new'}
            if h:item.update(last_status=h['status'],closed_at=h['closed_at'],retry_at=h['retry_at'])
            (closed if h else fresh).append(item)
        closed.sort(key=lambda x:str(x['closed_at'] or ''))              # 종료·취소된 상대는 맨 뒤 (오래된 순)
        return {'self':{**public(d.self),'eligible':d.self.eligible,'requirements':d.self.requirements},
                'items':fresh+closed,
                'data_source':mode,'notice':'합성 회원 실습 모드입니다. 실제 증빙 심사 결과가 아닙니다.' if mode=='synthetic' else None}

    @app.get('/api/internal/matching/membership')
    def membership(user=Depends(member)):
        return {**service.membership(user['user_id']),'data_source':mode}

    @app.post('/api/internal/matching/orders',status_code=201)
    def create_order(body: Order,request: Request,user=Depends(member)):
        result=service.order(user['user_id'],body)
        record(request,'membership.order',result['order_id'])
        return result

    @app.post('/api/internal/matching/orders/{order_id}/test-confirm')
    def confirm(order_id: UUID,request: Request,body: Empty=Body(...),user=Depends(member)):
        result=service.confirm_test_order(user['user_id'],order_id)
        record(request,'membership.test_confirm',order_id)
        return result

    @app.get('/api/internal/matching/requests')
    def requests(user=Depends(member)):
        return {**service.list(user['user_id']),'data_source':mode}

    @app.post('/api/internal/matching/requests',status_code=201)
    def propose(body: Proposal,request: Request,user=Depends(member)):
        result=service.propose(user['user_id'],directory(request,user),body)
        record(request,'matching.request',result['match_id'])
        p=pair(result['match_id'])
        if p and p['status']=='pending' and p['sender_id']==user['user_id']:
            notify(request,[p['recipient_id']],'match_requested')
        return result

    @app.post('/api/internal/matching/requests/{match_id}/decision')
    def decide(match_id: UUID,body: Decision,request: Request,user=Depends(member)):
        if body.action=='accept':
            d=directory(request,user)
            if not d.self.eligible:deny('ACCESS_DENIED','member_not_eligible')
            with service.transaction() as (db,t):
                item=service.owned(db,match_id,user['user_id'])
                if not any(str(p.user_id)==item['sender_id'] and p.eligible for p in d.items):
                    deny('ACCESS_DENIED','peer_not_eligible')
        before=pair(match_id)
        result=service.decide(user['user_id'],match_id,body.action)
        record(request,'matching.'+body.action,match_id)
        after=pair(match_id)
        if before and after and before['status']!=after['status']:
            other=after['sender_id'] if user['user_id']==after['recipient_id'] else after['recipient_id']
            notify(request,[other],{'accept':'match_accepted','reject':'match_rejected','cancel':'match_cancelled','end':'match_ended'}[body.action])
        return result

    @app.get('/api/internal/matching/requests/{match_id}/sharing')
    def sharing(match_id: UUID,user=Depends(member)):
        return service.grants(user['user_id'],match_id)

    @app.post('/api/internal/matching/requests/{match_id}/sharing')
    def share(match_id: UUID,body: Sharing,request: Request,user=Depends(member)):
        d=directory(request,user)
        if not d.self.eligible:deny('ACCESS_DENIED','member_not_eligible')
        result=service.share(user['user_id'],match_id,body)
        record(request,'matching.consent',match_id)
        return result

    @app.post('/api/internal/matching/requests/{match_id}/sharing/revoke')
    def revoke(match_id: UUID,request: Request,body: Empty=Body(...),user=Depends(member)):
        result=service.revoke(user['user_id'],match_id)
        record(request,'matching.consent_revoke',match_id)
        return result

    @app.get('/api/internal/matching/requests/{match_id}/profile')
    def profile(match_id: UUID,request: Request,user=Depends(member)):
        """Consent summary only. Every item becomes visible to the other member only as approved material
        (counselor request + approver approval); the screen shows it from the member's approved deliveries."""
        context=service.disclosure(user['user_id'],match_id)
        d=directory(request,user);check_peer(d,context)
        from .matching_release import contact_ready, mutual_missing, MUTUAL
        from .core import plain
        contact_ok=False;waiting=[]
        if ({'contact'}|MUTUAL)&set(context['scopes']):
            with service.transaction() as (db,t):
                grant=db.execute('SELECT * FROM matching_grants WHERE grant_id=%s',(str(context['grant_id']),)).fetchone()
                if grant:
                    contact_ok='contact' in context['scopes'] and contact_ready(db,plain(grant),strict=False)
                    waiting=mutual_missing(db,plain(grant),context['scopes'])     # 상대는 공개, 나는 아직 미공개
        record(request,'matching.profile_view',match_id)
        return {'member_id':context['member_id'],'fields':[],'expires_at':context['expires_at'],'data_source':mode,
                'approval_required':True,'consented_scopes':context['scopes'],
                'contact_pending':'contact' in context['scopes'] and not contact_ok,
                'photo_pending':'photo' in waiting,'mutual_pending':waiting,
                'release_only_scopes':context['scopes'],'unavailable_scopes':[]}

    app.state.matching=service
