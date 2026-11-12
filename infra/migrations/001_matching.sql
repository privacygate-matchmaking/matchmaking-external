-- Additive, repeatable migration. Run as db_owner, never grant DDL to app_user.
CREATE TABLE IF NOT EXISTS matching_profiles (
    member_id uuid PRIMARY KEY,
    user_id uuid UNIQUE NOT NULL,
    pseudo_id text NOT NULL,
    age_band text NOT NULL,
    job_group text NOT NULL,
    introduction text NOT NULL,
    updated_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS matching_orders (
    order_id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    idempotency_key uuid NOT NULL,
    plan_code text NOT NULL,
    amount_won integer NOT NULL CHECK(amount_won=3300000),
    status text NOT NULL CHECK(status IN ('pending','paid')),
    created_at timestamptz NOT NULL,
    paid_at timestamptz,
    UNIQUE(user_id,idempotency_key)
);
CREATE TABLE IF NOT EXISTS matching_memberships (
    membership_id uuid PRIMARY KEY,
    order_id uuid UNIQUE NOT NULL REFERENCES matching_orders(order_id),
    user_id uuid NOT NULL,
    credits integer NOT NULL CHECK(credits=5),
    starts_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS matching_requests (
    match_id uuid PRIMARY KEY,
    sender_id uuid NOT NULL,
    recipient_id uuid NOT NULL,
    membership_id uuid NOT NULL REFERENCES matching_memberships(membership_id),
    idempotency_key uuid NOT NULL,
    pair_key text NOT NULL,
    status text NOT NULL CHECK(status IN ('pending','matched','rejected','cancelled','expired','ended')),
    credit_state text NOT NULL CHECK(credit_state IN ('reserved','consumed','released')),
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    decided_at timestamptz,
    ended_at timestamptz,
    CHECK(sender_id<>recipient_id),
    UNIQUE(sender_id,idempotency_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS matching_active_pair_idx ON matching_requests(pair_key) WHERE status IN ('pending','matched');
CREATE INDEX IF NOT EXISTS matching_sender_idx ON matching_requests(sender_id,created_at);
CREATE INDEX IF NOT EXISTS matching_recipient_idx ON matching_requests(recipient_id);
CREATE TABLE IF NOT EXISTS matching_credit_events (
    event_id uuid PRIMARY KEY,
    match_id uuid NOT NULL REFERENCES matching_requests(match_id),
    user_id uuid NOT NULL,
    event_type text NOT NULL CHECK(event_type IN ('reserve','consume','release')),
    created_at timestamptz NOT NULL,
    UNIQUE(match_id,event_type)
);
CREATE TABLE IF NOT EXISTS matching_grants (
    grant_id uuid PRIMARY KEY,
    match_id uuid NOT NULL REFERENCES matching_requests(match_id),
    owner_id uuid NOT NULL,
    recipient_id uuid NOT NULL,
    scopes_json text NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL,
    revoked_at timestamptz
);
CREATE INDEX IF NOT EXISTS matching_grant_owner_idx ON matching_grants(match_id,owner_id);
GRANT SELECT,INSERT,UPDATE ON matching_profiles,matching_orders,matching_memberships,matching_requests,matching_grants TO app_user;
GRANT SELECT,INSERT ON matching_credit_events TO app_user;
