# Wazuh 연동·검증 가이드

일반 앱 실행은 [팀원 1 실행 가이드](team1-run-guide.md), 팀 간 요청은 [내부 전달 파일](internal-handoff.md)에 있습니다. 아래 PowerShell 예는 Windows 기준이며 Mac의 Python 경로는 `.venv/bin/python`입니다. Mac 고정 이미지 실행은 아직 검증하지 않았습니다.

## 구현한 구조

현재 PC의 기존 `matchmaking` 앱과 별개인 `ieum-wazuh` Compose 프로젝트다. 선택적으로 실행하며 정지해도 앱은 정지하지 않는다.

`internal-api / transfer-api / external-api → audit-logs 볼륨(읽기 전용) → Wazuh Manager → Indexer → Dashboard`

공식 Wazuh Docker 4.14.10 설정을 버전·SHA-256으로 고정하고 준비 도구가 로컬에서 변환한다. 외부 저장소에 도구·규칙만 추가하며 내부·공통 저장소를 수정하지 않는다. Manager가 직접 로컬 JSON을 읽으므로 이 단일 호스트 구성에 별도 Windows agent는 필요 없다. 다른 PC 로그를 모으려면 별도 수집·인증 구성이 필요하다.

대시보드만 `https://localhost:8444`에 공개한다. Manager API·Indexer·agent 등록 포트는 호스트에 공개하지 않는다. 업무 DB·증빙 원본·Docker 소켓은 마운트하지 않는다. 자동 IP·계정 차단, 이메일·메신저 통보는 추가하지 않는다.

## 현재 실행 제한

공식 Docker 단일 노드 기준은 4코어·8GB RAM·50GB 저장 공간이다. 이 실행 도구는 최초 이미지 설치 전 실제 Docker 데이터 드라이브 여유 50GiB를 요구한다. 필요한 고정 버전 이미지 5개가 모두 로컬에 존재하면, 이미 다운로드에 사용한 공간을 중복 요구하지 않고 프로젝트 자체 운영 기준인 **여유 20GiB**를 적용한다. 이는 공식 저장 공간 요구를 대체하는 수치가 아니라 설치 후 남겨둘 여유분이다. Docker 배정 4CPU·8GiB 검사도 유지한다. Windows 전체 RAM이 16GB라는 사실만으로 통과하지 않는다. 기존 앱의 메모리도 별도로 필요하다.

2026-11-13 사용자 PC에서 기동·대시보드 표시와 최신 12개 규칙 엔진 시험을 확인했다. 최신 합성 이벤트 12건 모두 Manager 경보와 Indexer 저장까지 확인했으나, 일괄 입력에서는 반복 경보 100112 대신 단일 경보 100110이 3건 발생했다. 별도 3초 간격 시험에서는 100110·100110·100112가 발생했다. 이에 smoke를 순차 입력으로 변경했으며, 사용자 PC에서 prepare → smoke 재실행 후 12개 이벤트의 예상 규칙·색인 검색 검증이 통과했다. 관제 도구 자동 시험 21개도 통과했다. 동시·일괄 입력의 반복 탐지 신뢰성은 미해결 확인 사항이며, 순차 시험 통과로 이를 보증하지 않는다. 파일을 임의 삭제하지 않는다. Docker 디스크 이미지를 다른 드라이브로 실제 이동한 경우에만 `--docker-data-path D:\`처럼 그 드라이브를 지정한다. 여유 공간이 있는 다른 경로만 지정해 검사를 우회하면 안 된다.

## 1. 준비

PowerShell의 matchmaking-external 폴더에서 실행한다. 기존 .venv와 프로젝트 의존성이 설치된 상태를 기준으로 한다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-wazuh.txt
.\.venv\Scripts\python.exe scripts/wazuh.py prepare
.\.venv\Scripts\python.exe scripts/wazuh.py check
```

`prepare`는 공식 설정의 체크섬을 검사하고 `.local/wazuh`에 설정·서로 다른 무작위 비밀번호를 만든다. 재실행해도 기존 비밀번호를 보존한다. 이 폴더 전체는 Git 제외다. `credentials.json`, .env, 인증서 개인키를 공유하거나 업로드하지 않는다. 준비에는 소량의 설정 다운로드가 있으나 Docker 이미지는 받지 않는다.

Wazuh Dashboard 로그인 계정은 `admin`, 비밀번호는 `.local/wazuh/credentials.json`의 `INDEXER_PASSWORD` 값이다. 서비스 계정 비밀번호는 별도로 생성한다. 비밀번호 변경·키 교체는 별도 절차이며 생성 파일을 지우고 재생성하는 방식으로 기존 볼륨에 덮어쓰지 않는다.

## 2. 공간·자원 확보 후 실행

Docker Desktop과 기존 앱이 실행된 상태에서:

```powershell
.\.venv\Scripts\python.exe scripts/wazuh.py start
.\.venv\Scripts\python.exe scripts/wazuh.py status
.\.venv\Scripts\python.exe scripts/wazuh.py logs
```

