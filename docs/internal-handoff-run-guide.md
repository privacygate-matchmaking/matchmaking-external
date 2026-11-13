# 내부 변경 연동 실행 안내

기존 실행 환경을 유지하면서 새 기능을 준비한 상태입니다. 이번 수정만으로 현재 Docker 이미지나 DB는 바뀌지 않습니다.

## 기존 외부 실행

Windows PowerShell에서 matchmaking-external 폴더로 이동한 뒤:

```powershell
.\Start.ps1
```

Start.ps1은 기존 세션 키를 보존하고 `.local/internal-admin.env`를 준비합니다. 실제 비밀값은 출력하지 않습니다. 기본 모드에서는 기존 유료 매칭 API·증빙·동의·반출 경로를 유지하며 새 탈퇴 UI/변경 API를 활성화하지 않습니다.

macOS 또는 Compose를 직접 사용할 때는 다음처럼 환경 파일 두 개를 함께 지정합니다. `.venv`에 프로젝트 실행 의존성이 설치되어 있어야 합니다.

```sh
python scripts/initialize.py
docker compose --project-directory ../privacygate --env-file ../privacygate/.env --env-file ./.local/internal-admin.env -f ../privacygate/compose.yaml -f ./compose.external.yaml --profile external config --quiet
docker compose --project-directory ../privacygate --env-file ../privacygate/.env --env-file ./.local/internal-admin.env -f ../privacygate/compose.yaml -f ./compose.external.yaml --profile external up -d --build
```

Windows에서 python 대신 `.\.venv\Scripts\python.exe`를 사용해도 됩니다. 공통 `.env`나 PowerShell 환경에 이미 INTERNAL_ADMIN_CLIENT_SECRET이 있으면 그 값을 사용하고 새 값으로 바꾸지 않습니다.

## 회원 상태·탈퇴 기능 활성화

**먼저 내부 담당자가 최신 업무 DB 스키마와 상태 API를 준비해야 합니다.** 기존 DB를 보존할지 시드를 새로 만들지 합의하기 전에는 초기화 명령을 실행하지 않습니다. 준비 후 기본 실행 명령에 `-f ./compose.member-lifecycle.yaml`을 추가합니다.

```sh
docker compose --project-directory ../privacygate --env-file ../privacygate/.env --env-file ./.local/internal-admin.env -f ../privacygate/compose.yaml -f ./compose.external.yaml -f ./compose.member-lifecycle.yaml --profile external config --quiet
docker compose --project-directory ../privacygate --env-file ../privacygate/.env --env-file ./.local/internal-admin.env -f ../privacygate/compose.yaml -f ./compose.external.yaml -f ./compose.member-lifecycle.yaml --profile external up -d --build
```

- 외부: https://localhost:8090
- 내부: https://localhost:8080
- 인증: https://localhost:8081
- Wazuh: https://localhost:8444 (별도 실행 중일 때)

확인 순서:

1. 회원 로그인 후 `/api/me`의 member_status_check=true, `/api/me/status`의 status=active 확인.
2. 증빙 관리 → 졸업증명서/재직증명서 선택 → 가상 PDF 제출 → 내부 담당자가 종류·심사 상태 확인.
3. 제공 동의 내역에 회원 코드 대신 상대 설명 표시 확인.
4. 내 계정 → 시험 회원으로 탈퇴 신청. 7일 유예 안내와 철회 버튼, 기존 업무 화면 숨김 확인.
5. 유예 중 기존 자료/매칭 API도 403인지 확인. 본인 신청 철회 후 정상 화면으로 돌아오는지 확인.
6. 직원이 처리한 시험 계정은 이유 안내만 표시하고 철회할 수 없는지 확인. 삭제 완료 상태는 안내 후 외부 세션이 종료되는지 확인.

탈퇴는 실제 시험 회원 상태를 변경하므로, 보존해야 할 주 시연 계정이 아닌 별도 합성 시험 계정으로 확인합니다. API 대역을 사용하는 자동 시험과 실제 DB 연동 시험은 구분합니다.

## 기존 유료 매칭 실습 보존

내부 매칭 방식으로 자동 전환하지 않았습니다. `compose.matching-lab.yaml`을 세 번째 파일로 사용하던 기존 절차는 그대로이며 lifecycle 파일을 함께 추가하지 않습니다. 내부 업무 DB에 없는 시험 계정은 실제 탈퇴·학력 심사 검증에 사용하지 않습니다.

내부 연결 상태에서 유료 매칭용 matching-directory/matching-disclosures 미구현 문제와 소개 파일의 주체 값 불일치는 [내부 전달 파일](internal-handoff-v0.6.md)에서 먼저 합의해야 합니다. 외부 수정 완료만으로 전체 매칭·반출 연결 완료를 선언하지 않습니다.

## Wazuh 새 규칙 적용 (전체 연결 확인 단계)

```powershell
.\.venv\Scripts\python.exe scripts/wazuh.py prepare
docker restart ieum-wazuh-wazuh.manager-1
.\.venv\Scripts\python.exe scripts/wazuh.py rules
.\.venv\Scripts\python.exe scripts/wazuh.py smoke
```

Manager 기동 시간이 필요합니다. analysisd 실행 상태 확인 후 rules를 실행합니다. 기존 합성 시험 9건에 새 삭제 오류 3건을 추가해 시험합니다. 실제 웹 요청 검증에서는 data.lab_fixture가 없는 API 감사 기록의 trace_id·응답 상태를 함께 확인합니다. 2026-11-13 최신 rules 12건과 합성 12건의 색인을 확인했습니다. 일괄 입력의 반복 경보 불일치로 smoke를 3초 간격 입력으로 변경했으며, 사용자 PC에서 변경 후 prepare → smoke 전체 검증도 통과했습니다. 순차 시험은 동시 입력 반복 탐지를 보증하지 않습니다.
