# 만남 일정 조율 — 구현 및 내부 화면 연결

2026-11-13. [내부 전달 파일](internal-handoff.md)의 정책 합의 전 외부 유료 매칭 기준입니다. 챗봇 없이 **직원 후보 제안 → 양쪽 회원 응답 → 직원 최종 확정**으로 진행합니다. 기존 유료 매칭이 성립한 건에 일정을 연결하며, 기존 매칭 방식이나 이용권 정책은 교체하지 않습니다.

## 사용 흐름

1. 담당 상담 직원이 날짜·시간·장소·주소 후보를 최대 3개 제안합니다. 과거 일정·공백 장소·중복 후보는 거부합니다. 제안 범위는 현재 이후 180일입니다.
2. 회원은 매칭 현황 → **만남 일정 확인**에서 각 후보에 `가능해요` 또는 `다른 일정 요청`으로 응답합니다. 의견은 선택이며 300자 이내입니다.
3. 양쪽이 같은 후보에 가능하다고 응답해도 자동 확정하지 않습니다. 담당 직원이 최종 확정해야 화면에 확정으로 표시됩니다.
4. 확정된 일정에 회원이 변경 요청을 보내면 확정을 즉시 해제합니다. 다시 양쪽 의사를 확인하고 직원이 확정해야 합니다.
5. 날짜·시간·장소를 바꾸려면 새 후보를 만듭니다. 이전 제안은 superseded로 남고, 이전 응답으로 새 일정을 확정할 수 없습니다. 이미 확정된 일정은 먼저 취소해야 새 후보를 제안할 수 있습니다.

변경 요청·직원 취소·재제안은 이용권/횟수를 추가 차감하지 않습니다. 기존 매칭 수락으로 사용된 1회는 기존 정책대로 유지하며, 일정 취소가 매칭 관계 종료나 기존 1회 환불을 자동 실행하지는 않습니다.

기존 백엔드에는 일정 버튼을 표시하지 않습니다. 새 `/api/me`의 `meeting_scheduling=true`를 받은 경우에만 표시하므로, 정적 파일과 API 이미지 교체 시점이 달라도 기존 매칭 화면이 새 API를 강제 호출하지 않습니다.

## 개인정보와 권한

- 회원은 매칭 당사자만 자기 일정에 응답할 수 있습니다. 응답 body에 user_id나 직원 ID를 넣을 수 없습니다. 브라우저 변경 요청에는 기존 CSRF·Origin 검사를 적용합니다.
- 다른 회원에게는 개인 의견을 보내지 않습니다. 직원도 내부 API가 **현재 담당 회원으로 확인한 사람의 의견만** 읽습니다. 상대 회원의 가능 여부는 일정 조율에 사용하지만 담당 밖 회원 의견 원문은 제외합니다.
- 상담 직원 역할과 업무 DB의 현재 활성·담당 관계를 함께 확인합니다. JWT에 과거 역할이 남아 있어도 내부 확인 API가 허용하지 않으면 관리할 수 없습니다.
- 매칭이 종료되거나 상태 연동에서 회원이 비활성으로 확인되면 회원 일정 API도 거부합니다.
- 매칭·이용권과 동일한 트랜잭션 잠금으로 확정/변경 요청을 직렬화합니다. 두 요청이 경쟁해도 다른 일정이나 동의하지 않은 후보를 확정하지 않습니다.
- 감사 로그에는 meeting.propose/reply/confirm/cancel, 가명 사용자, 대상 ID·추적 ID를 기록합니다. 의견·장소·주소·연락처·토큰 본문을 남기지 않습니다.

## 외부·중계 구현

| 위치 | 구현 |
|---|---|
| external-web | 매칭별 일정·확정 결과 확인, 가능 여부·개인 의견 제출, 확정 후 변경 요청 |
| external-api | 회원 일정 조회·응답 BFF. 서버 세션·CSRF 유지 |
| transfer-api | 직원 제안·확정·취소 API, 회원 응답, 담당 범위 확인·개인 의견 필터링 |
| 중계 DB | 002_meetings.sql의 별도 일정/응답 테이블. 기존 자료·매칭권 테이블 삭제 없음 |
| 직원 화면 부품 | integrations/internal-scheduling/panel.js. 내부 담당자가 기존 직원 화면에 연결 |

회원 API:

| 메서드 | 경로 | 입력 |
|---|---|---|
| GET | /api/me/matches/{match_id}/meetings | 없음 |
| POST | /api/me/matches/{match_id}/meetings/{proposal_id}/reply | `{availability:"available" 또는 "change_requested",private_note?:string}` |

회원 BFF는 중계 `/api/internal/matching/requests/{match_id}/meetings`와 그 하위 reply로 전달합니다. 기존 서비스 토큰과 X-User-Token을 검증합니다. 회원 응답에는 my_reply만 포함하며 상대 의견·직원 ID·직원용 replies는 포함하지 않습니다.

## 팀원 1이 연결해야 하는 내부 부분

### A. 현재 담당 관계 확인 API — 신규 필요

`POST /api/internal/matching-staff-checks`

인증: `Authorization`은 transfer-api 서비스 토큰, `X-User-Token`은 **상담 직원** access token입니다. 기존 회원 전용 member_via_transfer 가드를 그대로 사용하면 안 됩니다. 직원 토큰은 issuer/audience/azp/서명을 검증하고, 현재 업무 DB에서도 직원 활성 상태·counselor 역할을 다시 확인해야 합니다.

요청 예:

```json
{
  "participant_user_ids": ["<신청 회원 user UUID>", "<상대 회원 user UUID>"],
  "purpose": "meeting_schedule",
  "action": "read"
}
```

