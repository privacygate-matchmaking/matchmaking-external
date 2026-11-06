# 내부 담당자 연동 요구사항

이 문서는 외부 저장소의 현재 구현 계약입니다. 내부 저장소 코드·README는 수정하지 않았습니다. upstream에서 미구현인 업무 응답은 아래 형식으로 맞춰야 실제 업무가 연결됩니다. API 경로는 기존 설계를 따르고, 미정이던 응답 필드·세션 저장·비교 모드의 범위를 구체화했습니다.

## 1. 내부에 필요한 고정 API

중계는 transfer-api 서비스 토큰을 보냅니다. 회원 업무에는 X-User-Token을 함께 보냅니다. 내부는 두 토큰을 검증하고 회원 주체를 토큰에서 결정해야 합니다.

### POST /api/internal/disclosure-checks

입력:
```json
{
  "member_id": "<정보 주체 member UUID>",
  "recipient_id": "<수신자 user UUID>",
  "consent_id": "<동의 UUID>",
  "purpose": "introduction",
  "item": "profile"
}
```

허용 응답:
```json
{"allowed":true,"expires_at":"2026-12-01T00:00:00Z","member_pseudo_id":"M-001"}
```

거부 응답은 {"allowed":false} 또는 공통 403 오류입니다. 조회 실패·미구현을 허용으로 해석하지 않습니다. 내부에서 주체·수신자 관계, 해당 동의의 정보 주체/수신자/항목/목적/철회·만료를 확인해야 합니다. expires_at은 유효한 동의의 만료이며 member_pseudo_id는 파일의 정보 주체와 연결하는 가명입니다. 신청·승인·다운로드마다 다시 호출합니다.

### 증빙

- POST /api/evidence-files: multipart file(PDF), 주체는 회원 토큰. 성공 201, 최소 file_id 및 status를 반환하도록 합의가 필요합니다.
- GET /api/internal/my-evidence-files: {items:[{file_id,status,created_at,review_result}]}.
- 상태 예: submitted, in_review, reviewed, deleted. review_result는 선택입니다.
- 외부·중계는 원본을 영구 보관하지 않습니다. 내부가 암호화·열람 권한·삭제 기한을 담당합니다. PDF magic/크기 확인은 포맷 검사의 전부가 아니므로 내부에서 유효성 및 보관 정책을 수행해야 합니다.

### 동의

- GET /api/internal/my-consents:
```json
{
  "items":[{"consent_id":"<UUID>","counterpart_pseudo_id":"M-001","item":"profile","purpose":"introduction","expires_at":"2026-12-01T00:00:00Z","revoked_at":null}],
  "counterparts":[{"counterpart_member_id":"<UUID>","counterpart_pseudo_id":"M-001"}]
}
```
- counterparts는 회원에게 허용된 소개 상대만 포함합니다. 없으면 외부 화면의 새 동의 등록을 비활성화합니다.
- POST /api/internal/my-consents: counterpart_member_id, item, purpose, expires_at → 201 {consent_id,...}.
- POST /api/internal/my-consents/{consent_id}/revoke: {} → {consent_id,revoked_at}.
- 내부에서 본인 소유 및 관계를 확인합니다. 전달된 상대 UUID만으로 동의를 생성하면 안 됩니다.

## 2. 내부 생성 파일의 반출 등록

내부 내보내기 기능이 생성한 파일은 브라우저 공개 경로에 두지 않습니다. 내부 백엔드가 소유한 파일을 읽어 POST /api/transfer-files에 multipart로 전송하고 반환된 file_id를 연결합니다. 로컬 파일 경로나 DB·볼륨을 외부에 공유하지 않습니다. 중계 등록 호출에는 해당 counselor의 직원 토큰을 전달합니다.

현재 등록 주체는 counselor로 제한했습니다. data_admin의 정상 전체 내보내기는 내부에서 별도로 수행합니다. baseline에서 counselor가 확보한 합성 CSV는 반출 신청·승인 없이 차단된 대기 파일로 등록할 수 있습니다. 정상 최소 소개 JSON은 별도로 등록·신청·승인합니다. 관리자 API의 권한 검사 누락/적용 비교 구현은 내부 담당 범위입니다.

## 3. 다운로드 비교의 정확한 범위

delivery_id → approval_id → approved_file_id 연결은 DB가 보관합니다. B의 유효한 정상 제공 건은 유지하고 query file_id만 다른 합성 대기 파일 UUID로 변경합니다.

- baseline: 정상 승인·수신자·동의·만료·승인 파일 무결성 검증 후 요청 대상 선택의 연결 검사만 빠집니다.
- enforce: 요청 file_id가 승인 파일과 다르면 403 APPROVAL_MISMATCH, 바이트 미반환.
- 변경값을 무시하고 승인 파일을 200으로 반환하는 방식은 사용하지 않습니다.
- 내부 공격 차단 후 외부 공격을 독립 시험할 때는 합성 대기 파일을 미리 준비했음을 표시합니다.

## 4. 인증·현재 권한·추적

네 역할은 member / counselor / approver / data_admin입니다. approver는 유지하며 자기 신청 승인을 거부합니다.

JWT 역할 변경은 토큰 재발급 전까지 과거 값일 수 있습니다. 내부 BFF에서 현재 직원 활성 상태·업무 권한을 검사해야 합니다. 현재 중계는 유효 JWT를 검증하며 즉시 권한 회수는 아직 보장하지 않습니다. 즉시 강제가 요구되면 별도의 현재 권한 조회 계약을 함께 추가해야 합니다.

외부 BFF 세션은 SQLite + 전용 볼륨, 토큰 암호화 키는 로컬 Docker secret으로 결정했습니다. 외부 세션을 업무 DB에 넣지 않습니다. 외부→중계의 trace_id는 인증된 서비스 요청에서만 32자리 hex로 전달받습니다. 중계→내부도 헤더를 전달하지만 내부 middleware가 새 ID를 생성하면 추적이 끊기므로 팀 간 조정이 필요합니다.

감사 로그에는 가명 주체·결과·사유 코드만 기록합니다. 다운로드 거부 주 이벤트는 delivery ID, 별도 delivery.reference 이벤트는 승인/요청 파일 ID를 같은 trace_id로 연결합니다. 개인정보 본문·토큰은 기록하지 않습니다. Wazuh 상관 규칙은 참고 이벤트를 반복 요청 횟수로 중복 계산하면 안 됩니다.

## 5. 연결 직전 공동 확인

- 실제 내부 업무 API의 위 응답 및 오류 형식.
- 두 Compose 파일을 합친 빌드, 세션 키·볼륨 권한, PostgreSQL 스키마.
- 공개 Keycloak issuer/redirect URI와 컨테이너용 discovery/token/JWKS 주소.
- 내부 현재 권한 반영 방식, 동의 철회 후 다음 다운로드 차단.
- 로컬 호스트 시험과 두 노트북 물리 연결의 범위 구분.
- 합성 자료만 사용하여 정상/비정상 요청을 동일 조건에서 재시험.
