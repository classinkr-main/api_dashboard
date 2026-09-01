# 남은 작업 정리 (Backlog)

> 작성일 2026-09-01 · v0.1 구현 완료 시점 기준. 우선순위는 각 섹션 내 위→아래.

## 1. ClassIn 측 요청 없이는 못 하는 것 (외부 의존)

| # | 항목 | 상태 | 비고 |
|---|---|---|---|
| 1-1 | **Data Sub 웹훅 등록** | 요청 필요 | 엔드포인트 `https://api.classin.co.kr/dash/webhook/classin`, 구독 Cmd(Attendance, End, HomeworkSubmit, HomeworkScore, AnswerSheetScore, Rating, ExamScore), SID, 오류 알림 이메일. 등록 후 ClassIn이 `Test` Cmd로 검증 푸시 |
| 1-2 | **조회 API(get*) 활성화** | 요청 필요 | getCourseList/getCourseClass/getCourseStudent/getStudentList/getTeacherList/getClassMemberTime(Details). 활성화되면 → 실응답 스키마로 `reads.py` 파서 보정 + 페이지네이션 구현(현재 page=1 고정) |
| 1-3 | 催交(숙제 재촉) API 개방 문의 | 문의 | 관리자 화면엔 있으나 API 미개방(확정). 열리면 `/notify`에서 원클릭 리마인드 |
| 1-4 | AI 강의분석 결과 API 문의 | 문의 | 미제공 확정. 열리면 선생님 화면에 config 주입식 연결(ADR-0004 시임 준비됨) |
| 1-5 | 아웃바운드 메시지 API 문의 | 문의 | 현재 없음 확정 |

## 2. 배포/운영 체크리스트 (코드는 준비됨, 실행 필요)

- [ ] 서버에 `.env` 작성(`openssl rand -hex 32`로 DASH_SECRET_KEY), `docker compose up -d`
- [ ] nginx에 `deploy/nginx.conf.example` 적용 — `/dash/` 프리픽스 스트립 + HTTPS
- [ ] 서버 NTP 동기화 확인 (v2 서명 ±5분 허용)
- [ ] 실 SID/secret으로 로그인 → `verify_credentials` 실호출 확인 (서명 오류 시 시계/키 점검)
- [ ] 대시보드 "ClassIn에서 동기화" 눌러 조회 API 활성화 여부 실측
- [ ] 첫 웹훅 수신 확인 (`Test` Cmd 로그 + `data/webhook/*.jsonl` 생성 확인), SafeKey 검증 동작 확인
- [ ] `data/` 디렉토리 백업 자동화 (cron rsync 등)
- [ ] 운영 문서: `docs/14_operations.md`

## 3. 기능 백로그 (자체 개발 가능)

| # | 항목 | 우선순위 | 비고 |
|---|---|---|---|
| 3-1 | **학부모 연락처 입력/관리 화면** | 높음 | 현재 `students.parent_phone`을 채울 UI가 없음 → 알림 화면이 "연락처 없음"만 표시. 학생 목록에서 인라인 편집 or CSV 업로드 |
| 3-2 | **학생/선생님 수동 등록 화면** | 높음 | 웹훅 수신 전엔 명부가 비어 있음. 조회 API 활성화 전 임시 경로 |
| 3-3 | 토론-리마인드 우회 기능 | **구현됨(기본 꺼짐)** | `DASH_CLASSIN_APP_REMINDER=true`로 활성화 → 알림 미리보기에 "ClassIn 앱에도 게시" 체크박스. 코스당 토론 활동 1건(제목 50자, 미제출 학생명 나열) 생성·게시, 리마인드 단원 재사용. **실계정에서 앱 푸시 여부 1회 검증 후 켜세요** |
| 3-4 | 알림톡 live 연동 (알리고/솔라피) | 중간 | 카톡 템플릿 심사(2~3주) 선행. `notify/dispatcher.py` sender 플러그인으로 연결 |
| 3-5 | ExamScore Cmd 정규화 | 중간 | 현재 AnswerSheetScore만 학생 상세에 표시. ExamScore(단원평가) 파서+화면 추가 |
| 3-6 | 주간 학생 리포트 (Claude 생성) | 중간 | 참고 레포 weekly_report 패턴 이식 — 학부모 발송 문구 포함 |
| 3-7 | 수업 녹화/리플레이 링크 | 낮음 | Record 웹훅(VUrl) 수집 + getWebcastUrl 연결 |
| 3-8 | SSO 앱 실행 링크 | 낮음 | getLoginLinked 래퍼는 구현됨, 화면 미노출. 비밀번호 자동 로그인 아님을 안내 필요 |
| 3-9 | **오프라인 자료 임포트 (엑셀/시트/노션)** | v2 | 원래 요구의 후순위 항목. 참고 레포 data_merge 패턴(원본 불변, 애매하면 확인 큐) 이식 |
| 3-10 | 멀티 테넌트(학원 여러 곳) | v2 | 현재 단일 SID 전제 |

## 4. 기술 부채 / 보완

- **credential 모드 세션이 서버 재시작 시 소멸** (의도된 결정, ADR-0002) — 파일럿은 fixed 모드 권장. 필요 시 암호화 저장 검토
- 로그인 시도 rate limit 없음 — fixed 모드 운영 시 nginx `limit_req` 권장
- 폼 CSRF 토큰 없음 — SameSite=Lax 쿠키로 완화되나, 외부 공개 범위 넓어지면 토큰 추가
- End 이벤트 파서는 문서 확정 스키마 기준 — **실수신 페이로드로 1회 대조** (원본 JSONL 보존되므로 replay로 검증 가능)
- reads.py 응답 파싱은 방어적 추정 — 활성화 후 실응답으로 확정
- 대시보드 집계는 매 요청 풀스캔 — 데이터 수만 건 이상 시 캐시/사전집계 도입
- 웹훅 수신은 동기 처리 — 이벤트량 급증 시 큐(백그라운드 태스크)로 전환

## 5. 판단 필요 (결정 대기)

- 토론-리마인드 우회를 쓸지 여부 (LMS에 활동이 쌓이는 부작용 감수?)
- 알림톡 공급자 선택 (알리고 vs 솔라피) 및 발신번호/템플릿 준비 주체
- 학원 이름/브랜딩 (현재 알림 문구 기본값 "우리 학원")
- 원장/선생님 역할별 화면 접근 제한을 둘지 (현재 역할은 표시용, 기능 제한 없음)
