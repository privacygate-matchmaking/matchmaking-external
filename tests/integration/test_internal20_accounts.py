"""Offline account setup tests. Never connect to Docker or real Keycloak."""
import copy
import importlib.util
import io
import json
from pathlib import Path
from contextlib import redirect_stdout
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('internal20_accounts',ROOT/'scripts/internal20_accounts.py')
setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)

class Response:
    def __init__(self,status,data=None):self.status_code=status;self.data=data;self.content=b'json' if data is not None else b''
    def json(self):return copy.deepcopy(self.data)

class FakeKeycloak:
    def __init__(self,plan):
        self.users={row['user_id']:{'id':row['user_id'],'username':row['legacy_username'],'enabled':True}
                    for row in plan if row['legacy_username']}
        self.roles={uid:{'member'} for uid in self.users}
        self.writes=[];self.imports=[];self.expired_once=False;self.expire_next=False
        self.realm={'editUsernameAllowed':False};self.fail_import=False
    def post(self,path,**kwargs):return Response(200,{'access_token':'private-test-token'})
    def request(self,method,path,**kwargs):
        path=path.removeprefix('/admin/realms/matchmaking').lstrip('/')
        if self.expire_next and not self.expired_once:
            self.expired_once=True;return Response(401)
        if method not in ('GET',):self.writes.append((method,path,kwargs.get('json')))
        if not path:
            if method=='GET':return Response(200,self.realm)
            self.realm.update(kwargs['json']);return Response(204)
        if path=='roles/member':return Response(200,{'id':'role-member','name':'member'})
        if path=='users':
            name=kwargs['params']['username'];return Response(200,[u for u in self.users.values() if u['username']==name])
        if path=='partialImport':
            if self.fail_import:return Response(500,{'private':'not-for-output'})
            self.imports.append(kwargs['json'])
            for user in kwargs['json']['users']:
                self.users[user['id']]={k:v for k,v in user.items() if k!='credentials'}
                self.roles[user['id']]=set()
            return Response(200,{'added':1})
        parts=path.split('/');uid=parts[1]
        if len(parts)==2:
            if method=='GET':return Response(200,self.users[uid]) if uid in self.users else Response(404)
            if not self.realm['editUsernameAllowed']:return Response(400)
            self.users[uid].update(kwargs['json']);return Response(204)
        if method=='POST':self.roles[uid].update(r['name'] for r in kwargs['json']);return Response(204)
        return Response(200,[{'name':r} for r in self.roles[uid]])

