import hashlib
import json
from uuid import UUID

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from .core import deny, iso, now, plain, stamp, uid
from .identity import bearer
from privacygate.errors import GateError

PURPOSE='matching_introduction'
SUPPORTED={'profile','education','employment'}


class Preparation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    grant_id: UUID
    scopes: list[Literal['profile','education','employment']]=Field(min_length=1,max_length=3)


def grant_context(db,grant_id):
    raw=db.execute('SELECT * FROM matching_grants WHERE grant_id=%s',(str(grant_id),)).fetchone()
    if not raw:deny('CONSENT_INVALID','matching_grant_missing')
    grant=plain(raw)
    pair=db.execute('SELECT * FROM matching_requests WHERE match_id=%s',(grant['match_id'],)).fetchone()
    if not pair:deny('CONSENT_INVALID','matching_pair_missing')
    pair=plain(pair)
    if pair['status']!='matched' or grant['revoked_at'] or stamp(grant['expires_at'])<=now():
        deny('CONSENT_INVALID','matching_grant_inactive')
    if {grant['owner_id'],grant['recipient_id']}!={pair['sender_id'],pair['recipient_id']}:
        deny('CONSENT_INVALID','matching_grant_participant_mismatch')
    profile=db.execute('SELECT member_id FROM matching_profiles WHERE user_id=%s',(grant['owner_id'],)).fetchone()
    if not profile:deny('CONSENT_INVALID','matching_owner_missing')
    grant['member_id']=str(profile['member_id'])
    grant['scopes']=json.loads(grant['scopes_json'])
    return grant


def reviewed(upstream,grant,scopes,requester_id):
    body={k:grant[k] for k in ('owner_id','recipient_id','member_id')}
    result=upstream('POST','/api/internal/matching-release-checks',json={
        **body,'requester_id':requester_id,'scopes':sorted(scopes)}).json()
    from .matching_routes import Result
    try:result=Result.model_validate(result)
    except ValueError:deny('INTERNAL_ERROR','invalid_matching_release_fields')
    if str(result.member_id)!=grant['member_id'] or {f.scope for f in result.fields}!=set(scopes) or len(result.fields)!=len(scopes):
        deny('CONSENT_INVALID','matching_release_fields_mismatch')
    return [f.model_dump(mode='json') for f in result.fields]


def check_release(db,row,content,upstream):
    grant=grant_context(db,row['consent_id'])
    try:doc=json.loads(content)
    except (ValueError,UnicodeError):deny('CONSENT_INVALID','matching_release_invalid')
    keys={'schema_version','member_id','recipient_id','grant_id','match_id','purpose','scopes','fields'}
    if not isinstance(doc,dict) or set(doc)!=keys or type(doc.get('schema_version')) is not int or doc['schema_version']!=3:
        deny('CONSENT_INVALID','matching_release_schema_invalid')
    scopes=doc['scopes']
    if not isinstance(scopes,list) or not scopes or not all(isinstance(s,str) for s in scopes) or len(set(scopes))!=len(scopes) or not set(scopes)<=SUPPORTED:
        deny('CONSENT_INVALID','matching_release_scope_invalid')
    if not set(scopes)<=set(grant['scopes']):deny('CONSENT_INVALID','matching_release_scope_not_granted')
    expected={k:grant[k] for k in ('member_id','recipient_id','grant_id','match_id')}
    if any(doc[k]!=v for k,v in expected.items()) or doc['purpose']!='introduction' or any(
            str(row[k])!=grant[k] for k in ('member_id','recipient_id')):
        deny('CONSENT_INVALID','matching_release_binding_mismatch')
    if stamp(row['expires_at'])>stamp(grant['expires_at']):deny('CONSENT_INVALID','matching_release_expiry_mismatch')
    fields=reviewed(upstream,grant,scopes,row['requester_id'])
    if fields!=doc['fields']:deny('CONSENT_INVALID','matching_release_review_changed')
    return {'allowed':True,'member_id':grant['member_id'],'expires_at':grant['expires_at']}


