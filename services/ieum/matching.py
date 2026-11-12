"""Matching business rules; no original evidence, passwords or payment card data."""
import calendar
import json
from contextlib import contextmanager
from datetime import timedelta, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, AwareDatetime

from .core import deny, iso, now, plain, stamp, uid

PLAN={'code':'ieum_6m_5','name':'이음 정회원','amount_won':3300000,
      'credits':5,'months':6,'currency':'KRW','vat_included':True}
SCOPES=('profile','photo','contact','education','employment','income','marital_status')


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')


class Order(Input):
    plan_code: Literal['ieum_6m_5']
    idempotency_key: UUID


class Proposal(Input):
    recipient_member_id: UUID
    idempotency_key: UUID


class Decision(Input):
    action: Literal['accept','reject','cancel','end']


class Sharing(Input):
    scopes: list[Literal['profile','photo','contact','education','employment','income','marital_status']]=Field(min_length=1,max_length=7)
    expires_at: AwareDatetime


class Person(Input):
    member_id: UUID
    user_id: UUID
    pseudo_id: str=Field(min_length=1,max_length=40)
    age_band: str=Field(min_length=1,max_length=30)
    job_group: str=Field(min_length=1,max_length=60)
    introduction: str=Field(min_length=1,max_length=300)
    eligible: bool=Field(strict=True)


class Directory(Input):
    self: Person
    items: list[Person]=Field(max_length=100)


def months_after(date,months):
    number=date.year*12+date.month-1+months
    year,month=divmod(number,12);month+=1
    return date.replace(year=year,month=month,day=min(date.day,calendar.monthrange(year,month)[1]))


def public(person):
    data=person.model_dump(mode='json')
    return {k:data[k] for k in ('member_id','pseudo_id','age_band','job_group','introduction')}


def synthetic_directory(user_id):
    """Explicit lab-only data. No silent fallback if the real internal API fails."""
    people=[]
    for index,(suffix,name,job,intro) in enumerate([
        ('201','M-B01','기획·운영','주말 산책과 차분한 대화를 좋아하는 합성 회원입니다.'),
        ('202','M-C01','연구·개발','책과 새로운 배움을 좋아하는 합성 회원입니다.'),
        ('203','M-D01','교육','여행과 전시를 즐기는 합성 회원입니다.'),
        ('204','M-E01','디자인','일상의 취향을 함께 나누고 싶은 합성 회원입니다.'),
    ],start=1):
        people.append(Person(member_id=f'cccccccc-0000-4000-8000-{index:012d}',
            user_id=f'22222222-2222-4222-8222-222222222{suffix}',pseudo_id=name,
            age_band='30대',job_group=job,introduction=intro,eligible=True))
    current=next((x for x in people if str(x.user_id)==user_id),None)
    if not current:
        deny('ACCESS_DENIED','lab_member_not_registered','실습용 회원 계정으로 로그인해주세요.')
    return Directory(self=current,items=[x for x in people if x!=current])


