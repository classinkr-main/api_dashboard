# PRD — ClassIn API Dashboard (`api.classin.co.kr/dash`)

> 상태: v0.1 (초안) · 작성일 2026-09-01
> 참고 저장소: [Muuuuoouuun/class_api](https://github.com/Muuuuoouuun/class_api) (classin-toolkit)

## 1. 개요

ClassIn(EEO) 파트너 API 위에 올리는 **학원 운영 웹 대시보드**.
로컬 CLI 툴킷(classin-toolkit)의 패턴을 재사용하되, 서버 배포형 웹 앱으로 재구성한다.

- 배포 주소: `https://api.classin.co.kr/dash` (서브패스 배포, 리버스 프록시 뒤)
- 사용자: 원장/대표(모아보기), 선생님/교사(생성·발송)
- 주 데이터 소스: ClassIn 파트너 API (`api.eeo.cn/partner/api/course.api.php?action=...`, v2 MD5 서명)
- 보조 데이터 소스: ClassIn Data Sub(웹훅) 수신 이벤트 축적

## 2. 목표 / 비목표

### 목표 (v1)
1. **대시보드(모아보기)** — 원장님/대표님 버전
   - 코스·수업 몰아보기: 전체 코스 목록, 코스별 수업(차시) 목록, 기간·수강생 수 등 핵심 지표
   - 학생별 모아보기: 학생 단위 수강 코스, 출결/참여 데이터, 시험·과제 현황
   - 선생님 수업 데이터 모아보기: 수업 일지, 수업 시간(누적/추이), AI 강의 평가 목록 및 추이
2. **생성(원격 실행)** — 선생님/교사 버전
   - 수업/스케줄표 입력(텍스트·CSV) → Claude 파싱 → 코스/수업 일괄 생성
   - 코스에서 숙제 미제출 학생 단체 알림 발송 (ClassIn 메시지 API 가능 여부 검증 포함)
3. **로그인**: ClassIn SID(uid)/secret 기반 인증 → 서버 세션
4. **웹훅(Data Sub) 수신 준비**: 수신 엔드포인트 + SafeKey 검증 + 이벤트 저장

### 후순위 (v2+)
- 오프라인 자료 밀어넣기: 엑셀/시트/노션 → 시험·학생·코스 데이터 연결
- 카카오 알림톡 등 외부 알림 채널 live 연동
- 멀티 테넌트(학원 여러 곳) 지원

### 비목표
- ClassIn 수업 진행 자체(교실 기능)의 대체
- Notion DB 운영(참고 레포의 Layer 2)은 v1에서 제외 — 서버 로컬 저장(SQLite/JSON) 사용

## 3. 사용자 시나리오

| 역할 | 시나리오 |
|---|---|
| 원장/대표 | 로그인 → 대시보드에서 이번 주 수업 현황, 코스별 출석률, 선생님별 수업 시간·AI 평가 추이 확인 |
| 선생님 | 스케줄표 붙여넣기 → AI 파싱 미리보기 → 확인 후 코스/수업 일괄 생성 |
| 선생님 | 코스 선택 → 숙제 미제출 학생 목록 확인 → 단체 알림 메시지 발송(또는 문구 생성) |
| 시스템 | ClassIn Data Sub 웹훅 수신 → SafeKey 검증 → 이벤트 축적 → 대시보드 지표 반영 |

## 4. 기능 요구사항

### 4.1 인증
- FR-A1: uid(SID) + secret 입력 로그인. 서버가 ClassIn API 헬스체크 호출로 자격 검증 후 세션 발급.
- FR-A2: secret은 서버 측 세션에만 보관(암호화 쿠키 또는 서버 세션), 브라우저 노출 금지.
- FR-A3: (옵션) 서버 `.env`에 고정 자격을 두고 접속 비밀번호만으로 로그인하는 "보안 연결" 모드.

### 4.2 대시보드 (모아보기)
- FR-D1: 코스 목록 + 상태/기간/수강생 수, 코스 상세(수업 차시 목록).
- FR-D2: 학생별 뷰 — 수강 코스, 수업별 출결·참여 시간, (웹훅 축적 시) 과제/시험 현황.
- FR-D3: 선생님별 뷰 — 담당 수업 목록, 수업 시간 합계·추이, 수업 일지, AI 강의 평가 목록·추이(API 제공 범위 내).
- FR-D4: 데이터는 ClassIn API 실시간 조회 + 웹훅 축적본 병합. API 미제공 항목은 "웹훅 수집 후 표시" 상태로 명시.

### 4.3 생성 (원격 실행)
- FR-C1: 스케줄 입력(자유 텍스트/CSV 업로드) → Claude 파싱 → 구조화 미리보기 → 승인 시 addCourse/addCourseClass 일괄 호출.
- FR-C2: dry-run 기본. 실제 생성은 명시적 확인 후.
- FR-C3: 숙제 미제출 학생 조회(웹훅 축적 데이터 기반) + 단체 알림: ClassIn 메시지 API 지원 시 직접 발송, 미지원 시 문구 생성 + 수동 발송 안내.

### 4.4 웹훅 (Data Sub)
- FR-W1: `POST /dash/webhook/classin` 수신, SafeKey 검증, 원본 페이로드 저장(JSONL).
- FR-W2: Cmd별 파서(출결, 과제, 수업 종료 등) — 파싱 실패 시에도 원본 보존.
- FR-W3: 축적 이벤트를 대시보드 지표로 집계.

## 5. 비기능 요구사항
- 서브패스(`/dash`) 배포: FastAPI `root_path` + 상대 URL로 프록시 뒤에서 동작.
- 시크릿은 환경변수/config 파일로만 주입, 저장소 커밋 금지.
- ClassIn API 오류(errno) 일관 처리, 요청 로깅(시크릿 마스킹).
- 한국어 UI.

## 6. 아키텍처 (요약)

```
[브라우저] ── /dash ──> [리버스 프록시 (api.classin.co.kr)]
                            │
                     [FastAPI 앱 (uvicorn)]
                      ├─ web/        라우터 + Jinja 템플릿 (대시보드/생성 UI)
                      ├─ classin/    ClassIn API 클라이언트 (v2 서명) + 웹훅 스키마
                      ├─ intelligence/  Claude 스케줄 파서·알림 문구 생성
                      ├─ notify/     알림 디스패처 (classin 메시지 / dry-run)
                      └─ store/      이벤트·캐시 저장 (SQLite/JSONL)
```

세부 결정은 `docs/adr/` 참조.

## 7. API 조사 결과 (2026-09-01 확정)

공식 문서 미러(docs.eeo.cn 영문 전체) + 실구현 3종(공식 PHP SDK 포함) 조사 결과:

| 질문 | 결론 | 본 제품의 대응 |
|---|---|---|
| 원격 메시지 발송 API | **없음.** IM 그룹은 코스 생성 시 자동 생성만 되고 쓰기 API 없음. 유일한 IM API는 닉네임 변경뿐 | 문구 생성(AI/템플릿) + 발송 이력 기록 → 복사 발송. 알림톡(알리고/솔라피)은 Layer 5 플러그인으로 추후 연결 |
| AI 강의 평가 API | **미제공.** 문서·이벤트 전수 검색 0건. ClassIn 관리자 화면에만 존재 | Rating 웹훅(학생↔교사 상호평가)을 누적 표시. AI 분석 API는 config 주입식 시임으로 예약 |
| 조회(read) API | **존재하나 SID별 게이트.** getCourseList/getCourseClass/getCourseStudent/getStudentList/getTeacherList/getClassMemberTime 등이 공식 TOC에 주석 처리로 숨겨져 있음 | `classin/reads.py` 베스트에포트 구현 + 대시보드 "동기화" 버튼. 미활성 시 웹훅 축적 데이터로 동작(errno 102 등은 안내로 강등). **ClassIn 담당자에게 활성화 요청 필요** |
| 웹훅 재전송 정책 | **확정: 10초 간격 무한 재시도 + FIFO 헤드오브라인 블로킹.** 응답은 반드시 `{"error_info":{"errno":1,...}}` | 수신 즉시 원본 저장 → 무조건 ack → `_id` 중복 제거(at-least-once) |
| End 이벤트 스키마 | **확정: UID-키 맵.** 카메라/마이크 시간은 `equipmentsEnd`, 참여 시간은 `inoutEnd[uid].Total`, 손들기 `handsupEnd[uid].Total` | 파서를 확정 스키마로 구현 (참고 레포의 추정 파서 폐기) |
| 숙제 내용 작성 API | 없음 — 활동(껍데기)만 생성 가능, 빈 숙제는 출제 불가(errno 29601) | 생성 화면에 안내: 내용은 ClassIn에서 채운 뒤 출제 |
| SSO 링크 | getLoginLinked 유효하나 **2021-06 이후 비밀번호 없는 자동 로그인 아님** | 링크 생성만 제공, 기대치 안내 |

### ClassIn 담당자에게 요청할 것 (우선순위)
1. 조회 API 활성화: getCourseList, getCourseClass, getCourseInfo, getCourseStudent, getUserCourseList, getClassMemberTime(Details), getStudentList, getTeacherList — 파라미터/응답/페이지네이션 문서 포함
2. AI 강의분석 결과 API 제공 여부
3. 아웃바운드 메시지(IM/알림) API 제공 여부
4. Data Sub 등록: 엔드포인트 `https://api.classin.co.kr/dash/webhook/classin`, 구독 Cmd(Attendance, End, HomeworkSubmit, HomeworkScore, AnswerSheetScore, Rating, ExamScore), 오류 알림 이메일

## 8. 마일스톤
1. M1: 스캐폴드 + 로그인 + ClassIn 클라이언트 + 코스/수업 조회 대시보드
2. M2: 웹훅 수신 + 학생/선생님 뷰 + 축적 데이터 집계
3. M3: AI 스케줄 파싱 생성 + 미제출 알림 발송
4. M4: 배포(도커/리버스 프록시) + 운영 문서
5. v2: 오프라인 자료 임포트(엑셀/시트/노션)
