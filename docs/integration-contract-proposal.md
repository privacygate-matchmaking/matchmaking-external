# 외부·중계 연동 계약안 v0.2

> 과거 제안 기록입니다. 현재 구현 계약은 api-spec.md와 internal-handoff.md를 따릅니다. 이 문서의 제안 전체가 구현되었다는 뜻은 아닙니다.

상태: **GitHub 확인 후 작성한 제안. 팀 합의·구현·연동 검증 완료를 의미하지 않음.**
기존 API 경로·역할·DB 필드를 유지하고, 비어 있는 요청·응답과 연결 절차를 보충한다. 기존 코드를 이 문서만으로 변경하지 않았다.

## 1. 확인한 기준

- privacygate main: `e4a841987d70b0506f69c38a0eec895b9fed4ec0`
- matchmaking-internal main: `9b06793e1ad925dbf0598c622379752a8900fb3b`
- matchmaking-external 원격 저장소는 조회 시 비어 있었음. 외부 뼈대는 제공받은 ZIP 기준.
- 저장소의 전체 파일 목록 및 핵심 텍스트 파일 46개를 읽어 확인. 서버·테스트·컨테이너는 실행하지 않음.

| 구분 | 이미 정해진 내용 | 이번 문서에서 보충한 내용 |
|---|---|---|
| 이름 | 저장소·서비스·4개 업무 역할·쿠키·realm·기존 API 경로 | 새 역할·새 업무 DB를 추가하지 않음 |
| 식별자 | DB 식별자는 UUID; user_id=Keycloak sub, member_id는 별도 | API 문자열 직렬화·입력 필드 범위 |
| 시각 | timestamptz / API UTC ISO 8601 | 예시와 만료 검증 |
| 동의 | profile/photo/contact, member_id/counterpart_member_id, purpose | JSON 계약과 본인 확인 경계 |
| 목적 | seed에 introduction 존재 | 첫 시연의 목적은 이 값을 그대로 사용 |
| 다운로드 | delivery_problem의 requested_file_id 검사 및 baseline 인자 | 외부→중계의 선택적 file_id 전달 규약 |
| 오류 | errors.py에 형식·코드·HTTP 상태 정의 | 새 오류 코드를 임의로 추가하지 않음 |
| 세션 | 내부 서버 세션·외부 쿠키 이름·만료 정책 존재 | 외부 API 전용 저장 위치 |
| 업무 API | 경로·역할·일부 설명·DB 필드 존재 | 대부분의 입력·성공 응답은 신규 제안 |

## 2. 공통 규칙

- 기존 경로를 바꾸지 않는다. 내부 직원 브라우저는 internal-api를, 회원 브라우저는 external-api를 호출한다.
- 일반 JSON은 snake_case. 식별자는 UUID 문자열. 샘플 ID는 예시일 뿐 실제 레코드 존재를 보장하지 않는다.
- UTC 시각은 `2026-11-09T00:00:00Z` 형태. 요청에는 오프셋 있는 시각만 허용, 서버는 UTC로 정규화. 실제 만료는 현재 시각 이후로 계산한다.
- 목록 기본 형식은 `{"items": [...]}`. 첫 버전에서는 페이지네이션을 추가하지 않는다.
- 요청에 정의하지 않은 JSON 필드를 넣으면 422 INVALID_REQUEST. 필수 데이터형·길이·UUID·시각도 검증한다.
- 사용자 신원·역할은 토큰/서버 세션에서 얻는다. requester_id, approver_id, uploaded_by, member 본인 user_id는 브라우저 입력으로 받지 않는다.
- 버전·해시는 클라이언트의 주장을 믿지 않고 서버가 저장 파일에서 계산·조회한다.
- 브라우저의 변경 요청은 Origin + X-CSRF-Token 검사. 내부 서비스 호출은 브라우저 쿠키를 전달하지 않고 서비스/사용자 토큰으로 인증한다.
- 실제 토큰·비밀번호·세션 ID·파일 본문은 로그와 문서 예시에 넣지 않는다.
- 상태 충돌 409 INVALID_STATE, 형식·크기·허용하지 않은 파일 형식은 현재 공통 코드에 맞춰 422 INVALID_REQUEST. 서비스 장애는 기존 500 INTERNAL_ERROR, 내부 사유만 감사 기록에 남김.
- 성공 응답에 감사용 reason이나 내부 저장 경로를 노출하지 않는다.

