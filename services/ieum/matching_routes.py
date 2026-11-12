"""Member-only matching routes on the relay. Internal originals are never returned."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import Body, Depends, Request
from pydantic import AwareDatetime, Field, ValidationError

from .core import deny, iso, now, stamp
from .matching import Matching, Directory, Input, Order, Proposal, Decision, Sharing, SCOPES, public, synthetic_directory
from .models import Empty


class ResultField(Input):
    scope: Literal['profile','photo','contact','education','employment','income','marital_status']
    label: str=Field(min_length=1,max_length=80)
    value: str=Field(min_length=1,max_length=300)
    review_status: Literal['reviewed']
    reviewed_at: AwareDatetime


class Result(Input):
    member_id: UUID
    fields: list[ResultField]=Field(max_length=7)


def register_matching(app,config,database,member,upstream,audit):
    mode=config.get('matching_directory_mode','internal')
    if mode not in ('internal','synthetic'):raise RuntimeError('Invalid MATCHING_DIRECTORY_MODE')
    service=Matching(database,test_payments=config.get('test_payments',False))

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
        return {'self':{**public(d.self),'eligible':d.self.eligible},
                'items':[public(p) for p in d.items if p.eligible],
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
        result=service.decide(user['user_id'],match_id,body.action)
        record(request,'matching.'+body.action,match_id)
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
        context=service.disclosure(user['user_id'],match_id)
        d=directory(request,user);check_peer(d,context)
        if mode=='synthetic':
            peer=next(p for p in d.items if str(p.member_id)==context['member_id'])
            examples={'profile':peer.introduction,'education':'합성 학력: 졸업자료 검토 예시',
                'employment':'합성 재직: '+peer.job_group,'income':'합성 소득 구간: 5천만~7천만원',
                'marital_status':'합성 혼인상태 검토 예시'}
            result=Result(member_id=peer.member_id,fields=[ResultField(scope=k,label=k,value=v,review_status='reviewed',reviewed_at=now())
                for k,v in examples.items() if k in context['scopes']])
        else:
            try:
                result=Result.model_validate(upstream('POST','/api/internal/matching-disclosures',request,json={**context,'purpose':'introduction'}).json())
            except (ValidationError,ValueError):
                deny('INTERNAL_ERROR','matching_disclosure_invalid','검토·마스킹 결과의 응답 형식을 확인해주세요.')
        if str(result.member_id)!=context['member_id']:deny('ACCESS_DENIED','disclosure_subject_mismatch')
        # Recheck after internal work: a concurrent revoke/end must not release the result.
        current=service.disclosure(user['user_id'],match_id)
        if current['grant_id']!=context['grant_id']:deny('CONSENT_INVALID','consent_changed')
        fields=[f.model_dump(mode='json') for f in result.fields if f.scope in current['scopes']]
        record(request,'matching.profile_view',match_id)
        return {'member_id':str(result.member_id),'fields':fields,'expires_at':current['expires_at'],
                'data_source':mode,'unavailable_scopes':[s for s in current['scopes'] if s not in {f['scope'] for f in fields}]}

    app.state.matching=service
