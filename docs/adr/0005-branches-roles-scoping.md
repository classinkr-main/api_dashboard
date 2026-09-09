# ADR-0005: 관(지점)·선생님 단위 뷰 스코핑과 역할 기반 권한

- 상태: 승인
- 날짜: 2026-09-09

## 맥락
- 요구: "관별로, 선생님별로 뷰, 권한 가능한 요소로" — 원장/대표는 전체, 관장(관 책임자)은
  자기 관, 선생님은 자기 수업·학생만 보여야 한다.
- 현재 로그인은 SID/secret 또는 공용 비밀번호 + 역할 "선택"이라 권한이 표시용에 불과하다.
- ClassIn에는 사용자 단위 인증이 없고(SID 하나 = 학원 하나), "관"이라는 개념도 없다.
  관은 (a) 한 SID 안의 코스/선생님 묶음일 수도, (b) 별도 SID(별도 ClassIn 학원 계정)일 수도 있다.

## 결정
1. **로컬 계정 도입**: `users` 테이블(username, password_hash(PBKDF2), role, branch_id,
   teacher_uid, active). ClassIn 자격은 서버(.env 또는 관별 오버라이드)에만 둔다.
   - 역할: `owner`(전체) · `manager`(관 단위) · `teacher`(본인 수업 단위)
   - 최초 owner 부트스트랩: 기존 fixed 모드의 `DASH_ACCESS_PASSWORD`로 로그인하면 owner 세션
     (계정이 하나도 없을 때의 진입로). credential 모드(SID/secret 직접 로그인)도 owner로 유지.
2. **관(branch) 모델**: `branches`(id, name, sid?, secret?) + 코스→관 매핑(`courses.branch_id`)
   + 선생님→관 매핑(`teachers.branch_id`). 관에 SID/secret이 있으면 그 관의 동기화·생성 호출은
   그 자격을 쓴다 (b형 지원). 없으면 서버 기본 자격 (a형).
3. **스코프 객체**: 세션에서 `Scope(branch_ids | None, teacher_uids | None, course_ids | None)`을
   파생하고 **모든 조회 함수(metrics/store)와 생성·알림 라우터가 스코프를 필수 인자로 받는다**.
   `None` = 제한 없음(owner). 라우터에서 필터를 잊는 실수를 막기 위해 필터는 store 계층에서 건다.
   - manager: branch_ids = {자기 관} → 그 관의 코스·그 관 소속 선생님의 레코드
   - teacher: teacher_uids = {자기 uid} → 자기가 가르친 lesson_records와 자기 담당 코스
   - 미배정 코스(branch_id NULL)는 owner에게만 보인다.
4. **관리 화면(/admin, owner 전용)**: 관 CRUD, 코스→관 배정, 선생님→관 배정, 계정 관리
   (생성/비활성/비밀번호 재설정). manager는 자기 관의 선생님 계정만 생성 가능.
5. **상단 관 전환**: owner는 전체/관별 필터를 상단에서 전환(세션에 저장), manager/teacher는 고정.
6. **생성/알림 권한**: teacher는 자기 uid로만 코스/수업 생성(선생님 자동 배정 결과가 본인이 아니면
   차단), 알림은 자기 코스의 미제출자만. manager는 자기 관 선생님 범위.

## 결과
- ClassIn 측 기능 없이도 관/선생님 단위 권한이 동작한다.
- 관을 별도 SID로 운영하는 학원도 같은 화면으로 수용 (관에 자격 오버라이드).
- 기존 fixed/credential 로그인은 owner 진입로로 남아 하위 호환.
