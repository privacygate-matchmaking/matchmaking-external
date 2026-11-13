-- Scheduling is additive and does not change membership credits or evidence.
CREATE TABLE IF NOT EXISTS matching_meeting_proposals (
    proposal_id uuid PRIMARY KEY,
    round_id uuid NOT NULL,
    match_id uuid NOT NULL REFERENCES matching_requests(match_id),
    proposed_by uuid NOT NULL,
    starts_at timestamptz NOT NULL,
    venue text NOT NULL,
    address text NOT NULL,
    status text NOT NULL CHECK (status IN ('proposed','confirmed','superseded','cancelled')),
    created_at timestamptz NOT NULL,
    confirmed_at timestamptz,
    confirmed_by uuid
);
CREATE INDEX IF NOT EXISTS matching_meeting_match_idx ON matching_meeting_proposals(match_id,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS matching_one_confirmed_meeting_idx ON matching_meeting_proposals(match_id) WHERE status='confirmed';
CREATE TABLE IF NOT EXISTS matching_meeting_replies (
    proposal_id uuid NOT NULL REFERENCES matching_meeting_proposals(proposal_id),
    user_id uuid NOT NULL,
    availability text NOT NULL CHECK (availability IN ('available','change_requested')),
    private_note text NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL,
    PRIMARY KEY(proposal_id,user_id)
);
GRANT SELECT,INSERT,UPDATE ON matching_meeting_proposals,matching_meeting_replies TO app_user;