기존 공통 오류:

```json
{"error":{"code":"APPROVAL_MISMATCH","message":"승인된 자료가 아닙니다.","trace_id":"추적번호"}}
```

고정 상태: scan_status=pending/passed/blocked/error, request_status=pending/approved/rejected/revoked/expired.

## 3. 인증과 세션

### 이미 있는 계약 유지

- GET /auth/login → Keycloak로 302.
- GET /auth/callback → state·nonce·PKCE·토큰 검증 후 세션 생성, 화면으로 302. 토큰을 응답 JSON에 넣지 않음.
- POST /auth/logout → 200 `{"logout_url":"Keycloak 로그아웃 URL"}`. 서버 세션 삭제·쿠키 제거 후 프론트가 URL로 이동.
- GET /api/me → 200 `{"user_id":"UUID","pseudo_id":"가명","role":"member","csrf_token":"세션별 토큰","gate_mode":"enforce"}`.
- external_session 쿠키: HttpOnly/Secure/SameSite=Strict. 로그인 진행 바인딩 쿠키 external_login은 내부 구현의 internal_login처럼 /auth 경로·Lax·10분 유효로 제안.
- 유휴 30분, 절대 8시간. 로그인마다 새 세션 ID. 외부 계정은 member만 허용.
- Keycloak 외부 회원 토큰의 aud에 transfer-api/internal-api가 **이미 모두 설정**됨.
- 외부→중계: Authorization=external-api 서비스 토큰, X-User-Token=회원 access token.
- 중계→내부: Authorization=transfer-api 서비스 토큰, X-User-Token=같은 회원 토큰.

### 외부 저장소: 신규 제안

- 외부 API만 마운트하는 `external-session-data` Docker 볼륨.
- `/data/external-session/sessions.sqlite3`: login_flows와 sessions 저장. 업무 회원·동의·증빙·반출 원본은 저장하지 않음.
- sessions: session_hash, user_id, pseudo_id, csrf_token, id_token, access_token, refresh_token, access_expires_at, created_at, last_seen_at, absolute_expires_at.
- login_flows: state_hash, binding_hash, nonce, code_verifier, expires_at. 성공/실패 처리 시 일회 소비.
- SQLite 내부 테이블에 업무 DB의 users를 참조하는 FK를 만들지 않음. token sub를 인증 사용자 식별자로 사용.
- 파일·볼륨은 외부 API 실행 계정만 접근. 브라우저·Nginx·내부 API·중계에 마운트하지 않음. Git·이미지에 런타임 DB를 넣지 않음.
- 기본은 단일 external-api 인스턴스. 다중 인스턴스가 실제로 필요해지면 세션 저장 어댑터를 Redis 등으로 교체. 지금 Redis 서버는 추가하지 않음.
- 기존 realm의 access token 수명은 300초. 세션 8시간과 별개이므로 만료 전 갱신을 구현해야 함. 갱신 실패/로그아웃/절대 만료이면 세션 폐기. refresh token이 회전하면 서버 저장값도 교체.
- 외부 pseudo_id를 업무 DB에서 직접 읽지 않음. 기존 seed의 M-B01과 일치시키려면 Keycloak user attribute/claim으로 pseudo_id를 제공하도록 **추가 설정 제안**. preferred_username을 DB 가명과 같다고 임의로 간주하지 않음.

## 4. 반출 파일 등록

직원 브라우저 → internal-api POST /api/transfer-files → transfer-api 같은 경로.

### 일반 업로드

- multipart/form-data: `file` 필수 1개. 신원 필드는 받지 않음.
- 최대 파일 크기는 기존 로컬 기준을 이어 **2 MiB (2,097,152 bytes)**로 제안. 애플리케이션과 프록시 제한을 함께 설정. multipart 오버헤드는 별도 고려.
- 이름·절대 경로를 저장 키로 사용하지 않음. 서버가 UUID와 안전한 저장 키를 생성.
- 201 응답: file_id, file_version, file_hash, scan_status, export_banned, created_at.
- 최초 구현은 동기 검사 후 최종 상태 응답. 비동기 검사를 선택하면 별도 파일 상태 조회 API를 팀 합의 후 추가해야 함. 현재 ZIP에 그 경로가 있다고 가정하지 않음.

### 최초 시연 파일 정책: 신규 제안

