from uuid import UUID
from fastapi import Body, Depends, Request
from .matching import Order, Proposal, Decision, Sharing
from .models import Empty
from .meetings import Availability


def register_matching_bff(app,current,relay,audit):
    def call(request,method,path,body=None):
        return relay(request,method,'/api/internal/matching/'+path,
                     **({'json':body} if body is not None else {})).json()

    @app.get('/api/me/candidates')
    def candidates(request: Request,user=Depends(current)):
        return call(request,'GET','candidates')

    @app.get('/api/me/membership')
    def membership(request: Request,user=Depends(current)):
        return call(request,'GET','membership')

    @app.post('/api/me/orders',status_code=201)
    def order(body: Order,request: Request,user=Depends(current)):
        return call(request,'POST','orders',body.model_dump(mode='json'))

    @app.post('/api/me/orders/{order_id}/test-confirm')
    def confirm(order_id: UUID,request: Request,body: Empty=Body(...),user=Depends(current)):
        return call(request,'POST',f'orders/{order_id}/test-confirm',{})

    @app.get('/api/me/matches')
    def matches(request: Request,user=Depends(current)):
        return call(request,'GET','requests')

    @app.post('/api/me/matches',status_code=201)
    def propose(body: Proposal,request: Request,user=Depends(current)):
        return call(request,'POST','requests',body.model_dump(mode='json'))

    @app.post('/api/me/matches/{match_id}/decision')
    def decide(match_id: UUID,body: Decision,request: Request,user=Depends(current)):
        return call(request,'POST',f'requests/{match_id}/decision',body.model_dump(mode='json'))

    @app.get('/api/me/matches/{match_id}/sharing')
    def sharing(match_id: UUID,request: Request,user=Depends(current)):
        return call(request,'GET',f'requests/{match_id}/sharing')

    @app.post('/api/me/matches/{match_id}/sharing')
    def share(match_id: UUID,body: Sharing,request: Request,user=Depends(current)):
        return call(request,'POST',f'requests/{match_id}/sharing',body.model_dump(mode='json'))

    @app.post('/api/me/matches/{match_id}/sharing/revoke')
    def revoke(match_id: UUID,request: Request,body: Empty=Body(...),user=Depends(current)):
        return call(request,'POST',f'requests/{match_id}/sharing/revoke',{})

    @app.get('/api/me/matches/{match_id}/profile')
    def profile(match_id: UUID,request: Request,user=Depends(current)):
        return call(request,'GET',f'requests/{match_id}/profile')

    @app.get('/api/me/matches/{match_id}/meetings')
    def meetings(match_id: UUID,request: Request,user=Depends(current)):
        return call(request,'GET',f'requests/{match_id}/meetings')

    @app.post('/api/me/matches/{match_id}/meetings/{proposal_id}/reply')
    def availability(match_id: UUID,proposal_id: UUID,body: Availability,request: Request,user=Depends(current)):
        return call(request,'POST',f'requests/{match_id}/meetings/{proposal_id}/reply',body.model_dump())
