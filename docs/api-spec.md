# 외부·중계 API

2026-11-14 기준. 로그인·증빙·동의·반출 경로를 정리합니다. [매칭 API](matching-api.md), [일정 API](meeting-scheduling.md), [회원 상태·탈퇴 경로](internal-handoff.md)를 함께 확인합니다.

모든 ID는 UUID, 시각은 시간대 포함 ISO 8601입니다. JSON 모델의 미정의 필드는 422입니다. 오류는 {"error":{"code":"APPROVAL_MISMATCH","message":"…","trace_id":"…"}} 형태입니다. 401 인증, 403 정책 거부, 404 대상 없음, 409 상태 충돌, 422 입력 오류, 500 의존 서비스/내부 오류를 사용합니다.

## 브라우저 → external-api

회원 API는 external_session 쿠키가 필요합니다. POST에는 /api/me에서 받은 csrf_token을 X-CSRF-Token으로 보내며 Origin도 일치해야 합니다.

| 메서드·경로 | 입력 | 응답 |
|---|---|---|
| GET /auth/login | 없음 | Keycloak 리다이렉트 |
| GET /auth/callback | code, state | 세션 생성 후 / 리다이렉트 |
| GET /api/me | 없음 | user_id, pseudo_id, role, csrf_token, gate_mode, member_status_check, meeting_scheduling |
| POST /auth/logout | {} | logout_url |
| GET /api/me/deliveries | 없음 | {items:[제공 건]} |
| GET /api/me/deliveries/{delivery_id}/download | 선택 query file_id | 파일 또는 403 |
| GET /api/me/evidence-files | 없음 | {items:[증빙 상태]} |
| POST /api/me/evidence-files | multipart file(PDF, 2MiB 이하), kind=education/employment(기본 education) | 내부 접수 응답, 201 |
| GET /api/me/consents | 없음 | {items:[동의], counterparts:[허용 상대]} |
| POST /api/me/consents | 동의 모델 | 내부 생성 응답, 201 |
| POST /api/me/consents/{consent_id}/revoke | {} | 내부 철회 응답 |

## 회원 알림

| 메서드·경로 | 호출자 | 입력 / 응답 |
|---|---|---|
| GET /api/me/notifications | member (세션) | {unread, items:[{notification_id, kind, title, body, link_view, created_at, read}]} |
| POST /api/me/notifications/read · /delete | member (세션 + CSRF) | {ids:[UUID], all:false} → {updated\|deleted, unread} |
| GET·POST /api/internal/member-notifications(/read, /delete) (transfer-api) | external-api 서비스 토큰 + 회원 토큰 | internal-api /api/internal/my-notifications 로 전달 |

알림 문구는 internal-api 가 정합니다. transfer-api 는 매칭·일정·공개 승인 사건이 생기면 사건 종류와 대상 회원 id만 internal-api `POST /api/internal/member-notification-events` 로 보내며, 실패해도 업무 응답에는 영향이 없습니다.

## 회원가입 (로그인 전)

| 메서드·경로 | 호출자 | 입력 / 응답 |
|---|---|---|
| POST /api/signup (external-api) | 브라우저, 세션 없음. Origin 일치 필수, 접속 주소당 10분 5회·전체 60회 | 가입 모델 → {status:"registered"} (201) |
| POST /api/internal/member-signups (transfer-api) | external-api 서비스 토큰만 (X-User-Token 없음) | 가입 모델 → internal-api 같은 경로로 전달 |

가입 모델: username(영문 소문자 시작 4~20자, a-z0-9_), password(12~64자, 아이디 포함 불가), name, gender(남/여), birth_date(만 19세 이상), contact_phone, contact_email, education_level(고졸/전문대졸/4년제 대졸/대학원졸), job_group(40자), height_cm(120~230), weight_kg(30~200), introduction(300자) — 모두 필수, privacy_consent(true), consent_version("2026-11"). 계정 생성·회원 저장·담당 배정은 internal-api가 합니다. 비밀번호는 감사 로그·응답에 남기지 않습니다. 중복 아이디·이메일은 409 INVALID_STATE.

동의 모델: counterpart_member_id(UUID), item(profile/photo/contact), purpose(introduction), expires_at(미래 시각). 회원 본인 ID는 입력받지 않고 서버의 검증된 토큰으로 결정합니다.

제공 건: delivery_id, approval_id, file_id(승인 파일), file_version, purpose, request_status, expires_at, downloaded_at, download_url. ID가 보인다고 다른 회원 건에 접근할 수 있는 것은 아닙니다.

## 내부 BFF → transfer-api

Authorization: Bearer 직원 access token. aud=transfer-api, azp=internal-web. 내부 BFF는 브라우저 세션·CSRF 및 현재 업무 권한을 자체 검증해야 합니다.

