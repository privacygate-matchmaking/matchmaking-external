# 매칭 기능 실행·확인 가이드

## 실행 전

세 저장소를 같은 상위 폴더에 둔다. 기존 환경변수·TLS·세션 키가 준비된 로컬 Docker 환경을 기준으로 한다. 내부·공통 저장소의 코드와 README는 변경하지 않았다. 아래 명령은 **matchmaking-external 폴더**에서 실행한다.

매칭 기능이 포함된 최신 main을 내려받고 이미지를 다시 빌드해야 한다. 이전 이미지의 컨테이너를 시작하는 것만으로는 새 화면이 나타나지 않는다.

## 1. 내부 연결 모드

Windows PowerShell, macOS 터미널 공통:

```sh
docker compose --project-directory ../privacygate -f ../privacygate/compose.yaml -f ./compose.external.yaml --profile external config --quiet
docker compose --project-directory ../privacygate -f ../privacygate/compose.yaml -f ./compose.external.yaml --profile external up -d --build
```

`matching-migrate`는 DB owner로 기존 중계 DB에 별도 테이블만 추가하고 종료한다. 정상 완료는 Exited(0). 기존 테이블·자료를 삭제하지 않는다. 앱 계정에는 새 테이블의 필요한 권한만 부여한다. 실패 시 다음을 먼저 확인한다.

```sh
docker compose --project-directory ../privacygate -f ../privacygate/compose.yaml -f ./compose.external.yaml --profile external logs --tail 80 matching-migrate transfer-api
```

기본값 `MATCHING_DIRECTORY_MODE=internal`, `ENABLE_TEST_PAYMENTS=false`. 실제 결제는 연결되지 않았다. 내부의 `matching-directory`, `matching-disclosures` 구현 전에는 소개 후보·검토 결과를 불러올 수 없다는 안내가 정상이다. 자동으로 합성 결과로 바꾸지 않는다.

## 2. 독립 확인용 합성 실습 모드

내부 API 구현 전에도 매칭 업무 흐름을 확인하려면 세 번째 파일을 추가한다. 실제 회원·증빙을 사용하지 않는다. GATE_MODE는 기존 값을 유지한다.

```sh
docker compose --project-directory ../privacygate -f ../privacygate/compose.yaml -f ./compose.external.yaml -f ./compose.matching-lab.yaml --profile external up -d --build
docker compose --project-directory ../privacygate -f ../privacygate/compose.yaml -f ./compose.external.yaml -f ./compose.matching-lab.yaml --profile external exec -T keycloak bash /opt/matching-lab/setup-test-members.sh
docker compose --project-directory ../privacygate -f ../privacygate/compose.yaml -f ./compose.external.yaml -f ./compose.matching-lab.yaml --profile external ps -a
```

기존 `member_b`에 더해 `member_c`, `member_d`, `member_e` 시험 계정이 생성된다. 비밀번호는 공통 로컬 `.env`의 `KC_TEST_PASSWORD`. 기존 같은 이름의 계정 ID가 다르면 덮어쓰지 않고 중단한다. 키·비밀번호·.env를 팀 채팅이나 Git에 붙여넣지 않는다.

공통 Keycloak 최초 설정이 아직 안 끝났다면 먼저 기존 초기화 절차를 마친다. helper는 `matchmaking` realm과 `member` 역할이 이미 존재한다고 가정한다. Mac/Windows에서 셸 파일 줄바꿈은 LF여야 한다. 새 계정은 합성 실습 전용이며 내부 업무 DB 회원으로 자동 등록되지 않는다.

## 3. 브라우저 확인 순서

helper는 지정된 UUID를 유지하는 Keycloak 부분 가져오기 API를 사용하고 생성 후 ID를 다시 확인한다. 구현 근거: [Keycloak 26.0.7 UsersPartialImport](https://github.com/keycloak/keycloak/blob/26.0.7/services/src/main/java/org/keycloak/partialimport/UsersPartialImport.java). 컨테이너 내 실제 helper 실행은 추가 확인이 필요하다.

1. `https://localhost:8090` 접속. 기존 로컬 TLS 신뢰 설정을 사용한다.
2. 일반 브라우저에서 `member_b` 로그인. 소개 후보에 합성 실습 표시가 보이는지 확인한다.
3. 이용권 탭 → **시험 결제로 이용권 활성화**. 330만원·6개월·5개가 표시되지만 실제 금전 거래는 없다.
4. 소개 후보에서 M-C01에게 신청. 이용권은 사용 가능 4개·예약 1개가 된다.
5. 시크릿 창 또는 다른 브라우저에서 `member_c` 로그인. 무료 상태로 받은 신청 수락 가능.
6. C가 매칭 현황 → 수락. B 이용권은 예약 0개·사용 1개가 된다. B 화면은 새로고침한다.
7. B가 상대 정보 보기: 아직 동의가 없다는 안내가 나와야 한다.
8. C가 **내 공개 범위 설정**에서 재직 등 항목·기한 선택 후 저장. B가 해당 결과만 볼 수 있는지 확인한다.
9. C가 동의 철회 → B의 다음 서버 조회는 거부되어야 한다. 이미 받은 자료나 화면 캡처를 회수하는 기능은 아니다.
10. 별도 신청으로 거절·보낸 신청 취소를 확인한다. 예약은 해제된다. 매칭 성립 후 관계 종료는 사용한 횟수를 반환하지 않는다.

동일 브라우저의 탭은 Keycloak 로그인 상태를 공유한다. B와 C를 서로 다른 브라우저 세션에서 사용한다. 실습 모드의 사진·연락처는 자료 준비·연결 대기로 표시하며 원본 증빙을 노출하지 않는다.

기존 증빙·제공 동의 탭은 내부 API를 계속 사용하므로 내부가 501이면 오류를 표시할 수 있다. 합성 모드는 이를 대신 구현하지 않는다.

## 4. 원래 모드로 복귀

세 번째 파일을 **빼고** 1번의 up 명령을 실행한다. 공통 .env에 두 실습 변수를 별도로 설정했다면 `MATCHING_DIRECTORY_MODE=internal`, `ENABLE_TEST_PAYMENTS=false`로 되돌린다. 새 테이블·시험 이력은 남으며 실제 회원과 혼용하지 않는다. 시험 계정 정리는 Keycloak에서 별도로 한다. `down -v`로 업무 DB를 삭제하지 않는다.

## 5. 검사와 종료

```sh
python -m pytest tests/integration
docker compose --project-directory ../privacygate -f ../privacygate/compose.yaml -f ./compose.external.yaml --profile external stop
```

pytest에는 프로젝트와 공통 privacygate의 시험 의존성 설치가 필요하다. Windows에서는 `.\.venv\Scripts\python.exe -m pytest tests/integration`, Mac에서는 `.venv/bin/python -m pytest tests/integration`로 실행해도 된다.

이번 자동 시험은 SQLite 시험 어댑터·내부 API 대역·실제 RSA 검증을 사용한다. PostgreSQL 컨테이너 마이그레이션·실제 Keycloak 다중 계정 왕복은 위 실행 절차로 추가 확인해야 한다. Wazuh가 자동으로 설치되는 구성은 아니다.
