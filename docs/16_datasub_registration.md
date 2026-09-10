# ClassIn Data Sub (웹훅) 구독 신청

> ClassIn 은 Data Sub 를 셀프서비스로 등록할 수 없다. 지원팀/담당 매니저에게
> 아래 정보를 보내 등록을 요청하고, 등록 시 ClassIn 이 보내는 `Cmd: Test`
> 푸시로 검증이 끝난다. **SID 하나당 수신 URL 은 하나뿐**이며, 나중에 주소를
> 바꾸려면 재등록을 요청해야 한다.

## 1. 신청 정보

| 항목 | 값 |
|---|---|
| 기관 SID | `87372676` |
| 수신 엔드포인트 | `https://webhook.classin.cloud/classin-api/webhook` |
| 오류 알림 이메일 | `junhyuk.mun@classin.com` |
| 구독 이벤트 | 7종 (아래) + 선택 1종 |

### 구독 요청 이벤트

| Cmd | 내용 | 대시보드에서 쓰는 곳 |
|---|---|---|
| `Attendance` | 수업 종료 후 출결 (참여 시간·입퇴장 시각, 결석자 포함) | 출석률, 학생/선생님 명부 생성의 **기반** |
| `End` | 수업 요약 (참여 시간, 카메라/마이크, 손들기, 트로피, 응답) | 참여도 지표 |
| `HomeworkSubmit` | 숙제 제출 이벤트 (제출 시각·지각 여부) | **미제출 판정**(부재로 판단), 알림 |
| `HomeworkScore` | 숙제 채점 결과 (학생별) | 점수 추이 |
| `AnswerSheetScore` | 답안지(OMR) 채점 결과 (문항별) | 시험 결과 |
| `ExamScore` | 시험 채점 결과 (문항별) | 시험 결과 |
| `Record` | 수업 종료 후 녹화 링크 (`VUrl`, `Duration`) | 수업 다시보기 링크 |
| `Rating` *(선택)* | 학생↔교사 상호 평가 (점수·코멘트) | 선생님 강의 평가 화면 |

> `Rating` 은 선생님별 강의 평가 화면이 쓰는 데이터다. ClassIn AI 강의분석은
> 파트너 API 로 제공되지 않는 것이 확인되어(ADR-0004), 그 화면은 `Rating`
> 누적으로 대체하고 있다. 필요 없으면 목록에서 빼면 된다.

## 2. 보낼 본문 (영문)

```text
Subject: [ClassIn Korea] Data Sub (webhook) subscription request — SID 87372676

Hello,

We are integrating with the ClassIn API and would like to apply for a ClassIn
LMS data subscription (real-time webhook push), so that per-student
grading/submission data, attendance and class recording links are received
automatically. We would appreciate your support in enabling this so that the
API testing can proceed.

Please set this up with the information below.

1. Institution SID: 87372676

2. Subscription data types (7):
   - Attendance        — class attendance information after class
   - End               — class summary data after class
   - HomeworkSubmit    — homework submission events (submission rate / time)
   - HomeworkScore     — homework grading results (per student)
   - AnswerSheetScore  — answer sheet grading results (per student, per question)
   - ExamScore         — exam grading results (per student, per question)
   - Record            — class recording links (recording URLs after class ends)

   (Optional, if available: Rating — mutual teacher/student evaluation)

3. Endpoint URL (where the data will be received):
   https://webhook.classin.cloud/classin-api/webhook

4. Error notification email: junhyuk.mun@classin.com

The endpoint has been implemented to ClassIn's specifications:
   - SafeKey verification, MD5(SECRET + TimeStamp)
   - The successful-receipt response is returned for every push:
     {"error_info": {"errno": 1, "error": "程序正常执行"}}
   - Duplicate deliveries are de-duplicated on the _id field, so retries are safe.

If you send a test signal (Cmd: Test) during registration, it will be received
correctly.

Thank you for your help.

Best regards,
MOON (ClassIn Korea)
```