- 정상 소개 파일은 JSON으로 한정. `member_pseudo_id`, `age_band`, `job_group`, `introduction` 문자열 필드만 허용(각각 길이 제한). 이름·연락처·증빙 원문을 포함하지 않음.
- 전체 회원 CSV는 대기영역에 등록할 수 있으나 반출 금지로 분류. 일반 승인 경로로 제공하지 않음.
- PDF 증빙은 반출 파일이 아니라 7절의 증빙 제출 경로로 처리.
- 형식/허용 항목 검사를 passed라 부르더라도 백신 검사·실제 개인정보 전부 탐지·문서 진위 확인을 완료한 것이라고 표시하지 않음.
- 차단된 자료도 외부에 정적 공개하지 않음. 비교 모드의 파일 ID 변조가 대기 파일에 잘못 접근하는 상황을 재현.

### 내보내기 파일을 중계로 넘기는 연결: 신규 제안

- POST /api/admin/member-exports → 201 `{"export_id":"UUID","count":8,"created_at":"UTC 시각"}`. 개수는 실제 합성 DB 조회 결과이며 8로 고정하지 않음.
- 내부 서버가 export_id→파일·생성자·해시를 서버에서 관리. 현재 DB에 export manifest 테이블이 없으므로 저장 위치/메타데이터 방식은 팀원 1 구현 항목으로 명시해야 함.
- 같은 internal-api POST /api/transfer-files가 추가로 application/json `{"export_id":"UUID"}`를 받도록 제안.
- 이 경우 로그인한 직원 본인이 생성한 export인지 검사한 뒤 서버가 해당 CSV를 열어 중계에는 일반 multipart 업로드로 전달. JSON export_id를 중계가 내부 서버 경로처럼 열지 않음.
- 로컬 경로·임의 URL은 입력받지 않음. 기존 직원 역할/CSRF 검사는 유지.
- internal-api는 중계의 201 응답(file_id 등)을 직원에게 전달. A는 자기 export가 baseline에서 생성된 경우에만 이를 등록할 수 있음.
- enforce에서는 A의 내보내기 자체가 파일 생성 전에 거부되므로 같은 연결로 새 파일이 생기지 않음. 외부 방어 독립 시험은 미리 준비한 합성 대기 파일 사용.
- 데이터 관리자의 정상 내보내기 성공과 counselor의 반출 신청 권한은 별개. 이 연결 때문에 data_admin에 반출/승인 역할을 암묵적으로 추가하지 않음.

## 5. 반출 신청·승인·철회

### POST /api/export-requests

동일 경로의 내부 API는 신원을 확인한 후 중계에 JSON을 전달.

```json
{
  "file_id":"33333333-3333-4333-8333-333333333301",
  "member_id":"bbbbbbbb-0000-4000-8000-000000000001",
  "recipient_id":"22222222-2222-4222-8222-222222222201",
  "consent_id":"44444444-4444-4444-8444-444444444401",
  "purpose":"introduction",
  "expires_at":"2026-11-09T00:00:00Z"
}
```

- member_id는 소개 자료의 대상 회원. recipient_id는 받을 사람의 **user_id**. 둘을 같은 종류의 ID로 처리하지 않음.
- consent_id 예시는 임의 값. 실제 seed의 동의 ID는 DB 생성 UUID이므로 조회 결과를 사용.
- requester_id는 직원 토큰 sub. file_version/hash는 중계 파일 레코드에서 고정.
- 본인 업로드, passed, export_banned=false, 현재 동의·대상·수신자·목적 일치 확인. JSON 소개의 대상과 신청 member_id도 연결해 검사.
- 제안 만료 정책: 현재 이후 최대 7일, 동의 만료 이후로 제공 기한을 늘리지 않음.
- 201 응답: request_id, request_status=pending, created_at.

### 목록·상세

- GET /api/export-requests → items. counselor 본인 것, approver 심사 범위.
- GET /api/export-requests/{request_id} → 단일 객체.
- 공통 항목: request_id, requester_id, file_id, file_version, scan_status, member_id, member_pseudo_id, recipient_id, recipient_pseudo_id, consent_id, purpose, request_status, expires_at, created_at, approval_id(없으면 null), delivery_id(없으면 null).
- 상세에는 `preview`로 최소 소개 JSON을 제공해 승인자가 내용과 수신자를 확인. 승인자의 원본 증빙 열람 권한을 추가하는 것이 아님. 비공개 파일 경로는 반환하지 않음.
- 가명 표시값은 내부 서비스의 허용된 조회 결과로 얻어야 함. 중계가 업무 DB를 직접 읽지 않음.

