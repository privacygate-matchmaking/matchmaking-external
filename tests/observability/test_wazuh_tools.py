"""Offline safety checks. Actual Wazuh rule-engine and collection tests are separate."""
import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

ROOT=Path(__file__).resolve().parents[2]


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


tools=module('ieum_wazuh_tools','scripts/wazuh.py')
maintenance=module('ieum_wazuh_maintenance','infra/wazuh/maintenance.py')


def test_ca_upgrade_preserves_key_identity_and_leaf_trust(tmp_path,monkeypatch):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa,padding
    from cryptography.x509.oid import NameOID
    from datetime import datetime,timedelta,timezone
    monkeypatch.setattr(tools,'HOME',tmp_path)
    certdir=tmp_path/'config/wazuh_indexer_ssl_certs';certdir.mkdir(parents=True)
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'test local CA')])
    now=datetime.now(timezone.utc)
    builder=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(12).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=30)))
    ca=builder.add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).sign(key,hashes.SHA256())
    leaf=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'server')]))
        .issuer_name(name).public_key(key.public_key()).serial_number(13)
        .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1)).sign(key,hashes.SHA256()))
    encoded=ca.public_bytes(serialization.Encoding.PEM)
    for filename in ('root-ca.pem','root-ca-manager.pem'):(certdir/filename).write_bytes(encoded)
    secret=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    (certdir/'root-ca.key').write_bytes(secret)
    tools.ensure_ca_key_usage()
    saved=(certdir/'root-ca.pem').read_bytes()
    upgraded=x509.load_pem_x509_certificate(saved)
    usage=upgraded.extensions.get_extension_for_class(x509.KeyUsage)
    assert usage.critical and usage.value.key_cert_sign and usage.value.crl_sign
    assert upgraded.subject==ca.subject and upgraded.serial_number==ca.serial_number
    assert upgraded.not_valid_after_utc==ca.not_valid_after_utc
    upgraded.public_key().verify(leaf.signature,leaf.tbs_certificate_bytes,padding.PKCS1v15(),leaf.signature_hash_algorithm)
    upgraded.public_key().verify(upgraded.signature,upgraded.tbs_certificate_bytes,padding.PKCS1v15(),upgraded.signature_hash_algorithm)
    assert (certdir/'root-ca.key').read_bytes()==secret
    assert (certdir/'root-ca-manager.pem').read_bytes()==saved
    tools.ensure_ca_key_usage()
    assert (certdir/'root-ca.pem').read_bytes()==saved


def test_rule_runner_reads_stderr_and_ansi_output(monkeypatch,capsys):
    ids=[rid for _,rid in tools.cases()]
    result=type('Result',(),{'stdout':'','stderr':'\n'.join("\x1b[32mid: '"+rid+"'\x1b[0m" for rid in ids)})()
    monkeypatch.setattr(tools,'compose',lambda *a,**kw:result)
    tools.rules()
    assert 'logtest passed' in capsys.readouterr().out


def test_rule_runner_exposes_engine_loading_errors(monkeypatch,capsys):
    result=type('Result',(),{'stdout':'','stderr':'Error loading the rules: ieum_rules.xml'})()
    monkeypatch.setattr(tools,'compose',lambda *a,**kw:result)
    with pytest.raises(RuntimeError,match='rule-engine test failed'):tools.rules()
    assert 'Error loading the rules' in capsys.readouterr().out


@pytest.mark.skipif(tools.os.name!='nt',reason='Windows container bind-mount read-only attributes')
def test_certificate_update_restores_windows_readonly_attribute(tmp_path):
    target=tmp_path/'dashboard.pem'
    target.write_bytes(b'old')
    target.chmod(tools.stat.S_IREAD)
    try:
        tools.replace_certificate_file(target,b'updated')
        assert target.read_bytes()==b'updated'
        assert not target.stat().st_mode & tools.stat.S_IWRITE
    finally:
        target.chmod(tools.stat.S_IREAD | tools.stat.S_IWRITE)


def row(name,size):return {'index':name,'store.size':str(size)}


def test_quota_never_deletes_other_data():
    rows=[row('wazuh-alerts-4.x-2026.11.10',80),row('business-db',100000),
          row('wazuh-states-vulnerabilities-wazuh',100000)]
    assert maintenance.retirement(rows,'2026.11.12',100)==([],[])


def test_quota_removes_oldest_completed_day_only():
    rows=[row('wazuh-alerts-4.x-2026.11.10',80),row('wazuh-alerts-4.x-2026.11.11',50),
          row('wazuh-alerts-4.x-2026.11.12',20)]
    assert maintenance.retirement(rows,'2026.11.12',100)==(['wazuh-alerts-4.x-2026.11.10'],[])


