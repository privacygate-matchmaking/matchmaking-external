# 이음 · matchmaking-external

가상 결혼정보회사의 **외부 회원 화면·외부 API·반출 중계** 저장소입니다. 내부 직원 화면과 업무 DB는 `matchmaking-internal`, 공통 보안 패키지·기본 인프라는 `privacygate`가 담당합니다.

기준일: 2026-11-13. 실제 회원·문서·결제 대신 합성 자료를 사용하는 학교 프로젝트입니다.

## 먼저 읽을 문서

| 목적 | 문서 |
|---|---|
| 팀원에게 전달할 반영 결과·남은 요청·월요일 논의 | [외부 변경 정리 — 내부 담당 전달용](docs/internal-handoff.md) |
| 팀원 1의 Mac 실행, Windows 실행, 모드 선택 | [실행 가이드](docs/team1-run-guide.md) |
| 매칭 독립 실습 | [매칭 실행·확인](docs/matching-run-guide.md) |
| 실제 확인 결과와 미확인 범위 | [검증 현황](docs/verification.md) |
| Wazuh 실행·규칙·수집 확인 | [Wazuh 가이드](docs/wazuh-run-guide.md) |

## 지금 구현한 것

- 회원 로그인·라운지, 제공 자료 다운로드, 학력/재직 증빙 제출, 제공 동의 등록·철회.
- 유료 이용권·매칭 신청·상대 수락/거절·예약/사용 횟수, 항목별 공개 동의와 검토 결과 화면.
- 직원 일정 후보 제안 → 양쪽 회원 응답 → 직원 최종 확정의 중계 API·회원 화면·직원 화면 부품. 실제 내부 직원 화면 연결은 남아 있습니다.
- 회원 상태·탈퇴 신청/철회·안내 화면. 최신 내부 준비 후 별도 활성화해야 합니다.
- 승인 파일·버전·해시·수신자·동의·만료 검증과 제한된 baseline 비교.
- 이음 회원 Keycloak 테마, 외부 전용 암호화 세션, 토큰 갱신, CSRF·Origin 검사.
- 선택 실행 Wazuh: 실제 규칙 엔진 12건 및 순차 합성 이벤트 수집→규칙→색인 검색 통과.

**전체 업무 연동 완료 상태는 아닙니다.** 내부 상호 매칭과 외부 유료 매칭은 아직 통합하지 않았습니다. 후보/공개 결과 API·직원 일정 BFF·소개 파일 주체 값 합의가 필요합니다. AI/OCR 마스킹 엔진·연락처 최종 승인 전달·챗봇·실제 결제는 미구현입니다.

## 실행과 포트

세 저장소를 같은 부모 폴더 아래 둡니다.

```text
project/
  privacygate/
  matchmaking-internal/
  matchmaking-external/
```

기존 Windows 환경에서는 `matchmaking-external`의 PowerShell에서 `./Start.ps1`을 실행합니다. Mac 신규 설치와 합성 매칭/회원 상태 모드 선택은 [실행 가이드](docs/team1-run-guide.md)를 따릅니다. 기존 DB를 지우는 초기화는 일반 실행 절차에 포함하지 않습니다.

| 주소 | 용도 |
|---|---|
| https://localhost:8090 | 외부 회원 화면 |
| https://localhost:8080 | 내부 직원 화면 |
| https://localhost:8081 | Keycloak |
| https://localhost:8444 | Wazuh, 별도 기동 시 |

기본값은 `GATE_MODE=enforce`, `MATCHING_DIRECTORY_MODE=internal`, 시험 결제와 회원 상태 강제 연동은 꺼짐입니다. 회원 상태 강제 연동이 꺼진 것은 이전 내부 환경과의 호환 설정이며, 탈퇴 기능 운영 준비 완료를 뜻하지 않습니다. `.env`·개인키·세션·DB 백업·Wazuh 자격 증명은 Git에 올리지 않습니다.

## 기술 문서

[API](docs/api-spec.md) · [매칭 API](docs/matching-api.md) · [매칭 정책](docs/matching-implementation-plan.md) · [일정 조율·내부 추가 API](docs/meeting-scheduling.md) · [명칭](docs/naming-spec.md) · [권한](docs/permissions.md) · [인증·세션](docs/authentication.md) · [구조](docs/architecture.md) · [회원 로그인 테마](docs/member-login-theme.md)

예전 v0.2/v0.4 제안서와 v0.6 전달·중복 실행 안내는 현재 전달 문서와 실행 가이드로 통합했습니다. 과거 내용은 Git 이력에서 확인할 수 있습니다.
