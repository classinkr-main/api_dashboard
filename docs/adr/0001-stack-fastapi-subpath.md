# ADR-0001: FastAPI + Jinja 서버렌더 스택, `/dash` 서브패스 배포

- 상태: 승인
- 날짜: 2026-09-01

## 맥락
- 참고 저장소(classin-toolkit)가 Python이며 ClassIn v2 서명, 웹훅 스키마, Claude 파서 등 재사용 가능한 패턴이 모두 Python으로 존재한다.
- 배포 목표는 `api.classin.co.kr/dash` 단일 서브패스, 소규모 운영(학원 1곳 → 확장).
- SPA(React 등)를 별도 빌드하면 배포 파이프라인이 둘로 늘고, 서브패스 라우팅/자산 경로 문제가 커진다.

## 결정
- **FastAPI + Jinja2 서버 렌더링 + 경량 JS(htmx 수준의 자체 fetch)** 단일 앱.
- uvicorn `--root-path /dash` 로 서브패스 인식, 템플릿은 `request.url_for` 기반 상대 경로만 사용.
- 저장은 SQLite(웹훅 이벤트·캐시) + JSONL(웹훅 원본 보존).

## 결과
- 단일 컨테이너/프로세스 배포, 프록시 설정만으로 `/dash` 서빙.
- 프런트 상호작용이 복잡해지면 v2에서 부분적으로 SPA 전환 가능(API는 이미 JSON 라우터로 분리).
