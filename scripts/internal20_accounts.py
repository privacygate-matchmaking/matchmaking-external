"""Explicit local seed-20 account setup. No passwords/tokens are printed or stored.
Keycloak Admin REST: https://www.keycloak.org/docs-api/26.0.8/rest-api/index.html
"""
import argparse
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
from urllib.parse import urlsplit
from uuid import UUID

ROOT=Path(__file__).resolve().parents[1]
APPLY_MARKER='-- APPLY ACCOUNT LINKS'
STAFF_ROLES={'counselor','approver','data_admin'}


def load_plan():
    plan=json.loads((ROOT/'infra/lab/internal20-account-plan.json').read_text(encoding='utf-8'))
    if len(plan)!=20:raise RuntimeError('Account plan must contain exactly 20 members.')
    for i,row in enumerate(plan,1):
        if row['username']!=f'member_{i:03d}' or row['member_code']!=f'A-{i:03d}':
            raise RuntimeError('Account plan does not match seed member codes.')
        if str(UUID(row['member_id']))!=f'bbbbbbbb-0000-4000-8000-{i:012d}':
            raise RuntimeError('Unexpected member UUID in account plan.')
        suffix={1:202,2:203,3:201,4:204}.get(i,200+i)
        if str(UUID(row['user_id']))!=f'22222222-2222-4222-8222-222222222{suffix}':
            raise RuntimeError('Unexpected login UUID in account plan.')
        old=row.get('legacy_username')
        if old and not re.fullmatch('member_[bcde]',old):raise RuntimeError('Unexpected legacy username.')
    if len({row['user_id'] for row in plan})!=20:raise RuntimeError('Duplicate login UUID in account plan.')
    return plan


def read_env(path):
    values={}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        key,sep,value=line.strip().partition('=')
        if sep and not key.startswith('#'):values[key]=value.strip().strip('"\'')
    return values


class Admin:
    def __init__(self,client,username,password):
        self.client=client;self.username=username;self.password=password;self.token=None

    def login(self):
        r=self.client.post('/realms/master/protocol/openid-connect/token',data={
            'grant_type':'password','client_id':'admin-cli','username':self.username,'password':self.password})
        if r.status_code!=200:raise RuntimeError(f'Keycloak administrator authentication failed (HTTP {r.status_code}).')
        self.token=r.json()['access_token']

    def request(self,method,path,allow_missing=False,**kwargs):
        if self.token is None:self.login()
        for attempt in range(2):
            r=self.client.request(method,'/admin/realms/matchmaking'+('/'+path if path else ''),
                headers={'Authorization':'Bearer '+self.token},**kwargs)
            if r.status_code==401 and attempt==0:self.login();continue
            if r.status_code==404 and allow_missing:return None
            if r.status_code>=400:raise RuntimeError(f'Keycloak account operation failed (HTTP {r.status_code}); no credentials printed.')
            return r.json() if r.content else None
        raise RuntimeError('Keycloak account authentication expired.')

    def preflight(self,plan):
        role=self.request('GET','roles/member')
        existing={}
        for row in plan:
            uid=row['user_id'];name=row['username']
            owners=self.request('GET','users',params={'username':name,'exact':'true'})
            if any(u['id']!=uid for u in owners):raise RuntimeError('Username belongs to a different UUID: '+name)
            user=self.request('GET','users/'+uid,allow_missing=True)
            if user:
                if user.get('username') not in (name,row.get('legacy_username')):
                    raise RuntimeError('Unexpected existing username for '+name)
                if user.get('enabled') is not True:raise RuntimeError('Account disabled; not reactivated: '+name)
                roles=self.request('GET',f'users/{uid}/role-mappings/realm/composite')
                if STAFF_ROLES & {r['name'] for r in roles}:
                    raise RuntimeError('Refusing to change a staff account: '+name)
                existing[uid]=user
        return role,existing

    def apply(self,plan,role,existing,password):
        # Keycloak 26.0 username mutation obeys this realm setting even for API updates.
        # Temporarily allow it only if required; restore the exact original value.
        renaming=any(user['username']!=row['username'] for row in plan
                     if (user:=existing.get(row['user_id'])))
        previous=self.request('GET','').get('editUsernameAllowed',False)
        if type(previous) is not bool:raise RuntimeError('Unexpected realm username-edit setting.')
        restore=renaming and not previous
        try:
            if restore:self.request('PUT','',json={'editUsernameAllowed':True})
            self._apply(plan,role,existing,password)
        finally:
            if restore:
                try:self.request('PUT','',json={'editUsernameAllowed':previous})
                except Exception:
                    raise RuntimeError('Could not restore editUsernameAllowed=false. Restore it in the local Keycloak realm settings before continuing.') from None

    def _apply(self,plan,role,existing,password):
        for row in plan:
            uid=row['user_id'];name=row['username'];user=existing.get(uid)
            if not user:
                self.request('POST','partialImport',json={'ifResourceExists':'FAIL','users':[{
                    'id':uid,'username':name,'enabled':True,'email':name+'@example.test','emailVerified':True,
                    'firstName':'Synthetic','lastName':name,
                    'credentials':[{'type':'password','value':password,'temporary':False}]}]})
            elif user['username']!=name:
                latest=self.request('GET','users/'+uid)
                if latest.get('enabled') is not True or latest.get('username') not in (name,row.get('legacy_username')):
                    raise RuntimeError('Account changed during setup: '+name)
                roles=self.request('GET',f'users/{uid}/role-mappings/realm/composite')
                if STAFF_ROLES & {r['name'] for r in roles}:raise RuntimeError('Refusing to rename a staff account: '+name)
                payload={k:latest[k] for k in ('firstName','lastName') if k in latest}
                self.request('PUT','users/'+uid,json={**payload,'username':name,'email':name+'@example.test','emailVerified':True})
            verified=self.request('GET','users/'+uid)
            if verified.get('id')!=uid or verified.get('username')!=name:
                raise RuntimeError('Created/renamed account UUID verification failed: '+name)
            if verified.get('enabled') is not True:
                raise RuntimeError('Account is disabled; not reactivated: '+name)
            roles=self.request('GET',f'users/{uid}/role-mappings/realm/composite')
            if STAFF_ROLES & {r['name'] for r in roles}:
                raise RuntimeError('Refusing to grant member role to a staff account: '+name)
            self.request('POST',f'users/{uid}/role-mappings/realm',json=[role])
            roles=self.request('GET',f'users/{uid}/role-mappings/realm/composite')
            if 'member' not in {r['name'] for r in roles}:raise RuntimeError('Member role verification failed: '+name)
            print('Login account ready: '+name,flush=True)
        # Passwords of all previously existing accounts are preserved.


