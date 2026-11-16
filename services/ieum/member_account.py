"""Member lifecycle relay. Internal owns account status and withdrawal decisions."""
from typing import Literal

from fastapi import Body, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError

from .core import deny
from .models import Empty, Strict


class Withdrawal(Strict):
    reason: str | None = Field(default=None, max_length=200)


class ProfileUpdate(Strict):
    """내 계정: 키·몸무게(본인 신고)·연락처·소개글. 형식 검사(소개글의 연락 수단 금지 포함)와 저장은 내부가 최종 판단한다."""
    height_cm: int | None = Field(default=None, ge=120, le=230)
    weight_kg: int | None = Field(default=None, ge=30, le=200)
    contact_phone: str | None = Field(default=None, max_length=20)
    contact_email: str | None = Field(default=None, max_length=200)
    introduction: str | None = Field(default=None, max_length=300)


class MemberStatus(BaseModel):
    status: Literal['active','withdraw_pending','withdrawn','not_registered']
    by: Literal['self','staff'] | None = None
    reason: str | None = Field(default=None, max_length=200)
    withdraw_at: str | None = None
    withdrawn_at: str | None = None
    can_cancel: bool = Field(default=False, strict=True)
    message: str | None = Field(default=None, max_length=1000)


PHOTO_HEADERS={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
               'Content-Security-Policy':"sandbox; default-src 'none'",'Content-Disposition':'inline'}


def photo_response(result):
    """Only an image may pass back toward the browser (metadata-stripped copy made by internal)."""
    media=result.headers.get('content-type','').split(';')[0].strip()
    body=result.content
    jpeg=body.startswith(b'\xff\xd8\xff');png=body.startswith(b'\x89PNG\r\n\x1a\n')
    if (media,jpeg,png) not in (('image/jpeg',True,False),('image/png',False,True)) or len(body)>4*1024*1024:
        deny('INTERNAL_ERROR','invalid_photo_response')
    return Response(body,media_type=media,headers=PHOTO_HEADERS)


def status_from_response(response):
    try:
        result = MemberStatus.model_validate(response.json()).model_dump(exclude_none=True)
    except (ValueError, ValidationError):
        deny('INTERNAL_ERROR','invalid_member_status','회원 상태 응답을 확인할 수 없습니다. 잠시 후 다시 시도해주세요.')
    result['can_cancel'] = result['status']=='withdraw_pending' and result.get('by')=='self' and result['can_cancel']
    return result


def register_account_relay(app,member,upstream,audit,enabled):
    @app.get('/api/internal/member-status')
    def status(request: Request,user=Depends(member)):
        return status_from_response(upstream('GET','/api/internal/my-status',request))

    @app.post('/api/internal/member-withdrawal')
    def withdraw(body: Withdrawal,request: Request,user=Depends(member)):
        if not enabled():deny('INVALID_STATE','member_lifecycle_not_enabled','회원 상태 연동을 활성화한 뒤 사용할 수 있습니다.')
        result=upstream('POST','/api/internal/my-withdrawal',request,json=body.model_dump()).json()
        audit.record(request,'member.withdrawal')
        return result

    @app.get('/api/internal/member-profile')
    def profile(request: Request,user=Depends(member)):
        return upstream('GET','/api/internal/my-profile',request).json()

    @app.get('/api/internal/member-photo')
    def photo(request: Request,which: Literal['approved','latest']=Query('approved'),user=Depends(member)):
        return photo_response(upstream('GET','/api/internal/my-photo',request,params={'which':which}))

    @app.patch('/api/internal/member-profile')
    def profile_update(body: ProfileUpdate,request: Request,user=Depends(member)):
        result=upstream('PATCH','/api/internal/my-profile',request,json=body.model_dump(exclude_unset=True)).json()
        audit.record(request,'member.profile_update')
        return result

    @app.post('/api/internal/member-withdrawal/cancel')
    def cancel(request: Request,body: Empty=Body(...),user=Depends(member)):
        if not enabled():deny('INVALID_STATE','member_lifecycle_not_enabled','회원 상태 연동을 활성화한 뒤 사용할 수 있습니다.')
        # The internal API alone decides whether this is a cancellable self request.
        result=upstream('POST','/api/internal/my-withdrawal/cancel',request,json={}).json()
        audit.record(request,'member.withdrawal_cancel')
        return result


def register_account_bff(app,current,relay,audit):
    @app.get('/api/me/status')
    def status(request: Request,user=Depends(current)):
        return status_from_response(relay(request,'GET','/api/internal/member-status'))

    @app.post('/api/me/withdrawal')
    def withdraw(body: Withdrawal,request: Request,user=Depends(current)):
        result=relay(request,'POST','/api/internal/member-withdrawal',json=body.model_dump()).json()
        audit.record(request,'member.withdrawal')
        return result

    @app.get('/api/me/profile')
    def profile(request: Request,user=Depends(current)):
        return relay(request,'GET','/api/internal/member-profile').json()

    @app.get('/api/me/profile/photo')
    def photo(request: Request,which: Literal['approved','latest']=Query('approved'),user=Depends(current)):
        return photo_response(relay(request,'GET','/api/internal/member-photo',params={'which':which}))

    @app.patch('/api/me/profile')
    def profile_update(body: ProfileUpdate,request: Request,user=Depends(current)):
        result=relay(request,'PATCH','/api/internal/member-profile',json=body.model_dump(exclude_unset=True)).json()
        audit.record(request,'member.profile_update')
        return result

    @app.post('/api/me/withdrawal/cancel')
    def cancel(request: Request,body: Empty=Body(...),user=Depends(current)):
        result=relay(request,'POST','/api/internal/member-withdrawal/cancel',json={}).json()
        audit.record(request,'member.withdrawal_cancel')
        return result
