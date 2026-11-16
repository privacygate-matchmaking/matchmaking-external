"""Member notifications (bell in the member lounge header).

Storage and wording live in internal-api (same notifications table as staff, scoped by user).
transfer-api only reports *which* event happened to *which* member user ids; it cannot send
free text. Notification failures never block the business action: they are audited and ignored.

external-api  GET  /api/me/notifications, POST /api/me/notifications/read|delete   (session + CSRF)
transfer-api  GET  /api/internal/member-notifications, POST …/read|delete          (service + member token)
internal-api  GET  /api/internal/my-notifications, POST …/read|delete
transfer-api → internal-api  POST /api/internal/member-notification-events          (service token only)
"""
from typing import Literal
from uuid import UUID

from fastapi import Depends, Request
from privacygate.errors import GateError
from pydantic import Field

from .models import Strict

Kind = Literal['match_requested','match_accepted','match_rejected','match_cancelled','match_ended','match_ended_by_staff',
               'meeting_proposed','meeting_accepted','meeting_change_requested','meeting_confirmed','meeting_cancelled',
               'release_approved','release_revoked']


class Select(Strict):
    ids: list[UUID] = Field(default_factory=list,max_length=200)
    all: bool = False


def notifier(upstream,audit):
    """notify(request, user_ids, kind): best effort, never raises."""
    def notify(request,user_ids,kind: Kind):
        ids=[str(u) for u in dict.fromkeys(user_ids) if u]
        if not ids:return
        try:
            upstream('POST','/api/internal/member-notification-events',json={'user_ids':ids,'kind':kind})
        except GateError:
            audit.record(request,'member.notify','deny','notify_failed:'+kind)
    return notify


def register_notification_relay(app,member,upstream):
    @app.get('/api/internal/member-notifications')
    def notifications(request: Request,user=Depends(member)):
        return upstream('GET','/api/internal/my-notifications',request).json()

    @app.post('/api/internal/member-notifications/read')
    def read(body: Select,request: Request,user=Depends(member)):
        return upstream('POST','/api/internal/my-notifications/read',request,json=body.model_dump(mode='json')).json()

    @app.post('/api/internal/member-notifications/delete')
    def delete(body: Select,request: Request,user=Depends(member)):
        return upstream('POST','/api/internal/my-notifications/delete',request,json=body.model_dump(mode='json')).json()


def register_notification_bff(app,current,relay):
    @app.get('/api/me/notifications')
    def notifications(request: Request,user=Depends(current)):
        return relay(request,'GET','/api/internal/member-notifications').json()

    @app.post('/api/me/notifications/read')
    def read(body: Select,request: Request,user=Depends(current)):
        return relay(request,'POST','/api/internal/member-notifications/read',json=body.model_dump(mode='json')).json()

    @app.post('/api/me/notifications/delete')
    def delete(body: Select,request: Request,user=Depends(current)):
        return relay(request,'POST','/api/internal/member-notifications/delete',json=body.model_dump(mode='json')).json()
