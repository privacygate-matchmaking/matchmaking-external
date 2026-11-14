-- Synthetic seed-20 account linking. Run only through the account setup tool.
-- psql --single-transaction is required. No member/UUID/approval/evidence data is deleted.
CREATE TEMP TABLE internal20_targets(mid uuid,uid uuid,code text,username text,legacy_username text) ON COMMIT DROP;
INSERT INTO internal20_targets VALUES
('bbbbbbbb-0000-4000-8000-000000000001'::uuid,'22222222-2222-4222-8222-222222222202'::uuid,'A-001','member_001','member_c'),
('bbbbbbbb-0000-4000-8000-000000000002'::uuid,'22222222-2222-4222-8222-222222222203'::uuid,'A-002','member_002','member_d'),
('bbbbbbbb-0000-4000-8000-000000000003'::uuid,'22222222-2222-4222-8222-222222222201'::uuid,'A-003','member_003','member_b'),
('bbbbbbbb-0000-4000-8000-000000000004'::uuid,'22222222-2222-4222-8222-222222222204'::uuid,'A-004','member_004','member_e'),
('bbbbbbbb-0000-4000-8000-000000000005'::uuid,'22222222-2222-4222-8222-222222222205'::uuid,'A-005','member_005',NULL),
('bbbbbbbb-0000-4000-8000-000000000006'::uuid,'22222222-2222-4222-8222-222222222206'::uuid,'A-006','member_006',NULL),
('bbbbbbbb-0000-4000-8000-000000000007'::uuid,'22222222-2222-4222-8222-222222222207'::uuid,'A-007','member_007',NULL),
('bbbbbbbb-0000-4000-8000-000000000008'::uuid,'22222222-2222-4222-8222-222222222208'::uuid,'A-008','member_008',NULL),
('bbbbbbbb-0000-4000-8000-000000000009'::uuid,'22222222-2222-4222-8222-222222222209'::uuid,'A-009','member_009',NULL),
('bbbbbbbb-0000-4000-8000-000000000010'::uuid,'22222222-2222-4222-8222-222222222210'::uuid,'A-010','member_010',NULL),
('bbbbbbbb-0000-4000-8000-000000000011'::uuid,'22222222-2222-4222-8222-222222222211'::uuid,'A-011','member_011',NULL),
('bbbbbbbb-0000-4000-8000-000000000012'::uuid,'22222222-2222-4222-8222-222222222212'::uuid,'A-012','member_012',NULL),
('bbbbbbbb-0000-4000-8000-000000000013'::uuid,'22222222-2222-4222-8222-222222222213'::uuid,'A-013','member_013',NULL),
('bbbbbbbb-0000-4000-8000-000000000014'::uuid,'22222222-2222-4222-8222-222222222214'::uuid,'A-014','member_014',NULL),
('bbbbbbbb-0000-4000-8000-000000000015'::uuid,'22222222-2222-4222-8222-222222222215'::uuid,'A-015','member_015',NULL),
('bbbbbbbb-0000-4000-8000-000000000016'::uuid,'22222222-2222-4222-8222-222222222216'::uuid,'A-016','member_016',NULL),
('bbbbbbbb-0000-4000-8000-000000000017'::uuid,'22222222-2222-4222-8222-222222222217'::uuid,'A-017','member_017',NULL),
('bbbbbbbb-0000-4000-8000-000000000018'::uuid,'22222222-2222-4222-8222-222222222218'::uuid,'A-018','member_018',NULL),
('bbbbbbbb-0000-4000-8000-000000000019'::uuid,'22222222-2222-4222-8222-222222222219'::uuid,'A-019','member_019',NULL),
('bbbbbbbb-0000-4000-8000-000000000020'::uuid,'22222222-2222-4222-8222-222222222220'::uuid,'A-020','member_020',NULL);
LOCK TABLE users, members IN SHARE ROW EXCLUSIVE MODE;
DO $$
DECLARE r record; m record;
BEGIN
  IF current_database()<>'matchmaking_business' OR (SELECT count(*) FROM members)<>20 THEN
    RAISE EXCEPTION 'Expected the synthetic 20-member business database; no account links changed';
  END IF;
  FOR r IN SELECT * FROM internal20_targets LOOP
    SELECT * INTO m FROM members WHERE member_id=r.mid AND pseudo_id=r.code AND status='active';
    IF NOT FOUND THEN RAISE EXCEPTION 'Seed member missing/inactive: %',r.code; END IF;
    IF m.user_id IS NOT NULL AND m.user_id<>r.uid THEN
      RAISE EXCEPTION 'Member already linked to a different account: %',r.code;
    END IF;
    IF EXISTS (SELECT 1 FROM users WHERE user_id=r.uid AND
        (role<>'member' OR NOT active OR pseudo_id<>r.code OR
         display_name NOT IN (r.username,coalesce(r.legacy_username,r.username))))
      OR EXISTS (SELECT 1 FROM users WHERE pseudo_id=r.code AND user_id<>r.uid)
      OR EXISTS (SELECT 1 FROM users WHERE display_name=r.username AND user_id<>r.uid)
      OR EXISTS (SELECT 1 FROM members WHERE user_id=r.uid AND member_id<>r.mid) THEN
      RAISE EXCEPTION 'Existing account conflicts with seed mapping: %',r.code;
    END IF;
  END LOOP;
END $$;
-- APPLY ACCOUNT LINKS
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT * FROM internal20_targets LOOP
    INSERT INTO users(user_id,pseudo_id,display_name,role) VALUES(r.uid,r.code,r.username,'member')
      ON CONFLICT(user_id) DO UPDATE SET display_name=EXCLUDED.display_name;
    UPDATE members SET user_id=r.uid WHERE member_id=r.mid;
  END LOOP;
END $$;
SELECT count(*) AS members_count FROM members;
SELECT u.display_name,m.pseudo_id FROM members m JOIN users u ON u.user_id=m.user_id
  WHERE u.role='member' ORDER BY m.pseudo_id;
