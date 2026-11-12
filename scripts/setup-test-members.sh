#!/bin/bash
# Run explicitly in the local Keycloak container. Uses its existing test password.
set -euo pipefail
KC=/opt/keycloak/bin/kcadm.sh
$KC config credentials --server http://keycloak:8080 --realm master \
  --user "$KC_BOOTSTRAP_ADMIN_USERNAME" --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null
for spec in 'member_c:202' 'member_d:203' 'member_e:204'; do
  name=${spec%:*}
  suffix=${spec#*:}
  expected="22222222-2222-4222-8222-222222222${suffix}"
  existing=$($KC get users -r matchmaking -q username="$name" -q exact=true --fields id --format csv --noquotes)
  if [ -z "$existing" ]; then
    # Partial import preserves fixture UUIDs; normal POST users generates its own ID.
    $KC create partialImport -r matchmaking -s ifResourceExists=SKIP \
      -s "users=[{\"id\":\"$expected\",\"username\":\"$name\",\"enabled\":true,\"firstName\":\"Synthetic\",\"lastName\":\"$name\",\"email\":\"$name@example.invalid\",\"emailVerified\":true}]" >/dev/null
    existing=$($KC get users -r matchmaking -q username="$name" -q exact=true --fields id --format csv --noquotes)
    if [ "$existing" != "$expected" ]; then
      echo "Test account ID verification failed; no password or role changed." >&2
      exit 1
    fi
  elif [ "$existing" != "$expected" ]; then
    echo "Existing test account has a different ID; stopping without overwriting." >&2
    exit 1
  fi
  $KC set-password -r matchmaking --username "$name" --new-password "$KC_TEST_PASSWORD" >/dev/null
  $KC add-roles -r matchmaking --uusername "$name" --rolename member >/dev/null
  echo "Synthetic matching account ready: $name"
done