def compose(*args,input=None):
    command=['docker','compose','-p','matchmaking-20','--project-directory',str(ROOT.parent/'privacygate'),
        '--env-file',str(ROOT.parent/'privacygate/.env'),'--env-file',str(ROOT/'.local/internal-admin.env'),
        '-f',str(ROOT.parent/'privacygate/compose.yaml'),'-f',str(ROOT/'compose.external.yaml'),
        '-f',str(ROOT/'compose.internal20-lab.yaml'),'--profile','external',*args]
    return subprocess.run(command,input=input,text=True,encoding='utf-8',check=True,cwd=ROOT)


def db(sql):
    compose('exec','-T','business-db','psql','-U','db_owner','-d','matchmaking_business',
        '-v','ON_ERROR_STOP=1','--single-transaction','-q','-f','-',input=sql)


def validate_sql(plan,sql):
    values=[]
    for row in plan:
        old="'"+row['legacy_username']+"'" if row['legacy_username'] else 'NULL'
        values.append("('%s'::uuid,'%s'::uuid,'%s','%s',%s)" %
            (row['member_id'],row['user_id'],row['member_code'],row['username'],old))
    data=sql.partition('INSERT INTO internal20_targets VALUES\n')[2].partition(';\nLOCK TABLE')[0]
    if data!=',\n'.join(values) or sql.count(APPLY_MARKER)!=1:
        raise RuntimeError('Account plan and SQL mapping differ; no changes applied.')


def setup():
    import httpx
    plan=load_plan();env=read_env(ROOT.parent/'privacygate/.env')
    def setting(name,default=None):return os.environ.get(name,env.get(name,default))
    base=setting('KC_PUBLIC_URL','https://localhost:8081').rstrip('/')
    url=urlsplit(base)
    if url.scheme!='https' or url.hostname not in ('localhost','127.0.0.1','::1') or url.path:
        raise RuntimeError('Account setup only permits a local HTTPS Keycloak URL.')
    password=setting('KC_BOOTSTRAP_ADMIN_PASSWORD');test_password=setting('KC_TEST_PASSWORD')
    if not password or not test_password:raise RuntimeError('Local Keycloak passwords are not configured.')
    context=ssl.create_default_context()
    context.load_verify_locations(cafile=str(ROOT.parent/'privacygate/infra/secrets/tls/cert.pem'))
    sql=(ROOT/'infra/lab/internal20-accounts.sql').read_text(encoding='utf-8')
    validate_sql(plan,sql)
    with httpx.Client(base_url=base,verify=context,trust_env=False,timeout=15) as client:
        admin=Admin(client,setting('KC_BOOTSTRAP_ADMIN_USERNAME','infra_admin'),password)
        # Validate all 20 accounts before making the first Keycloak change.
        print('Checking all 20 Keycloak identities and current DB links (no account changes yet).',flush=True)
        role,existing=admin.preflight(plan)
        db(sql.split(APPLY_MARKER)[0])
        compose('stop','internal-api','external-api','transfer-api')
        try:
            # Recheck the DB after writers stop. SQL always validates again during apply.
            db(sql.split(APPLY_MARKER)[0])
            admin.apply(plan,role,existing,test_password)
            print('Updating internal account labels/links; member data count remains 20.',flush=True)
            db(sql)
            print('All 20 member logins linked. Existing UUIDs, passwords, evidence and matching history preserved.')
        finally:
            # A failed step can be rerun; never delete accounts or reset the data to recover.
            compose('start','internal-api','external-api','transfer-api')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    if not args.apply:
        for row in load_plan():print(row['member_code']+' -> '+row['username'])
        print('Preview only. Use --apply to prepare Keycloak and DB account links.')
        return
    try:setup()
    except Exception as exc:
        if isinstance(exc,RuntimeError):print(str(exc),file=sys.stderr)
        elif isinstance(exc,subprocess.CalledProcessError):print('Docker/SQL operation failed; inspect the preceding output. Existing data was not reset.',file=sys.stderr)
        elif isinstance(exc,ImportError):print('Install the project dependencies in .venv (httpx required).',file=sys.stderr)
        else:print('Account setup failed ('+type(exc).__name__+'). No credentials printed; inspect local TLS/connectivity and rerun.',file=sys.stderr)
        sys.exit(1)


if __name__=='__main__':main()
