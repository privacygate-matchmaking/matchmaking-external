# 매칭·이용권 연동 명세

2026-11-12 · 외부 저장소 구현 기준. 내부 저장소는 변경하지 않았다.

## 데이터와 책임

| 기준 데이터 | 담당 서비스 | 원칙 |
|---|---|---|
| 회원, 직원 역할·활성 상태·담당 관계, 증빙 원본·검토 결과 | internal-api | 현재 DB 기준으로 검사. 과거 토큰의 역할만으로 상세 열람 허용 금지 |
| 이용권·주문·매칭 신청·정보 공개 동의 | transfer-api | `matchmaking_transfer`의 `matching_*` 별도 테이블. 외부에서 정한 회원 ID·횟수·가격을 신뢰하지 않음 |
| 회원 세션·암호화된 토큰 | external-api | 외부 전용 SQLite·Docker 볼륨 유지 |
| 원본 AI/OCR·사진 마스킹 | 이번 범위 밖 | 직원 검토를 보조하는 후속 기능. AI 결과를 문서 진위 보증으로 취급하지 않음 |

후보는 **공개 가능한 최소 정보만** 포함한다. 증빙 심사 완료가 원본 공개 승인을 뜻하지 않는다. 매칭 성립, 상대 방향의 공개 동의, 현재 검토 결과를 모두 확인한 뒤 결과 항목만 제공한다.

## 외부 API

모두 로그인한 `member` 전용. 사용자 식별은 서버 세션에서 얻는다. POST에는 기존 `Origin` 및 `X-CSRF-Token`이 필요하다. UUID 입력, 알 수 없는 필드 거부, UTC ISO 8601 시각을 사용한다.

외부 `/api/me/…`는 중계의 `/api/internal/matching/…`로 호출한다. 중계는 기존 서비스 인증과 회원 토큰을 **모두** 검사한다.

| 외부 경로 | 중계 끝 경로 | 입력·결과 |
|---|---|---|
| GET `/api/me/candidates` | `candidates` | `self`, `items`, `data_source`, `notice` |
| GET `/api/me/membership` | `membership` | `plan`, `wallet`, `orders`, `test_payments_enabled`, `data_source` |
| POST `/api/me/orders` | `orders` | `{plan_code:"ieum_6m_5",idempotency_key:UUID}` → 주문, 201 |
| POST `/api/me/orders/{id}/test-confirm` | `orders/{id}/test-confirm` | `{}` → `{order_id,status:"paid",test_payment:true}` |
| GET `/api/me/matches` | `requests` | 본인이 참여한 신청 `items`, `data_source` |
| POST `/api/me/matches` | `requests` | `{recipient_member_id:UUID,idempotency_key:UUID}` → 신청, 201 |
| POST `/api/me/matches/{id}/decision` | `requests/{id}/decision` | `{action:"accept"\|"reject"\|"cancel"\|"end"}` → 신청 |
| GET `/api/me/matches/{id}/sharing` | `requests/{id}/sharing` | `{match_id,status,own_grant}` |
| POST 같은 `/sharing` | 같은 경로 | `{scopes:[항목],expires_at:시각}` → `{grant_id,scopes,expires_at}` |
| POST `/api/me/matches/{id}/sharing/revoke` | `requests/{id}/sharing/revoke` | `{}` → `{match_id,revoked:true}` |
| GET `/api/me/matches/{id}/profile` | `requests/{id}/profile` | 상대가 허용한 검토 결과만 반환 |

`items`의 공개 프로필: `member_id`, `pseudo_id`, `age_band`, `job_group`, `introduction`. 후보의 `user_id`는 브라우저로 보내지 않는다. `self`만 신청 자격 안내용 `eligible`을 추가한다.

신청 결과: `match_id`, `status`, `credit_state`, `created_at`, `expires_at`, `decided_at`, `ended_at`, `direction(sent/received)`, `peer(최소 프로필)`.

`wallet`: `active,total,reserved,used,available,expires_at` 및 활성 이용권의 `membership_id`. 금액·기간·총횟수는 서버 고정 값이다. 주문 확인은 30분 이내, 본인 주문만 가능하다. 동일 요청 키·동일 응답의 재전송은 중복 차감·지급하지 않는다.

`scopes`: `profile`, `photo`, `contact`, `education`, `employment`, `income`, `marital_status`. 동의 기한은 현재 이후 최대 7일. 동의는 **내 정보 → 해당 상대** 방향이다. 상대가 자기 정보를 공개하려면 별도로 동의해야 한다. 수락 시 이용권 만료를 다시 차감하지 않으며 신청 시 예약한 권리를 확정한다.

## 내부에 필요한 추가 API — 구현 요청