### 결정

- POST /api/export-requests/{request_id}/approve: body `{}`. approver, 자기 승인 금지, pending/파일/동의/만료 재확인.
- 200: request_id, request_status=approved, approval_id, delivery_id, expires_at.
- 승인·전달 생성은 한 트랜잭션. DB의 신청자≠승인자 제약도 사용. 동시에 두 번 승인하면 한 번만 성공, 나머지 409.
- POST .../reject: body `{}`, 200 request_id, request_status=rejected.
- POST /api/approvals/{approval_id}/revoke: body `{}`, 200 approval_id, request_status=revoked, revoked_at.
- 임의 note 필드를 새로 받지 않음. 기존 DB에 note 컬럼이 없으므로 사유 자유문 저장을 당연한 기존 기능으로 보지 않음.

## 6. 관계·동의 확인: 내부 API 계약 신규 제안

POST /api/internal/disclosure-checks. transfer-api 서비스 토큰만 허용.

```json
{
  "member_id":"bbbbbbbb-0000-4000-8000-000000000001",
  "recipient_id":"22222222-2222-4222-8222-222222222201",
  "consent_id":"44444444-4444-4444-8444-444444444401",
  "item":"profile",
  "purpose":"introduction"
}
```

200 응답 제안:

```json
{
  "allowed":true,
  "consent_id":"44444444-4444-4444-8444-444444444401",
  "expires_at":"2026-11-09T00:00:00Z",
  "member_pseudo_id":"M-001",
  "recipient_pseudo_id":"M-B01"
}
```

- recipient_id의 활성 member 사용자→members.member_id를 서버가 매핑. source member_id와 counterpart_member_id가 정확히 맞는 동의만 유효.
- item, purpose, revoked_at, expires_at 확인. 조건 불충족은 200 allowed=false, 나머지 선택 필드는 null/필요 최소 가명만. 토큰 오류는 기존 401/403.
- 이 결과는 신뢰된 중계에만 제공하며 외부에 그대로 노출하지 않음. 외부에는 CONSENT_INVALID 등 공통 오류만.
- 현재 스키마에 별도 매칭 관계 테이블은 없음. 추가적인 '소개 관계' 요건이 필요하면 명시적으로 정의해야 하며, 없는 관계 테이블이 이미 있는 것처럼 구현하지 않음.
- 승인 시와 실제 다운로드 시 다시 확인. 장애/판정 실패 때 파일을 먼저 보내지 않음.

## 7. 증빙과 회원 동의

### 증빙

- POST /api/me/evidence-files → POST /api/internal/evidence-submissions → POST /api/evidence-files.
- multipart `file` 1개, 첫 버전 PDF만·최대 2 MiB 제안. 서버가 실제 형식 확인, 이름·경로 신뢰 금지.
- member_id/제출자/reviewer_user_id를 회원 요청으로 받지 않음. 내부 API가 토큰 sub→본인 회원 매핑 및 심사자 배정.
- 201: file_id, status=submitted, created_at, delete_after.
- GET /api/me/evidence-files → GET /api/internal/evidence-submissions → GET /api/internal/my-evidence-files.
- 200 items 항목: file_id, status(submitted/in_review/reviewed/deleted), created_at, review_result(passed/rejected/null).
- 원본·저장 경로·암호키·심사자 개인정보는 외부 응답에서 제외.
- 외부·중계는 증빙을 영구 보관하지 않음. HTTP 수신 중 제한된 임시 버퍼/스풀을 쓰더라도 정리하고 audit 대상에서 제외. 큰 본문을 무제한 메모리로 읽지 않음.

### 동의