def register_releases(app,matching,database,counselor,upstream,verified,audit):
    def scope(request,user,pair):
        request.state.user_token=bearer(request)
        result=upstream('POST','/api/internal/matching-staff-checks',request,json={
            'participant_user_ids':[pair['sender_id'],pair['recipient_id']],
            'purpose':'matching_overview','action':'read'}).json()
        assigned=result.get('assigned_user_ids') if isinstance(result,dict) else None
        if not isinstance(result,dict) or type(result.get('allowed')) is not bool or not isinstance(assigned,list) or any(
                who not in (pair['sender_id'],pair['recipient_id']) for who in assigned):
            deny('INTERNAL_ERROR','invalid_staff_scope_response')
        return assigned if result['allowed'] else []

    @app.get('/api/matching/overview')
    def overview(request: Request,user=Depends(counselor)):
        items=[]
        with matching.transaction() as (db,t):
            pairs=db.execute('SELECT * FROM matching_requests ORDER BY created_at DESC').fetchall()
            for raw in pairs:
                pair=plain(raw)
                try:assigned=scope(request,user,pair)
                except GateError as exc:
                    if exc.code.value=='ACCESS_DENIED':continue
                    raise
                if not assigned:continue
                item={k:pair[k] for k in ('match_id','sender_id','recipient_id','status','created_at','expires_at')}
                grants=[]
                for owner in (pair['sender_id'],pair['recipient_id']):
                    raw=db.execute('SELECT * FROM matching_grants WHERE match_id=%s AND owner_id=%s ORDER BY created_at DESC LIMIT 1',
                                   (pair['match_id'],owner)).fetchone()
                    if not raw:continue
                    g=plain(raw)
                    active=pair['status']=='matched' and not g['revoked_at'] and stamp(g['expires_at'])>t
                    scopes=json.loads(g['scopes_json'])
                    grants.append({k:g[k] for k in ('grant_id','owner_id','recipient_id','expires_at','revoked_at')})
                    grants[-1].update(scopes=scopes,active=active,can_prepare=active and owner in assigned,
                                      supported_scopes=sorted(set(scopes)&SUPPORTED))
                item['grants']=grants
                rows=db.execute('SELECT request_id,consent_id,request_status,expires_at FROM export_requests WHERE purpose=%s AND consent_id IN (SELECT grant_id FROM matching_grants WHERE match_id=%s) ORDER BY created_at DESC',
                                (PURPOSE,pair['match_id'])).fetchall()
                item['releases']=[plain(r) for r in rows]
                items.append(item)
        return {'items':items,'data_source':'internal'}

    @app.post('/api/matching/releases',status_code=201)
    def prepare(body: Preparation,request: Request,user=Depends(counselor)):
        file_id=uid();path=verified/file_id
        try:
            with matching.transaction() as (db,t):
                grant=grant_context(db,body.grant_id)
                scopes=sorted(set(body.scopes))
                if not set(scopes)<=set(grant['scopes']):deny('CONSENT_INVALID','matching_release_scope_not_granted')
                fields=reviewed(upstream,grant,scopes,user['user_id'])
                doc={k:grant[k] for k in ('member_id','recipient_id','grant_id','match_id')}
                doc.update(schema_version=3,purpose='introduction',scopes=scopes,fields=fields)
                content=json.dumps(doc,ensure_ascii=False).encode('utf-8');digest=hashlib.sha256(content).hexdigest()
                path.write_bytes(content)
                db.execute('INSERT INTO transfer_files (file_id,file_version,file_hash,uploaded_by,staging_key,verified_key,scan_status,scan_reason,export_banned,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (file_id,1,digest,user['user_id'],None,file_id,'passed','matching_reviewed_fields',False,t))
                request_id=uid()
                db.execute('INSERT INTO export_requests (request_id,requester_id,file_id,file_version,file_hash,recipient_id,member_id,consent_id,purpose,request_status,expires_at,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (request_id,user['user_id'],file_id,1,digest,grant['recipient_id'],grant['member_id'],grant['grant_id'],PURPOSE,'pending',stamp(grant['expires_at']),t))
        except Exception:
            path.unlink(missing_ok=True)
            raise
        audit.record(request,'matching.release_request',target=request_id,target_type='export_request')
        return {'request_id':request_id,'request_status':'pending','file_id':file_id,'consent_id':grant['grant_id']}
