# 팀원 1 실행 가이드 — Mac 기준

기준일: 2026-11-13. 팀원 1이 자기 Mac에서 내부·중계·외부를 함께 띄워 보는 안내입니다. 두 노트북을 직접 연결하는 안내는 아닙니다. Windows 차이는 마지막에 있습니다. Mac 전체 실행은 아직 실제 검증하지 않았습니다.

Windows의 새 내부 20명 전환과 시험 계정 연결은 [별도 적용 안내](internal20-start-guide.md)에 있습니다. 이는 아래 기존 합성 4명 모드와 다른 내부 데이터 모드이며 파일을 혼합하지 않습니다.

## 0. 어떤 모드로 볼지 먼저 선택

| 모드 | 확인 목적 | 주의 |
|---|---|---|
| 기본 | 회원 로그인·기존 증빙/동의/반출 연결 | 내부 추가 API 전에는 유료 매칭 후보가 안 나올 수 있음 |
| 합성 매칭 실습 | 내부 새 API 없이 유료 신청·수락·공개 범위·일정 회원 화면 확인 | C/D/E는 내부 업무 DB 회원이 아님. 시험 결제만 제공 |
| 회원 상태 활성화 | 실제 내부 탈퇴·안내·철회 연동 확인 | 최신 내부 DB·API 준비 후 사용, 합성 모드와 혼합 금지 |

내부 담당 확인 API·BFF·직원 화면 연결 코드를 추가했습니다. 내부·중계 이미지를 함께 재빌드한 뒤 실제 직원 일정 발송을 검증해야 합니다. 회원 화면이 열린다는 이유만으로 전체 연동이 끝난 것은 아닙니다. [내부 담당 전달 파일](internal-handoff.md)을 먼저 확인해 주세요.

## 1. 폴더와 도구

Docker Desktop을 실행하고 Git·Python 3.12 이상을 준비합니다. 터미널에서:

```sh
docker version
docker compose version
python3 --version
```

Docker는 Client와 Server가 모두 나와야 합니다. 아래 세 저장소는 같은 부모 폴더에 둡니다.

```text
project/
  privacygate/
  matchmaking-internal/
  matchmaking-external/
```

이미 작업 중인 내부 저장소는 그대로 둡니다. 없는 폴더만 clone합니다.

```sh
git clone https://github.com/privacygate-matchmaking/privacygate.git
git clone https://github.com/privacygate-matchmaking/matchmaking-internal.git
git clone https://github.com/privacygate-matchmaking/matchmaking-external.git
```

기존 외부 저장소는 `git status`로 미커밋 작업을 먼저 확인한 후 `git pull --ff-only`로 갱신합니다. 아래 기능은 외부 최신 변경의 GitHub 업로드 후 내려받아야 합니다.

## 2. 공통 환경 준비 — privacygate 폴더

이미 사용하던 `.env`·키·TLS는 보존합니다. 신규 설치라면:

```sh
cd privacygate
bash infra/scripts/init-env.sh
```

확인할 파일은 `.env`, `infra/secrets/evidence_master.key`, `infra/secrets/tls/cert.pem`, `infra/secrets/tls/key.pem`입니다. 기존 `.env`가 있으면 초기화 스크립트가 새 필드를 자동 보충하지 않을 수 있습니다. 다음 외부 initialize가 필요한 internal-admin 로컬 값을 준비합니다. 비밀번호·개인키를 서로 보내거나 Git에 올리지 않습니다.

내부 최신 스키마는 내부 담당자가 자기 DB에 맞게 준비합니다. `up --build`만으로 기존 업무 DB가 새 시드로 바뀌지는 않습니다. 이 안내에는 전체 데이터 삭제 명령을 넣지 않습니다.

## 3. 외부 준비 — matchmaking-external 폴더

```sh
cd ../matchmaking-external
python3 -m venv .venv
.venv/bin/python scripts/initialize.py
```

이미 `.venv`가 있으면 생성은 생략합니다. initialize는 표준 라이브러리만 사용합니다. 기존 세션 키는 보존하며 `.local/internal-admin.env`를 준비합니다. 실제 값은 출력하지 않습니다.

Mac 기본 셸(zsh)에서 다음 배열을 정의합니다. 새 터미널을 열면 다시 정의해야 합니다.

