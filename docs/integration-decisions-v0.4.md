# 외부·중계 연동 계약안 v0.4

구현 결과 기준 / 팀 공유용 / 2026.11.06

> 외부 화면·외부 BFF·반출 중계를 구현했다. 아래는 새로 정한 계약과 팀 간 연결 시 지켜야 할 사항이다. 공통 privacygate 패키지는 변경하지 않았으며 내부 저장소의 코드·README도 수정하지 않았다.

## 01  추가 결정과 구현 결과

| 항목 | 결정 · 이유 · 적용 결과 |
| --- | --- |
| 다운로드 식별자 | delivery_id로 승인 문맥을 찾는다. 선택 query file_id는 비교 시험에 사용한다. 별도 approval_id 입력은 받지 않으며 DB에서 연결한다. |
| 비교 범위 | baseline도 정상 승인·수신자·동의·만료·승인 파일 무결성을 검증한다. 요청 file_id와 승인 파일의 연결만 생략한다. enforce는 불일치 시 403 APPROVAL_MISMATCH로 거부한다. |
| 외부 로그인 세션 | external-api 전용 SQLite + external-session-data 볼륨. 토큰은 별도 키로 암호화하고 브라우저에는 external_session 쿠키만 제공한다. 업무 DB 접근을 추가하지 않는다. |
| 인증·갱신 | Keycloak external-web / Code+PKCE. access token 만료 임박 시 서버에서 갱신·신원 재검증한다. 유휴 30분·절대 8시간이며 변경 요청은 CSRF+Origin 검사한다. |
| 반출 파일 등록 | 내부 백엔드가 파일 바이트를 multipart로 중계에 전송하고 UUID file_id를 받는다. 외부와 내부의 파일 경로·볼륨을 공유하지 않는다. |
| 정상 승인 파일 | 소개 JSON의 키를 member_pseudo_id, age_band, job_group, introduction으로 고정했다. 각 값은 1~300자 문자열. 신청 시 동의의 정보 주체 가명과 일치해야 한다. |
| 대기 파일 처리 | CSV 등 스키마 밖 파일은 blocked / export_banned=true로 사설 대기영역에 등록된다. 정상 승인할 수 없고 비교 시험에서 비승인 대상 파일로 사용한다. 백신·범용 DLP 검사를 뜻하지 않는다. |

## 02  역할·기간·데이터 형식

역할은 member / counselor / approver / data_admin을 유지한다. counselor는 등록·신청, approver는 승인·거절·철회하며 자기 승인은 금지한다. data_admin의 전체 회원 내보내기는 내부 담당 범위다.

ID는 UUID, 시각은 시간대 포함 ISO 8601. purpose=introduction, 동의 item=profile/photo/contact. 현재 소개 자료 반출은 profile 동의를 사용한다. 반출 기한은 최대 7일이며 동의 만료를 넘을 수 없다.

# 내부 담당자가 맞춰야 할 계약

## 03  내부 → 중계 호출

내부 BFF는 직원 access token(aud=transfer-api, azp=internal-web)을 Authorization: Bearer로 전달한다. 화면의 메뉴 숨김과 별개로 내부 서버의 현재 업무 권한 검사도 필요하다.

| 중계 API | 입력 · 결과 |
| --- | --- |
| POST /api/transfer-files | multipart file → file_id, file_version, file_hash, scan_status, export_banned (201) |
| POST /api/export-requests | file_id, member_id, recipient_id, consent_id, purpose, expires_at → request_id (201) |
| POST /api/export-requests/{request_id}/approve | {} → request_id, approval_id, delivery_id, expires_at. approver만 허용. |
| 조회·거절·철회 | GET /api/export-requests 및 /{request_id}; POST /{request_id}/reject; POST /api/approvals/{approval_id}/revoke. |

## 04  중계가 요구하는 내부 응답

중계 → 내부에는 transfer-api 서비스 토큰을 보낸다. 회원 증빙·동의 API에는 X-User-Token도 전달한다. 내부가 두 토큰과 회원 소유권을 검증해야 한다.

