# 이음 · matchmaking-external

가상 결혼정보회사의 외부 회원 화면과 반출 중계 백엔드입니다. external-web → external-api → transfer-api를 통해 승인된 소개 자료를 제공합니다. 내부 업무 저장소는 변경하지 않습니다.

## 구현 범위

- 회원 화면: Keycloak 로그인, 제공 자료 목록·다운로드, 학력·재직 증빙 PDF 제출·상태, 상대 설명을 사용하는 제공 동의 등록·철회. 별도 활성화 시 회원 상태·탈퇴 안내·본인 신청 철회.
- 매칭: 최소 소개 후보, 신청·수락·거절·취소·종료, 이용권·예약/사용 횟수, 상대별·항목별·기한별 공개 동의와 검토 결과 열람.
- 만남 일정: 직원 후보 제안 → 양쪽 응답 → 직원 최종 확정, 변경 요청·재확정, 담당 회원 의견 보호. 회원 화면·중계 API·직원용 화면 부품을 제공하며 실제 내부 직원 화면 연결은 필요합니다. [구현·내부 연결 안내](docs/meeting-scheduling.md)
- 이용권: 330만원(VAT 포함)·6개월·5회라는 프로젝트 시험 정책. 실제 결제 연결은 없고 별도 합성 실습 모드에서만 시험 결제를 활성화합니다.
- 외부 BFF: OIDC Authorization Code + PKCE, 서버 세션, CSRF·Origin 검사, 토큰 갱신, 중계 API 호출.
- 반출 중계: 파일 등록·검사, 신청·독립 승인·거절·철회, 동의 재확인, 승인 파일·버전·해시·수신자·만료 검사, 감사 기록.
- 세션: 외부 전용 SQLite 볼륨, 토큰 별도 키 암호화. 브라우저에는 불투명한 세션 쿠키만 보냅니다.
- 공통 privacygate 패키지를 그대로 사용합니다. 화면에서 보안 검사를 끄는 기능은 없습니다.
- 회원 Keycloak 로그인: 이음 디자인의 `ieum-members` 테마를 `external-web`에만 적용합니다. [테마 적용 가이드](docs/member-login-theme.md)
- Wazuh: 별도 `ieum-wazuh` 프로젝트를 필요할 때만 실행합니다. 기존 두 공격·매칭 권한 위반·파일 무결성 규칙, 검색 인덱스 7일 보관·2GiB 예산, 실제 엔진/수집 검증 명령을 준비했습니다. [설치·검증 가이드](docs/wazuh-run-guide.md). 기존 규칙의 실제 엔진·합성 수집·대시보드 확인은 완료했습니다. 새 탈퇴 관련 규칙 세 개는 적용 후 실제 재검증이 필요합니다.

**최신 내부 전달 문서 반영:** [반영 내용·내부 담당자 요청 v0.6](docs/internal-handoff-v0.6.md) · [연동 실행 안내](docs/internal-handoff-run-guide.md). 기존 유료 매칭은 유지하며 내부 상호 매칭·자동 동의는 별도 합의 전 연결하지 않습니다. 회원 상태·탈퇴는 최신 내부 DB 준비 후 `compose.member-lifecycle.yaml`로 활성화합니다. 기존 실행에는 새 상태 API를 필수로 추가하지 않습니다.

**남은 통합 제한:** 내부의 정상 소개 파일과 disclosure 응답의 주체 값이 실명/회원 코드로 달라 정상 반출이 거부될 수 있습니다. 기존 유료 매칭의 matching-directory/matching-disclosures도 별도 내부 구현이 필요합니다. 주체 확인을 생략하거나 내부 오류를 성공으로 대체하지 않습니다. 상세 근거는 v0.6 전달 파일에 있습니다.

## 폴더 배치와 실행

**매칭 기능 확인:** [실행·확인 가이드](docs/matching-run-guide.md) · [매칭·이용권 API와 내부 추가 요구사항](docs/matching-api.md) · [업무 정책](docs/matching-implementation-plan.md). 내부 `matching-directory`·`matching-disclosures` API 구현 전에는 명시적으로 켜는 합성 실습 모드로 확인할 수 있습니다. AI/OCR·사진/PDF 마스킹 엔진은 이번 범위가 아니며, 내부에서 승인한 공개용 텍스트 결과의 계약과 화면을 구현했습니다. 새 매칭 동의는 기존 소개 파일 동의와 아직 자동 연동되지 않습니다.

세 저장소를 같은 상위 폴더에 둡니다.

```text
project/
  privacygate/             # 공통 패키지·Keycloak·Compose·DB 스키마
  matchmaking-internal/    # 내부 담당자 저장소
  matchmaking-external/    # 이 저장소
```

확인 기준은 privacygate의 e4a841987d70b0506f69c38a0eec895b9fed4ec0, 내부의 9b06793e1ad925dbf0598c622379752a8900fb3b입니다. 후속 변경이 있으면 연동 계약을 다시 확인하세요.

