"""Local opt-in SIEM lifecycle. No business repo edits, remote ports or automatic response."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
HOME=ROOT/'.local/wazuh'
SOURCE=ROOT/'infra/wazuh'
MANIFEST=json.loads((SOURCE/'upstream.json').read_text())
VERSION=MANIFEST['version']


def docker(*args, input=None, capture=False):
    return subprocess.run(['docker',*map(str,args)],input=input,text=True,
        encoding='utf-8',capture_output=capture,check=True)


def compose(*args,**kw):
    return docker('compose','-p','ieum-wazuh','--project-directory',HOME,
        '--env-file',HOME/'credentials.env','-f',HOME/'docker-compose.yml',*args,**kw)


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(value,encoding='utf-8',newline='\n')


def upstream(name):
    cache=ROOT/'.local/wazuh-upstream'/name
    data=cache.read_bytes() if cache.exists() else urlopen(MANIFEST['base']+name,timeout=30).read()
    if hashlib.sha256(data).hexdigest()!=MANIFEST['sha256'][name]:
        raise RuntimeError('Official source checksum mismatch: '+name)
    return data.decode('utf-8')


def prepare():
    import bcrypt
    import yaml
    HOME.mkdir(parents=True,exist_ok=True)
    os.chmod(HOME,0o700)
    credentials=HOME/'credentials.json'
    if credentials.exists():keys=json.loads(credentials.read_text())
    else:
        keys={k:secrets.token_urlsafe(30)+'aA1!' for k in ('INDEXER_PASSWORD','DASHBOARD_PASSWORD','API_PASSWORD')}
        write(credentials,json.dumps(keys));os.chmod(credentials,0o600)
    write(HOME/'credentials.env',''.join(k+'='+v+'\n' for k,v in keys.items()))
    os.chmod(HOME/'credentials.env',0o600)
    for name in MANIFEST['sha256']:
        write(HOME/name,upstream(name))
    data=yaml.safe_load((HOME/'docker-compose.yml').read_text())
    services=data['services']
    for name,service in services.items():
        service.pop('ports',None)
        service['restart']='no'
        service['logging']={'driver':'json-file','options':{'max-size':'10m','max-file':'3'}}
        service['environment']={k:v.strip('"') for k,v in (s.split('=',1) for s in service.get('environment',[]))}
        for key in keys:
            if key in service['environment']:service['environment'][key]='${'+key+':?}'
    manager=services['wazuh.manager'];indexer=services['wazuh.indexer'];dash=services['wazuh.dashboard']
    manager['mem_limit']='2g';indexer['mem_limit']='3g';dash['mem_limit']='1536m'
    manager['volumes'] += ['audit-source:/var/log/matchmaking:ro',
        './config/ieum_rules.xml:/var/ossec/etc/rules/ieum_rules.xml:ro',
        'ieum-smoke:/var/log/ieum-tests']
    manager['depends_on']={'smoke-init':{'condition':'service_completed_successfully'}}
    dash['ports']=['127.0.0.1:8444:5601']
    data['volumes']['audit-source']={'external':True,'name':'${IEUM_AUDIT_VOLUME:-matchmaking_audit-logs}'}
    data['volumes']['ieum-smoke']={}
    data['volumes'].setdefault('wazuh_var_multigroups',{})
    data['networks']={'default':{'internal':True},'dashboard-access':{}}
    dash['networks']=['default','dashboard-access']
    services['smoke-init']={'image':'python:3.13-alpine','restart':'no','network_mode':'none',
        # The upstream init tries to restore empty volumes from data_tmp, which
        # it deletes after first boot. Keep this agent-free cache nonempty.
        'entrypoint':['python','-c',"from pathlib import Path;Path('/smoke/smoke.json').touch();Path('/multigroups/.ieum-bootstrap').mkdir(exist_ok=True)"],
        'volumes':['ieum-smoke:/smoke','wazuh_var_multigroups:/multigroups']}
    services['ieum-retention']={'image':'python:3.13-alpine','restart':'no','mem_limit':'128m',
        'environment':{'INDEXER_PASSWORD':'${INDEXER_PASSWORD:?}'},
        'entrypoint':['python','/app/maintenance.py'],
        'volumes':['./maintenance.py:/app/maintenance.py:ro',
            './config/wazuh_indexer_ssl_certs/root-ca.pem:/certs/root-ca.pem:ro'],
        'depends_on':['wazuh.indexer'],'logging':{'driver':'json-file','options':{'max-size':'10m','max-file':'3'}}}
    services['certs']={'image':'wazuh/wazuh-certs-generator:0.0.5','profiles':['init'],
        'environment':{'CERT_TOOL_VERSION':'.'.join(VERSION.split('.')[:2]),'CERT_TOOL_REPOSITORY':'prod'},
        'network_mode':'bridge',
        'volumes':['./config/certs.yml:/config/certs.yml:ro',
            './config/wazuh_indexer_ssl_certs:/certificates/']}
    write(HOME/'docker-compose.yml',yaml.safe_dump(data,sort_keys=False))
    write(HOME/'config/ieum_rules.xml',(SOURCE/'ieum_rules.xml').read_text())
    write(HOME/'maintenance.py',(SOURCE/'maintenance.py').read_text())
    # Disable unrelated host scanning and all executable active response.
    xml=ET.fromstring('<root>'+upstream('config/wazuh_cluster/wazuh_manager.conf')+'</root>')
    conf=xml[0]
    for tag in ('localfile','active-response','command','remote'):
        for child in conf.findall(tag):conf.remove(child)
    # wazuh-control validates remoted even in a local-file-only deployment.
    remote=ET.SubElement(conf,'remote')
    for tag,value in (('connection','secure'),('port','1514'),('protocol','tcp'),('local_ip','127.0.0.1')):
        ET.SubElement(remote,tag).text=value
    for tag,child in (('rootcheck','disabled'),('syscheck','disabled'),('sca','enabled'),
                      ('vulnerability-detection','enabled'),('auth','disabled')):
        node=conf.find(tag+'/'+child)
        if node is not None:node.text='no' if child=='enabled' else 'yes'
    for wodle in conf.findall('wodle'):
        disabled=wodle.find('disabled')
        if disabled is not None:disabled.text='yes'
    for name in ('internal-api','transfer-api','external-api','audit-verifier'):
        entry=ET.SubElement(conf,'localfile')
        ET.SubElement(entry,'log_format').text='json'
        ET.SubElement(entry,'location').text='/var/log/matchmaking/'+name+'.json'
        ET.SubElement(entry,'only-future-events').text='yes'
    entry=ET.SubElement(conf,'localfile')
    ET.SubElement(entry,'log_format').text='json'
    ET.SubElement(entry,'location').text='/var/log/ieum-tests/smoke.json'
    ET.SubElement(entry,'only-future-events').text='yes'
    write(HOME/'config/wazuh_cluster/wazuh_manager.conf',ET.tostring(conf,encoding='unicode'))
    users={'_meta':{'type':'internalusers','config_version':2}}
    for user,key in (('admin','INDEXER_PASSWORD'),('kibanaserver','DASHBOARD_PASSWORD')):
        users[user]={'hash':bcrypt.hashpw(keys[key].encode(),bcrypt.gensalt()).decode(),'reserved':True}
        if user=='admin':users[user]['backend_roles']=['admin']
    write(HOME/'config/wazuh_indexer/internal_users.yml',yaml.safe_dump(users))
    write(HOME/'config/wazuh_dashboard/wazuh.yml',yaml.safe_dump({'hosts':[{'1513629884013':{
        'url':'https://wazuh.manager','port':55000,'username':'wazuh-wui',
        'password':keys['API_PASSWORD'],'run_as':True}}]}))
    # Official defaults disable disk watermarks; enable them for the local laptop.
    index=yaml.safe_load((HOME/'config/wazuh_indexer/wazuh.indexer.yml').read_text())
    index['cluster.routing.allocation.disk.threshold_enabled']=True
    index['cluster.routing.allocation.disk.watermark.low']='80%'
    index['cluster.routing.allocation.disk.watermark.high']='85%'
    index['cluster.routing.allocation.disk.watermark.flood_stage']='90%'
    write(HOME/'config/wazuh_indexer/wazuh.indexer.yml',yaml.safe_dump(index,sort_keys=False))
    print('Prepared local configuration. Passwords: .local/wazuh/credentials.json (do not upload).')


def required_images():
    return ['wazuh/wazuh-manager:'+VERSION,'wazuh/wazuh-indexer:'+VERSION,
        'wazuh/wazuh-dashboard:'+VERSION,'python:3.13-alpine','wazuh/wazuh-certs-generator:0.0.5']


def images_cached():
    try:
        docker('image','inspect',*required_images(),capture=True)
        return True
    except subprocess.CalledProcessError:
        return False


def preflight(data_path):
    free=shutil.disk_usage(data_path).free
    print('Free disk on checked path: %.1f GiB'%(free/1024**3))
    if free<20*1024**3:
        raise RuntimeError('Need at least 20 GiB runtime reserve on Docker data drive. No image pull/start performed.')
    if free<50*1024**3:
        if not images_cached():
            raise RuntimeError('Need at least 50 GiB free for initial installation; required images are missing. No image pull/start performed.')
        print('All required images are installed; applying the local 20 GiB runtime reserve.')
    info=json.loads(docker('info','--format','{{json .}}',capture=True).stdout)
    print('Docker allocation: %s CPUs, %.2f GiB RAM'%(info['NCPU'],info['MemTotal']/1024**3),flush=True)
    if info['MemTotal']<8*1024**3 or info['NCPU']<4:
        raise RuntimeError('Docker needs at least 8 GiB RAM and 4 CPUs; existing apps also need headroom.')
    print('Docker resources meet the minimum. This does not guarantee combined workload capacity.')


def replace_certificate_file(path,data):
    # Container chmod 0400/0444 maps to the Windows read-only attribute.
    original=path.stat().st_mode
    readonly=os.name=='nt' and not original & stat.S_IWRITE
    if readonly:os.chmod(path,original | stat.S_IWRITE)
    try:
        path.write_bytes(data)
    finally:
        if readonly:os.chmod(path,original)


def ensure_ca_key_usage():
    """Keep the original CA key/identity while fixing the generator's legacy CA."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    certdir=HOME/'config/wazuh_indexer_ssl_certs'
    path=certdir/'root-ca.pem'
    ca=x509.load_pem_x509_certificate(path.read_bytes())
    try:
        ca.extensions.get_extension_for_class(x509.KeyUsage)
        return
    except x509.ExtensionNotFound:pass
    key=serialization.load_pem_private_key((certdir/'root-ca.key').read_bytes(),password=None)
    if key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)!=ca.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo):
        raise RuntimeError('CA private key does not match certificate; no files modified')
    if ca.subject!=ca.issuer or not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
        raise RuntimeError('Expected a local self-signed CA; no files modified')
    builder=(x509.CertificateBuilder().subject_name(ca.subject).issuer_name(ca.issuer)
        .public_key(ca.public_key()).serial_number(ca.serial_number)
        .not_valid_before(ca.not_valid_before_utc).not_valid_after(ca.not_valid_after_utc))
    for extension in ca.extensions:
        builder=builder.add_extension(extension.value,extension.critical)
    builder=builder.add_extension(x509.KeyUsage(digital_signature=False,content_commitment=False,
        key_encipherment=False,data_encipherment=False,key_agreement=False,
        key_cert_sign=True,crl_sign=True,encipher_only=False,decipher_only=False),critical=True)
    encoded=builder.sign(key,hashes.SHA256()).public_bytes(serialization.Encoding.PEM)
    for name in ('root-ca.pem','root-ca-manager.pem'):
        target=certdir/name
        if target.exists():replace_certificate_file(target,encoded)
    print('Updated local CA Key Usage; original CA key and server certificates retained.')


