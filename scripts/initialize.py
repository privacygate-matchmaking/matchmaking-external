"""Generate the local session-at-rest key once. Never print its contents."""
from pathlib import Path
import base64
import os
import secrets

root=Path(__file__).resolve().parents[1]
target=root/'.local/secrets/session.key'
target.parent.mkdir(parents=True,exist_ok=True)
if not target.exists():
    with target.open('xb') as f:
        f.write(base64.urlsafe_b64encode(os.urandom(32)))
    try:
        target.chmod(0o600)
    except OSError:
        pass
    print('Session key generated in .local/secrets/session.key (do not commit).')
else:
    print('Existing session key preserved.')

# The current internal infrastructure requires a new service-client secret.
# Keep private defaults outside either teammate's tracked files and .env.
base_env=root.parent/'privacygate/.env'
values={}
if base_env.is_file():
    for line in base_env.read_text(encoding='utf-8-sig').splitlines():
        name,sep,value=line.strip().partition('=')
        if sep and not name.startswith('#'):values[name]=value.strip().strip('"\'')
env_path=root/'.local/internal-admin.env'
if not (os.environ.get('INTERNAL_ADMIN_CLIENT_SECRET') or values.get('INTERNAL_ADMIN_CLIENT_SECRET')):
    has_local = env_path.exists() and any(line.startswith('INTERNAL_ADMIN_CLIENT_SECRET=') and line.partition('=')[2]
        for line in env_path.read_text(encoding='utf-8').splitlines())
    if not has_local:
        with env_path.open('w',encoding='utf-8',newline='\n') as stream:
            stream.write('INTERNAL_ADMIN_CLIENT_SECRET='+secrets.token_urlsafe(32)+'\n')
        env_path.chmod(0o600)
        print('Local internal-admin client secret prepared (do not commit).')
else:
    # An explicit owner-provided value takes priority over a previous local default.
    env_path.write_text('# INTERNAL_ADMIN_CLIENT_SECRET is supplied by the base .env or shell.\n',encoding='utf-8')
print('Local Compose env file: .local/internal-admin.env')