1. Docker Desktop Linux 컨테이너를 실행합니다.
2. privacygate README에 따라 로컬 환경변수·TLS·증빙 키를 준비합니다. 해당 저장소의 infra/scripts/init-env.sh는 WSL 또는 Git Bash 등 Bash 환경에서 실행합니다. 생성한 비밀값과 키는 Git에 올리지 않습니다.
3. 외부 저장소의 PowerShell에서 실행합니다.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[test]" "../privacygate[web]"
.\.venv\Scripts\python.exe scripts/initialize.py
docker compose --project-directory ../privacygate --env-file ../privacygate/.env --env-file ./.local/internal-admin.env -f ../privacygate/compose.yaml -f ./compose.external.yaml --profile external up -d --build
```

Python 3.12도 사용할 수 있습니다. 이미 .venv가 있으면 생성 단계는 생략합니다. 이후에는 ./Start.ps1로 실행할 수 있습니다. **두 Compose 파일을 함께 사용해야** 이 저장소의 빌드 컨텍스트·세션 볼륨이 추가됩니다.

- 회원 화면: https://localhost:8090
- 내부 화면: https://localhost:8080
- Keycloak: https://localhost:8081

로컬 TLS 인증서를 브라우저에서 신뢰하도록 준비해야 합니다. 회원 계정·시험 비밀번호는 공통 저장소의 로컬 초기화 설정을 따릅니다. 이 저장소에는 계정 비밀번호를 포함하지 않습니다. 공개 포트는 공통 Compose의 127.0.0.1 바인딩을 유지합니다. 이 구성은 한 호스트의 Docker 논리망 기준이며 두 노트북 간 직접 연결 설정까지 제공하지 않습니다.

```powershell
docker compose --project-directory ../privacygate --env-file ../privacygate/.env --env-file ./.local/internal-admin.env -f ../privacygate/compose.yaml -f ./compose.external.yaml --profile external ps
docker compose --project-directory ../privacygate --env-file ../privacygate/.env --env-file ./.local/internal-admin.env -f ../privacygate/compose.yaml -f ./compose.external.yaml --profile external logs --tail 80 external-api transfer-api
```

external-session-data에는 세션, .local/secrets/session.key에는 암호화 키가 있습니다. 키를 분실하면 기존 세션을 복구할 수 없습니다. down -v는 공통 업무 DB 등도 삭제할 수 있으므로 임의 실행하지 마세요.

## 비교 시험 모드

기본 GATE_MODE=enforce입니다. 합성 자료만 사용하는 격리된 로컬 시험에서 공통 .env의 GATE_MODE=baseline으로 컨테이너를 재생성하면 비교할 수 있습니다. 전체 스택 변수이므로 내부 서비스의 모드도 달라집니다.

이 저장소의 baseline은 **정상 승인 조건 검증 후 요청 파일과 승인 파일의 연결 검사만 생략**합니다. 수신자·동의·만료·철회·승인 파일 무결성 검사는 두 모드에서 유지합니다. upstream의 넓은 baseline=True 우회는 호출하지 않습니다. 적용 모드는 다른 file_id를 지정하면 403 APPROVAL_MISMATCH를 반환합니다. file_id를 생략하면 서버의 승인 파일을 사용합니다.

## 시험 및 현재 확인 수준

```powershell
.\.venv\Scripts\python.exe -m pytest
```

현재 시험 결과는 [내부 전달 파일](docs/internal-handoff-v0.6.md)과 실제 실행 결과를 함께 확인합니다. Python 시험은 실제 RSA 검증과 FastAPI 요청 흐름을 포함하며 업무 API는 계약 검증용 대역·SQLite 어댑터를 사용합니다. 기존 매칭권 예약/차감·동시성·동의·다운로드 비교 시험을 유지하고, 증빙 종류 전달·탈퇴 상태·CSRF·개인정보 로그 제외·초기화 키 보존 시험을 추가했습니다. 화면 제어는 Node 시험도 실행합니다.

2026-11-13: 일정 조율 추가 후 Python **109개**, Node **9개** 통과. Compose 병합은 기본·회원 상태 활성화·합성 실습 세 구성을 검사했습니다. 실제 최신 내부 DB와 새 화면의 브라우저 연동 검증은 다음 단계입니다.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration tests/observability
node --test tests/frontend/account.test.cjs tests/frontend/meetings.test.cjs
```

사용자 PC에서는 중계 DB 백업 후 기존 매칭 마이그레이션과 002_meetings.sql 적용, 일정 테이블 두 개 생성, 외부·중계 이미지 빌드·기동과 화면 확인을 완료했습니다. 내부 업무 DB와 내부 API 이미지는 기존 상태를 보존했습니다. Wazuh 기존 규칙의 엔진·합성 수집·대시보드 표시도 확인했습니다. 실제 직원 일정 관리 BFF·담당 관계 확인, 내부 최신 스키마의 회원 상태·탈퇴, 매칭 자동 동의·연락처 전달은 전체 연동 완료 범위에 포함하지 않습니다.

[API](docs/api-spec.md) · [아키텍처](docs/architecture.md) · [인증](docs/authentication.md) · [역할](docs/permissions.md) · [내부 담당자 전달사항](docs/internal-handoff.md) · [검증 범위](docs/verification.md)

Wazuh 최신 검증: 내부 삭제 오류 3종을 포함한 실제 규칙 엔진 12건과, 3초 간격 합성 이벤트 12건의 수집 → 예상 규칙 → Indexer 검색이 사용자 PC에서 통과했습니다. 진단 변경 후 관제 도구 자동 시험 21개도 통과했습니다. 일괄 입력에서는 반복 경보가 누락되는 현상이 남아 있어 동시 입력 반복 탐지는 추가 확인이 필요합니다. 이는 실제 웹 요청의 차단·반출 시험 완료를 뜻하지 않습니다. 절차와 한계는 [Wazuh 가이드](docs/wazuh-run-guide.md)를 참고합니다.