- GET /api/me/consents → GET /api/internal/member-consents → GET /api/internal/my-consents.
- POST /api/me/consents → POST /api/internal/member-consents → POST /api/internal/my-consents.
- POST /api/me/consents/{consent_id}/revoke → POST /api/internal/member-consents/{consent_id}/revoke → POST /api/internal/my-consents/{consent_id}/revoke.
- 등록 JSON: counterpart_member_id(UUID), item(profile/photo/contact), purpose(첫 시연 introduction), expires_at(UTC ISO).
- 동의의 주체 member_id는 로그인 회원 본인으로 내부에서 결정. 받을 사람이 다른 회원 명의로 동의를 만들 수 없음.
- 201: consent_id, counterpart_member_id, counterpart_pseudo_id, item, purpose, expires_at, revoked_at=null.
- 목록은 같은 객체의 items. 철회 body `{}`, 200 consent_id, revoked_at. SQL DELETE 대신 기존 revoked_at 컬럼 사용.
- 화면에서 상대를 고르는 자료는 별도 미확정. 제안: 내부 GET my-consents의 응답에 `counterparts` 최소 목록(counterpart_member_id, counterpart_pseudo_id)을 함께 제공. 선택 가능한 소개 상대를 내부가 결정하고 이름·연락처를 보내지 않음. 허용 상대 정책은 팀원 1이 명시해야 하며 모든 회원을 무조건 선택 가능하게 만들지 않음.

## 8. 제공 목록과 다운로드

- GET /api/me/deliveries → POST /api/internal/delivery-queries.
- 중계 목록 요청 JSON은 `{}`. 수신자는 X-User-Token의 sub에서 결정, recipient_id 입력은 받지 않음.
- 200 items 항목: delivery_id, approval_id, file_id(본인 승인 건의 승인 파일 ID만), file_version, member_pseudo_id, purpose, request_status, expires_at, downloaded_at(null 가능), download_url.
- 공개 목록은 본인 제공 건만. downloaded_at은 기존 스키마대로 최근 수령 시각 한 개이며 별도 전체 다운로드 이력 테이블을 만들었다고 간주하지 않음.
- 표시 상태는 승인 취소/시간 만료를 반영. UI는 만료 등을 안내하되 실제 제공 여부는 서버가 매번 다시 판단.
- 정상 URL: GET /api/me/deliveries/{delivery_id}/download.
- 비교 시험용 선택 입력: `?file_id=UUID`. 정상 UI는 delivery_id만 사용해도 됨. 시험에서는 정상 승인 file_id를 넣은 요청을 준비한 뒤 그 값만 변경할 수 있음.
- external-api는 이 선택 입력을 검증·전달하고, 같은 경로의 transfer-api도 이를 받도록 변경 제안. 임의 파일 경로/URL은 받지 않음.
- enforce: 승인 파일 ID와 다르면 403 APPROVAL_MISMATCH, 파일 본문 없음. 같거나 생략됐어도 제공 파일은 서버 승인 기록에서 결정.
- baseline: 유효한 본인 승인 건의 권한을 요청 file_id에도 잘못 적용하는 결함만 비교 재현. 로그인·수신자·승인 건 유효성·CSRF·감사 기록은 유지.
- 현재 공통 delivery_problem(baseline=True)은 수신자 검사 직후 반환해 동의·만료 등까지 건너뜀. '대상 파일 연결 누락'만 재현하려면 공통 모듈의 baseline 분기 범위를 팀원 1과 조정해야 함. 새 코드에서 별도로 또 다른 판단 함수를 만들지 않음.
- 다른 요청 파일 ID를 무시하고 정상 파일을 200으로 보내는 방식도 유출을 방지하지만, 변조 요청을 명시적으로 거부한 것이 아니므로 이 프로젝트의 403 비교 기준과 구분.
- 성공 200은 실제 파일 바이트. JSON 소개라면 Content-Type application/json, Content-Disposition attachment, Cache-Control no-store. 오류에는 attachment 헤더·파일 일부도 포함하지 않음.
- 타인 delivery_id는 기존 설계의 공통 규칙에 맞춰 원칙적으로 404 NOT_FOUND. 이미 본인 것으로 확인된 제공 건의 파일 불일치는 403 APPROVAL_MISMATCH. 공통 함수의 타인 수신자 결과와 API의 조회/은닉 처리 차이를 시험으로 구분.
- 권한·승인·동의·버전·검사 상태·반출 금지 여부를 확인한 뒤 서버 파일을 읽음. 실제 바이트 해시도 검증하고 일치할 때만 응답을 전송. 파일 경로는 저장소의 서버 레코드에서만 선택. 반출 파일 교체 API를 임의로 만들지 않음.

## 9. 별도 개발 중 필요한 것 / 연결할 때 확인할 것

지금 고정: 경로·메서드, 위 JSON, UUID 의미, token header, 공통 오류, 파일 분류, 세션 저장소, 공격 ①→② 연결.

나중에 실환경 검증: 두 노트북 주소/방화벽/인증서, Keycloak 리다이렉트·발급 토큰, Docker 빌드·볼륨 권한, 실제 PostgreSQL 트랜잭션·망 분리, Wazuh 수집.