class Matching:
    def __init__(self,database,clock=now,test_payments=False):
        self.database=database;self.clock=clock;self.test_payments=test_payments

    @contextmanager
    def transaction(self):
        with self.database.connection() as db:
            # Serialize wallet, active pair and transitions across processes.
            # Small lab scale: one PostgreSQL transaction lock, SQLite BEGIN IMMEDIATE in tests.
            if self.database.test:db.lock()
            else:db.execute('SELECT pg_advisory_xact_lock(87341052)')
            timestamp=self.clock()
            expired=db.execute("SELECT * FROM matching_requests WHERE status='pending' AND expires_at<=%s",(timestamp,)).fetchall()
            for raw in expired:
                row=plain(raw)
                db.execute("UPDATE matching_requests SET status='expired',credit_state='released',decided_at=%s WHERE match_id=%s",(timestamp,row['match_id']))
                self.event(db,row,'release',timestamp)
            yield db,timestamp

    def event(self,db,row,kind,timestamp):
        db.execute('INSERT INTO matching_credit_events (event_id,match_id,user_id,event_type,created_at) VALUES (%s,%s,%s,%s,%s)',
            (uid(),row['match_id'],row['sender_id'],kind,timestamp))

    def remember(self,directory):
        with self.database.connection() as db:
            for p in [directory.self,*directory.items]:
                values=p.model_dump(mode='json')
                db.execute('''INSERT INTO matching_profiles (member_id,user_id,pseudo_id,age_band,job_group,introduction,updated_at)
                  VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(member_id) DO UPDATE SET
                  user_id=excluded.user_id,pseudo_id=excluded.pseudo_id,age_band=excluded.age_band,
                  job_group=excluded.job_group,introduction=excluded.introduction,updated_at=excluded.updated_at''',
                  tuple(values[k] for k in ('member_id','user_id','pseudo_id','age_band','job_group','introduction'))+(self.clock(),))

    def wallet(self,db,user_id,timestamp):
        raw=db.execute('SELECT * FROM matching_memberships WHERE user_id=%s AND starts_at<=%s AND expires_at>%s ORDER BY expires_at DESC LIMIT 1',
            (user_id,timestamp,timestamp)).fetchone()
        if not raw:return {'active':False,'total':0,'reserved':0,'used':0,'available':0,'expires_at':None}
        row=plain(raw)
        counts={r['credit_state']:r['n'] for r in db.execute('SELECT credit_state,count(*) AS n FROM matching_requests WHERE membership_id=%s GROUP BY credit_state',(row['membership_id'],))}
        reserved,used=counts.get('reserved',0),counts.get('consumed',0)
        return {'active':True,'membership_id':row['membership_id'],'total':row['credits'],
            'reserved':reserved,'used':used,'available':row['credits']-reserved-used,'expires_at':row['expires_at']}

    def membership(self,user_id):
        with self.transaction() as (db,t):
            orders=[plain(r) for r in db.execute('SELECT order_id,plan_code,amount_won,status,created_at,paid_at FROM matching_orders WHERE user_id=%s ORDER BY created_at DESC LIMIT 10',(user_id,))]
            return {'plan':PLAN,'wallet':self.wallet(db,user_id,t),'orders':orders,'test_payments_enabled':self.test_payments}

    def order(self,user_id,body):
        with self.transaction() as (db,t):
            old=db.execute('SELECT * FROM matching_orders WHERE user_id=%s AND idempotency_key=%s',(user_id,str(body.idempotency_key))).fetchone()
            if old:return plain(old)
            if self.wallet(db,user_id,t)['active']:
                deny('INVALID_STATE','membership_already_active','현재 이용권이 유효합니다. 중복 구매는 지원하지 않습니다.')
            order_id=uid()
            db.execute("INSERT INTO matching_orders (order_id,user_id,idempotency_key,plan_code,amount_won,status,created_at) VALUES (%s,%s,%s,%s,%s,'pending',%s)",
                (order_id,user_id,str(body.idempotency_key),PLAN['code'],PLAN['amount_won'],t))
            return {'order_id':order_id,'plan_code':PLAN['code'],'amount_won':PLAN['amount_won'],'status':'pending','created_at':iso(t)}

    def confirm_test_order(self,user_id,order_id):
        if not self.test_payments:
            deny('ACCESS_DENIED','test_payment_disabled','시험 결제가 비활성화되어 있습니다. 실제 결제는 연동되지 않았습니다.')
        with self.transaction() as (db,t):
            row=db.execute('SELECT * FROM matching_orders WHERE order_id=%s AND user_id=%s',(str(order_id),user_id)).fetchone()
            if not row:deny('NOT_FOUND','order_missing')
            if row['status']=='paid':return {'order_id':str(order_id),'status':'paid','test_payment':True}
            if stamp(row['created_at'])+timedelta(minutes=30)<=t:
                deny('INVALID_STATE','order_expired','주문 유효시간이 지났습니다. 새로 주문해주세요.')
            if self.wallet(db,user_id,t)['active']:deny('INVALID_STATE','membership_already_active')
            db.execute("UPDATE matching_orders SET status='paid',paid_at=%s WHERE order_id=%s",(t,str(order_id)))
            db.execute('INSERT INTO matching_memberships (membership_id,order_id,user_id,credits,starts_at,expires_at) VALUES (%s,%s,%s,%s,%s,%s)',
                (uid(),str(order_id),user_id,PLAN['credits'],t,months_after(t,PLAN['months'])))
            return {'order_id':str(order_id),'status':'paid','test_payment':True}

    def propose(self,user_id,directory,body):
        if not directory.self.eligible:deny('ACCESS_DENIED','member_not_eligible','회원 검토가 완료된 후 신청할 수 있습니다.')
        peer=next((p for p in directory.items if p.member_id==body.recipient_member_id and p.eligible),None)
        if not peer or str(peer.user_id)==user_id:deny('NOT_FOUND','candidate_missing')
        recipient=str(peer.user_id);pair=':'.join(sorted((user_id,recipient)))
        with self.transaction() as (db,t):
            old=db.execute('SELECT * FROM matching_requests WHERE sender_id=%s AND idempotency_key=%s',(user_id,str(body.idempotency_key))).fetchone()
            if old:
                if str(old['recipient_id'])!=recipient:deny('INVALID_STATE','idempotency_conflict')
                return self.serialize(db,plain(old),user_id)
            wallet=self.wallet(db,user_id,t)
            if not wallet['active']:deny('ACCESS_DENIED','paid_membership_required','매칭 신청은 유효한 정회원 이용권이 필요합니다.')
            if wallet['available']<1:deny('INVALID_STATE','credits_exhausted','사용 가능한 매칭권이 없습니다.')
            if db.execute("SELECT match_id FROM matching_requests WHERE pair_key=%s AND status IN ('pending','matched')",(pair,)).fetchone():
                deny('INVALID_STATE','duplicate_pair','해당 상대와 진행 중인 신청 또는 매칭이 있습니다.')
            if db.execute("SELECT match_id FROM matching_requests WHERE pair_key=%s AND status='rejected' AND decided_at>%s",(pair,t-timedelta(days=30))).fetchone():
                deny('INVALID_STATE','rejection_cooldown','거절된 상대에게는 30일 동안 재신청할 수 없습니다.')
            korea=timezone(timedelta(hours=9))
            day=t.astimezone(korea).replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc)
            n=db.execute('SELECT count(*) AS n FROM matching_requests WHERE sender_id=%s AND created_at>=%s',(user_id,day)).fetchone()['n']
            if n>=3:deny('INVALID_STATE','daily_limit','하루에 최대 3건까지 신청할 수 있습니다.')
            n=db.execute("SELECT count(*) AS n FROM matching_requests WHERE sender_id=%s AND status='pending'",(user_id,)).fetchone()['n']
            if n>=3:deny('INVALID_STATE','pending_limit','동시에 대기할 수 있는 신청은 최대 3건입니다.')
            mid=uid()
            db.execute("""INSERT INTO matching_requests (match_id,sender_id,recipient_id,membership_id,idempotency_key,pair_key,status,credit_state,created_at,expires_at)
              VALUES (%s,%s,%s,%s,%s,%s,'pending','reserved',%s,%s)""",
                (mid,user_id,recipient,wallet['membership_id'],str(body.idempotency_key),pair,t,t+timedelta(hours=72)))
            row=plain(db.execute('SELECT * FROM matching_requests WHERE match_id=%s',(mid,)).fetchone())
            self.event(db,row,'reserve',t)
            return self.serialize(db,row,user_id)

    def owned(self,db,match_id,user_id):
        row=db.execute('SELECT * FROM matching_requests WHERE match_id=%s AND (sender_id=%s OR recipient_id=%s)',(str(match_id),user_id,user_id)).fetchone()
        if not row:deny('NOT_FOUND','match_missing')
        return plain(row)

    def serialize(self,db,row,user_id):
        peer=row['recipient_id'] if row['sender_id']==user_id else row['sender_id']
        profile=db.execute('SELECT member_id,pseudo_id,age_band,job_group,introduction FROM matching_profiles WHERE user_id=%s',(peer,)).fetchone()
        result={k:row[k] for k in ('match_id','status','credit_state','created_at','expires_at','decided_at','ended_at')}
        result['direction']='sent' if row['sender_id']==user_id else 'received'
        result['peer']=plain(profile) if profile else {'pseudo_id':'소개 상대'}
        return result

    def list(self,user_id):
        with self.transaction() as (db,t):
            rows=db.execute('SELECT * FROM matching_requests WHERE sender_id=%s OR recipient_id=%s ORDER BY created_at DESC LIMIT 100',(user_id,user_id)).fetchall()
            return {'items':[self.serialize(db,plain(r),user_id) for r in rows]}

    def decide(self,user_id,match_id,action):
        with self.transaction() as (db,t):
            row=self.owned(db,match_id,user_id)
            target={'accept':'matched','reject':'rejected','cancel':'cancelled','end':'ended'}[action]
            if action in ('accept','reject') and user_id!=row['recipient_id']:deny('ACCESS_DENIED','recipient_only')
            if action=='cancel' and user_id!=row['sender_id']:deny('ACCESS_DENIED','sender_only')
            if row['status']==target:return self.serialize(db,row,user_id)
            if (action=='end' and row['status']!='matched') or (action!='end' and row['status']!='pending'):
                deny('INVALID_STATE','match_transition_invalid','현재 상태에서는 처리할 수 없습니다.')
            credit='consumed' if action in ('accept','end') else 'released'
            db.execute('UPDATE matching_requests SET status=%s,credit_state=%s,decided_at=%s,ended_at=%s WHERE match_id=%s',
                (target,credit,t,t if action=='end' else None,str(match_id)))
            if action!='end':self.event(db,row,'consume' if action=='accept' else 'release',t)
            if action=='end':db.execute('UPDATE matching_grants SET revoked_at=%s WHERE match_id=%s AND revoked_at IS NULL',(t,str(match_id)))
            return self.serialize(db,self.owned(db,match_id,user_id),user_id)

    def grants(self,user_id,match_id):
        with self.transaction() as (db,t):
            row=self.owned(db,match_id,user_id)
            own=db.execute('SELECT * FROM matching_grants WHERE match_id=%s AND owner_id=%s ORDER BY created_at DESC LIMIT 1',(str(match_id),user_id)).fetchone()
            def clean(r):
                if not r:return None
                p=plain(r)
                return {'grant_id':p['grant_id'],'scopes':json.loads(p['scopes_json']),
                        'expires_at':p['expires_at'],'revoked_at':p['revoked_at'],
                        'active':row['status']=='matched' and p['revoked_at'] is None and stamp(p['expires_at'])>t}
            return {'match_id':str(match_id),'status':row['status'],'own_grant':clean(own)}

    def share(self,user_id,match_id,body):
        with self.transaction() as (db,t):
            row=self.owned(db,match_id,user_id)
            if row['status']!='matched':deny('ACCESS_DENIED','active_match_required','상호 매칭이 성립한 후 동의를 설정할 수 있습니다.')
            if not t<body.expires_at<=t+timedelta(days=7):deny('INVALID_REQUEST','grant_expiry','동의 기한은 현재 이후 최대 7일입니다.')
            db.execute('UPDATE matching_grants SET revoked_at=%s WHERE match_id=%s AND owner_id=%s AND revoked_at IS NULL',(t,str(match_id),user_id))
            recipient=row['recipient_id'] if user_id==row['sender_id'] else row['sender_id']
            gid=uid()
            db.execute('INSERT INTO matching_grants (grant_id,match_id,owner_id,recipient_id,scopes_json,expires_at,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                (gid,str(match_id),user_id,recipient,json.dumps(sorted(set(body.scopes))),body.expires_at,t))
            return {'grant_id':gid,'scopes':sorted(set(body.scopes)),'expires_at':iso(body.expires_at)}

    def revoke(self,user_id,match_id):
        with self.transaction() as (db,t):
            self.owned(db,match_id,user_id)
            db.execute('UPDATE matching_grants SET revoked_at=%s WHERE match_id=%s AND owner_id=%s AND revoked_at IS NULL',(t,str(match_id),user_id))
            return {'match_id':str(match_id),'revoked':True}

    def disclosure(self,user_id,match_id):
        with self.transaction() as (db,t):
            row=self.owned(db,match_id,user_id)
            if row['status']!='matched':deny('ACCESS_DENIED','active_match_required','상호 매칭이 유효한 경우에만 열람할 수 있습니다.')
            peer=row['recipient_id'] if user_id==row['sender_id'] else row['sender_id']
            grant=db.execute('SELECT * FROM matching_grants WHERE match_id=%s AND owner_id=%s AND recipient_id=%s AND revoked_at IS NULL AND expires_at>%s ORDER BY created_at DESC LIMIT 1',
                (str(match_id),peer,user_id,t)).fetchone()
            if not grant:deny('CONSENT_INVALID','peer_consent_required','상대가 제공 동의를 설정하지 않았거나 동의가 만료·철회되었습니다.')
            p=plain(grant);profile=db.execute('SELECT * FROM matching_profiles WHERE user_id=%s',(peer,)).fetchone()
            if not profile:deny('NOT_FOUND','peer_missing')
            return {'member_id':str(profile['member_id']),'recipient_id':user_id,'grant_id':p['grant_id'],
                    'match_id':str(match_id),'scopes':json.loads(p['scopes_json']),'expires_at':p['expires_at']}
