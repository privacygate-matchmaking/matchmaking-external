"""Generate the local session-at-rest key once. Never print its contents."""
from pathlib import Path
import base64
import os

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