```sh
composeArgs=(
  --project-directory ../privacygate
  --env-file ../privacygate/.env
  --env-file ./.local/internal-admin.env
  -f ../privacygate/compose.yaml
  -f ./compose.external.yaml
  --profile external
)
```

## 4. 기본 실행

기존 자료를 보존해야 하는 중계 DB가 있다면 재빌드 전 백업합니다. 쓰기 중인 서비스를 멈추고 DB만 실행한 상태에서 수행합니다.

```sh
mkdir -p .local/db-backups
docker compose "${composeArgs[@]}" stop external-api transfer-api
docker compose "${composeArgs[@]}" up -d --no-deps transfer-db
```

DB가 healthy가 된 뒤에만 진행합니다.

```sh
docker compose "${composeArgs[@]}" ps -a transfer-db
```

```sh
backupStamp=$(date +%Y%m%d-%H%M%S)
docker compose "${composeArgs[@]}" exec -T transfer-db pg_dump -U db_owner -d matchmaking_transfer -Fc -f /tmp/transfer-before-update.dump
transferDbId=$(docker compose "${composeArgs[@]}" ps -q transfer-db)
docker cp "${transferDbId}:/tmp/transfer-before-update.dump" ".local/db-backups/transfer-${backupStamp}.dump"
ls -lh .local/db-backups
```

각 명령이 실패하면 다음 단계로 가지 않습니다. 신규 설치로 보존 데이터가 없다면 백업은 생략할 수 있습니다.

```sh
docker compose "${composeArgs[@]}" config --quiet
docker compose "${composeArgs[@]}" up -d --build
docker compose "${composeArgs[@]}" ps -a
```

`config --quiet`가 실패하면 up을 실행하지 않습니다. up은 내부 서비스를 포함한 전체 스택도 빌드할 수 있으므로 팀원 1의 내부 소스·DB 준비가 끝난 뒤 사용합니다. 최신 내부 DB 준비를 대신하는 명령은 아닙니다.

정상 기준: DB·Keycloak healthy, API·웹 Up, `matching-migrate`·`meeting-migrate`·설정용 일회성 서비스는 성공 후 Exited(0). 마이그레이션은 중계 매칭/일정 테이블을 추가하며 기존 DB 전체를 지우지 않습니다.

## 5. 브라우저 확인

| 주소 | 확인 |
|---|---|
| https://localhost:8090 | 외부 이음 → 회원 로그인 → member_b |
| https://localhost:8080 | 내부 직원 화면 |
| https://localhost:8081 | Keycloak, 직접 열면 master 관리 로그인이 나올 수 있음 |
| https://localhost:8444 | Wazuh를 별도 기동했을 때만 |

시험 비밀번호는 자기 `privacygate/.env`의 `KC_TEST_PASSWORD`입니다. 회원은 반드시 8090의 로그인 버튼으로 시작합니다. `/realms/master/` 관리 로그인에 회원 계정을 넣지 않습니다. 자체 서명 인증서는 로컬에서 만든 것인지 확인하고 신뢰를 설정합니다.

확인 순서: 로그인 → 라운지 → 증빙 종류/동의 상대 표시 → 로그아웃. 내부가 501이거나 계약이 다르면 해당 API부터 확인합니다. 파일 주체 불일치 문제는 [전달 파일 6절](internal-handoff.md)에 있습니다. 오류를 없애려고 검사를 끄지 않습니다.

## 6. 유료 매칭 독립 실습 (선택)

기본 파일 뒤에 **합성 파일만** 추가합니다.

```sh
docker compose "${composeArgs[@]}" -f ./compose.matching-lab.yaml up -d --build
docker compose "${composeArgs[@]}" -f ./compose.matching-lab.yaml exec -T keycloak bash /opt/matching-lab/setup-test-members.sh
```

Keycloak 초기 설정 성공 후 계정 준비 스크립트를 실행합니다. member_b/c/d/e의 비밀번호는 위 KC_TEST_PASSWORD입니다. B와 C는 서로 다른 브라우저 세션으로 로그인합니다.

B 시험 이용권 활성화 → C에게 신청 → C 수락 → C가 공개 항목 동의 → B 결과 확인 순서입니다. 실제 돈은 결제되지 않습니다. 330만원·6개월·5회는 시험 상품이며 최종 사업 정책은 논의 가능합니다. [세부 실습](matching-run-guide.md)을 참고합니다.

