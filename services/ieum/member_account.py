"""Member lifecycle relay. Internal owns account status and withdrawal decisions."""
from typing import Literal

from fastapi import Body, Depends, Request
from pydantic import BaseModel, Field, ValidationError

from .core import deny
from .models import Empty, Strict


class Withdrawal(Strict):
    reason: str | None = Field(default=None, max_length=200)


class MemberStatus(BaseModel):
    status: Literal['active','withdraw_pending','withdrawn','not_registered']
    by: Literal['self','staff'] | None = None
    reason: str | None = Field(default=None, max_length=200)
    withdraw_at: str | None = None
    withdrawn_at: str | None = None
    can_cancel: bool = Field(default=False, strict=True)
    message: str | None = Field(default=None, max_length=1000)


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

    @app.post('/api/me/withdrawal/cancel')
    def cancel(request: Request,body: Empty=Body(...),user=Depends(current)):
        result=relay(request,'POST','/api/internal/member-withdrawal/cancel',json={}).json()
        audit.record(request,'member.withdrawal_cancel')
        return result
