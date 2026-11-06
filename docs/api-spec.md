# 외부·중계 API (v0.3)

모든 ID는 UUID, 시각은 시간대 포함 ISO 8601입니다. JSON 모델의 미정의 필드는 422입니다. 오류는 {"error":{"code":"APPROVAL_MISMATCH","message":"…","trace_id":"…"}} 형태입니다. 401 인증, 403 정책 거부, 404 대상 없음, 409 상태 충돌, 422 입력 오류, 500 의존 서비스/내부 오류를 사용합니다.

## 브라우저 → external-api

회원 API는 external_session 쿠키가 필요합니다. POST에는 /api/me에서 받은 csrf_token을 X-CSRF-Token으로 보내며 Origin도 일치해야 합니다.

| 메서드·경로 | 입력 | 응답 |
|---|---|---|
| GET /auth/login | 없음 | Keycloak 리다이렉트 |
| GET /auth/callback | code, state | 세션 생성 후 / 리다이렉트 |
| GET /api/me | 없음 | user_id, pseudo_id, role, csrf_token, gate_mode |
| POST /auth/logout | {} | logout_url |
| GET /api/me/deliveries | 없음 | {items:[제공 건]} |
| GET /api/me/deliveries/{delivery_id}/download | 선택 query file_id | 파일 또는 403 |
| GET /api/me/evidence-files | 없음 | {items:[증빙 상태]} |
| POST /api/me/evidence-files | multipart file(PDF, 2MiB 이하) | 내부 접수 응답, 201 |
| GET /api/me/consents | 없음 | {items:[동의], counterparts:[허용 상대]} |
| POST /api/me/consents | 동의 모델 | 내부 생성 응답, 201 |
| POST /api/me/consents/{consent_id}/revoke | {} | 내부 철회 응답 |

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

소개 JSON 파일의 정확한 키: member_pseudo_id, age_band, job_group, introduction. 값은 1~300자 문자열입니다. 이는 형식 검사이며 악성코드 검사나 모든 개인정보 식별을 보장하지 않습니다. CSV 등은 blocked/export_banned=true로 사설 대기영역에 등록되지만 승인할 수 없습니다.

## external-api → transfer-api

Authorization: Bearer external-api 서비스 토큰 및 X-User-Token: 회원 access token이 둘 다 필요합니다.

| 메서드·경로 | 입력 |
|---|---|
| POST /api/internal/delivery-queries | {} |
| GET /api/internal/deliveries/{delivery_id}/download | 선택 query file_id |
| GET /api/internal/evidence-submissions | 없음 |
| POST /api/internal/evidence-submissions | multipart file |
| GET /api/internal/member-consents | 없음 |
| POST /api/internal/member-consents | 동의 모델 |
| POST /api/internal/member-consents/{consent_id}/revoke | {} |

다운로드 URL의 delivery_id가 정상 승인 문맥을 식별합니다. 별도의 approval_id 입력은 받지 않습니다. baseline은 그 문맥의 유효성 검사 후 요청 file_id만 잘못 신뢰합니다. enforce는 file_id 불일치를 403으로 거부합니다. 정상 file_id 생략 요청은 두 모드에서 승인 파일을 반환합니다.

GET /health는 로그인 없이 프로세스 생존 상태만 반환하며 의존 서비스 준비 완료를 뜻하지 않습니다.
