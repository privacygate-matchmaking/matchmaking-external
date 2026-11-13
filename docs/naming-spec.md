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
| 인증 클라이언트 | internal-web, external-web, transfer-api, external-api, internal-admin(내부 계정 정리 전용) |
| 역할 | member, counselor, approver, data_admin |
| 외부 세션 쿠키 | external_session |
| 감사 로그 서비스명 | internal-api, external-api, transfer-api |
| 관제 Compose 프로젝트 | ieum-wazuh |
| 회원 로그인 테마 | ieum-members |
| 중계 매칭·일정 테이블 | matching_* (일정: matching_meeting_proposals, matching_meeting_replies) |

리소스 식별자는 UUID 문자열, 시각은 시간대가 있는 ISO 8601입니다. 목적 코드는 introduction, 동의 항목은 profile, photo, contact입니다. 현재 소개자료 반출에는 profile 동의를 사용합니다. file_id, request_id, approval_id, delivery_id는 별개 식별자입니다. 매칭 공개 동의의 scopes는 기존 반출 item과 별개이며 profile/photo/contact/education/employment/income/marital_status를 사용합니다. 관리자 회원 코드와 외부 공개 별칭은 구분합니다. [매칭 계약](matching-api.md)과 [차이·합의 사항](internal-handoff.md)을 확인합니다.
