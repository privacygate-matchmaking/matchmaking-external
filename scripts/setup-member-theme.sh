#!/bin/bash
# Only external-web's visual theme is updated. No passwords or auth flows change.
set -euo pipefail
KC=/opt/keycloak/bin/kcadm.sh
$KC config credentials --server http://keycloak:8080 --realm master \
  --user "$KC_BOOTSTRAP_ADMIN_USERNAME" --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null
client=$($KC get clients -r matchmaking -q clientId=external-web --fields id --format csv --noquotes)
if [ -z "$client" ] || [[ "$client" == *$'\n'* ]]; then
  echo "Expected exactly one external-web client; theme not changed." >&2
  exit 1
fi
$KC update "clients/$client" -r matchmaking -s 'attributes."login_theme"=ieum-members'
echo "IEUM member login theme applied to external-web."
