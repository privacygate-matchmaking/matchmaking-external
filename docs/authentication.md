# 인증·세션

| 대상 | 방식 |
|---|---|
| 회원 로그인 | Keycloak realm matchmaking, client external-web, Code + PKCE S256 |
| 브라우저 | external_session 불투명 난수 쿠키, Secure·HttpOnly·SameSite=Strict |
| 로그인 결합 | state·nonce·브라우저 결합 쿠키(external_login, Lax)·일회성 흐름, 10분 |
| 외부→중계 | external-api 서비스 토큰 + X-User-Token 회원 토큰 |
| 중계→내부 | transfer-api 서비스 토큰, 회원 업무에는 회원 토큰도 전달 |
| 직원→중계 | 내부 BFF가 전달하는 internal-web 직원 토큰 |

JWT는 RS256 서명·issuer·audience·만료·azp를 검증합니다. 회원 토큰의 중계 audience는 transfer-api, azp는 external-web입니다. 미확인 kid 또는 캐시 만료 시 JWKS를 다시 읽습니다. URL은 신뢰한 OIDC discovery 설정에서 가져옵니다.

외부 세션은 SQLite 전용 볼륨에 보관합니다. 쿠키 원문 대신 해시를 저장하며 토큰·로그인 흐름 값은 별도 Fernet 키로 암호화합니다. 유휴 30분·절대 8시간 제한입니다. 만료 임박 30초 이내이면 서버가 갱신하고 새 토큰의 신원·회원 역할을 재확인합니다. 갱신 실패 시 접근을 거부하며 인증 거절이면 세션을 폐기합니다. 로그아웃은 서버 세션을 먼저 삭제한 뒤 Keycloak 로그아웃으로 이동합니다.

변경 요청은 CSRF 토큰 및 정확한 Origin을 함께 검사합니다. 브라우저가 제출한 사용자 ID나 역할을 신원으로 사용하지 않습니다.

**역할 변경의 시간차:** 이미 발급된 JWT의 claim은 발급 당시 값이므로 계정 역할을 바꿔도 토큰 자체가 바뀌지 않습니다. 공통 realm의 접근 토큰 수명은 300초입니다. 현 구현은 서명 검증 방식이므로 계정 정지·직원 역할 변경의 즉시 반영을 보장하지 않습니다. 내부의 현재 업무 권한 조회/차단 연동이 필요하며 즉시 반영을 완료했다고 표현하면 안 됩니다. 갱신된 회원 토큰의 역할 변경은 외부 BFF에서 재검증합니다.

토큰·쿠키·비밀번호·증빙 본문은 감사 기록에 넣지 않습니다. 일반 access log는 꺼 두었습니다. 감사 저장 실패는 공통 모듈의 best-effort 정책을 따릅니다.
