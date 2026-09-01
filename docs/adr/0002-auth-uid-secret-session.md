# ADR-0002: 인증 — ClassIn SID/secret 로그인 + 서명쿠키 세션 (이중 모드)

- 상태: 승인
- 날짜: 2026-09-01

## 맥락
- ClassIn 파트너 API 인증은 SID(uid) + secret 기반 v2 MD5 서명뿐이며, 사용자 단위 OAuth가 없다.
- 요구사항: "로그인 - uid/secret 으로 혹은 보안 연결 지어서 구성".
- secret이 브라우저에 남으면 안 된다.

## 결정
두 가지 모드를 지원한다 (환경변수로 선택):

1. **credential 모드 (기본)**: 로그인 폼에 SID/secret 입력 → 서버가 ClassIn API 검증 호출로 확인 → secret은 서버 메모리 세션 저장, 브라우저에는 itsdangerous 서명 세션쿠키(세션 ID만) 발급.
2. **fixed 모드 (보안 연결)**: SID/secret을 서버 `.env`에 고정하고, 접속은 별도 `DASH_ACCESS_PASSWORD` 로 로그인. 역할(원장/선생님)은 로그인 시 선택.

공통:
- 쿠키는 `HttpOnly`, `Secure`(운영), `SameSite=Lax`.
- 세션 만료 12시간, 서버 재시작 시 credential 모드 세션은 무효(재로그인).

## 결과
- 시크릿이 클라이언트로 내려가지 않는다.
- 파일럿(학원 1곳)은 fixed 모드로 간단 운영, 멀티 계정은 credential 모드로 수용.