## 3. 보낼 본문 (국문)

```text
제목: [클래스인 코리아] Data Sub(웹훅) 구독 신청 — SID 87372676

안녕하세요.

ClassIn API 연동을 진행 중이며, 학생별 채점/제출 데이터와 출결, 수업 녹화
링크를 자동으로 수신하기 위해 ClassIn LMS 데이터 구독(실시간 웹훅 푸시)을
신청합니다. 아래 정보로 등록 부탁드립니다.

1. 기관 SID: 87372676

2. 구독 데이터 종류 (7종)
   - Attendance        수업 종료 후 출결 정보
   - End               수업 종료 후 수업 요약 데이터
   - HomeworkSubmit    숙제 제출 이벤트 (제출률/제출 시각)
   - HomeworkScore     숙제 채점 결과 (학생별)
   - AnswerSheetScore  답안지 채점 결과 (학생별/문항별)
   - ExamScore         시험 채점 결과 (학생별/문항별)
   - Record            수업 녹화 링크 (수업 종료 후 녹화 URL)

   (가능하다면 추가: Rating — 교사/학생 상호 평가)

3. 수신 엔드포인트 URL
   https://webhook.classin.cloud/classin-api/webhook

4. 오류 알림 이메일: junhyuk.mun@classin.com

수신 엔드포인트는 ClassIn 규격에 맞춰 구현을 마쳤습니다.
   - SafeKey 검증: MD5(SECRET + TimeStamp)
   - 모든 푸시에 대해 정상 수신 응답을 반환합니다:
     {"error_info": {"errno": 1, "error": "程序正常执行"}}
   - _id 기준 중복 제거를 하므로 재전송이 발생해도 안전합니다.

등록 과정에서 테스트 신호(Cmd: Test)를 보내주시면 정상 수신됩니다.

감사합니다.

문준혁 드림 (클래스인 코리아)
```

## 4-0. 현재 호스팅 상태 (2026-09-10 확인)

DNS 조회로 확인한 현재 상태. **HTTP 응답 자체는 확인하지 못했다** (개발 샌드박스에서
해당 호스트로의 아웃바운드가 정책상 차단됨). 아래는 DNS 레코드만 근거로 한 것이다.

| 호스트 | DNS | 가리키는 곳 |
|---|---|---|
| `webhook.classin.cloud` | 있음 | Cloudflare 프록시 (104.21.88.162, 172.67.186.9, 2606:4700:…) |
| `classin.co.kr`, `classin.cloud` | 있음 | Vercel (216.198.79.1, 64.29.17.1) |
| `api.classin.co.kr` | **없음** | — |
| `dash.classin.co.kr` | 없음 | — |

세 가지 결론:

1. **Vercel 에는 이 앱을 올릴 수 없다.** 서버리스라 파일시스템이 휘발성인데, 이 앱은
   SQLite(`dashboard.db`)와 원본 JSONL 을 볼륨에 계속 쌓는 상시 프로세스다.
   `classin.co.kr` 홈페이지가 Vercel 에 있는 것은 그대로 두고 웹훅만 분리한다.
2. **웹훅 호스트는 Cloudflare 뒤에 있다.** 레코드는 살아 있으나 origin 이 어디로 잡혀
   있는지는 Cloudflare 대시보드에서만 보인다. 앱을 올릴 서버 IP 로 A 레코드를 잡아야 한다.
3. **대시보드 주소(`api.classin.co.kr`)는 아직 DNS 자체가 없다.** 새로 파거나,
   이미 Cloudflare 를 쓰는 `dash.classin.cloud` 같은 이름으로 가는 편이 간단하다.

### Cloudflare 프록시(주황 구름) 주의

