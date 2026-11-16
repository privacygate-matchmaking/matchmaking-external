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
# 반출 자료로 보낼 수 있는 항목: 검토 결과 텍스트 + 검토된 사진·마스킹 문서 사본·연락처(조건부)
# Every scope reaches the other member only through a counselor request + approver approval.
# Contact additionally needs mutual contact consent and a confirmed meeting (contact_ready).
SUPPORTED={'name','profile','education','employment','income','marital_status','property','photo','documents','contact'}
ReleaseScope=Literal['name','profile','education','employment','income','marital_status','property','photo','documents','contact']


class Preparation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    grant_id: UUID
    scopes: list[ReleaseScope]=Field(min_length=1,max_length=10)


def contact_ready(db,grant,strict=True):
    """연락처 전달 조건: 상대도 연락처 제공에 동의(유효) + 만남 일정 확정. 요청·승인·다운로드마다 다시 확인한다."""
    reverse=db.execute('SELECT * FROM matching_grants WHERE match_id=%s AND owner_id=%s AND recipient_id=%s AND revoked_at IS NULL ORDER BY created_at DESC LIMIT 1',
                       (grant['match_id'],grant['recipient_id'],grant['owner_id'])).fetchone()
    mutual=bool(reverse) and stamp(plain(reverse)['expires_at'])>now() and 'contact' in json.loads(plain(reverse)['scopes_json'])
    meeting=bool(db.execute("SELECT 1 FROM matching_meeting_proposals WHERE match_id=%s AND status='confirmed'",(grant['match_id'],)).fetchone())
    if strict and not mutual:
        deny('CONSENT_INVALID','contact_mutual_consent_required','연락처는 두 회원이 모두 연락처 제공에 동의해야 전달할 수 있습니다.')
    if strict and not meeting:
        deny('CONSENT_INVALID','contact_meeting_required','만남 일정이 확정된 뒤에 연락처를 전달할 수 있습니다.')
    return mutual and meeting


# 상호주의: 신원을 특정하거나 민감한 항목은 같은 항목을 서로 공개해야만 전달한다 (연락처는 contact_ready 가 따로 확인).
# 한쪽만 공개해 상대 정보를 일방적으로 모으는 것을 막는다. 문서 사본은 함께 선택한 서류 항목을 따라간다.
MUTUAL={'name','photo','income','property','marital_status'}
MUTUAL_LABELS={'name':'이름','photo':'프로필 사진','income':'소득 구간','property':'부동산','marital_status':'혼인관계'}


def reverse_scopes(db,grant):
    """같은 매칭에서 상대가 나에게 공개 동의한 항목 (유효한 최신 동의)."""
    reverse=db.execute('SELECT * FROM matching_grants WHERE match_id=%s AND owner_id=%s AND recipient_id=%s AND revoked_at IS NULL ORDER BY created_at DESC LIMIT 1',
                       (grant['match_id'],grant['recipient_id'],grant['owner_id'])).fetchone()
    if not reverse or stamp(plain(reverse)['expires_at'])<=now():return set()
    return set(json.loads(plain(reverse)['scopes_json']))


def mutual_missing(db,grant,scopes):
    """상호 항목 중 상대가 공개하지 않은 것."""
    return sorted((set(scopes)&MUTUAL)-reverse_scopes(db,grant))


def mutual_ready(db,grant,scopes,strict=True):
    """요청·승인·다운로드마다 확인: 한쪽이 철회하면 다른 쪽 자료도 더 이상 전달되지 않는다."""
    missing=mutual_missing(db,grant,scopes)
    if strict and missing:
        names='·'.join(MUTUAL_LABELS[s] for s in missing)
        deny('CONSENT_INVALID','mutual_consent_required',f'{names}은(는) 두 회원이 모두 같은 항목 공개에 동의해야 서로 볼 수 있습니다.')
    return not missing


def photo_ready(db,grant,strict=True):
    """(이전 이름 호환) 프로필 사진 상호 공개 확인."""
    return mutual_ready(db,grant,['photo'],strict)


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
    return [f.model_dump(mode='json',exclude_none=True) for f in result.fields]


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
    if 'contact' in scopes:contact_ready(db,grant)
    mutual_ready(db,grant,scopes)
    expected={k:grant[k] for k in ('member_id','recipient_id','grant_id','match_id')}
    if any(doc[k]!=v for k,v in expected.items()) or doc['purpose']!='introduction' or any(
            str(row[k])!=grant[k] for k in ('member_id','recipient_id')):
        deny('CONSENT_INVALID','matching_release_binding_mismatch')
    if stamp(row['expires_at'])>stamp(grant['expires_at']):deny('CONSENT_INVALID','matching_release_expiry_mismatch')
    fields=reviewed(upstream,grant,scopes,row['requester_id'])
    if fields!=doc['fields']:deny('CONSENT_INVALID','matching_release_review_changed')
    return {'allowed':True,'member_id':grant['member_id'],'expires_at':grant['expires_at']}


def meeting_state(db,pair,t):
    """Staff list summary: confirmed, awaiting_approval (both members agreed), proposed or none."""
    rows=db.execute("SELECT proposal_id,status,starts_at FROM matching_meeting_proposals WHERE match_id=%s AND status IN ('proposed','confirmed')",
                    (pair['match_id'],)).fetchall()
    rows=[plain(r) for r in rows if plain(r)['status']=='confirmed' or stamp(plain(r)['starts_at'])>t]
    if any(r['status']=='confirmed' for r in rows):return 'confirmed'
    for r in rows:
        agreed={x['user_id'] for x in db.execute("SELECT user_id FROM matching_meeting_replies WHERE proposal_id=%s AND availability='available'",
                                                 (r['proposal_id'],)).fetchall()}
        if {pair['sender_id'],pair['recipient_id']}<={str(u) for u in agreed}:return 'awaiting_approval'
    return 'proposed' if rows else 'none'


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
                    supported=set(scopes)&SUPPORTED
                    ready=active and 'contact' in supported and contact_ready(db,g,strict=False)
                    if 'contact' in supported and not ready:supported.discard('contact')
                    waiting=mutual_missing(db,g,supported) if active else sorted(set(supported)&MUTUAL)
                    supported-=set(waiting)
                    grants[-1].update(scopes=scopes,active=active,can_prepare=active and owner in assigned,
                                      supported_scopes=sorted(supported),contact_ready=ready,
                                      photo_ready='photo' in scopes and 'photo' not in waiting,mutual_waiting=waiting)
                item['grants']=grants
                rows=db.execute('SELECT request_id,consent_id,request_status,expires_at FROM export_requests WHERE purpose=%s AND consent_id IN (SELECT grant_id FROM matching_grants WHERE match_id=%s) ORDER BY created_at DESC',
                                (PURPOSE,pair['match_id'])).fetchall()
                item['releases']=[plain(r) for r in rows]
                item['meeting_state']=meeting_state(db,pair,t)
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
                if 'contact' in scopes:contact_ready(db,grant)
                mutual_ready(db,grant,scopes)
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
