# ClassIn 학원 대시보드

`https://api.classin.co.kr/dash` 에서 서비스되는 ClassIn(EEO) 파트너 API 기반 학원 운영 웹 대시보드.

- **대시보드(모아보기)** — 원장/대표용: 코스·수업, 학생별, 선생님별 데이터를 한 화면에서 확인
- **생성(원격 실행)** — 선생님용: 스케줄표를 붙여넣으면 Claude가 파싱해 코스/수업을 일괄 생성, 미제출 학생 알림 문구 생성

전체 요구사항은 [`docs/PRD.md`](docs/PRD.md), 설계 결정은 [`docs/adr/`](docs/adr/)를 참고한다.

## 아키텍처 요약

```
[브라우저] ── /dash ──> [리버스 프록시 (nginx)] ── / ──> [FastAPI 앱 (uvicorn, :8100)]
                                                          ├─ web/           라우터 + Jinja 템플릿
                                                          ├─ classin/       ClassIn API 클라이언트 (v1/v2 서명) + 웹훅 스키마
                                                          ├─ intelligence/  Claude 스케줄 파서 · 알림 문구 생성
                                                          ├─ notify/        알림 디스패처
                                                          └─ store/         SQLite(정규화 이벤트) + JSONL(웹훅 원본)
```

- FastAPI + Jinja2 서버 렌더링 단일 앱, uvicorn `root_path=/dash`로 서브패스 인식 (ADR-0001).
- ClassIn 파트너 API를 v1/v2 서명 클라이언트로 실시간 조회, Data Sub 웹훅으로 이벤트(출결·과제·시험·AI 평가 등)를 수신해 SQLite/JSONL에 축적 (ADR-0003).
- Claude(Anthropic API)로 스케줄 텍스트 파싱 및 알림 문구를 생성한다.

## 빠른 시작

### 로컬 개발

```bash
pip install -e .
cp .env.example .env   # 값 채우기 — docs/14_operations.md 14.1절 참고
classin-dash
```

기본적으로 `http://127.0.0.1:8100/dash` 에서 뜬다.

### 테스트

```bash
pip install -e ".[dev]"
pytest
```

### Docker 배포

```bash
docker compose up -d --build
```

자세한 운영 절차(환경변수, 시크릿 생성, 웹훅 등록, 백업, NTP, 트러블슈팅)는 [`docs/14_operations.md`](docs/14_operations.md) 참고.
배포 관련 파일: [`Dockerfile`](Dockerfile), [`docker-compose.yml`](docker-compose.yml), [`deploy/nginx.conf.example`](deploy/nginx.conf.example).

## 화면 설명

| 경로 | 설명 |
|---|---|
| `/login` | ClassIn SID/secret 로그인 (또는 `fixed` 모드에서는 접속 비밀번호만) |
| `/dashboard` | 대시보드 홈 — 코스·수업 모아보기 |
| `/students` | 학생별 뷰 — 수강 코스, 출결/참여, 과제·시험 현황 |
| `/teachers` | 선생님별 뷰 — 담당 수업, 수업 시간 추이, AI 강의 평가 |
| `/create` | 생성 — 스케줄 파싱(Claude) → 코스/수업 일괄 생성 |
| `/notify` | 알림 — 숙제 미제출 학생 조회, 알림 문구 생성/발송 |
| `/webhook/classin` | ClassIn Data Sub 웹훅 수신 엔드포인트 (인증: SafeKey, 사람이 쓰는 화면 아님) |

경로는 모두 `DASH_ROOT_PATH`(기본 `/dash`) 뒤에 붙는다 — 예: `https://api.classin.co.kr/dash/dashboard`.

## 문서

- [`docs/PRD.md`](docs/PRD.md) — 제품 요구사항
- [`docs/adr/`](docs/adr/) — 아키텍처 결정 기록 (스택/서브패스, 인증, 데이터 전략)
- [`docs/14_operations.md`](docs/14_operations.md) — 운영 가이드 (배포, 웹훅 등록, 백업, 트러블슈팅)

## 제약사항

- **조회 API는 SID(파트너 계정)별로 ClassIn 측 활성화가 필요**하다. 아직 활성화되지 않은 SID에서는 실시간 조회 항목이 비어 있을 수 있으며, 이 경우 대시보드는 **Data Sub 웹훅으로 수신·축적된 데이터만으로 동작**한다(해당 항목은 "수집 대기"로 표시).
- ClassIn 파트너 API에는 **메시지(단체 알림) 직접 발송 API가 없다** — `/notify`는 Claude로 알림 문구를 생성하고, 실제 발송은 외부 채널(카카오톡, 이메일 등)로 수동 전달하는 흐름이다.
- **AI 강의 평가(AI 수업 리포트) 상세를 제공하는 조회 API가 없다** — `Rating` 웹훅 이벤트를 수신·축적한 값만 `/teachers` 뷰에 표시된다.