action은 read/propose/confirm/cancel입니다. 두 회원이 활성 상태이고 업무상 연결 가능한 소개 관계인지 확인한 뒤, 직원에게 두 회원 중 담당 회원이 하나 이상 있을 때만 일정 조율을 허용하는 계약입니다. 일반 직원의 전체 회원 조회를 허용하는 API로 만들면 안 됩니다.

허용 예:

```json
{"allowed":true,"assigned_user_ids":["<현재 직원이 실제 담당하는 참여 회원 user UUID>"]}
```

거부는 `{allowed:false,assigned_user_ids:[]}` 또는 공통 403입니다. assigned_user_ids는 요청 참여자 중 실제 담당자만 포함하며, 회원 member UUID가 아닌 **user UUID**입니다. 이 목록에 없는 사람의 개인 의견은 중계가 직원 응답에서 제거합니다. 목록이 생략되면 의견 원문을 모두 숨깁니다. API 실패·미구현·잘못된 응답을 허용으로 처리하지 않습니다.

### B. 직원 화면용 BFF

내부 브라우저에서 아래 주소를 internal-api로 호출하고, internal-api가 같은 경로의 transfer-api로 전달합니다. 기존 transfer_client를 이용하되 서비스 비밀값/JWT를 브라우저에 넣지 않습니다. counselor 로그인·POST CSRF 검사를 유지합니다.

| 메서드 | 내부 BFF → 중계 경로 | 입력 |
|---|---|---|
| GET | /api/matching/requests | 없음. 담당 가능한 성사 매칭 목록, 최대 100건 |
| GET | /api/matching/requests/{match_id}/meetings | 없음 |
| POST | /api/matching/requests/{match_id}/meetings | `{options:[{starts_at,venue,address}]}` |
| POST | /api/matching/requests/{match_id}/meetings/{proposal_id}/confirm | `{}` |
| POST | /api/matching/requests/{match_id}/meetings/{proposal_id}/cancel | `{}` |

starts_at은 UTC 또는 오프셋이 명시된 ISO 날짜입니다. 직원용 화면 부품은 한국 시간으로 입력받아 +09:00을 명시해 전송합니다. 임의 upstream 경로를 브라우저가 지정하는 범용 프록시는 만들지 않습니다.

### C. 직원용 화면 부품

`integrations/internal-scheduling/panel.js`를 내부 정적 리소스에 추가하고, 기존 직원 화면의 일정 관리 영역에서 호출합니다. 이 파일은 완성된 독립 직원 앱이 아니라 기존 직원 로그인·BFF를 사용하는 화면 부품입니다.

```javascript
await MeetingStaffUI.render({
  root: document.querySelector('#meeting-panel'),
  api: async (path, options = {}) => {
    // 내부 앱의 기존 CSRF·로그인 처리 함수를 사용하고 JSON 객체를 반환하세요.
    return await existingInternalApi(path, options);
  },
  toast: showMessage,
  isCurrent: () => currentPage === 'meetings'
});
```

matchLabel 함수를 추가하면 내부 직원에게 허용된 이름·코드로 선택 목록을 표시할 수 있습니다. 회원 화면에 이 직원용 응답을 재사용하면 안 됩니다.

## 실행과 현재 한계

기존처럼 Start.ps1 또는 Compose 빌드로 실행하면 matching-migrate 후 meeting-migrate가 002_meetings.sql을 적용합니다. 성공 상태는 Exited(0)이며 실패하면 transfer-api를 시작하지 않습니다. 기존 DB를 지우지 않습니다.

현재 코드는 내부·공통 저장소를 수정하지 않았습니다. A/B/C가 내부에 연결되기 전에는 실제 직원 화면에서 일정을 발송할 수 없습니다. 기본 내부 연결 모드는 확인 API가 없을 때 거부합니다. 명시적인 합성 실습 모드에서는 기존 실습 counselor_a와 합성 회원 4명만 대상으로 제한하며, 이를 실제 담당 관계 검증으로 해석하지 않습니다.

이번 범위는 **일정 조율**입니다. 일정 확정이 연락처 제공 동의·반출 승인이나 연락처 전송을 자동 생성하지 않습니다. 별도 연락처 최종 승인·기간 제한 제공과 만남 후 피드백은 후속 구현입니다. 기존 매칭의 72시간 응답 기한도 이번에는 24시간으로 바꾸지 않았습니다.

## 확인 결과

Python 109개, Node 화면 제어 9개 통과. 일정 관련 시험은 양쪽 동의 전 확정 거부, 중복 확정, 변경 요청 후 확정 해제, 이전 제안 응답 재사용 거부, 타 회원·비담당 직원 접근 거부, 담당 밖 회원 의견 제외, 의견 로그 제외, 추가 차감 없음, 확정과 변경 요청의 동시 실행을 포함합니다. 기존 매칭·다운로드·회원 상태·Wazuh 도구 시험도 함께 통과했습니다.

기본·회원 상태 활성화·합성 실습 Compose 병합 세 구성과 JavaScript 구문·HTML 참조를 확인했습니다. 이후 사용자 PC에서 중계 DB를 백업하고 002_meetings.sql을 실제 PostgreSQL에 적용해 두 테이블 생성을 확인했습니다. 외부·중계 이미지를 빌드·기동하고 화면 확인을 마쳤습니다. 시험의 내부 담당 확인 API는 대역이며, 실제 내부 직원 BFF·화면 연결은 남아 있습니다. 내부 업무 DB는 변경하지 않았습니다.