기존 감사 볼륨 기본 이름은 `matchmaking_audit-logs`다. 실제 앱 프로젝트명이 다르면 확인 후 `$env:IEUM_AUDIT_VOLUME='실제볼륨명'`으로 지정한다. 볼륨이 없으면 실행을 중단하며 빈 업무 볼륨을 대신 만들지 않는다.

도구는 이미지 pull 전에 공간·Docker 자원을 검사한다. 이후 Indexer 이미지로 `vm.max_map_count`를 확인한다. 값이 262144 미만이면 서비스는 시작하지 않는다. 해당 안내가 나온 경우 PowerShell에서 실행한다.

```powershell
wsl -d docker-desktop -u root sysctl -w vm.max_map_count=262144
.\.venv\Scripts\python.exe scripts/wazuh.py start
```

WSL/Docker 재시작 후 설정이 초기화될 수 있다. 실행 도구가 매번 확인한다. 인증서는 공식 generator로 만들고, Dashboard 인증서는 같은 CA로 `localhost`·127.0.0.1 SAN을 포함해 발급한다. 브라우저가 CA를 신뢰하지 않으면 경고가 나올 수 있으므로 로컬 CA 신뢰 여부를 사용자가 확인한다. TLS 검증을 전역 해제하지 않는다.

초기화에 시간이 필요하다. 컨테이너 Up만으로 준비 완료를 판단하지 말고 아래 규칙·수집 검증까지 수행한다.

재시작 직후 logtest가 분석 프로세스에 연결하지 못하면 먼저 아래를 실행한다. 설정 검사에 ERROR/CRITICAL이 없고 상태에 `wazuh-analysisd is running`이 표시된 뒤 rules를 실행한다. 설정 오류가 있으면 반복 재시작 대신 해당 오류를 확인한다.

```powershell
docker exec ieum-wazuh-wazuh.manager-1 /var/ossec/bin/wazuh-analysisd -t
docker exec ieum-wazuh-wazuh.manager-1 /var/ossec/bin/wazuh-control status
```

## 3. 실제 Wazuh 엔진과 수집 검증

```powershell
.\.venv\Scripts\python.exe scripts/wazuh.py rules
.\.venv\Scripts\python.exe scripts/wazuh.py smoke
```

`rules`: 실제 `/var/ossec/bin/wazuh-logtest`에 합성 로그를 넣고 예상 규칙 ID와 비교한다. 같은 계정·서로 다른 trace_id의 1·2회는 100110, 3회는 100112여야 한다. 엔진 시험 미통과 시 반복 경보를 완성했다고 판단하지 않는다.

`smoke`: **별도 시험 파일** `/var/log/ieum-tests/smoke.json`에 `lab_fixture:true`가 표시된 합성 이벤트 12건을 3초 간격으로 넣고, 실제 Indexer에서 trace_id별 규칙 ID가 검색되는지 확인한다. 입력에 약 36초가 걸린다. 검색은 최대 24회, 재시도 간격 5초, 요청별 제한 20초이므로 연결 장애 시 2분을 넘길 수 있다. 실패하면 저장 건수·누락 건수·예상/실제 규칙 불일치·마지막 오류 종류를 표시한다. 원본 감사 파일에는 합성 로그를 쓰지 않는다. 이는 순차 입력 수집 시험이며 동시 입력 탐지 성능이나 실제 공격·정상 업무 재현 결과를 뜻하지 않는다.

## 4. 대시보드에서 볼 것

`https://localhost:8444` 로그인 → 보안 이벤트/Threat hunting 화면에서 기간을 최근 15분으로 선택한다. 메뉴명은 버전에 따라 달라질 수 있다.

| 필터 | 의미 |
|---|---|
| `rule.groups:privacygate` | 프로젝트 감사 이벤트 |
| `rule.id:100110` | 내부 관리자 API 권한 거부 |
| `rule.id:100112` | 같은 계정의 관리자 요청 60초 내 3회 거부 |
| `rule.id:100111` | 승인 파일·수신자 불일치 |
| `rule.id:100120` | 파일 해시·버전 무결성 오류 |
| `rule.id:100130` | 매칭 수락·취소 등의 소유자 권한 위반 |
| `rule.id:100102` | 정상 허용 기록 |
| `rule.id:100131` | 시험 baseline의 파일 연결 검사 생략 기록 |
| `rule.id:100132` | 시험 baseline에서 역할 검사 없이 허용된 관리자 내보내기 |
| `rule.id:100140` | 내부 회원 정보 삭제 실패 |
| `rule.id:100141` | 내부 증빙 원본 삭제 실패 |
| `rule.id:100142` | 계정 정리 중 비회원 계정 삭제 거부 |
| `rule.id:100150` | 반복 거부로 계정 자동 임시 잠금 (사후 대응) |
| `rule.id:100151` | 임시 잠긴 계정의 요청 차단 |
| `rule.id:100152` | 총관리자 사후 조치 (잠금 해제·계정 정지·복구·받은 자료 회수) |
| `rule.id:100153` | 중계가 공격 ② 보안 사건을 내부에 알리지 못함 |
| `rule.id:100160` | 감사 로그 위변조 (해시 체인 손상, audit-verify.sh 결과) |
| `rule.id:100161` | 감사 로그 파일 없음·수집 중단 의심 |
| `rule.id:100170` | 철회·만료된 동의(또는 회수된 승인)로 자료 요청 |
| `rule.id:100171` | 요청자가 자기 반출 요청을 승인하려 함 |
| `rule.id:100172` | 심사·마스킹이 끝나지 않은 자료 요청 |
| `rule.id:100173` | 일회용 다운로드 토큰 재사용·위조·만료 |
| `rule.id:100174` | 다운로드 횟수 한도 도달 |
| `rule.id:100175` | 같은 자료 5분 안에 3번 다운로드 |
| `rule.id:100177` | 담당이 아닌 회원 조회 |
| `rule.id:100180` | Keycloak 로그인 실패 |
| `rule.id:100181` | 같은 계정 2분 안에 로그인 5번 실패 |
| `rule.id:100182` | Keycloak 무차별 대입 잠금 |
| `rule.id:100183` | Keycloak 역할·그룹 변경 (관리자, 앱 자동 처리 제외) |
| `data.lab_fixture:true` | 합성 수집 시험 이벤트 |
| `data.trace_id:요청값` | 한 요청의 서비스별 기록 추적 |

