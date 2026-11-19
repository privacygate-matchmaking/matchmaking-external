import hashlib
import json
import os
import re
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

import httpx
from fastapi import Body, Depends, File, Form, Header, Request, UploadFile
from fastapi.responses import Response
from privacygate.transfer_rules import ApprovedDelivery, StoredFile, delivery_problem

from .core import MAX_FILE, Audit, Database, application, deny, iso, now, plain, required, stamp, uid
from .identity import Identity, bearer, principal
from .models import Consent, Empty, Release
from .matching_release import PURPOSE as MATCHING_PURPOSE, check_release, grant_context, register_releases

# 학력·재직·신분증·혼인관계·소득·부동산은 PDF, 프로필 사진은 JPEG/PNG
EvidenceKind=Literal['education','employment','identity','marital','income','property','photo']


def evidence_format_ok(kind,content):
    if kind=='photo':return content.startswith(b'\xff\xd8\xff') or content.startswith(b'\x89PNG\r\n\x1a\n')
    return content.startswith(b'%PDF-')
from privacygate import mtls
from privacygate.errors import GateError

PROFILE_FIELDS={'schema_version','member_id','display_name','age_band','job_group','introduction'}


def profile_v2(body):
    if not isinstance(body,dict) or set(body)!=PROFILE_FIELDS:
        return False
    if type(body['schema_version']) is not int or body['schema_version']!=2:
        return False
    if not all(isinstance(body[k],str) and 1<=len(body[k])<=300 for k in PROFILE_FIELDS-{'schema_version'}):
        return False
    try:UUID(body['member_id'])
    except (ValueError,TypeError):return False
    return True


def check_profile_subject(content,member_id):
    try:body=json.loads(content)
    except (ValueError,UnicodeError):deny('INVALID_STATE','profile_v2_required')
    if not profile_v2(body):deny('INVALID_STATE','profile_v2_required','소개 자료를 새 형식으로 다시 생성하고 승인해주세요.')
    if UUID(body['member_id'])!=UUID(str(member_id)):
        deny('CONSENT_INVALID','profile_subject_mismatch')
    return body