def issue_dashboard_certificate():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID
    certdir=HOME/'config/wazuh_indexer_ssl_certs'
    certfile=certdir/'wazuh.dashboard.pem'
    existing=x509.load_pem_x509_certificate(certfile.read_bytes())
    try:
        if 'localhost' in existing.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName):return
    except x509.ExtensionNotFound:pass
    ca=x509.load_pem_x509_certificate((certdir/'root-ca.pem').read_bytes())
    signer=serialization.load_pem_private_key((certdir/'root-ca.key').read_bytes(),password=None)
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    t=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')]))
        .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(t-timedelta(minutes=5)).not_valid_after(t+timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.DNSName('wazuh.dashboard'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
        .sign(signer,hashes.SHA256()))
    replace_certificate_file(certdir/'wazuh.dashboard-key.pem',key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    replace_certificate_file(certfile,cert.public_bytes(serialization.Encoding.PEM))


def start(data_path):
    preflight(data_path)
    if not (HOME/'credentials.env').exists():raise RuntimeError('Run prepare first')
    compose('config','--quiet')
    docker('volume','inspect',os.getenv('IEUM_AUDIT_VOLUME','matchmaking_audit-logs'),capture=True)
    if not images_cached():
        compose('pull','wazuh.manager','wazuh.indexer','wazuh.dashboard','ieum-retention','smoke-init')
    count=int(docker('run','--rm','--pull=never','--entrypoint','/bin/cat',
        'wazuh/wazuh-indexer:'+VERSION,'/proc/sys/vm/max_map_count',capture=True).stdout.strip())
    if count<262144:raise RuntimeError('Set WSL Docker host vm.max_map_count >= 262144 first; services not started')
    certdir=HOME/'config/wazuh_indexer_ssl_certs'
    expected=('root-ca.pem','root-ca.key','wazuh.indexer.pem','wazuh.indexer-key.pem',
              'wazuh.manager.pem','wazuh.manager-key.pem','wazuh.dashboard.pem','wazuh.dashboard-key.pem','admin.pem','admin-key.pem')
    if not certdir.exists() or not any(certdir.iterdir()):
        certdir.mkdir(parents=True,exist_ok=True);compose('--profile','init','run','--rm','certs')
    if not all((certdir/name).is_file() for name in expected):
        raise RuntimeError('Partial certificate set: inspect without overwriting existing keys')
    ensure_ca_key_usage()
    issue_dashboard_certificate()
    compose('up','-d','wazuh.manager','wazuh.indexer','wazuh.dashboard','ieum-retention')
    print('Started. Allow initialization time, inspect status/logs, then run rules and smoke.')


def event(service='internal-api',action='POST /api/admin/member-exports',result='deny',reason='role_not_allowed',actor='U-LAB-A',**kw):
    return {'ts':datetime.now(timezone.utc).isoformat(),'service':service,'trace_id':secrets.token_hex(16),
        'actor':actor,'role':'counselor','action':action,'target_type':None,'target_id':None,
        'result':result,'reason':reason,'lab_fixture':'true',**kw}


def cases():
    batch_actor='U-LAB-'+secrets.token_hex(5)
    dl='LAB-DELIVERY-'+secrets.token_hex(5)
    kc_user='kc-lab-'+secrets.token_hex(4)
    return [(event(actor=batch_actor),'100110'),(event(actor=batch_actor),'100110'),(event(actor=batch_actor),'100112'),
        (event(service='transfer-api',action='GET /api/internal/deliveries/{delivery_id}/download',reason='requested_file_not_approved'),'100111'),
        (event(service='transfer-api',reason='actual_file_hash_mismatch'),'100120'),
        (event(service='transfer-api',action='POST /api/internal/matching/requests/{match_id}/decision',reason='recipient_only'),'100130'),
        (event(service='external-api',action='GET /api/me',reason='no_session',actor=None),'100101'),
        (event(service='transfer-api',action='GET /api/internal/matching/requests/{match_id}/profile',reason='peer_consent_required'),'100101'),
        (event(service='external-api',action='login',result='allow',reason=None),'100102'),
        (event(action='member_purge',result='error',reason='purge_failed'),'100140'),
        (event(action='evidence_delete',result='error',reason='delete_failed'),'100141'),
        (event(action='member_account_delete',result='error',reason='not_member_account'),'100142'),
        (event(action='security.auto_lock',result='allow',reason='repeated_role_denials',actor='system',target_id='U-LAB-B'),'100150'),
        (event(action='GET /api/members',reason='account_temporarily_locked',actor='U-LAB-C'),'100151'),
        (event(action='security.suspend_account',result='allow',reason='keycloak_done',actor='U-LAB-D',target_id='U-LAB-C'),'100152'),
        (event(service='transfer-api',action='security.report',result='error',reason='security_report_failed',actor='U-LAB-E'),'100153'),
        (event(service='audit-verifier',action='audit.chain_verify',result='error',reason='audit_chain_broken',actor='system',target_id='transfer-api.json'),'100160'),
        (event(service='audit-verifier',action='audit.chain_verify',result='error',reason='audit_collection_stale',actor='system',target_id='external-api.json'),'100161'),
        (event(service='transfer-api',action='GET /api/internal/deliveries/{delivery_id}/download',reason='consent_invalid',actor='U-LAB-F'),'100170'),
        (event(service='transfer-api',action='POST /api/export-requests/{request_id}/approve',reason='self_approval',actor='U-LAB-G'),'100171'),
        (event(service='transfer-api',action='POST /api/export-requests',reason='file_not_eligible',actor='U-LAB-H'),'100172'),
        (event(service='transfer-api',action='GET /api/internal/deliveries/{delivery_id}/download',reason='download_token_used',actor='U-LAB-I'),'100173'),
        (event(service='transfer-api',action='POST /api/internal/deliveries/{delivery_id}/download-tokens',reason='download_limit_reached',actor='U-LAB-J'),'100174'),
        (event(service='transfer-api',action='delivery.download',result='allow',reason='download_1',actor='U-LAB-K',target_id=dl),'100176'),
        (event(service='transfer-api',action='delivery.download',result='allow',reason='download_2',actor='U-LAB-K',target_id=dl),'100176'),
        (event(service='transfer-api',action='delivery.download',result='allow',reason='download_3',actor='U-LAB-K',target_id=dl),'100175'),
        (event(action='GET /api/members/{member_id}',reason='member_not_assigned',actor='U-LAB-L'),'100177'),
        *[(event(action='keycloak.login_error',reason='invalid_user_credentials',actor=kc_user),'100180') for _ in range(4)],
        (event(action='keycloak.login_error',reason='invalid_user_credentials',actor=kc_user),'100181'),
        (event(action='keycloak.login_error',reason='user_temporarily_disabled',actor='kc-lab-other'),'100182'),
        (event(action='keycloak.admin_event',result='allow',reason='create_realm_role_mapping',actor='kc-admin-lab',target_id='users/x/role-mappings/realm'),'100183'),
        (event(action='keycloak.admin_event',result='allow',reason='create_realm_role_mapping',actor='svc:internal-admin',target_id='users/y/role-mappings/realm'),'100102')]


def rules():
    import re
    values=cases();payload='\n'.join(json.dumps(e) for e,_ in values)+'\n'
    out=compose('exec','-T','wazuh.manager','/var/ossec/bin/wazuh-logtest',input=payload,capture=True)
    output=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',(out.stdout or '')+'\n'+(out.stderr or ''))
    found=re.findall(r"\bid:\s*['\"](\d+)['\"]",output)
    expected=[rid for _,rid in values]
    if found!=expected:
        print(json.dumps({'expected':expected,'actual':found}))
        print('Wazuh logtest diagnostic output (synthetic test events only):\n'+output[-16000:])
        raise RuntimeError('Wazuh rule-engine test failed; do not claim validated rules')
    print('Actual Wazuh logtest passed for attack, threshold, integrity, download, Keycloak and normal cases.')


def smoke():
    values=cases();payload=''.join(json.dumps(e)+'\n' for e,_ in values)
    # Live analysis is asynchronous. Exercise ordered requests explicitly rather
    # than treating one bulk append as proof of concurrent correlation support.
    compose('exec','-T','wazuh.manager','/var/ossec/framework/python/bin/python3','-c',
        "import sys,time\n"
        "for line in sys.stdin:\n"
        "    with open('/var/log/ieum-tests/smoke.json','a') as f:f.write(line)\n"
        "    time.sleep(3)\n",input=payload)
    expected={e['trace_id']:rid for e,rid in values}
    compose('exec','-T','ieum-retention','python','/app/maintenance.py','probe',input=json.dumps(expected))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['prepare','check','start','stop','status','rules','smoke','logs'])
    parser.add_argument('--docker-data-path',default=str(ROOT),help='Path on actual Docker disk-image drive; default assumes project drive')
    args=parser.parse_args()
    if args.command=='prepare':prepare()
    elif args.command=='check':preflight(args.docker_data_path)
    elif args.command=='start':start(args.docker_data_path)
    elif args.command=='stop':compose('stop')
    elif args.command=='status':compose('ps','-a')
    elif args.command=='logs':compose('logs','--tail','40','ieum-retention','wazuh.manager','wazuh.indexer','wazuh.dashboard')
    elif args.command=='rules':rules()
    elif args.command=='smoke':smoke()


if __name__=='__main__':
    try:main()
    except (RuntimeError,subprocess.CalledProcessError,ImportError) as exc:
        if isinstance(exc,ImportError):print('Missing Python dependency: '+str(exc)+'. Install requirements-wazuh.txt into your Python environment.',file=sys.stderr)
        elif isinstance(exc,subprocess.CalledProcessError):print('Docker command failed; inspect local Docker status/logs. Exit '+str(exc.returncode),file=sys.stderr)
        else:print(str(exc),file=sys.stderr)
        sys.exit(1)