두 API는 이번 외부 저장소에서 호출 계약만 만들었다. 내부 담당자가 구현해야 기본 모드에서 실제 연동된다. 기존 내부 API와 충돌하지 않는 새 경로이며, 내부는 서비스 인증·회원 토큰을 검증해야 한다.

### POST `/api/internal/matching-directory`

입력 `{}`. 조회자 신원은 토큰으로 결정한다. 응답:

```json
{
  "self": {
    "member_id": "cccccccc-0000-4000-8000-000000000001",
    "user_id": "22222222-2222-4222-8222-222222222201",
    "pseudo_id": "M-B01", "age_band": "30대", "job_group": "기획·운영",
    "introduction": "공개용 소개", "eligible": true
  },
  "items": []
}
```

`items`도 같은 형식, 최대 100명. `self`는 토큰의 sub와 일치해야 한다. 자신·중복 회원을 후보에 넣지 않는다. `eligible`은 실제 bool이며 내부의 현재 활성 상태와 필수 검토 완료 여부로 결정한다. 후보용 소개 문구에도 이름·정확한 직장·연락처·문서 번호를 넣지 않는다. 정렬은 내부 정책이며 이번 구현에 AI 추천 점수는 없다.

### POST `/api/internal/matching-disclosures`

중계가 현재 매칭·방향별 동의 검사 후 서버에서 구성하는 입력:

```json
{
  "member_id": "cccccccc-0000-4000-8000-000000000002",
  "recipient_id": "22222222-2222-4222-8222-222222222201",
  "grant_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  "match_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  "scopes": ["employment"],
  "expires_at": "2026-11-13T03:00:00Z",
  "purpose": "introduction"
}
```

내부는 호출 서비스·회원 토큰·수신자 일치, 대상 회원의 현재 활성·검토 상태를 확인한다. 브라우저가 직접 구성한 문맥을 신뢰하면 안 된다. 응답은 원본, 저장 경로, 서명 없는 파일 URL을 포함하지 않는다.

```json
{
  "member_id": "cccccccc-0000-4000-8000-000000000002",
  "fields": [
    {"scope":"employment", "label":"재직 확인", "value":"재직 자료 검토 완료 · 연구·개발",
     "review_status":"reviewed", "reviewed_at":"2026-11-12T03:00:00Z"}
  ]
}
```

최대 7개, label 80자·value 300자. 내부에서 미리 공개용으로 승인·마스킹한 텍스트만 반환한다. 중계도 동의한 항목만 다시 필터링하고, 내부 처리 후 동의 철회·관계 종료 여부를 재검사한다. 아직 제공할 수 없는 항목은 생략한다. 외부 응답의 `unavailable_scopes`에 표시된다. **현재 사진·마스킹 PDF 다운로드 계약은 없으며 텍스트 결과만 연결 가능하다.** 후속 파일 연동 시 기존 승인 파일·버전·해시 검사를 적용할 계약이 별도로 필요하다.

직원이 원본을 열람·검토하는 API는 내부에서 **현재 역할·담당 회원**을 매 요청 검사해야 한다. 직원 화면에 메뉴를 숨기는 조치만으로 충족되지 않는다. 이 두 신규 API는 직원 원본 열람 API가 아니다.

## 기존 반출과의 관계

기존 `/api/me/deliveries` 및 `/api/me/consents`는 내부 업무의 소개 파일·동의 계약을 그대로 사용한다. 새 매칭 동의는 `matching_grants`가 기준이며 **기존 파일 동의와 자동 동기화되지 않는다**. 기존 공격 비교의 정상 승인·수신자·동의·만료 검사와 file_id 연결 차이는 바꾸지 않았다.

따라서 이번 매칭 결과 열람의 동의 철회가 기존에 별도 승인한 다운로드까지 자동 철회한다고 설명하면 안 된다. 이를 하나의 업무로 통합하려면 내부가 신규 매칭 관계·방향별 동의 ID를 기존 반출 신청에 연결하고, 중계가 다운로드 시 현재 동의를 재확인하도록 후속 합의해야 한다. 동일 동의의 기준을 두 곳에서 각각 편집하는 설계는 피한다.

## 오류와 감사

기존 오류 포맷 `{error:{code,message,trace_id}}` 유지. 세션 없음 401, 권한·공개 동의 위반 403, 없는 대상 404, 중복·잘못된 상태·횟수 소진 409, 입력 오류 422. 내부 501·중계 인증 오류는 사용자 세션을 지우지 않고 서비스 오류 500으로 안내한다.

매칭 신청·응답·동의 변경·결과 열람·시험 결제 확인을 기존 감사 JSON에 가명 사용자·대상 ID로 기록한다. 실패도 기존 예외 처리에서 사유 코드를 기록한다. 결과 본문·비밀번호·토큰은 기록하지 않는다. **Wazuh 신규 매칭 경보 규칙과 실제 수집 확인은 이번에 완료하지 않았다.**
