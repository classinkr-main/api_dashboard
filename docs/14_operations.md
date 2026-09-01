# 14. 운영 가이드

배포 주소: `https://api.classin.co.kr/dash` (리버스 프록시 뒤, 서브패스 배포)

관련 문서: [`docs/PRD.md`](./PRD.md), [`docs/adr/`](./adr/), 루트 [`README.md`](../README.md)

## 14.1 초기 설정

1. `.env.example`을 복사해 `.env` 작성 (저장소에 커밋 금지).

   ```bash
   cp .env.example .env
   ```

2. 세션 서명용 시크릿 생성 후 `DASH_SECRET_KEY`에 채워 넣는다.

   ```bash
   openssl rand -hex 32
   ```

3. 나머지 값 채우기 (`.env` 항목 요약):

   | 변수 | 설명 |
   |---|---|
   | `DASH_ROOT_PATH` | 서브패스, 기본 `/dash` (프록시 설정과 반드시 일치) |
   | `DASH_PORT` | uvicorn 리슨 포트, 기본 `8100` |
   | `DASH_SECRET_KEY` | 쿠키/세션 서명 키 (위에서 생성) |
   | `DASH_COOKIE_SECURE` | `true` — HTTPS 배포에서는 항상 true |
   | `DASH_AUTH_MODE` | `credential`(로그인 시 SID/secret 입력) 또는 `fixed`(서버 고정 자격 + 접속 비밀번호) |
   | `DASH_ACCESS_PASSWORD` | `fixed` 모드일 때만 필요 |
   | `DASH_CLASSIN_SID` / `DASH_CLASSIN_SECRET` | `fixed` 모드 및 웹훅 검증에 사용하는 ClassIn 파트너 자격 |
   | `DASH_CLASSIN_BASE_URL` | 기본 `https://api.eeo.cn` |
   | `DASH_WEBHOOK_SAFEKEY` | ClassIn Data Sub 웹훅 서명 검증 키 |
   | `DASH_ANTHROPIC_API_KEY` / `DASH_ANTHROPIC_MODEL` | Claude 스케줄 파싱·알림 문구 생성용 |
   | `DASH_DATA_DIR` | SQLite/JSONL 저장 경로, 기본 `data` (컨테이너에서는 `/app/data`로 매핑) |

## 14.2 실행

### Docker (권장, 운영 환경)

```bash
docker compose up -d --build
docker compose logs -f dash
```

- `docker-compose.yml`은 `127.0.0.1:8100`에만 바인딩한다 — 외부 노출은 nginx가 담당(`deploy/nginx.conf.example` 참고).
- `./data`가 컨테이너의 `/app/data`로 마운트되어 SQLite DB와 웹훅 원본(JSONL)이 호스트에 남는다.

### 로컬 (개발/디버깅)

```bash
pip install -e .
classin-dash
```

`http://127.0.0.1:8100` 에서 직접 접근 가능 (프록시 없이 테스트할 때는 `DASH_ROOT_PATH=` 로 비워서 실행).

## 14.3 웹훅(Data Sub) 등록 절차

엔드포인트는 SID(파트너 계정)당 하나씩 **ClassIn 지원팀이 수동으로 등록**한다. 자체 등록 API는 없으므로, 아래 정보를 정리해서 ClassIn 지원팀(계정 매니저)에게 요청한다.

**요청 시 전달할 정보**

- **엔드포인트 URL**: `https://api.classin.co.kr/dash/webhook/classin`
- **구독할 Cmd 목록**:
  - `Attendance` (출결)
  - `End` (수업 종료)
  - `HomeworkSubmit` (과제 제출)
  - `HomeworkScore` (과제 채점)
  - `AnswerSheetScore` (답안지 채점)
  - `Rating` (AI 강의 평가)
  - `ExamScore` (시험 점수)
- **SID**: 대상 파트너 계정의 SID
- **오류 알림 이메일**: 웹훅 전송 실패 시 통지받을 이메일 주소 (운영 담당자 메일)