내부 비활성화·역할 변경: 내부 API는 현재 DB를 요청마다 확인하므로 정상 내부 경유 경로에서 즉시 거부 가능. 중계가 보는 과거 access token의 role은 그 토큰 만료 전까지 남을 수 있음. 현재 realm 수명은 5분이므로 중계 전체의 즉시 취소가 완성됐다고 표현하지 않음. 첫 범위는 내부의 현재 권한 확인+짧은 토큰 수명 유지; 중계까지 즉시 취소가 필수면 신뢰된 현재 사용자 상태 조회/취소 검증을 추가 설계.

추가 연동 사항:
- external-api에서 공통 privacygate 유틸리티를 사용하려면 Docker 빌드에도 패키지 설치가 필요. 현재 ZIP의 external-api Dockerfile은 이를 설치하지 않음.
- 현재 external-api Compose에는 GATE_MODE가 없음. /api/me의 gate_mode를 표시한다면 같은 설정을 주입하거나 신뢰된 서버 결과로 맞춰야 함. 클라이언트가 모드를 선택하지 않음.
- 공통 trace middleware는 API마다 새 trace_id를 만들므로 호출 연결용 ID 전파를 팀 합의 후 보완. 외부 임의 헤더를 검증 없이 감사 식별자로 신뢰하지 않음.
- 세션 저장 볼륨/권한, Keycloak pseudo_id claim, 파일 메타데이터 등 제안의 인프라 변경은 privacygate 담당과 합의.
- 파일 이름·다운로드 MIME을 안정적으로 저장하려면 transfer_files에 original_filename/media_type/size_bytes 같은 최소 메타데이터 컬럼 추가를 제안. 현재 컬럼에 이미 있다고 가정하지 않음. 감사 로그에는 파일명·본문을 넣지 않음.

## 10. 병합·제출 경계

- ZIP을 기존 폴더에 통째로 덮어쓰지 않음. 기존 UI는 재사용하고 인증/DB/오류/경로를 이 계약에 맞춰 이식.
- 새 역할·DB 컬럼·라우트를 기존 확정 사항처럼 표현하지 않음. 위 신규 제안은 합의 뒤 공통 명세와 코드에 반영.
- README.md 업로드 제외 유지. .venv, runtime DB/토큰, .env와 실제 키/인증서/비밀값 제외.
- 지금은 .gitignore와 이 검토 문서만 로컬에 작성. GitHub 수정/업로드·서버 연결·비교 공격 실행 없음.

## 확인한 원문

- [공통 반출 규칙](https://github.com/privacygate-matchmaking/privacygate/blob/e4a841987d70b0506f69c38a0eec895b9fed4ec0/src/privacygate/transfer_rules.py)
- [중계 DB](https://github.com/privacygate-matchmaking/privacygate/blob/e4a841987d70b0506f69c38a0eec895b9fed4ec0/infra/db/transfer/01-schema.sql)
- [업무 DB](https://github.com/privacygate-matchmaking/privacygate/blob/e4a841987d70b0506f69c38a0eec895b9fed4ec0/infra/db/business/01-schema.sql)
- [회원·동의 seed](https://github.com/privacygate-matchmaking/privacygate/blob/e4a841987d70b0506f69c38a0eec895b9fed4ec0/infra/db/business/03-seed.sql)
- [Keycloak realm](https://github.com/privacygate-matchmaking/privacygate/blob/e4a841987d70b0506f69c38a0eec895b9fed4ec0/infra/keycloak/realm-matchmaking.json)
- [Compose](https://github.com/privacygate-matchmaking/privacygate/blob/e4a841987d70b0506f69c38a0eec895b9fed4ec0/compose.yaml)
- [내부 업무 API 뼈대](https://github.com/privacygate-matchmaking/matchmaking-internal/blob/9b06793e1ad925dbf0598c622379752a8900fb3b/internal-api/app/routers/business.py)
- [내부 로그인 구현](https://github.com/privacygate-matchmaking/matchmaking-internal/blob/9b06793e1ad925dbf0598c622379752a8900fb3b/internal-api/app/routers/auth.py)
- [API 명세](https://github.com/privacygate-matchmaking/matchmaking-internal/blob/9b06793e1ad925dbf0598c622379752a8900fb3b/docs/api-spec.md)
