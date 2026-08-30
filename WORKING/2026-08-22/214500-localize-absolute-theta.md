# localize-absolute-theta

### 목표
- localization1f/2f의 theta가 왜 절대값처럼 안 먹히는지 규명
- pose/theta를 맵 기준 절대값으로 고정 (선택지 C: localize에 node 파라미터)

### 지금
- **실기 확정: UmLocalize poseTh 는 절대값이고 그대로 유지된다.** 어댑터 수정 불필요.
  현장에서 본 "상대" 증상의 출처를 다시 좁혀야 한다 — 사용자에게 관측 경로를 질문한 상태.

### 완료
- **원인**: 어댑터 구간은 이미 절대값이었다. theta는 `float(params["theta"])` 그대로
  `um_localize(poseTh=)`로 나간다. 진짜 "상대"는 x/y — 둘 다 비면 `_vehicle._x/_y`
  (로봇의 '추정' 위치)로 앵커했다. 엘리베이터 직후처럼 추정이 못 믿을 때가 이 recipe가
  필요한 바로 그 순간이라 기준점이 매번 흔들렸다.
- 올바른 heading은 "층"이 아니라 "진행 방향"에 종속. 1_05 -> 2_01 = +89°, 2_01 -> 1_05 = -91°.
- **구현**: `localize`에 `node` 파라미터.
  - 조회 순서 Goal/GoalWithHeading/Dock -> PathPoint. Goal/Dock만 heading 제공.
    PathPoint는 위치만(이 맵 PathPoint theta 44개 전부 0.00) -> theta 없으면 FAILED.
  - 명시 x/y/theta가 node 값을 필드 단위로 덮어씀. target != pose면 FAILED.
    맵에 없는 이름이면 FAILED. `[LOCALIZE]` 로그에 node= 추가.
  - `core/registry.py`: WebUI localize 폼에 `node` 칸.
  - `config/recipes.hcl`: localization1f/2f -> `{ node = "p2", theta = -90 / 90 }`.
    (p2 = PathPoint, Goal 2_01과 좌표 동일 16377 1666. FMS 주행 그래프가 PathPoint 기준)
- 문서: `docs/superpowers/specs/2026-08-20-localization-recipe-...md` §10 추가.

### 다음
- **확정(실기 2026-08-22, robot 192.168.101.62, scripts/probe-jibot-localize-theta.py)**
  - H=90 에서 poseTh=30 전송 -> t=1.3s 에 th=29 반영, **t=59.1s 까지 58초간 완전 고정**.
    ABSOLUTE 잔차 -1.00 / 차순위 RELATIVE_SUB -31.00. x/y 도 절대(dx=-1mm).
  - 1deg 차이는 로봇이 th 를 **정수로 보고**하기 때문(pos=(16300,-1121,29)). 분해능 +-1deg.
  - **재수렴(seed) 가설 폐기.** 직전 --watch-only 에서 90 이 나온 것은 자동 수렴이 아니라
    내가 요청한 수동 재위치의 결과였다(한 프로세스 관측으로 판별됨).
- **그래서 어댑터/클라이언트는 고칠 것이 없다.** theta 는 이미 절대값으로 나가고 로봇도
  절대값으로 받는다.
- **남은 질문(사용자 답변 대기): 현장에서 "상대로 먹는다"를 어느 경로로 관측했는가.**
  후보:
  (a) recipe(localization1f/2f) — node=p2 라 poseX/poseY 가 **맵 좌표**로 나간다.
      로봇이 실제로 p2 에 없었으면 위치까지 옮겨져 전혀 다른 현상이 된다.
  (b) 재위치 후 주행 — `_send_node_goto`(adapter_jibot.py:4956)가 도착 시
      `UmGoto target=pose poseTh=`로 heading 을 강제한다. 이 사이트 PathPoint theta 는
      전부 0.00 이라 도착마다 0 으로 돌아간다. WORKING/...-goto-arrival-theta.md 와 동일 건.
      -> 재위치 theta 가 "안 먹는 것처럼" 보이는 가장 유력한 후보.
  (c) WebUI localize 폼 — 이 경우 이번 측정과 같은 경로라 절대값이어야 한다.

### 검증
- 진단 도구 scripts/probe-jibot-localize-theta.py 신규(read-only 모드 포함). 가짜 로봇 4종
  (absolute / relative_add / relative_sub / 재수렴 / 반영지연)으로 판정 로직 검증.
  초기 버그 2건 자체 발견·수정: gap=0(로봇 무응답) 과 pose 읽기 실패 시 캐시(0,0) 폴백
  -- 후자는 --apply 시 로봇을 맵 원점으로 재앵커할 뻔했다.
- 안정성 판정 휴리스틱 수정: 첫 샘플이 아니라 "반영 시점"부터 마지막까지로 비교.
  (첫 샘플은 명령 반영 전이라, 가만히 있는 로봇도 '움직였다'로 오판했다.)
- TDD: 신규 9개(test_localize_node_pose.py) + test_registry.py 1개를 RED로 먼저 확인 후 구현.
  오류 케이스는 result_description까지 검증해 "theta 없음"에 의한 무의미한 통과를 차단.
- mutation 2회로 테스트 실효성 확인: theta override 제거 -> 3 failed, node 좌표 조회 제거 -> 4 failed.
- 출하 recipe 실측(scratchpad/verify_shipped.py): 실제 config/recipes.hcl 파싱 -> registry.execute ->
  추정 위치를 (999,888)로 틀어둔 채 `um_localize(pose, None, 16377, 1666, -90/+90)` FINISHED 확인.
- localize 관련 스위트 95 passed.
- **최종 전체 스위트(p2 반영본, 트리 미변경 상태): 2166 passed / 13 failed, 6분 6초.**
  13개는 airShower(5) + unknown_config_key(4) + recipes_config(4)이고, HEAD 설정으로 되돌려
  동일 13개가 그대로 실패하는 것을 확인했다 -> 전부 사전 실패, 내 변경과 무관.
  (중간 실행에서 나온 test_hcledit 1건은 그 귀속 확인 중 recipes.hcl을 바꿔치기해서 생긴
  자체 간섭. 단독 재실행 33 passed, 최종 실행에서도 통과.)
