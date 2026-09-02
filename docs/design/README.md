# 디자인 시안 — 스케줄 입력/파싱 화면

캔버스: https://claude.ai/code/artifact/d4497643-0ff1-4c0c-a4b3-80b4300dc426

| 아트보드 | 방향 | 요지 |
|---|---|---|
| `Main.dc.html` | A · 한 칸 입력 (추천) | 붙여넣기 한 칸 → 코스·선생님·일정 모듈 카드, 확인 필요 항목만 강조 |
| `DirectionB.dc.html` | B · 대화형 정리 | AI가 정리해서 되묻는 흐름, 진행 단계 레일 |
| `DirectionC.dc.html` | C · 캘린더 보드 | 인식된 수업을 월간 달력에 배치, 좌측 입력+모듈 목록 |

기존 앱 토큰(`web/templates/base.html`) 그대로 사용: 배경 #f6f7f9, 패널 #fff, 테두리 #e3e6ea,
강조 #2f6fed, 라운드 10px, 14px 시스템 폰트.
