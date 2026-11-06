# 명칭

| 구분 | 명칭 |
|---|---|
| 내부 저장소 | matchmaking-internal |
| 외부·중계 저장소 | matchmaking-external |
| 공통 보안 패키지·인프라 | privacygate |
| 가상 서비스 | 이음 |
| Compose 서비스 | internal-web, internal-api, external-web, external-api, transfer-api |
| 업무 / 반출 DB | matchmaking_business / matchmaking_transfer |
| 저장소 볼륨 | evidence-storage, transfer-staging, transfer-verified, external-session-data |
| 인증 realm | matchmaking |
| 인증 클라이언트 | internal-web, external-web, transfer-api, external-api |
| 역할 | member, counselor, approver, data_admin |
| 외부 세션 쿠키 | external_session |
| 감사 로그 서비스명 | external-api, transfer-api |

리소스 식별자는 UUID 문자열, 시각은 시간대가 있는 ISO 8601입니다. 목적 코드는 introduction, 동의 항목은 profile, photo, contact입니다. 현재 소개자료 반출에는 profile 동의를 사용합니다. file_id, request_id, approval_id, delivery_id는 별개 식별자입니다.
