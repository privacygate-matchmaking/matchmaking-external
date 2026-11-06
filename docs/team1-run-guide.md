# 팀원 1 실행 가이드

Windows PowerShell / 개인 PC에서 전체 환경 기동 / 2026.11.06

> 목표: 내부·중계·외부를 각자의 PC에서 먼저 실행한다. 팀원 1의 기존 내부 작업 폴더는 보존하고 미커밋 작업이 있는 폴더를 덮어쓰지 않는다. 이 가이드는 물리적으로 두 PC를 연결하는 절차가 아니다.

## 01  준비와 폴더 배치

Docker Desktop(Linux 컨테이너), Git for Windows, Python 3.12 이상을 준비한다. PowerShell에서 docker version에 Client·Server가 모두 표시되는지 확인한다. 외부 코드가 GitHub에 업로드된 뒤 아래 clone을 사용한다. 업로드 전에는 전달받은 소스 ZIP을 같은 이름의 폴더에 푼다.

```powershell
project/
  privacygate/
  matchmaking-internal/
  matchmaking-external/
```

```powershell
git clone https://github.com/privacygate-matchmaking/privacygate.git
git clone https://github.com/privacygate-matchmaking/matchmaking-internal.git
git clone https://github.com/privacygate-matchmaking/matchmaking-external.git
```

이미 있는 저장소는 clone하지 않는다. 특히 팀원 1의 기존 matchmaking-internal은 그대로 사용한다. 저장소 업데이트 전에는 git status로 본인 작업을 확인한다.

## 02  공통 초기화: privacygate 폴더

Git Bash를 privacygate 폴더에서 열어 다음을 실행한다. MSYS_NO_PATHCONV는 Windows에서 인증서의 /CN=localhost가 경로로 변환되는 일을 막는다.

```powershell
MSYS_NO_PATHCONV=1 bash ./infra/scripts/init-env.sh
```

생성 확인: .env, infra/secrets/evidence_master.key, infra/secrets/tls/cert.pem, key.pem. .env의 비밀번호는 PC마다 다르게 생성되므로 상대방 값을 복사하지 않는다.

## 03  외부 세션 키: matchmaking-external 폴더

