"""Normalize only known local startup shell scripts; preserve their original encoding."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TARGETS=(
    ROOT.parent/'privacygate/infra/db/business/02-app-role.sh',
    ROOT.parent/'privacygate/infra/db/transfer/02-app-role.sh',
    ROOT.parent/'privacygate/infra/keycloak/realm-setup.sh',
    ROOT/'scripts/setup-test-members.sh',
    ROOT/'scripts/setup-member-theme.sh',
)


def normalize(path):
    data=path.read_bytes()
    fixed=data.replace(b'\r\n',b'\n').replace(b'\r',b'\n')
    if fixed!=data:
        path.write_bytes(fixed)
        return True
    return False


if __name__=='__main__':
    for path in TARGETS:
        if not path.is_file():raise SystemExit('Startup script missing: '+str(path))
        if normalize(path):print('Normalized shell line endings: '+str(path))
    print('Startup shell scripts use LF line endings.')