| 메서드·경로 | 역할 | 입력 / 응답 |
|---|---|---|
| POST /api/transfer-files | counselor | multipart file → file_id, file_version, file_hash, scan_status, export_banned, created_at (201) |
| POST /api/export-requests | counselor | 반출 신청 모델 → request_id, request_status, created_at (201) |
| GET /api/export-requests | counselor / approver | counselor 본인 신청, approver 전체 → {items:[신청]} |
| GET /api/export-requests/{request_id} | counselor / approver | 신청 + scan_status + preview |
| POST /api/export-requests/{request_id}/approve | approver | {} → request_id, request_status, approval_id, delivery_id, expires_at |
| POST /api/export-requests/{request_id}/reject | approver | {} → request_id, request_status |
| POST /api/approvals/{approval_id}/revoke | approver | {} → approval_id, request_status, revoked_at |

반출 신청 모델: file_id, member_id(정보 주체), recipient_id(외부 수신자 user UUID), consent_id, purpose=introduction, expires_at. 유효 동의 및 파일 적합성·주체 일치가 필요합니다. 만료는 현재 이후 7일 이내이며 동의 만료를 넘을 수 없습니다.

소개 JSON v2의 정확한 키: schema_version(정수 2), member_id(업무 회원 UUID 문자열), display_name, age_band, job_group, introduction. 나머지 다섯 값은 1~300자 문자열입니다. 파일/신청/동의 응답의 member_id를 비교하며 실명은 판단에 사용하지 않습니다. 기존 형식은 재생성·재승인이 필요합니다. 이는 형식 검사이며 악성코드 검사나 모든 개인정보 식별을 보장하지 않습니다. CSV 등은 blocked/export_banned=true로 사설 대기영역에 등록되지만 승인할 수 없습니다.

## external-api → transfer-api

Authorization: Bearer external-api 서비스 토큰 및 X-User-Token: 회원 access token이 둘 다 필요합니다.

| 메서드·경로 | 입력 |
|---|---|
| POST /api/internal/delivery-queries | {} |
| GET /api/internal/deliveries/{delivery_id}/download | 선택 query file_id |
| GET /api/internal/evidence-submissions | 없음 |
| POST /api/internal/evidence-submissions | multipart file, kind=education/employment(기본 education) |
| GET /api/internal/member-consents | 없음 |
| POST /api/internal/member-consents | 동의 모델 |
| POST /api/internal/member-consents/{consent_id}/revoke | {} |

다운로드 URL의 delivery_id가 정상 승인 문맥을 식별합니다. 별도의 approval_id 입력은 받지 않습니다. baseline은 그 문맥의 유효성 검사 후 요청 file_id만 잘못 신뢰합니다. enforce는 file_id 불일치를 403으로 거부합니다. 정상 file_id 생략 요청은 두 모드에서 승인 파일을 반환합니다.

GET /health는 로그인 없이 프로세스 생존 상태만 반환하며 의존 서비스 준비 완료를 뜻하지 않습니다.


## 명시적 매칭 동의 기반 자료 제공

- 직원 GET `/api/matching/overview`: 내부 상담 BFF에서 중계 조회. 매칭 ID·양쪽 user UUID·상태·최근 방향별 동의와 새 반출 상태를 반환. 내부 BFF는 담당 회원 코드/이름, 비담당 상대의 최소 설명으로 표시.
- 직원 GET `/api/matching/members/{user_id}/membership`(중계, 내부 internal-api 가 직원 토큰을 X-User-Token 으로 함께 보냄): counselor·data_admin 만. 먼저 내부 `POST /api/internal/member-scope-checks` 로 담당 범위를 확인하고, 아니면 403 `member_not_in_scope`. 200: `{paid, plan:{name,credits,months}, wallet:{active,total,reserved,used,available,expires_at}, last_paid_at, orders:[{plan_code, amount_won, status, created_at, paid_at}]}` (주문 번호·결제 정보 없음). 감사 `matching.membership_view`.
- 내부 BFF GET `/api/matching/overview?member_id=<업무 UUID>`: 현재 담당 범위를 확인한 뒤 해당 회원의 항목만 반환.
- 직원 POST `/api/matching/releases`: `{ "grant_id": "UUID", "scopes": ["education"] }`. 201: request_id, file_id, consent_id, request_status=pending. 내부 BFF는 CSRF와 Origin 필수. profile/education/employment 중 동의된 항목만 선택 가능.
- 서비스 POST `/api/internal/matching-release-checks`: owner_id, recipient_id, member_id, requester_id, scopes. transfer-api 서비스 인증 전용. 실제 현재 검토 결과를 반환하며 미심사·반려 항목은 409, 주체/담당/활성/소개 관계 위반은 403.
- 새 자료 형식 3: schema_version=3, member_id, recipient_id, grant_id, match_id, purpose=introduction, scopes, fields(각 scope/label/value/review_status/reviewed_at). 원본·연락처·실명은 포함하지 않음.
- 새 export_requests의 purpose=matching_introduction은 중계 서버가 지정. consent_id는 실제 matching_grants.grant_id 참조. 기존 소개 purpose=introduction/형식2 경로와 구별하며 기존 기록은 변경하지 않음.
- 승인·다운로드는 현재 회원 동의·매칭·기간·검토·담당과 파일 고정값을 재확인. 새 형식 파일은 일반 업로드로 등록 불가. 기존 API를 통한 임의 목적 변경 불가.