여기부터 PowerShell을 사용한다. Python 3.12 사용자는 첫 줄의 -3.13을 -3.12로 바꾼다. 이미 .venv가 있으면 생성은 생략한다. Docker 실행만 할 때는 pip 설치가 필요하지 않다.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe scripts\initialize.py
```

.local/secrets/session.key가 생성되면 준비 완료다. 기존 키는 덮어쓰지 않는다.

# Docker 실행과 화면 확인

## 04  셸 스크립트 줄바꿈 확인

Windows clone에서 CRLF로 바뀌면 /bin/sh^M 또는 pipefail 오류가 난다. 최초 기동 전에 외부 폴더의 PowerShell에서 아래를 실행한다. 공통 스크립트의 로컬 줄바꿈만 LF로 바꾼다.

```powershell
$paths = @(
  "../privacygate/infra/db/business/02-app-role.sh",
  "../privacygate/infra/db/transfer/02-app-role.sh",
  "../privacygate/infra/keycloak/realm-setup.sh"
)
foreach ($p in $paths) {
  $f = (Resolve-Path -LiteralPath $p).Path
  $t = [IO.File]::ReadAllText($f).Replace("`r`n", "`n").Replace("`r", "`n")
  [IO.File]::WriteAllText($f, $t, [Text.UTF8Encoding]::new($false))
}
```

## 05  설정 검사·빌드·실행

같은 PowerShell 창에서 아래 변수와 명령을 실행한다. 새 터미널을 열면 $composeArgs를 다시 정의해야 한다.

```powershell
$composeArgs = @(
  "--project-directory", "../privacygate",
  "-f", "../privacygate/compose.yaml",
  "-f", "./compose.external.yaml",
  "--profile", "external"
)
docker compose @composeArgs config --quiet
docker compose @composeArgs up -d --build
docker compose @composeArgs ps -a
```

설정 검사에서 오류가 나면 실행을 진행하지 않는다. DB 두 개·Keycloak은 healthy, API·웹은 Up, keycloak-setup은 설정 완료 후 Exited(0)이 정상이다.

## 06  브라우저와 시험 계정

| 주소 | 화면 · 계정 |
| --- | --- |
| https://localhost:8090 | 외부 이음 화면 → 회원 로그인 → member_b |
| https://localhost:8080 | 내부 직원 화면. counselor_a / data_admin / approver. 직원 첫 로그인에는 OTP 등록 요구가 있을 수 있다. |
| https://localhost:8081 | Keycloak. 직접 접속 시 관리 로그인(master)이 나올 수 있다. member_b를 입력하는 화면이 아니다. |

시험 계정 비밀번호는 자기 PC의 privacygate/.env에서 KC_TEST_PASSWORD를 확인한다. 회원 로그인 주소는 /realms/matchmaking/이어야 한다. /realms/master/는 관리용이다. 자체 서명 경고는 직접 만든 localhost 인증서인지 확인한 뒤 해당 주소에서만 진행한다.

# 확인·오류 대응·종료

## 07  기동 후 확인 순서

① 외부 첫 화면 → ② member_b 로그인 후 8090 복귀 → ③ 로그아웃 → ④ 내부 담당 API 연결 → ⑤ 정상 신청·승인·다운로드 순으로 확인한다. 자료가 없으면 목록이 비어 있을 수 있다. 내부 501 응답은 기능 미구현이므로 초기화로 해결하지 않는다.

## 08  상태·로그·DB 확인

```powershell
docker compose @composeArgs ps -a
docker compose @composeArgs logs --since 5m --tail 80 external-api transfer-api internal-api keycloak-setup
```

업무 DB에서 앱 계정과 합성 데이터를 확인한다. 복사하기 쉬운 명령 원문은 같은 이름의 Markdown 가이드에 수록했다.

```powershell
docker compose @composeArgs exec -T business-db psql -U db_owner -d matchmaking_business -c "SELECT rolname FROM pg_roles WHERE rolname='app_user'; SELECT count(*) FROM users; SELECT count(*) FROM members;"
```

새 DB의 기대값: app_user 존재, users=4, members=8. API 프로세스 Up만으로 DB 접근 권한·업무 연동 성공이 검증되는 것은 아니다.

## 09  자주 만나는 오류

| 증상 | 처리 |
| --- | --- |
| /bin/sh^M / pipefail 오류 | 스크립트 CRLF를 LF로 수정한다. DB 초기화가 중단됐다면 재시작만으로 남은 스크립트가 실행되지 않는다. 현재 역할·데이터를 조회한 후 실패한 단계만 복구한다. |
| 인증서 생성 안 됨 | 초기화 시 MSYS_NO_PATHCONV=1 사용. cert.pem과 key.pem 모두 존재하는지 확인한다. |
| 브라우저 인증서 경고 | 자체 서명 때문일 수 있다. localhost:8090과 :8081을 각각 확인한다. 브라우저 보안 전체를 끄지 않는다. |
| master 로그인 화면 | 8090을 직접 열고 회원 로그인 버튼을 사용한다. 관리 로그인에 회원 계정을 넣지 않는다. |
| 로그인 실패 / API 500 | 외부·중계·Keycloak 로그와 브라우저 오류를 확인한다. 비밀번호·토큰·쿠키는 공유하지 않는다. DB나 볼륨을 먼저 삭제하지 않는다. |

## 10  종료와 다음 실행

```powershell
docker compose @composeArgs stop
# 다음 실행: Docker Desktop 실행 후
docker compose @composeArgs up -d
```

코드를 수정했으면 up -d --build를 사용한다. down -v는 DB·파일·세션 볼륨을 삭제할 수 있어 일반 종료에 사용하지 않는다. .env·비밀키·DB는 Git에 올리지 않는다.