def test_current_day_is_blocked_not_deleted_if_it_exceeds_budget():
    current='wazuh-alerts-4.x-2026.11.12'
    assert maintenance.retirement([row(current,120)],'2026.11.12',100)==([],[current])


def test_seven_day_policy_is_narrowly_scoped():
    policy=maintenance.policy()['policy']
    assert policy['ism_template'][0]['index_patterns']==['wazuh-alerts-4.x-*']
    assert policy['states'][0]['transitions'][0]['conditions']['min_index_age']=='7d'


def test_probe_rejects_wrong_rules_even_when_all_events_are_indexed(monkeypatch,capsys):
    monkeypatch.setattr(maintenance,'request',lambda *a,**kw:{'hits':{'hits':[
        {'_source':{'data':{'trace_id':'test-third'},'rule':{'id':'100110'}}}]}})
    assert not maintenance.probe({'test-third':'100112'},attempts=1)
    output=capsys.readouterr().out
    assert '"missing_events": 0' in output
    assert '"expected": "100112", "actual": "100110"' in output


def test_probe_retries_missing_events_and_accepts_exact_rules(monkeypatch):
    results=iter([{}, {'hits':{'hits':[
        {'_source':{'data':{'trace_id':'test-third'},'rule':{'id':'100112'}}}]}}])
    monkeypatch.setattr(maintenance,'request',lambda *a,**kw:next(results))
    monkeypatch.setattr(maintenance.time,'sleep',lambda _:None)
    assert maintenance.probe({'test-third':'100112'},attempts=2)


def test_probe_reports_query_failure_without_exception_secrets(monkeypatch,capsys):
    def fail(*a,**kw):raise RuntimeError('secret-response-body')
    monkeypatch.setattr(maintenance,'request',fail)
    assert not maintenance.probe({'test-third':'100112'},attempts=1)
    output=capsys.readouterr().out
    assert 'RuntimeError' in output and 'secret-response-body' not in output


def test_low_disk_blocks_before_any_docker_operation(monkeypatch):
    monkeypatch.setattr(tools.shutil,'disk_usage',lambda p:type('Disk',(),{'free':10*1024**3})())
    monkeypatch.setattr(tools,'docker',lambda *a,**kw:pytest.fail('Must not contact/pull Docker when disk is low'))
    with pytest.raises(RuntimeError,match='No image pull'):tools.preflight(ROOT)


@pytest.mark.parametrize('cached',[True,False])
def test_post_download_reserve_requires_all_images(monkeypatch,cached):
    monkeypatch.setattr(tools.shutil,'disk_usage',lambda p:type('Disk',(),{'free':45*1024**3})())
    calls=[]
    def fake_docker(*args,**kwargs):
        calls.append(args)
        if args[:2]==('image','inspect'):
            assert list(args[2:])==tools.required_images()
            if not cached:raise tools.subprocess.CalledProcessError(1,args)
            return type('Result',(),{'stdout':'[]'})()
        assert args[0]=='info'
        return type('Result',(),{'stdout':json.dumps({'MemTotal':10*1024**3,'NCPU':18})})()
    monkeypatch.setattr(tools,'docker',fake_docker)
    if cached:tools.preflight(ROOT)
    else:
        with pytest.raises(RuntimeError,match='initial installation'):tools.preflight(ROOT)
        assert len(calls)==1


@pytest.mark.parametrize('memory,cpus',[(4*1024**3,4),(8*1024**3,2)])
def test_insufficient_docker_allocation_blocks_start(monkeypatch,memory,cpus):
    monkeypatch.setattr(tools.shutil,'disk_usage',lambda p:type('Disk',(),{'free':60*1024**3})())
    monkeypatch.setattr(tools,'docker',lambda *a,**kw:type('Info',(),{'stdout':json.dumps({'MemTotal':memory,'NCPU':cpus})})())
    with pytest.raises(RuntimeError,match='8 GiB'):tools.preflight(ROOT)


def test_changed_upstream_stops_instead_of_running_unknown_config(tmp_path,monkeypatch):
    monkeypatch.setattr(tools,'ROOT',tmp_path)
    bad=tmp_path/'.local/wazuh-upstream/docker-compose.yml'
    bad.parent.mkdir(parents=True);bad.write_text('modified source')
    with pytest.raises(RuntimeError,match='checksum mismatch'):tools.upstream('docker-compose.yml')