실제 앱 검증은 본인 랩에서 생성한 요청의 응답 상태·자료 반환 여부와 같은 trace_id의 경보를 함께 확인한다. 경보만으로 차단 성공·파일 유출 성공을 판정하지 않는다. 관리자 허용 기록이 있어도 실제 업무가 501일 수 있다. 현재 내부 기능 일부가 미구현이므로 전체 공격 ①의 파일 생성·후속 반출까지 완료됐다고 표현하지 않는다.

외부 BFF의 중계 오류 재기록과 참조용 `delivery.reference`를 반복 공격 횟수에 합산하지 않는다. 일반 세션 없음·동의 미설정·매칭권 소진은 100101 감사 기록이며 그 자체로 고위험 공격 경보가 아니다. IP가 같다는 이유로 A와 B를 공범으로 자동 확정하지 않는다. 승인·파일 ID의 연결 기록은 실제 남아 있는 범위에서만 해석한다.

## 5. 보관·용량 정책

- 검색 대상은 `wazuh-alerts-4.x-*`만이다. ISM으로 **인덱스 생성 후 7일**이 지나면 삭제한다. 개별 이벤트 기준 정각 7일 삭제가 아니라 주기 실행이므로 차이가 있을 수 있다.
- 별도 관리 컨테이너가 5분마다 정책 적용과 경보 인덱스 **2GiB 예산**을 확인한다. 초과 시 지난 일자의 인덱스부터 정리하므로 7일 이전 기록도 먼저 삭제될 수 있다.
- 당일 활성 인덱스는 삭제하지 않는다. 당일 데이터만으로 예산을 넘으면 인덱스 쓰기를 제한하고 `quota_blocked`를 기록한다. 이는 업무 API 차단이 아니라 관제 저장 제한이다. 로그를 확인한 뒤 원인·예산을 조정하고 쓰기 제한을 수동 해제해야 한다.
- 주기 점검이므로 순간 초과는 가능하다. **모든 Docker 볼륨에 대한 파일시스템 하드 쿼터가 아니다.** 컨테이너 표준 출력 로그는 각 10MB×3으로 제한한다. Manager의 로컬 경보 파일·큐와 원본 앱 감사 파일은 별개이며 주기적으로 디스크 상태를 확인한다.
- 원본 감사 볼륨은 읽기 전용이다. 검색 데이터 정리와 함께 원본 증빙·업무 DB·원본 감사 로그를 삭제하지 않는다.
- 정책 적용 상태는 `scripts/wazuh.py logs`에서 `ieum-retention`의 `status:ok`, `policy:ieum-alerts-7d`를 확인한다. 오류가 계속되면 보관 정책이 적용됐다고 간주하지 않는다.

## 6. 종료

```powershell
.\.venv\Scripts\python.exe scripts/wazuh.py stop
```

Wazuh 프로젝트만 정지하며 앱과 DB는 유지한다. `down -v`를 사용하지 않는다. 다음 실행도 `start`를 사용한다. 자동 시작 설정은 하지 않았다.

## 검증 수준

설정 생성·Compose 병합·소스 체크섬·안전 경계 및 관리 도구는 오프라인 시험으로 확인한다. 실제 Wazuh 이미지 실행, 규칙 엔진, 수집·색인·대시보드 검증은 디스크 확보 후 별도로 수행해야 한다. 스크립트의 예상 규칙 ID가 있다는 사실은 실제 엔진 검증 완료의 증거가 아니다.

공식 근거: [Docker 구성](https://github.com/wazuh/wazuh-docker/tree/v4.14.10/single-node) · [Docker 요구사항](https://documentation.wazuh.com/current/deployment-options/docker/wazuh-container.html) · [규칙 문법](https://documentation.wazuh.com/current/user-manual/ruleset/ruleset-xml-syntax/rules.html) · [인덱스 수명주기](https://documentation.wazuh.com/current/user-manual/wazuh-indexer-cluster/index-lifecycle-management.html)