웹훅 경로를 주황 구름 뒤에 두면 **Bot Fight Mode / WAF 기본 규칙이 ClassIn 의 푸시를
막을 위험이 크다.** ClassIn 서버는 브라우저가 아닌 자동 POST 를 보내고, 한 번 막히면
10초 간격 무한 재시도 + FIFO 블로킹으로 **이벤트 스트림 전체가 정지한다.**

- **권장: 회색 구름(DNS only)** — 서버에서 Let's Encrypt 로 직접 인증서. 변수가 가장 적다.
- 주황 구름을 유지한다면: `/classin-api/webhook` 경로에 **WAF Skip 규칙**을 반드시 추가하고,
  SSL/TLS 모드를 **Full (strict)** 로 둔다.

## 4. 보내기 전 자체 점검

신청 메일을 보내기 **전에** 아래를 모두 통과시켜야 한다. ClassIn 이 등록
직후 `Test` 를 쏘는데 그때 endpoint 가 죽어 있으면 등록이 실패한다.

- [ ] DNS: `webhook.classin.cloud` 가 서버 IP 로 해석되는가
- [ ] TLS: 유효한 인증서로 HTTPS 응답 (자체 서명 불가)
- [ ] 앱 기동: `docker compose up -d` 후 `curl -s localhost:8100/health` → `{"ok":true,...}`
- [ ] nginx: `deploy/nginx.conf.example` 의 `webhook.classin.cloud` 서버 블록 반영
- [ ] `.env` 의 `DASH_WEBHOOK_SAFEKEY` 에 ClassIn 이 발급한 웹훅 시크릿 입력
- [ ] 서버 시계 NTP 동기화 (v2 서명 ±5분)
- [ ] **왕복 검증** — 외부에서 실제 URL 로 Test 페이로드를 쏴 본다:

```bash
TS=$(date +%s)
SK=$(printf '%s%s' "$DASH_WEBHOOK_SAFEKEY" "$TS" | md5sum | cut -d' ' -f1)
curl -s -X POST https://webhook.classin.cloud/classin-api/webhook \
  -H 'Content-Type: application/json' \
  -d "{\"Cmd\":\"Test\",\"SID\":87372676,\"TimeStamp\":$TS,\"SafeKey\":\"$SK\",\"_id\":\"selftest-1\"}"
```

기대 응답 (이것 외에는 등록이 실패한다):

```json
{"error_info": {"errno": 1, "error": "程序正常执行"}}
```

- [ ] 서버에서 원본이 쌓였는지 확인: `ls data/webhook/` → `YYYY-MM-DD.jsonl`

## 5. 등록 후 확인

1. ClassIn 이 보낸 `Test` 가 `data/webhook/<오늘>.jsonl` 에 있는지 확인.
2. 실제 수업을 한 번 진행 → **수업 종료 20분 뒤** `Attendance`/`End` 수신 확인
   (After-Class 이벤트는 20분 지연이 정상이다).
3. 대시보드 → 웹훅 수신 현황에 Cmd 별 누적 건수가 잡히는지 확인.
4. 첫 실 페이로드로 `End` 파서 대조 (원본 JSONL 이 남으므로 replay 로 재처리 가능):
   `python samples/replay.py http://127.0.0.1:8100 data/webhook/<날짜>.jsonl`

## 6. 운영상 주의

- **SID 당 엔드포인트 1개.** 스테이징 주소를 따로 받으려면 별도 요청이 필요하다.
- **재전송은 무한 + 순서 보장.** ACK 를 못 받은 메시지를 10초마다 재시도하고,
  그동안 뒤 이벤트가 전부 밀린다. 그래서 수신부는 파싱 실패든 SafeKey 불일치든
  **항상 성공 응답을 반환**하고, 원본만 따로 남긴다.
- **오류 알림 메일은 시간당 1회.** 최근 1시간 내 오류 메일이 없으면 전송은 정상.
- 이 신청과 **조회 API(get\* 계열) 활성화 요청은 별건**이다 —
  `docs/15_backlog.md` 1-2 참고.
