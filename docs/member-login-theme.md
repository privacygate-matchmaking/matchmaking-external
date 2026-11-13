# 이음 회원 로그인 테마

Keycloak 26.0.7의 `keycloak` 테마를 상속해 색상·간격·로고·장식·문구만 변경한다. 로그인 폼 템플릿과 자격 증명 처리 코드는 교체하지 않는다.

## 적용 범위

- 회원 로그인 클라이언트 `external-web`의 `login_theme`를 `ieum-members`로 설정한다.
- 직원용 `internal-web` 테마와 `master` 관리자 로그인은 그대로 둔다.
- Keycloak 테마 파일과 설정 스크립트는 모두 이 외부 저장소에 있다. 내부·공통 저장소의 파일은 수정하지 않는다.
- OAuth/OIDC 주소·PKCE·세션·회원 역할·비밀번호·OTP 정책은 변경하지 않는다.
- 기본 로그인 폼의 오류, 언어 선택, 비밀번호 보기, 후속 인증 화면을 유지한다.

## 적용 방법

[실행 가이드](team1-run-guide.md)에 따라 현재 선택한 모드의 Compose 인수를 준비한 뒤 적용합니다. 아래는 Windows PowerShell 예이며 Mac은 `@composeArgs` 대신 `"${composeArgs[@]}"`를 사용합니다.

```powershell
docker compose @composeArgs up -d keycloak member-theme-setup
docker compose @composeArgs ps -a member-theme-setup
docker compose @composeArgs logs --tail 30 member-theme-setup
```

`member-theme-setup`은 성공 시 `Exited (0)`이며 `IEUM member login theme applied to external-web.`을 출력한다. 신규 설치는 기존 전체 `up -d --build` 명령에서도 자동 적용된다. Keycloak 재생성 동안 로그인 요청이 잠시 중단될 수 있다. DB 볼륨을 삭제할 필요는 없다.

`https://localhost:8090`에서 로그아웃한 뒤 **회원 로그인**을 눌러 확인한다. 이미 로그인했다면 인증 화면을 건너뛸 수 있으므로 새 시크릿 창을 사용해도 된다. 기존 로그인 화면의 주소를 즐겨찾기로 재사용하지 않는다.

추가·변경된 CSS가 캐시돼 있으면 로그인 화면에서 `Ctrl+Shift+R`로 새로고침한다. Mac은 `Command+Shift+R`. 이 테마는 직원/관리자 로그인 화면에서는 나타나지 않는다.

## 확인 상태

Compose 병합 및 테마 파일 구문을 확인했습니다. 사용자 PC에서 전체 화면 확인은 마쳤지만 Mac의 테마 렌더링과 로그인 왕복은 별도 확인해야 합니다. 테마 적용 실패 시 `member-theme-setup` 로그를 먼저 확인한다. 인증이 실패하면 오류 문구를 숨기거나 인증 검사를 우회하지 않는다.

공식 구현 방식: [Keycloak 테마 가이드](https://www.keycloak.org/ui-customization/themes).
