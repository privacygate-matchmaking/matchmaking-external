# 매칭 기능 실행·확인 가이드

2026-11-13. 기존 유료 신청·상대 수락 방식을 독립 확인하는 합성 실습입니다. 내부 상호 매칭·자동 동의와 통합 완료한 시험은 아닙니다. 실행 명령·환경 파일은 [팀원 1 실행 가이드](team1-run-guide.md) 1~6절을 사용합니다.

## 1. 실행 준비

- 세 저장소와 로컬 `.env`·TLS·세션 키를 준비합니다. `scripts/initialize.py`를 실행하고 환경 파일 두 개를 Compose에 전달합니다.
- `compose.external.yaml` 뒤에 `compose.matching-lab.yaml`만 추가합니다. `compose.member-lifecycle.yaml`과 혼합하지 않습니다.
- `matching-migrate`, `meeting-migrate`는 Exited(0), DB/Keycloak은 healthy여야 합니다.
- Keycloak 초기 설정 후 `setup-test-members.sh`를 실행합니다. 사용자 Windows PC에서 C/D/E 준비 성공 출력과 일정 테이블 생성을 확인했습니다. Mac은 별도 확인이 필요합니다.
- 시험 비밀번호는 자기 공통 `.env`의 KC_TEST_PASSWORD입니다. C/D/E는 내부 업무 DB 회원으로 자동 등록되지 않습니다.

## 2. 회원 화면 확인 순서

1. `https://localhost:8090`에서 member_b 로그인. 합성 실습 표시를 확인합니다.
2. 이용권 탭 → 시험 결제로 활성화합니다. 330만원·6개월·5개는 프로젝트 시험값이며 실제 금전 거래는 없습니다.
3. B가 합성 상대 M-C01에게 신청합니다. 사용 가능 4개·예약 1개가 되어야 합니다.
4. 다른 브라우저/시크릿 창에서 member_c 로그인합니다. 무료 상태로 받은 신청 수락이 가능합니다.
5. C 수락 후 B는 예약 0개·사용 1개가 되어야 합니다.
6. B가 상세 결과를 열면 C의 공개 동의 전에는 거부되어야 합니다.
7. C가 내 공개 범위에서 재직 등 항목과 기간을 선택합니다. B는 해당 검토 결과만 볼 수 있어야 합니다. 사진·연락처는 현재 자료/계약 준비 대기이며 원본 증빙을 공개하지 않습니다.
8. C가 동의를 철회한 뒤 B의 다음 서버 조회는 거부되어야 합니다. 이미 받은 화면이나 캡처를 회수하는 기능은 아닙니다.
9. 별도 신청으로 거절·수락 전 취소·만료를 확인합니다. 예약은 해제됩니다. 성사 후 종료는 이미 사용한 1회를 돌려주지 않지만 추가 벌점은 없습니다.
10. 매칭 현황의 만남 일정 확인을 엽니다. 실제 직원 후보 제안 전에는 일정이 비어 있을 수 있습니다. 직원 화면 연결은 [일정 명세](meeting-scheduling.md)가 필요합니다.

동일 브라우저의 일반 탭들은 Keycloak 로그인 상태를 공유합니다. B/C는 서로 다른 브라우저 세션을 사용합니다. 기존 증빙·제공 동의 탭은 여전히 내부 API를 사용하며, 합성 매칭 모드가 내부 미구현을 대신하지 않습니다.

## 3. 정상 내부 연결 모드와 차이

합성 파일을 빼고 기본 Compose로 재기동하면 `MATCHING_DIRECTORY_MODE=internal`, `ENABLE_TEST_PAYMENTS=false`가 기본입니다. 공통 `.env`나 셸에 실습 값을 직접 설정했다면 그것도 되돌립니다. 시험 이력은 남으므로 실제 회원 이력과 섞지 않습니다.

기본 모드에는 내부 matching-directory/matching-disclosures 계약이 필요합니다. 내부 my-matches와 같은 API가 아닙니다. 없거나 응답 형식이 다르면 서비스 오류를 표시하며 합성 결과로 자동 대체하지 않습니다. [내부 전달 파일](internal-handoff.md)에 연결 요청과 정책 차이를 정리했습니다.

## 4. 자동 시험과 검증 범위

시험 의존성 설치 후:

```sh
python -m pytest tests/integration
node --test tests/frontend/account.test.cjs tests/frontend/meetings.test.cjs
```

Windows Python은 `.\.venv\Scripts\python.exe`, Mac은 `.venv/bin/python`을 사용합니다. 시험의 내부 API는 응답 대역이며 DB는 SQLite 시험 어댑터를 사용합니다. 실제 PostgreSQL 동시성·최신 내부 전체 흐름·Mac 로그인 왕복까지 보장하지 않습니다. 현재 확인 결과는 [검증 현황](verification.md)에 있습니다.
