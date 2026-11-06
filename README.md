# 이음 · matchmaking-external

가상 결혼정보회사의 외부 회원 화면과 반출 중계 백엔드입니다. external-web → external-api → transfer-api를 통해 승인된 소개 자료를 제공합니다. 내부 업무 저장소는 변경하지 않습니다.

## 구현 범위

- 회원 화면: Keycloak 로그인, 제공 자료 목록·다운로드, 증빙 PDF 제출·상태, 제공 동의 등록·철회.
- 외부 BFF: OIDC Authorization Code + PKCE, 서버 세션, CSRF·Origin 검사, 토큰 갱신, 중계 API 호출.
- 반출 중계: 파일 등록·검사, 신청·독립 승인·거절·철회, 동의 재확인, 승인 파일·버전·해시·수신자·만료 검사, 감사 기록.
- 세션: 외부 전용 SQLite 볼륨, 토큰 별도 키 암호화. 브라우저에는 불투명한 세션 쿠키만 보냅니다.
- 공통 privacygate 패키지를 그대로 사용합니다. 화면에서 보안 검사를 끄는 기능은 없습니다.

**현재 통합 제한:** 확인한 matchmaking-internal의 증빙·동의·제공 확인 등 업무 API 일부가 501 뼈대입니다. 전체 업무가 실제로 동작하려면 내부 담당자가 [연동 요구사항](docs/internal-handoff.md)을 구현해야 합니다. 자동 시험의 내부 서비스는 계약 검증용 대역이며 운영 코드에는 대체 성공 응답이 없습니다.

## 폴더 배치와 실행

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
docker compose --project-directory ../privacygate -f ../privacygate/compose.yaml -f ./compose.external.yaml --profile external up -d --build
```

Python 3.12도 사용할 수 있습니다. 이미 .venv가 있으면 생성 단계는 생략합니다. 이후에는 ./Start.ps1로 실행할 수 있습니다. **두 Compose 파일을 함께 사용해야** 이 저장소의 빌드 컨텍스트·세션 볼륨이 추가됩니다.

- 회원 화면: https://localhost:8090
- 내부 화면: https://localhost:8080
- Keycloak: https://localhost:8081

로컬 TLS 인증서를 브라우저에서 신뢰하도록 준비해야 합니다. 회원 계정·시험 비밀번호는 공통 저장소의 로컬 초기화 설정을 따릅니다. 이 저장소에는 계정 비밀번호를 포함하지 않습니다. 공개 포트는 공통 Compose의 127.0.0.1 바인딩을 유지합니다. 이 구성은 한 호스트의 Docker 논리망 기준이며 두 노트북 간 직접 연결 설정까지 제공하지 않습니다.

```powershell
docker compose --project-directory ../privacygate -f ../privacygate/compose.yaml -f ./compose.external.yaml --profile external ps
docker compose --project-directory ../privacygate -f ../privacygate/compose.yaml -f ./compose.external.yaml --profile external logs --tail 80 external-api transfer-api
```

external-session-data에는 세션, .local/secrets/session.key에는 암호화 키가 있습니다. 키를 분실하면 기존 세션을 복구할 수 없습니다. down -v는 공통 업무 DB 등도 삭제할 수 있으므로 임의 실행하지 마세요.

## 비교 시험 모드

기본 GATE_MODE=enforce입니다. 합성 자료만 사용하는 격리된 로컬 시험에서 공통 .env의 GATE_MODE=baseline으로 컨테이너를 재생성하면 비교할 수 있습니다. 전체 스택 변수이므로 내부 서비스의 모드도 달라집니다.

이 저장소의 baseline은 **정상 승인 조건 검증 후 요청 파일과 승인 파일의 연결 검사만 생략**합니다. 수신자·동의·만료·철회·승인 파일 무결성 검사는 두 모드에서 유지합니다. upstream의 넓은 baseline=True 우회는 호출하지 않습니다. 적용 모드는 다른 file_id를 지정하면 403 APPROVAL_MISMATCH를 반환합니다. file_id를 생략하면 서버의 승인 파일을 사용합니다.

## 시험 및 현재 확인 수준

```powershell
.\.venv\Scripts\python.exe -m pytest
```

격리된 자동 시험 43개가 통과했습니다. 실제 RSA 토큰 검증과 FastAPI 요청 흐름을 포함하며, 업무 흐름은 내부 API 응답 대역과 SQLite 시험 어댑터를 사용합니다. GitHub Actions에도 같은 시험을 구성했습니다.

2026-11-06 사용자 PC 확인: Docker 전체 컨테이너 실행, PostgreSQL 두 DB healthy, app_user 존재, users 4건·members 8건, Keycloak 설정 Exited(0), 외부 첫 화면 표시를 확인했습니다. Windows clone에서 공통 초기화 스크립트의 CRLF를 LF로 변환하고 중단된 DB 계정·시험 데이터 초기화를 이어서 실행했습니다. 실제 로그인·토큰 갱신·전체 업무 흐름·Wazuh 경보는 아직 확인 전입니다. DB 기동 확인을 실제 업무 트랜잭션 검증 완료로 해석하지 않습니다.

[구현 기준 연동 계약안 v0.4](docs/integration-decisions-v0.4.md) · [팀원 1 실행 가이드](docs/team1-run-guide.md)

[API](docs/api-spec.md) · [아키텍처](docs/architecture.md) · [인증](docs/authentication.md) · [역할](docs/permissions.md) · [내부 담당자 전달사항](docs/internal-handoff.md) · [검증 범위](docs/verification.md)