| 내부 API | 반드시 맞출 내용 |
| --- | --- |
| POST /api/internal/disclosure-checks | 입력: member_id, recipient_id, consent_id, purpose, item=profile. 허용: allowed=true, expires_at, member_pseudo_id. 거부: allowed=false 또는 공통 403. 신청·승인·다운로드 때 재조회한다. |
| POST /api/evidence-files | multipart PDF(최대 2MiB). 주체는 회원 토큰. 성공 201 응답에 file_id, status를 사용한다. 내부가 원본 암호화·심사·삭제를 담당한다. |
| GET /api/internal/my-evidence-files | items 배열: file_id, status, created_at, 선택 review_result. 외부·중계에 원본 조회 경로를 제공하지 않는다. |
| GET /api/internal/my-consents | items 배열 + counterparts 배열. 허용 상대는 counterpart_member_id, counterpart_pseudo_id를 포함한다. 허용 상대가 없으면 외부 새 동의 등록은 비활성화된다. |
| POST /api/internal/my-consents | counterpart_member_id, item, purpose, expires_at → 201 consent_id. 회원 본인 ID를 입력받아 신뢰하지 않는다. |
| POST /api/internal/my-consents/{consent_id}/revoke | {} → consent_id, revoked_at. 본인 동의인지 내부에서 확인한다. |

위 응답 필드는 외부 구현의 기대 계약이다. 현재 내부 일부 API는 501 뼈대이므로, 경로만 존재하는 것을 연동 완료로 간주하지 않는다. 상세 JSON 예시는 docs/internal-handoff.md에 있다.

# 연동 전 확인과 완료 범위

## 05  팀원이 알아야 하는 구현 세부

| 항목 | 현재 구현과 남은 확인 |
| --- | --- |
| 승인 파일 고정 | 승인 시 파일 ID·버전·SHA-256을 고정한다. 다운로드 때 실제 파일 바이트의 해시도 검사한다. 요청 파일 ID를 무시하고 정상 파일을 200으로 돌려주는 방식은 쓰지 않는다. |
| 외부 데이터 경계 | external-api는 업무 DB·반출 DB·파일 볼륨에 직접 접근하지 않는다. 외부의 증빙·동의 요청은 transfer-api가 내부의 고정 API로 전달한다. |
| 현재 권한 반영 | 발급된 JWT에는 이전 역할이 남을 수 있다. 중계의 서명 검증만으로 직원 비활성화·역할 변경을 즉시 반영하지 못한다. 즉시 회수가 필요하면 내부의 현재 권한 조회 계약을 추가해야 한다. |
| 감사 기록·추적 | 가명 ID·결과·사유를 기록하고 본문·토큰·쿠키는 제외한다. delivery.reference 이벤트로 승인/파일 ID를 연결한다. Wazuh 반복 경보가 이를 요청 횟수로 중복 계산하지 않게 조정해야 한다. |
| trace_id | 외부 → 중계에는 인증된 서비스가 보낸 trace_id를 연결한다. 중계 → 내부도 헤더를 보내지만 내부가 새 ID로 덮어쓰면 추적이 끊겨 공동 조정이 필요하다. |
| 실행 설정 | 공통 compose.yaml + 외부 compose.external.yaml을 함께 사용한다. 외부 세션 키는 .local/secrets/session.key, 세션 DB는 전용 Docker 볼륨이다. 키·DB·.env는 업로드하지 않는다. |
| 노트북 연결 | 현재는 한 PC의 localhost + Docker 논리망 구성이다. 두 노트북 간 통신은 호스트 바인딩·방화벽·TLS 이름·OIDC URL을 따로 합의해야 하며 이번 기동 확인에는 포함하지 않는다. |

## 06  확인된 결과와 미완료 항목

확인됨: 격리 자동 시험 43개 통과, 다운로드 파일명 변경 관련 3개 재시험 통과. 사용자 PC에서 전체 컨테이너 기동, DB healthy, app_user 생성, users 4건·members 8건, Keycloak 설정 Exited(0), 외부 첫 화면 표시 확인.

> 미확인: 실제 회원 로그인·갱신·로그아웃, 실제 내부 API 기반 신청→승인→다운로드, 두 공격의 실제 전체 연결, Wazuh 수집·경보. 자동 시험의 내부 API는 응답 대역이며 업무 DB는 SQLite 시험 어댑터를 사용했다.

다음 공동 작업: ① 위 내부 응답 구현 ② 내부 파일 등록·반출 프록시 연결 ③ 정상 업무 검증 ④ 동일 요청의 baseline/enforce 비교 ⑤ 관제 규칙 검증.