def test_fixtures_have_distinct_requests_and_are_always_marked():
    values=tools.cases()
    assert len({e['trace_id'] for e,_ in values})==len(values)
    assert all(e['lab_fixture']=='true' for e,_ in values)
    assert [expected for _,expected in values[:3]]==['100110','100110','100112']


def test_repetition_is_limited_to_authoritative_admin_denials():
    tree=ET.parse(ROOT/'infra/wazuh/ieum_rules.xml')
    base=tree.find("rule[@id='100110']");repeat=tree.find("rule[@id='100112']")
    assert base.find("field[@name='service']").text=='^internal-api$'
    assert repeat.find('if_sid').text==repeat.find('if_matched_sid').text=='100110'
    assert repeat.find('different_field').text=='trace_id'
    assert repeat.attrib['frequency']=='3' and repeat.attrib['timeframe']=='60'


def test_rules_use_dedicated_static_action_conditions():
    tree=ET.parse(ROOT/'infra/wazuh/ieum_rules.xml')
    assert tree.find(".//field[@name='action']") is None
    assert tree.find("rule[@id='100110']/action").text=='^POST /api/admin/member-exports$'
    assert tree.find("rule[@id='100131']/action").attrib['type']=='pcre2'
    for field in tree.findall('.//field'):
        if '(' in field.text:assert field.attrib.get('type')=='pcre2'


def test_prepare_preserves_secrets_and_exposes_only_loopback_dashboard(tmp_path,monkeypatch):
    import bcrypt,yaml
    monkeypatch.setattr(tools,'HOME',tmp_path/'output')
    minimal={
        'docker-compose.yml':yaml.safe_dump({'services':{name:{'image':'test',
            'environment':['INDEXER_PASSWORD=old','API_PASSWORD=old'],
            'ports':['55000:55000'],'volumes':[]} for name in ('wazuh.manager','wazuh.indexer','wazuh.dashboard')},'volumes':{}}),
        'config/wazuh_cluster/wazuh_manager.conf':'<ossec_config><active-response/><localfile><command>old</command></localfile><global/><auth><disabled>no</disabled></auth></ossec_config>',
        'config/wazuh_indexer/wazuh.indexer.yml':'network.host: 0.0.0.0',
        'config/wazuh_dashboard/opensearch_dashboards.yml':'server.port: 5601',
        'config/certs.yml':'nodes: {}'}
    monkeypatch.setattr(tools,'upstream',lambda name:minimal[name])
    tools.prepare();saved=(tools.HOME/'credentials.json').read_bytes()
    tools.prepare();assert (tools.HOME/'credentials.json').read_bytes()==saved
    config=yaml.safe_load((tools.HOME/'docker-compose.yml').read_text())
    assert config['services']['wazuh.dashboard']['ports']==['127.0.0.1:8444:5601']
    assert config['services']['certs']['environment']=={'CERT_TOOL_VERSION':'4.14','CERT_TOOL_REPOSITORY':'prod'}
    assert config['services']['certs']['network_mode']=='bridge'
    assert config['networks']['default']['internal'] is True
    assert config['services']['wazuh.dashboard']['networks']==['default','dashboard-access']
    assert all('dashboard-access' not in service.get('networks',[]) for name,service in config['services'].items() if name!='wazuh.dashboard')
    assert all(not service.get('ports') for name,service in config['services'].items() if name!='wazuh.dashboard')
    assert 'audit-source:/var/log/matchmaking:ro' in config['services']['wazuh.manager']['volumes']
    assert './config/ieum_rules.xml:/var/ossec/etc/rules/ieum_rules.xml:ro' in config['services']['wazuh.manager']['volumes']
    assert not any('/wazuh-config-mount/etc/rules/' in v for v in config['services']['wazuh.manager']['volumes'])
    assert config['volumes']['audit-source']['external'] is True
    assert not any('docker.sock' in v for s in config['services'].values() for v in s.get('volumes',[]))
    tree=ET.parse(tools.HOME/'config/wazuh_cluster/wazuh_manager.conf')
    assert tree.find('active-response') is None
    assert tree.find('auth/disabled').text=='yes'
    assert tree.find('remote/local_ip').text=='127.0.0.1'
    assert tree.find('remote/connection').text=='secure'
    assert config['services']['smoke-init']['network_mode']=='none'
    assert 'wazuh_var_multigroups:/multigroups' in config['services']['smoke-init']['volumes']
    keys=json.loads(saved);users=yaml.safe_load((tools.HOME/'config/wazuh_indexer/internal_users.yml').read_text())
    assert set(users)=={'_meta','admin','kibanaserver'}
    assert bcrypt.checkpw(keys['INDEXER_PASSWORD'].encode(),users['admin']['hash'].encode())