class AccountsTest(unittest.TestCase):
    def setUp(self):
        self.plan=setup.load_plan();self.client=FakeKeycloak(self.plan)
        self.admin=setup.Admin(self.client,'infra_admin','private-admin-password')
    def apply(self):
        role,existing=self.admin.preflight(self.plan)
        with redirect_stdout(io.StringIO()) as output:
            self.admin.apply(self.plan,role,existing,'private-test-password')
        self.assertNotIn('private-',output.getvalue())
    def test_plan_has_twenty_distinct_members_and_preserves_legacy_ids(self):
        self.assertEqual(len({x['member_id'] for x in self.plan}),20)
        self.assertEqual(len({x['user_id'] for x in self.plan}),20)
        self.assertEqual(self.plan[2]['user_id'],'22222222-2222-4222-8222-222222222201')
        self.assertEqual(self.plan[0]['legacy_username'],'member_c')
    def test_rename_preserves_ids_and_existing_passwords(self):
        ids=set(self.client.users);self.apply()
        self.assertEqual(len(self.client.users),20)
        self.assertTrue(ids<=set(self.client.users))
        self.assertEqual(len(self.client.imports),16)
        for row in self.plan:self.assertEqual(self.client.users[row['user_id']]['username'],row['username'])
        for method,path,body in self.client.writes:
            if method=='PUT':self.assertNotIn('credentials',body)
        self.assertFalse(any('/reset-password' in path for _,path,_ in self.client.writes))
    def test_rerun_does_not_create_more_users_or_reset_passwords(self):
        self.apply();self.client.writes.clear();self.client.imports.clear();self.apply()
        self.assertEqual(len(self.client.users),20)
        self.assertEqual(self.client.imports,[])
        self.assertFalse(any(method=='PUT' for method,_,_ in self.client.writes))
    def test_last_username_collision_blocks_all_changes(self):
        self.client.users['wrong-uuid']={'id':'wrong-uuid','username':'member_020','enabled':True}
        with self.assertRaisesRegex(RuntimeError,'different UUID'):self.admin.preflight(self.plan)
        self.assertEqual(self.client.writes,[])
    def test_staff_account_is_not_renamed_or_given_member_role(self):
        self.client.roles[self.plan[0]['user_id']].add('counselor')
        with self.assertRaisesRegex(RuntimeError,'staff account'):self.admin.preflight(self.plan)
        self.assertEqual(self.client.writes,[])
    def test_disabled_account_is_not_reactivated(self):
        self.client.users[self.plan[0]['user_id']]['enabled']=False
        with self.assertRaisesRegex(RuntimeError,'disabled'):self.admin.preflight(self.plan)
        self.assertEqual(self.client.writes,[])
    def test_unexpected_username_does_not_get_overwritten(self):
        self.client.users[self.plan[0]['user_id']]['username']='unrelated-account'
        with self.assertRaisesRegex(RuntimeError,'Unexpected existing username'):self.admin.preflight(self.plan)
        self.assertEqual(self.client.writes,[])
    def test_expired_admin_token_is_renewed_once(self):
        self.client.expire_next=True;self.apply();self.assertTrue(self.client.expired_once)
    def test_username_edit_setting_is_restored_after_success(self):
        self.apply()
        self.assertFalse(self.client.realm['editUsernameAllowed'])
        updates=[body for method,path,body in self.client.writes if path=='']
        self.assertEqual(updates,[{'editUsernameAllowed':True},{'editUsernameAllowed':False}])
    def test_username_edit_setting_is_restored_after_failure(self):
        self.client.fail_import=True
        with self.assertRaises(RuntimeError):self.apply()
        self.assertFalse(self.client.realm['editUsernameAllowed'])
    def test_existing_username_edit_preference_is_preserved(self):
        self.client.realm['editUsernameAllowed']=True
        self.apply()
        self.assertTrue(self.client.realm['editUsernameAllowed'])
        self.assertFalse(any(path=='' for _,path,_ in self.client.writes))
    def test_sql_mapping_matches_plan_and_detects_drift(self):
        sql=(ROOT/'infra/lab/internal20-accounts.sql').read_text(encoding='utf-8')
        setup.validate_sql(self.plan,sql)
        with self.assertRaisesRegex(RuntimeError,'mapping differ'):
            setup.validate_sql(self.plan,sql.replace("'member_020'","'incorrect'"))
        self.assertNotIn('UPDATE consents',sql)
        self.assertNotIn('UPDATE matching_',sql)
        self.assertNotIn('DELETE FROM',sql)
    def setup_context(self):
        client=self.client
        class Context:
            def __enter__(self):return client
            def __exit__(self,*args):return False
        return Context()
    def test_db_apply_failure_resumes_apis_without_deleting_accounts(self):
        calls=[];db_calls=[]
        def sql_runner(sql):
            db_calls.append(sql)
            if len(db_calls)==3:raise RuntimeError('test-db-apply-failed')
        fake_httpx=SimpleNamespace(Client=lambda **kw:self.setup_context())
        with patch.dict('sys.modules',{'httpx':fake_httpx}), patch.dict(setup.os.environ,{},clear=True), \
             patch.object(setup,'read_env',return_value={'KC_BOOTSTRAP_ADMIN_PASSWORD':'test','KC_TEST_PASSWORD':'test'}), \
             patch.object(setup.ssl,'create_default_context',return_value=SimpleNamespace(load_verify_locations=lambda **kw:None)), \
             patch.object(setup,'db',side_effect=sql_runner), \
             patch.object(setup,'compose',side_effect=lambda *a,**kw:calls.append(a)), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'test-db-apply-failed'):setup.setup()
        self.assertEqual(calls,[('stop','internal-api','external-api','transfer-api'),
                                ('start','internal-api','external-api','transfer-api')])
        self.assertEqual(len(self.client.users),20)
        self.assertFalse(any(method=='DELETE' for method,_,_ in self.client.writes))
    def test_db_preflight_failure_does_not_stop_apis_or_mutate_keycloak(self):
        calls=[];fake_httpx=SimpleNamespace(Client=lambda **kw:self.setup_context())
        with patch.dict('sys.modules',{'httpx':fake_httpx}), patch.dict(setup.os.environ,{},clear=True), \
             patch.object(setup,'read_env',return_value={'KC_BOOTSTRAP_ADMIN_PASSWORD':'test','KC_TEST_PASSWORD':'test'}), \
             patch.object(setup.ssl,'create_default_context',return_value=SimpleNamespace(load_verify_locations=lambda **kw:None)), \
             patch.object(setup,'db',side_effect=RuntimeError('test-preflight-failed')), \
             patch.object(setup,'compose',side_effect=lambda *a,**kw:calls.append(a)), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'test-preflight-failed'):setup.setup()
        self.assertEqual(calls,[])
        self.assertEqual(self.client.writes,[])
    def test_db_uses_transaction_and_explicit_stdin_file(self):
        calls=[];original=setup.compose
        setup.compose=lambda *args,**kwargs:calls.append((args,kwargs))
        try:setup.db('test-sql')
        finally:setup.compose=original
        args,kw=calls[0]
        self.assertIn('--single-transaction',args)
        self.assertEqual(args[-2:],('-f','-'))
        self.assertEqual(kw['input'],'test-sql')

if __name__=='__main__':unittest.main()