등록 완료 후 ClassIn 측에서 SafeKey를 발급/확인해 주면 `.env`의 `DASH_WEBHOOK_SAFEKEY`에 반영하고 컨테이너를 재시작한다.

```bash
docker compose up -d   # .env 변경 후 재적용
```

## 14.4 백업

영속 데이터는 전부 `DASH_DATA_DIR`(도커 배포 시 호스트 `./data`) 아래에 있다:

- `data/dashboard.db` — SQLite (정규화된 이벤트/캐시)
- `data/webhook/YYYY-MM-DD.jsonl` — 웹훅 원본 페이로드 (일자별 append-only)

백업 예시 (컨테이너 중단 없이, SQLite는 파일 복사만으로 충분히 안전한 경우가 많지만 운영 시간 외 스냅샷을 권장):

```bash
tar czf backup-$(date +%F).tar.gz data/
```

`data/webhook/*.jsonl`은 파싱 실패 이벤트도 원본 그대로 보존하므로, 스키마가 바뀌면 이 파일들로 재처리(replay)할 수 있다 (`docs/adr/0003-data-webhook-store.md` 참고).

## 14.5 시계 동기화 (NTP)

ClassIn v2 API 서명 및 웹훅 서명 검증은 타임스탬프 기반이며 **±5분** 오차까지만 허용한다. 서버 시계가 이보다 벗어나면 정상 요청도 서명 오류로 거부된다.

- 서버에 NTP 클라이언트(chrony/systemd-timesyncd 등)가 활성화되어 있는지 확인한다.

  ```bash
  timedatectl status   # "System clock synchronized: yes" 확인
  ```

- Docker 컨테이너는 호스트 커널 시계를 그대로 쓰므로, **호스트**의 NTP 동기화만 확인하면 된다 (컨테이너 안에서 별도 NTP 설정 불필요).

## 14.6 로그 확인

```bash
docker compose logs -f dash        # 실시간
docker compose logs --since 1h dash
```

로컬 실행 시에는 표준 출력으로 uvicorn 접근 로그 및 앱 로그가 그대로 출력된다.

## 14.7 트러블슈팅

| 증상 | 원인 / 조치 |
|---|---|
| ClassIn API 응답 `errno` 코드 반환 (0이 아님) | 해당 SID에 API 미활성화 또는 SID/secret 오류. ClassIn 지원팀에 API 활성화 여부 확인, `.env`의 `DASH_CLASSIN_SID`/`DASH_CLASSIN_SECRET` 재확인. |
| `errno 101002005` | 서명 오류 — 대부분 **서버 시계 오차**(±5분 초과)가 원인. `timedatectl status`로 NTP 동기화 확인(14.5절), 그다음 SID/secret 값 재확인. |
| 조회 API가 특정 SID에서 계속 비어있거나 미활성 상태 | ClassIn 파트너 API는 SID별로 활성화가 필요하다. 조회 API가 아직 활성화되지 않은 경우에도 **대시보드는 완전히 멈추지 않는다** — 웹훅(Data Sub)으로 수신·축적된 데이터만으로 동작하며, 실시간 조회가 필요한 항목은 "수집 대기"로 표시된다. API가 활성화되면 자동으로 실시간 조회 데이터가 병합된다. |
| 웹훅이 전혀 안 들어옴 | (1) nginx에서 `/dash/webhook/classin`이 인증/차단 없이 통과하는지 확인(`deploy/nginx.conf.example`), (2) ClassIn 지원팀에 등록된 엔드포인트 URL·SID가 정확한지 확인, (3) `data/webhook/`에 파일 자체가 생성되는지 확인해 프록시 문제인지 앱 문제인지 구분. |
| 로그인 후 바로 세션이 풀림 | `DASH_SECRET_KEY`가 재배포마다 바뀌면 기존 세션 쿠키가 무효화된다. `.env`에 고정값으로 저장했는지 확인. `DASH_COOKIE_SECURE=true`인데 HTTP로 접근하면 쿠키가 저장되지 않으니 HTTPS 경로로 접근했는지도 확인. |