기본 모드 복귀는 합성 파일을 빼고 4절의 up 명령을 사용합니다. 공통 `.env`에 실습 변수를 직접 넣었다면 `MATCHING_DIRECTORY_MODE=internal`, `ENABLE_TEST_PAYMENTS=false`도 확인합니다.

## 7. 회원 상태·탈퇴 활성화 (선택)

내부 최신 스키마·my-status·탈퇴 API·계정 정리 설정이 준비된 뒤, 합성 파일 대신 아래 파일만 추가합니다.

```sh
docker compose "${composeArgs[@]}" -f ./compose.member-lifecycle.yaml config --quiet
docker compose "${composeArgs[@]}" -f ./compose.member-lifecycle.yaml up -d --build
```

시험 회원으로 로그인 → `/api/me`의 member_status_check=true → 상태 active → 본인 탈퇴 신청 → 유예 안내와 업무 제한 → 본인 철회 → 정상 복귀를 확인합니다. 직원 처리 건은 철회 불가, withdrawn은 안내 후 세션 종료여야 합니다. 주 시연 계정 대신 별도 시험 회원을 사용합니다. 내부 삭제 작업은 실제 데이터를 변경합니다.

## 8. Wazuh (선택)

Windows 사용자 PC에서는 실제 규칙 엔진·순차 수집·색인을 확인했습니다. Mac, 특히 Apple Silicon의 고정 이미지 실행/아키텍처와 메모리 여유는 별도 확인이 필요합니다. Mac에서 명령을 그대로 실행해 성공한다고 보장하지 않습니다.

```sh
.venv/bin/python -m pip install -r requirements-wazuh.txt
.venv/bin/python scripts/wazuh.py prepare
.venv/bin/python scripts/wazuh.py check
.venv/bin/python scripts/wazuh.py start
.venv/bin/python scripts/wazuh.py status
```

초기화 후 analysisd 실행을 확인하고:

```sh
docker exec ieum-wazuh-wazuh.manager-1 /var/ossec/bin/wazuh-analysisd -t
docker exec ieum-wazuh-wazuh.manager-1 /var/ossec/bin/wazuh-control status
.venv/bin/python scripts/wazuh.py rules
.venv/bin/python scripts/wazuh.py smoke
```

앞 단계 오류 시 중단합니다. smoke는 3초 간격 순차 입력이며 동시 입력 반복 경보를 보증하지 않습니다. 비밀번호 확인 위치·자원 검사·상세 진단은 [Wazuh 가이드](wazuh-run-guide.md)를 따릅니다.

## 9. 로그·종료·다음 실행

```sh
docker compose "${composeArgs[@]}" logs --since 5m --tail 60 external-api transfer-api internal-api
.venv/bin/python scripts/wazuh.py stop
docker compose "${composeArgs[@]}" stop
```

Wazuh를 준비하지 않았다면 해당 stop은 생략합니다. 다음 실행은 선택한 모드와 같은 Compose 파일 조합으로 `up -d`, 코드 변경 후에는 `up -d --build`입니다. `down -v`는 일반 종료에 사용하지 않습니다. 로그 공유 전 토큰·개인정보 포함 여부를 확인합니다.

## 10. Windows PowerShell에서의 차이

Mac의 `cd` 대신 `Set-Location -LiteralPath`, Python 경로는 `.\.venv\Scripts\python.exe`를 사용합니다. Mac용 배열 문법을 PowerShell에 붙여넣지 않습니다.

```powershell
$composeArgs = @(
  '--project-directory', '../privacygate',
  '--env-file', '../privacygate/.env',
  '--env-file', './.local/internal-admin.env',
  '-f', '../privacygate/compose.yaml',
  '-f', './compose.external.yaml',
  '--profile', 'external'
)
docker compose @composeArgs ps -a
```

기본 실행은 `.\Start.ps1`, 합성 모드는 `docker compose @composeArgs -f ./compose.matching-lab.yaml up -d --build`입니다. 회원 상태 모드는 합성 파일 대신 lifecycle 파일을 사용합니다. macOS의 `${composeArgs[@]}`와 PowerShell의 `@composeArgs`는 서로 다른 문법입니다.
