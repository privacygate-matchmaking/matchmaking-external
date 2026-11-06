import hashlib
import json
import os
import re
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import httpx
from fastapi import Body, Depends, File, Request, UploadFile
from fastapi.responses import Response
from privacygate.transfer_rules import ApprovedDelivery, StoredFile, delivery_problem

from .core import MAX_FILE, Audit, Database, application, deny, iso, now, plain, required, stamp, uid
from .identity import Identity, bearer, principal
from .models import Consent, Empty, Release

PROFILE_FIELDS={'member_pseudo_id','age_band','job_group','introduction'}


def create_app(config=None, identity=None, database=None, internal=None):
    config=config or {'database':required('DATABASE_URL'),'discovery':required('OIDC_DISCOVERY_URL'),
        'service_id':os.getenv('OIDC_CLIENT_ID','transfer-api'),'secret':required('OIDC_CLIENT_SECRET'),
        'internal':os.getenv('INTERNAL_API_URL','http://internal-api:8000'),
        'staging':os.getenv('STAGING_DIR','/data/transfer-staging'),'verified':os.getenv('VERIFIED_DIR','/data/transfer-verified'),
        'audit':os.getenv('AUDIT_LOG_PATH','/var/log/matchmaking/transfer-api.json'),'mode':os.getenv('GATE_MODE','enforce')}
    if config['mode'] not in ('enforce','baseline'):
        raise RuntimeError('Invalid GATE_MODE')
    database=database or Database(config['database'])
    identity=identity or Identity(config['discovery'])
    internal=internal or httpx.Client(base_url=config['internal'],timeout=15,trust_env=False)
    staging,verified=Path(config['staging']),Path(config['verified'])
    staging.mkdir(parents=True,exist_ok=True); verified.mkdir(parents=True,exist_ok=True)

    def insert(event):
        keys=('ts','trace_id','actor','role','action','target_type','target_id','result','reason')
        with database.connection() as db:
            db.execute('INSERT INTO audit_log (ts,trace_id,actor,role,action,target_type,target_id,result,reason) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',tuple(event.get(k) for k in keys))

    audit=Audit('transfer-api',config['audit'],insert)
    app=application('transfer-api',audit)

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
        if result.status_code>=400:
            try:
                code=result.json()['error']['code']
            except (ValueError,KeyError,TypeError):
                code='INTERNAL_ERROR'
            if code not in ('AUTH_REQUIRED','ACCESS_DENIED','CONSENT_INVALID','INVALID_STATE','NOT_FOUND','INVALID_REQUEST'):
                code='INTERNAL_ERROR'
            deny(code,'internal_request_failed')
        return result

    def disclosure(row):
        fields={k:str(row[k]) for k in ('member_id','recipient_id','consent_id','purpose')}
        result=upstream('POST','/api/internal/disclosure-checks',json={**fields,'item':'profile'}).json()
        if type(result.get('allowed')) is not bool:
            deny('INTERNAL_ERROR','invalid_disclosure_response')
        if not result['allowed']:
            deny('CONSENT_INVALID','consent_invalid')
        if not result.get('expires_at') or stamp(result['expires_at'])<=now():
            deny('CONSENT_INVALID','consent_expired')
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
            if isinstance(body,dict) and set(body)==PROFILE_FIELDS and all(isinstance(x,str) and 1<=len(x)<=300 for x in body.values()):
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
            preview=json.loads(bytes_for(file))
            if not consent.get('member_pseudo_id') or preview['member_pseudo_id']!=consent['member_pseudo_id']:
                deny('CONSENT_INVALID','profile_subject_mismatch')
            request_id=uid()
            db.execute('INSERT INTO export_requests (request_id,requester_id,file_id,file_version,file_hash,recipient_id,member_id,consent_id,purpose,request_status,expires_at,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (request_id,user['user_id'],str(body.file_id),file['file_version'],file['file_hash'],str(body.recipient_id),str(body.member_id),str(body.consent_id),body.purpose,'pending',expires,now()))
        audit.record(request,'release.request',target=request_id,target_type='export_request')
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
                consent=disclosure(item)
                if stamp(item['expires_at'])>stamp(consent['expires_at']):
                    deny('CONSENT_INVALID','consent_expiry_changed')
                file=fetch_file(db,item['file_id'])
                if file['scan_status']!='passed' or file['export_banned'] or file['file_version']!=item['file_version'] or file['file_hash']!=item['file_hash']:
                    deny('APPROVAL_MISMATCH','file_changed_before_approval')
                bytes_for(file)
                approval_id,delivery_id=uid(),uid()
                db.execute('INSERT INTO approvals (approval_id,request_id,requester_id,approver_id,approved_file_id,approved_file_version,approved_file_hash,expires_at,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (approval_id,str(request_id),item['requester_id'],user['user_id'],item['file_id'],item['file_version'],item['file_hash'],stamp(item['expires_at']),now()))
                db.execute('INSERT INTO deliveries (delivery_id,approval_id,recipient_id,created_at) VALUES (%s,%s,%s,%s)',(delivery_id,approval_id,item['recipient_id'],now()))
            status='approved' if approve else 'rejected'
            db.execute('UPDATE export_requests SET request_status=%s WHERE request_id=%s',(status,str(request_id)))
        audit.record(request,'release.'+status)
        result={'request_id':str(request_id),'request_status':status}
        if approve:
            result.update(approval_id=approval_id,delivery_id=delivery_id,expires_at=item['expires_at'])
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
            row=db.execute('SELECT request_id FROM approvals WHERE approval_id=%s',(str(approval_id),)).fetchone()
            if not row:
                deny('NOT_FOUND','approval_missing')
            db.execute('UPDATE approvals SET revoked_at=%s WHERE approval_id=%s',(now(),str(approval_id)))
            db.execute("UPDATE export_requests SET request_status='revoked' WHERE request_id=%s",(row['request_id'],))
        audit.record(request,'release.revoke')
        return {'approval_id':str(approval_id),'request_status':'revoked','revoked_at':iso(now())}

    JOIN='''SELECT r.*, a.approval_id,a.approved_file_id,a.approved_file_version,a.approved_file_hash,
      a.expires_at AS approval_expires_at,a.revoked_at,d.delivery_id,d.downloaded_at
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
                result.append({'delivery_id':row['delivery_id'],'approval_id':row['approval_id'],'file_id':row['approved_file_id'],
                    'file_version':row['approved_file_version'],'purpose':row['purpose'],'request_status':status,
                    'expires_at':iso(min(stamp(row['expires_at']),stamp(row['approval_expires_at']))),
                    'downloaded_at':row['downloaded_at'],'download_url':f'/api/me/deliveries/{row["delivery_id"]}/download'})
            return {'items':result}

    @app.get('/api/internal/deliveries/{delivery_id}/download')
    def download(delivery_id: UUID,request: Request,file_id: UUID | None=None,user=Depends(member)):
        request.state.delivery_id=str(delivery_id)
        with database.connection() as db:
            db.lock()
            raw=db.execute(JOIN+' WHERE d.delivery_id=%s AND d.recipient_id=%s'+('' if database.test else ' FOR UPDATE'),(str(delivery_id),user['user_id'])).fetchone()
            if not raw:
                deny('NOT_FOUND','delivery_missing')
            row=plain(raw)
            request.state.audit_references=[('approval',row['approval_id'])]
            if file_id:
                request.state.audit_references.append(('requested_file',str(file_id)))
            if row['revoked_at']:
                deny('ACCESS_DENIED','approval_revoked')
            consent=disclosure(row)
            approved=fetch_file(db,row['approved_file_id'])
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
            approved_bytes=bytes_for(approved)
            selected=fetch_file(db,file_id) if config['mode']=='baseline' and file_id else approved
            content=bytes_for(selected) if selected['file_id']!=approved['file_id'] else approved_bytes
            db.execute('UPDATE deliveries SET downloaded_at=%s WHERE delivery_id=%s',(now(),str(delivery_id)))
        audit.record(request,'delivery.download',reason='baseline_file_binding_omitted' if config['mode']=='baseline' else None,target=str(delivery_id))
        return Response(content,media_type='application/json' if selected['scan_status']=='passed' else 'text/csv',
                        headers={'Content-Disposition':'attachment; filename="ieum-material"'})

    @app.post('/api/internal/evidence-submissions',status_code=201)
    async def evidence_submit(request: Request,file: UploadFile=File(...),user=Depends(member)):
        content=await file.read(MAX_FILE+1)
        await file.close()
        if not content or len(content)>MAX_FILE or not content.startswith(b'%PDF-'):
            deny('INVALID_REQUEST','invalid_evidence')
        return upstream('POST','/api/evidence-files',request,files={'file':('evidence.pdf',content,'application/pdf')}).json()

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

    from privacygate.web import assert_all_routes_guarded
    assert_all_routes_guarded(app)
    app.state.database,app.state.staff,app.state.member=database,staff,member
    return app
