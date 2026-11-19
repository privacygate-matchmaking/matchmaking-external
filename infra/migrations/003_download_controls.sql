-- 안전한 반출: 제공 건별 다운로드 횟수, 일회용 다운로드 토큰(원문 대신 SHA-256 해시만 저장), 감사 로그 수정·삭제 차단.
-- compose 가 매번 실행하므로 여러 번 실행해도 안전해야 한다.
ALTER TABLE deliveries ADD COLUMN IF NOT EXISTS download_count int NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS download_tokens (
    token_hash   text PRIMARY KEY CHECK (char_length(token_hash) = 64),
    delivery_id  uuid NOT NULL REFERENCES deliveries(delivery_id),
    recipient_id uuid NOT NULL,
    created_at   timestamptz NOT NULL,
    expires_at   timestamptz NOT NULL,
    used_at      timestamptz
);
CREATE INDEX IF NOT EXISTS download_tokens_delivery_idx ON download_tokens(delivery_id) WHERE used_at IS NULL;
GRANT SELECT,INSERT,UPDATE ON download_tokens TO app_user;

-- 감사 로그: 앱 계정은 INSERT 만. DB 소유자로도 수정·삭제·TRUNCATE 불가 (보관기간 지난 행만 purge_audit_log 로 삭제)
CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' AND current_setting('privacygate.audit_purge', true) = 'on'
       AND OLD.ts < now() - make_interval(days => current_setting('privacygate.audit_retention_days', true)::int) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'audit_log is append-only (%)', TG_OP;
END $$;
DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log;
CREATE TRIGGER audit_log_no_update BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log;
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only();
CREATE OR REPLACE FUNCTION purge_audit_log(p_days int DEFAULT 365) RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE n bigint;
BEGIN
    IF p_days < 365 THEN RAISE EXCEPTION 'retention must be >= 365 days'; END IF;
    PERFORM set_config('privacygate.audit_purge', 'on', true);
    PERFORM set_config('privacygate.audit_retention_days', p_days::text, true);
    DELETE FROM audit_log WHERE ts < now() - make_interval(days => p_days);
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END $$;
REVOKE ALL ON FUNCTION purge_audit_log(int) FROM PUBLIC;