def create_app(config=None, identity=None, database=None, internal=None):
    config=config or {'database':required('DATABASE_URL'),'discovery':required('OIDC_DISCOVERY_URL'),
        'service_id':os.getenv('OIDC_CLIENT_ID','transfer-api'),'secret':required('OIDC_CLIENT_SECRET'),
        'internal':os.getenv('INTERNAL_API_URL','http://internal-api:8000'),
        'staging':os.getenv('STAGING_DIR','/data/transfer-staging'),'verified':os.getenv('VERIFIED_DIR','/data/transfer-verified'),
        'audit':os.getenv('AUDIT_LOG_PATH','/var/log/matchmaking/transfer-api.json'),'mode':os.getenv('GATE_MODE','enforce'),
        'matching_directory_mode':os.getenv('MATCHING_DIRECTORY_MODE','internal'),
        'test_payments':os.getenv('ENABLE_TEST_PAYMENTS','false').lower()=='true',
        'download_tokens':os.getenv('DOWNLOAD_TOKENS','true').lower()=='true',
        'download_token_ttl':int(os.getenv('DOWNLOAD_TOKEN_TTL','60')),'max_downloads':int(os.getenv('MAX_DOWNLOADS','5'))}
    config.setdefault('member_status_check',os.getenv('MEMBER_STATUS_CHECK','false').lower()=='true')
    # 안전한 반출 (enforce 에서만): 일회용 다운로드 토큰(짧은 만료·재사용 차단)과 제공 건당 다운로드 횟수 제한.
    # 시험용 config 에 값이 없으면 끈다 (기존 시험 호환). 배포 기본: 토큰 사용, 60초, 3회.
    config.setdefault('download_tokens',False)
    config.setdefault('download_token_ttl',60)
    config.setdefault('max_downloads',0)
    if config['mode'] not in ('enforce','baseline'):
        raise RuntimeError('Invalid GATE_MODE')
    database=database or Database(config['database'])
    identity=identity or Identity(config['discovery'])
    internal=internal or httpx.Client(base_url=config['internal'],timeout=15,trust_env=False,**mtls.client_options())
    staging,verified=Path(config['staging']),Path(config['verified'])
    staging.mkdir(parents=True,exist_ok=True); verified.mkdir(parents=True,exist_ok=True)

    def insert(event):
        keys=('ts','trace_id','actor','role','action','target_type','target_id','result','reason')
        with database.connection() as db:
            db.execute('INSERT INTO audit_log (ts,trace_id,actor,role,action,target_type,target_id,result,reason) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',tuple(event.get(k) for k in keys))

    audit=Audit('transfer-api',config['audit'],insert)

    # 사후 대응: 회원의 승인 외 자료 요청(공격 ②)을 내부 보안 사건으로 알린다. 대상 회원은 내부가 회원 토큰으로 정한다.
    # 승인 파일 무결성·버전 문제(시스템 쪽 원인)는 알리지 않는다. 알림 실패는 차단 결정을 바꾸지 않는다.
    REPORT_REASONS={'requested_file_not_approved','recipient_mismatch'}

    def report_mismatch(request,reason,blocked):
        if getattr(request.state,'principal',{}).get('role')!='member' or not getattr(request.state,'user_token',None):
            return
        try:
            body={'kind':'approval_mismatch','reason':reason,'blocked':blocked}
            if getattr(request.state,'delivery_id',None):body['delivery_id']=request.state.delivery_id
            upstream('POST','/api/internal/security-events',request,json=body)
            audit.record(request,'security.report',reason=reason,target=getattr(request.state,'delivery_id',None))
        except GateError:
            audit.record(request,'security.report','error','security_report_failed')

    def on_deny(request,exc):
        if exc.code.value=='APPROVAL_MISMATCH' and exc.reason in REPORT_REASONS:
            report_mismatch(request,exc.reason,True)

    app=application('transfer-api',audit,on_deny)

    def staff(request: Request):
        user=principal(identity.verify(bearer(request),'transfer-api','internal-web'))
        if user['role'] not in ('counselor','approver','data_admin'):
            deny('ACCESS_DENIED','staff_required')
        request.state.principal=user
        return user

    def require_role(user,roles):
        if user['role'] not in roles:
            deny('ACCESS_DENIED','role_not_allowed')

    def role(*roles):
        def guard(user=Depends(staff)):
            require_role(user,roles)
            return user
        setattr(guard,'__privacygate_policy__','roles:'+','.join(sorted(roles)))
        return guard

    def member(request: Request):
        identity.verify(bearer(request),'transfer-api','external-api')
        token=request.headers.get('X-User-Token','')
        if not token:
            deny('AUTH_REQUIRED','member_token_missing')
        user=principal(identity.verify(token,'transfer-api','external-web'))
        if user['role']!='member':
            deny('ACCESS_DENIED','member_required')
        request.state.principal=user
        request.state.user_token=token
        correlation=request.headers.get('X-Trace-Id','')
        if re.fullmatch(r'[0-9a-f]{32}',correlation):
            request.state.trace_id=correlation
        if config['member_status_check'] and request.url.path not in (
                '/api/internal/member-status','/api/internal/member-withdrawal/cancel'):
            from .member_account import status_from_response
            status=status_from_response(upstream('GET','/api/internal/my-status',request))
            if status['status']!='active':
                deny('ACCESS_DENIED','member_not_active',status.get('message') or '현재 회원 서비스를 이용할 수 없는 상태입니다.')
        return user

    setattr(member,'__privacygate_policy__','service:external-api')

    def upstream(method,path,request=None,**kwargs):
        headers={'Authorization':'Bearer '+identity.service(config['service_id'],config['secret'])}
        if request is not None:
            headers['X-User-Token']=request.state.user_token
            headers['X-Trace-Id']=request.state.trace_id
        try:
            result=internal.request(method,path,headers=headers,**kwargs)
        except httpx.HTTPError:
            deny('INTERNAL_ERROR','internal_unavailable')
        if result.status_code==501:
            deny('INTERNAL_ERROR','internal_not_implemented','내부 업무 API가 아직 구현·연결되지 않았습니다. 담당자 연결 후 사용할 수 있습니다.')
        if result.status_code>=400:
            try:
                code=result.json()['error']['code']
            except (ValueError,KeyError,TypeError):
                code='INTERNAL_ERROR'
            if code not in ('AUTH_REQUIRED','ACCESS_DENIED','CONSENT_INVALID','INVALID_STATE','NOT_FOUND','INVALID_REQUEST'):
                code='INTERNAL_ERROR'
            try:message=result.json()['error'].get('message')
            except (ValueError,KeyError,TypeError):message=None
            if not isinstance(message,str) or len(message)>200 or '\n' in message or '\r' in message:message=None
            deny(code,'internal_request_failed',message)
        return result

    from .member_notify import notifier
    notify=notifier(upstream,audit)

    def disclosure(row):
        fields={k:str(row[k]) for k in ('member_id','recipient_id','consent_id','purpose')}
        result=upstream('POST','/api/internal/disclosure-checks',json={**fields,'item':'profile'}).json()
        if not isinstance(result,dict):deny('INTERNAL_ERROR','invalid_disclosure_response')
        if type(result.get('allowed')) is not bool:
            deny('INTERNAL_ERROR','invalid_disclosure_response')
        if not result['allowed']:
            deny('CONSENT_INVALID','consent_invalid')
        if not result.get('expires_at') or stamp(result['expires_at'])<=now():
            deny('CONSENT_INVALID','consent_expired')
        try:subject=UUID(result['member_id'])
        except (KeyError,ValueError,TypeError,AttributeError):deny('INTERNAL_ERROR','disclosure_member_id_required')
        if subject!=UUID(str(row['member_id'])):deny('CONSENT_INVALID','disclosure_subject_mismatch')
        return result

    def fetch_file(db,file_id):
        row=db.execute('SELECT * FROM transfer_files WHERE file_id=%s',(str(file_id),)).fetchone()
        if not row:
            deny('NOT_FOUND','file_missing')
        return plain(row)

    def bytes_for(file):
        directory=verified if file.get('verified_key') else staging
        key=file.get('verified_key') or file.get('staging_key')
        # Do not accept paths from callers or even escape the owned volume through a DB value.
        if key!=str(file['file_id']):
            deny('INTERNAL_ERROR','invalid_storage_key')
        path=directory/key
        if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_FILE:
            deny('INVALID_STATE','file_unavailable')
        try:
            content=path.read_bytes()
        except OSError:
            deny('INVALID_STATE','file_unavailable')
        if len(content)>MAX_FILE or hashlib.sha256(content).hexdigest()!=file['file_hash']:
            deny('APPROVAL_MISMATCH','actual_file_hash_mismatch')
        return content

    def release_check(db,row,file):
        content=bytes_for(file)
        if row['purpose']==MATCHING_PURPOSE:
            if not database.test:db.execute('SELECT pg_advisory_xact_lock(87341052)')
            return check_release(db,row,content,upstream)
        consent=disclosure(row)
        check_profile_subject(content,row['member_id'])
        return consent

    @app.post('/api/transfer-files',status_code=201)
    async def register(request: Request,file: UploadFile=File(...),user=Depends(role('counselor'))):
        require_role(user,('counselor',))
        content=await file.read(MAX_FILE+1)
        await file.close()
        if not content or len(content)>MAX_FILE:
            deny('INVALID_REQUEST','file_size_invalid')
        status,reason,banned='blocked','unsupported_format',True
        try:
            body=json.loads(content.decode('utf-8'))
            if profile_v2(body):
                status,reason,banned='passed','profile_schema_passed',False
        except (ValueError,UnicodeError):
            pass
        file_id,digest=uid(),hashlib.sha256(content).hexdigest()
        (staging/file_id).write_bytes(content)
        if status=='passed':
            (verified/file_id).write_bytes(content)
        try:
            with database.connection() as db:
                db.execute('INSERT INTO transfer_files (file_id,file_version,file_hash,uploaded_by,staging_key,verified_key,scan_status,scan_reason,export_banned,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (file_id,1,digest,user['user_id'],file_id if status!='passed' else None,file_id if status=='passed' else None,status,reason,banned,now()))
            if status=='passed':
                (staging/file_id).unlink(missing_ok=True)
        except Exception:
            (staging/file_id).unlink(missing_ok=True); (verified/file_id).unlink(missing_ok=True)
            raise
        audit.record(request,'file.register',reason=reason,target=file_id,target_type='transfer_file')
        return {'file_id':file_id,'file_version':1,'file_hash':digest,'scan_status':status,'export_banned':banned,'created_at':iso(now())}

    @app.post('/api/export-requests',status_code=201)
    def create(body: Release,request: Request,user=Depends(role('counselor'))):
        require_role(user,('counselor',))
        expires=body.expires_at
        if not now()<expires<=now()+timedelta(days=7):
            deny('INVALID_REQUEST','invalid_expiry')
        item=body.model_dump(mode='json')
        consent=disclosure(item)
        if expires>stamp(consent['expires_at']):
            deny('CONSENT_INVALID','consent_ends_before_delivery')
        with database.connection() as db:
            file=fetch_file(db,body.file_id)
            if file['uploaded_by']!=user['user_id']:
                deny('NOT_FOUND','not_own_file')
            if file['scan_status']!='passed' or file['export_banned']:
                deny('INVALID_STATE','file_not_eligible')
            check_profile_subject(bytes_for(file),body.member_id)
            request_id=uid()
            db.execute('INSERT INTO export_requests (request_id,requester_id,file_id,file_version,file_hash,recipient_id,member_id,consent_id,purpose,request_status,expires_at,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (request_id,user['user_id'],str(body.file_id),file['file_version'],file['file_hash'],str(body.recipient_id),str(body.member_id),str(body.consent_id),body.purpose,'pending',expires,now()))
        audit.record(request,'release.request',target=request_id,target_type='export_request',owner_id=body.member_id,
                     recipient_id=body.recipient_id,consent_id=body.consent_id,file_id=body.file_id,file_hash=file['file_hash'])
        return {'request_id':request_id,'request_status':'pending','created_at':iso(now())}

    def visible(db,request_id,user,lock=False):
        query='SELECT * FROM export_requests WHERE request_id=%s'+(' FOR UPDATE' if lock else '')
        row=db.execute(query,(str(request_id),)).fetchone()
        if not row or (user['role']!='approver' and str(row['requester_id'])!=user['user_id']):
            deny('NOT_FOUND','request_missing')
        return plain(row)

    @app.get('/api/export-requests')
    def request_list(user=Depends(role('counselor','approver'))):
        require_role(user,('counselor','approver'))
        with database.connection() as db:
            rows=db.execute('SELECT * FROM export_requests ORDER BY created_at DESC') if user['role']=='approver' else db.execute('SELECT * FROM export_requests WHERE requester_id=%s ORDER BY created_at DESC',(user['user_id'],))
            return {'items':[plain(row) for row in rows]}

    @app.get('/api/export-requests/{request_id}')
    def detail(request_id: UUID,user=Depends(role('counselor','approver'))):
        require_role(user,('counselor','approver'))
        with database.connection() as db:
            item=visible(db,request_id,user)
            file=fetch_file(db,item['file_id'])
            item['scan_status']=file['scan_status']
            item['preview']=json.loads(bytes_for(file)) if file['scan_status']=='passed' and not file['export_banned'] else None
            return item

    def decide(request_id,request,user,approve):
        require_role(user,('approver',))
        with database.connection() as db:
            db.lock()
            item=visible(db,request_id,user,True)
            if item['requester_id']==user['user_id']:
                deny('ACCESS_DENIED','self_approval')
            if item['request_status']!='pending' or stamp(item['expires_at'])<=now():
                deny('INVALID_STATE','request_not_pending')
            if approve:
                file=fetch_file(db,item['file_id'])
                consent=release_check(db,item,file)
                if stamp(item['expires_at'])>stamp(consent['expires_at']):
                    deny('CONSENT_INVALID','consent_expiry_changed')
                file=fetch_file(db,item['file_id'])
                if file['scan_status']!='passed' or file['export_banned'] or file['file_version']!=item['file_version'] or file['file_hash']!=item['file_hash']:
                    deny('APPROVAL_MISMATCH','file_changed_before_approval')
                approval_id,delivery_id=uid(),uid()
                db.execute('INSERT INTO approvals (approval_id,request_id,requester_id,approver_id,approved_file_id,approved_file_version,approved_file_hash,expires_at,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (approval_id,str(request_id),item['requester_id'],user['user_id'],item['file_id'],item['file_version'],item['file_hash'],stamp(item['expires_at']),now()))
                db.execute('INSERT INTO deliveries (delivery_id,approval_id,recipient_id,created_at) VALUES (%s,%s,%s,%s)',(delivery_id,approval_id,item['recipient_id'],now()))
            status='approved' if approve else 'rejected'
            db.execute('UPDATE export_requests SET request_status=%s WHERE request_id=%s',(status,str(request_id)))
        audit.record(request,'release.'+status,target=str(request_id),target_type='export_request',owner_id=item['member_id'],
                     recipient_id=item['recipient_id'],consent_id=item['consent_id'],file_id=item['file_id'],file_hash=item['file_hash'],
                     approval_id=approval_id if approve else None)
        result={'request_id':str(request_id),'request_status':status}
        if approve:
            result.update(approval_id=approval_id,delivery_id=delivery_id,expires_at=item['expires_at'])
            notify(request,[item['recipient_id']],'release_approved')
        return result

    @app.post('/api/export-requests/{request_id}/approve')
    def approve(request_id: UUID,request: Request,body: Empty=Body(...),user=Depends(role('approver'))):
        return decide(request_id,request,user,True)

    @app.post('/api/export-requests/{request_id}/reject')
    def reject(request_id: UUID,request: Request,body: Empty=Body(...),user=Depends(role('approver'))):
        return decide(request_id,request,user,False)

    @app.post('/api/approvals/{approval_id}/revoke')
    def revoke(approval_id: UUID,request: Request,body: Empty=Body(...),user=Depends(role('approver'))):
        require_role(user,('approver',))
        with database.connection() as db:
            row=db.execute('SELECT a.request_id,a.revoked_at,r.recipient_id FROM approvals a JOIN export_requests r ON r.request_id=a.request_id WHERE a.approval_id=%s',(str(approval_id),)).fetchone()
            if not row:
                deny('NOT_FOUND','approval_missing')
            db.execute('UPDATE approvals SET revoked_at=%s WHERE approval_id=%s',(now(),str(approval_id)))
            db.execute("UPDATE export_requests SET request_status='revoked' WHERE request_id=%s",(row['request_id'],))
            drop_tokens(db,approval_id=str(approval_id))
        audit.record(request,'release.revoke')
        if not row['revoked_at']:notify(request,[str(row['recipient_id'])],'release_revoked')
        return {'approval_id':str(approval_id),'request_status':'revoked','revoked_at':iso(now())}

    JOIN='''SELECT r.*, a.approval_id,a.approved_file_id,a.approved_file_version,a.approved_file_hash,
      a.expires_at AS approval_expires_at,a.revoked_at,d.delivery_id,d.downloaded_at,d.download_count
      FROM deliveries d JOIN approvals a ON a.approval_id=d.approval_id
      JOIN export_requests r ON r.request_id=a.request_id'''

    @app.post('/api/internal/delivery-queries')
    def deliveries(body: Empty,user=Depends(member)):
        with database.connection() as db:
            rows=db.execute(JOIN+' WHERE d.recipient_id=%s ORDER BY d.created_at DESC',(user['user_id'],)).fetchall()
            result=[]
            for raw in rows:
                row=plain(raw)
                status=row['request_status']
                if row['revoked_at']:
                    status='revoked'
                elif min(stamp(row['expires_at']),stamp(row['approval_expires_at']))<=now():
                    status='expired'
                if status=='approved' and row['purpose']==MATCHING_PURPOSE:
                    try:grant_context(db,row['consent_id'])
                    except GateError:status='consent_required'
                match_id=None
                if row['purpose']==MATCHING_PURPOSE:
                    grant=db.execute('SELECT match_id FROM matching_grants WHERE grant_id=%s',(str(row['consent_id']),)).fetchone()
                    match_id=str(plain(grant)['match_id']) if grant else None
                result.append({'match_id':match_id,'delivery_id':row['delivery_id'],'approval_id':row['approval_id'],'file_id':row['approved_file_id'],
                    'file_version':row['approved_file_version'],'purpose':row['purpose'],'request_status':status,
                    'expires_at':iso(min(stamp(row['expires_at']),stamp(row['approval_expires_at']))),
                    'downloaded_at':row['downloaded_at'],'download_count':int(row.get('download_count') or 0),'download_limit':config['max_downloads'] if config['mode']=='enforce' else 0,'download_url':f'/api/me/deliveries/{row["delivery_id"]}/download'})
            return {'items':result}

    def drop_tokens(db,approval_id):
        """승인 회수·자료 회수 시 아직 쓰지 않은 다운로드 토큰도 폐기한다."""
        if config['download_tokens']:
            db.execute('UPDATE download_tokens SET used_at=%s WHERE used_at IS NULL AND delivery_id IN '
                       '(SELECT delivery_id FROM deliveries WHERE approval_id=%s)',(now(),approval_id))

    def eligible(db,request,delivery_id,user,file_id=None):
        """다운로드·토큰 발급 공통 판단: 수신자·승인·회수·동의·만료·파일 ID/버전/해시·검사 상태·횟수."""
        raw=db.execute(JOIN+' WHERE d.delivery_id=%s AND d.recipient_id=%s'+('' if database.test else ' FOR UPDATE'),(str(delivery_id),user['user_id'])).fetchone()
        if not raw:
            deny('NOT_FOUND','delivery_missing')
        row=plain(raw)
        request.state.audit_references=[('approval',row['approval_id'])]
        request.state.audit_detail={'owner_id':row['member_id'],'recipient_id':row['recipient_id'],'consent_id':row['consent_id'],
                                    'approval_id':row['approval_id'],'file_id':row['approved_file_id'],'file_hash':row['approved_file_hash']}
        if file_id:
            request.state.audit_references.append(('requested_file',str(file_id)))
        if row['revoked_at']:
            deny('ACCESS_DENIED','approval_revoked')
        approved=fetch_file(db,row['approved_file_id'])
        consent=release_check(db,row,approved)
        if approved['export_banned']:
            deny('ACCESS_DENIED','approved_file_banned')
        record=ApprovedDelivery(str(delivery_id),row['recipient_id'],row['request_status'],
            min(stamp(row['expires_at']),stamp(row['approval_expires_at']),stamp(consent['expires_at'])),
            row['approved_file_id'],row['approved_file_version'],row['approved_file_hash'],True)
        stored=StoredFile(approved['file_id'],approved['file_version'],approved['file_hash'],approved['scan_status'])
        # Always enforce the common approval eligibility checks, even in baseline.
        # Upstream baseline=True would skip consent/expiry and is intentionally NOT used.
        problem=delivery_problem(record,stored,requester_id=user['user_id'],now=now(),
            requested_file_id=str(file_id) if file_id and config['mode']=='enforce' else None,baseline=False)
        if problem:
            deny(problem[0].value,problem[1])
        if config['mode']=='enforce' and config['max_downloads'] and int(row.get('download_count') or 0)>=config['max_downloads']:
            deny('ACCESS_DENIED','download_limit_reached',f'이 자료는 최대 {config["max_downloads"]}회까지 내려받을 수 있습니다. 담당 매니저에게 문의해 주세요.')
        return row,approved

    def token_hash(token):
        return hashlib.sha256(token.encode('utf-8')).hexdigest()

    @app.post('/api/internal/deliveries/{delivery_id}/download-tokens',status_code=201)
    def issue_token(delivery_id: UUID,request: Request,body: Empty=Body(...),user=Depends(member)):
        """일회용 다운로드 토큰. 발급 때도 같은 판단을 하고, 원문은 응답에만 담고 DB 에는 해시만 둔다.
        같은 제공 건의 쓰지 않은 이전 토큰은 폐기한다 (한 번에 하나)."""
        request.state.delivery_id=str(delivery_id)
        if not config['download_tokens'] or config['mode']!='enforce':
            return {'token':None,'expires_in':0,'required':False}
        token=secrets.token_urlsafe(32)
        with database.connection() as db:
            db.lock()
            eligible(db,request,delivery_id,user)
            db.execute('UPDATE download_tokens SET used_at=%s WHERE delivery_id=%s AND used_at IS NULL',(now(),str(delivery_id)))
            db.execute('INSERT INTO download_tokens (token_hash,delivery_id,recipient_id,created_at,expires_at) VALUES (%s,%s,%s,%s,%s)',
                (token_hash(token),str(delivery_id),user['user_id'],now(),now()+timedelta(seconds=config['download_token_ttl'])))
        audit.record(request,'delivery.token_issue',target=str(delivery_id))
        return {'token':token,'expires_in':config['download_token_ttl'],'required':True}

    def consume_token(db,delivery_id,user,token):
        if not token:
            deny('ACCESS_DENIED','download_token_required','다운로드 버튼으로 다시 내려받아 주세요.')
        row=db.execute('SELECT * FROM download_tokens WHERE token_hash=%s'+('' if database.test else ' FOR UPDATE'),(token_hash(token),)).fetchone()
        if not row:
            deny('ACCESS_DENIED','download_token_invalid')
        row=plain(row)
        if str(row['delivery_id'])!=str(delivery_id) or str(row['recipient_id'])!=user['user_id']:
            deny('ACCESS_DENIED','download_token_invalid')
        if row['used_at']:
            deny('ACCESS_DENIED','download_token_used','이미 사용한 다운로드 링크입니다. 다시 내려받으려면 버튼을 눌러 주세요.')
        if stamp(row['expires_at'])<=now():
            deny('ACCESS_DENIED','download_token_expired','다운로드 링크가 만료되었습니다. 다시 시도해 주세요.')
        db.execute('UPDATE download_tokens SET used_at=%s WHERE token_hash=%s',(now(),token_hash(token)))

    @app.get('/api/internal/deliveries/{delivery_id}/download')
    def download(delivery_id: UUID,request: Request,file_id: UUID | None=None,user=Depends(member),
                 token: str | None=Header(None,alias='X-Download-Token',max_length=128)):
        """토큰은 주소창·접속 기록에 남지 않게 헤더로만 받는다."""
        request.state.delivery_id=str(delivery_id)
        with database.connection() as db:
            db.lock()
            row,approved=eligible(db,request,delivery_id,user,file_id)
            # 판단을 모두 통과한 뒤 토큰을 소비한다 (승인 외 파일 요청은 토큰과 무관하게 먼저 '승인 불일치'로 거부·보고)
            if config['download_tokens'] and config['mode']=='enforce':
                consume_token(db,delivery_id,user,token)
            approved_bytes=bytes_for(approved)
            selected=fetch_file(db,file_id) if config['mode']=='baseline' and file_id else approved
            swapped=selected['file_id']!=approved['file_id']
            content=bytes_for(selected) if swapped else approved_bytes
            count=int(row.get('download_count') or 0)+1
            db.execute('UPDATE deliveries SET downloaded_at=%s,download_count=%s WHERE delivery_id=%s',(now(),count,str(delivery_id)))
        audit.record(request,'delivery.download',reason='baseline_file_binding_omitted' if config['mode']=='baseline' else f'download_{count}',
                     target=str(delivery_id),file_id=selected['file_id'],file_hash=hashlib.sha256(content).hexdigest())
        if swapped:   # baseline: 막지 못했지만 탐지는 한다 → '차단 안 됨' 보안 사건
            report_mismatch(request,'requested_file_not_approved',False)
        return Response(content,media_type='application/json' if selected['scan_status']=='passed' else 'text/csv',
                        headers={'Content-Disposition':'attachment; filename="ieum-material"'})

    # 보안 사건 대응(총관리자): 회원이 받은 소개 자료 승인을 모두 회수. internal-api 가 총관리자 토큰으로 부른다
    @app.post('/api/security/recipients/{user_id}/revoke-deliveries')
    def revoke_recipient(user_id: UUID,request: Request,body: Empty=Body(...),user=Depends(role('data_admin'))):
        require_role(user,('data_admin',))
        with database.connection() as db:
            rows=db.execute('SELECT a.approval_id,a.request_id FROM deliveries d JOIN approvals a ON a.approval_id=d.approval_id '
                            'WHERE d.recipient_id=%s AND a.revoked_at IS NULL',(str(user_id),)).fetchall()
            for row in rows:
                db.execute('UPDATE approvals SET revoked_at=%s WHERE approval_id=%s',(now(),str(row['approval_id'])))
                db.execute("UPDATE export_requests SET request_status='revoked' WHERE request_id=%s",(str(row['request_id']),))
                drop_tokens(db,approval_id=str(row['approval_id']))
        audit.record(request,'security.revoke_deliveries',reason=f'revoked_{len(rows)}',target=str(user_id),target_type='user')
        if rows:notify(request,[str(user_id)],'release_revoked')
        return {'recipient_id':str(user_id),'revoked':len(rows)}

    @app.post('/api/internal/evidence-submissions',status_code=201)
    async def evidence_submit(request: Request,file: UploadFile=File(...),
                              kind: EvidenceKind=Form('education'),user=Depends(member)):
        content=await file.read(MAX_FILE+1)
        await file.close()
        if not content or len(content)>MAX_FILE or not evidence_format_ok(kind,content):
            deny('INVALID_REQUEST','invalid_evidence')
        name,media=(('photo.jpg','image/jpeg') if content.startswith(b'\xff\xd8\xff') else ('photo.png','image/png')) \
            if kind=='photo' else ('evidence.pdf','application/pdf')
        return upstream('POST','/api/evidence-files',request,data={'kind':kind},
                        files={'file':(name,content,media)}).json()

    @app.get('/api/internal/evidence-submissions')
    def evidence_status(request: Request,user=Depends(member)):
        return upstream('GET','/api/internal/my-evidence-files',request).json()

    @app.get('/api/internal/member-consents')
    def consents(request: Request,user=Depends(member)):
        return upstream('GET','/api/internal/my-consents',request).json()

    @app.post('/api/internal/member-consents',status_code=201)
    def consent_create(body: Consent,request: Request,user=Depends(member)):
        if body.expires_at<=now():
            deny('INVALID_REQUEST','consent_expired')
        return upstream('POST','/api/internal/my-consents',request,json=body.model_dump(mode='json')).json()

    @app.post('/api/internal/member-consents/{consent_id}/revoke')
    def consent_revoke(consent_id: UUID,request: Request,body: Empty=Body(...),user=Depends(member)):
        return upstream('POST',f'/api/internal/my-consents/{consent_id}/revoke',request,json={}).json()

    from .matching_routes import register_matching
    register_matching(app,config,database,member,upstream,audit)
    from .meeting_routes import register_meetings
    register_meetings(app,app.state.matching,member,role('counselor'),upstream,config,audit)
    register_releases(app,app.state.matching,database,role('counselor'),upstream,verified,audit)

    # 담당 회원 상세(내부 직원 화면)용 이용권 정보: 정회원 여부·남은 매칭권·구매일. 범위는 internal-api 가 다시 확인
    @app.get('/api/matching/members/{user_id}/membership')
    def member_membership(user_id: UUID,request: Request,user=Depends(role('counselor','data_admin'))):
        request.state.user_token=bearer(request)
        check=upstream('POST','/api/internal/member-scope-checks',request,json={'user_id':str(user_id)}).json()
        if not isinstance(check,dict) or check.get('allowed') is not True:
            deny('ACCESS_DENIED','member_not_in_scope')
        data=app.state.matching.membership(str(user_id))
        paid=[o for o in data['orders'] if o['status']=='paid']
        audit.record(request,'matching.membership_view')
        return {'paid':bool(data['wallet']['active']),'wallet':data['wallet'],
            'plan':{k:data['plan'][k] for k in ('name','credits','months')},
                'last_paid_at':(paid[0].get('paid_at') or paid[0]['created_at']) if paid else None,
                'orders':[{k:o.get(k) for k in ('plan_code','amount_won','status','created_at','paid_at')} for o in data['orders']]}
    from .member_account import register_account_relay
    register_account_relay(app,member,upstream,audit,lambda:config['member_status_check'])
    from .member_signup import register_signup_relay
    register_signup_relay(app,identity,upstream,audit)
    from .member_notify import register_notification_relay
    register_notification_relay(app,member,upstream)
    from privacygate.web import assert_all_routes_guarded
    assert_all_routes_guarded(app)
    app.state.database,app.state.staff,app.state.member=database,staff,member
    return app
