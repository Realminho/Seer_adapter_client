# Localization Recipe & Vendor Portability — 설계/조사 문서

- 날짜: 2026-08-20
- 상태: **구현·검증 완료.** (§8 참조)
- 목표: `localization1f` / `localization2f` recipe 추가. 둘 다 UmLocalize(재위치)를
  실행하되 **위치는 로봇의 현재값 그대로, heading(theta)만 0 / -90**. 나아가 이 구조가
  나중에 SEER AMR로 바뀌어도 코드 변경이 없도록 만드는 것.
- 확정 사항: pose = **실행시점 현재값 동적**(하드코딩 없음), theta = **1f=0 / 2f=-90**.

---

## 1. recipe의 step은 무엇을 실행하는가

recipe(`adaptor/config/recipes.hcl`)의 각 `step "이름"`은 **등록된 action type(라벨)**이다.
recipe 엔진이 그 이름으로 registry에서 handler를 찾아 실행한다 — 임의 코드/vehicle 메서드를
직접 부르는 경로는 없다.

- 실행 경로: `adaptor/extensions/recipes/__init__.py:110`
  `ctx.adapter._action_registry.execute(action, ...)` — action_type 이름으로만 dispatch.
- 부팅 검증: `adaptor/extensions/recipes/__init__.py:221` — step 이름이 등록 목록에 없으면
  `references unavailable extension '...'`로 **부팅 실패**.
- 미등록 type을 실행하면: `adaptor/core/action_registry.py:308`
  `unregistered extension action: <type>` FAILED.

### step(action type) 등록 소스 3곳

| step 예시 | 정의 파일 | 위치 |
|---|---|---|
| `pioInit` `pioWriteOut` `pioDisconnect` | `adaptor/extensions/pio/__init__.py` | `PIO_ACTION_TYPES`(21) → `action_specs()`(1123) |
| `elevatorEnter/Inside/Passed`, `airShowerEnter/Inside/Passed` | `adaptor/extensions/facility/__init__.py` | 매핑(22~29) → `action_specs()`(144) |
| `manualMove` `jibotMotionRule` `gotoNearestNode` `manualStop` `switchMap` | `adaptor/core/action_bridge.py` | `action_specs()`(37~67) |
| `clamp*` | `adaptor/extensions/clamp/__init__.py` | `action_specs()`(728) |
| `ezio*` | `adaptor/extensions/ezio/__init__.py` | `action_specs()`(355) |
| config `action "..."` 블록 | `extensions.hcl` → `config.actions` | plugin action |

등록 취합: `adaptor/core/action_modules.py:130 first_party_action_specs()`
= extension `action_specs()` 전부 + `action_bridge` + `config.actions`.

이렇게 registry에 올라간 것을 **composable action**이라 부른다(recipe step / order action으로
이름으로 조합 가능). 반대로 **non-composable** = 어댑터의 instant-action switch
(`adaptor/adapter_jibot.py`)에서만 하드코딩 처리되는 built-in: `localize`, `manualDrive`,
`enableMotor`, `disableMotor`, `clearErrors`, `testSound`, `setSoundVolume` 등. WebUI 버튼으로만
실행되고 recipe step으로는 못 쓴다.

---

## 2. 걸림돌: `localize`(UmLocalize)는 지금 recipe에서 못 쓴다

`localize`는 non-composable이다. VDA5050 instant-action switch에만 있다:
- `adaptor/adapter_jibot.py:5897` `elif action.action_type == "localize": self._handle_localize_instant_action(action)`
- WebUI 버튼 정의: `adaptor/core/registry.py:244` `_JIBOT_INSTANT_ACTIONS`의 `localize`(target/goal/x/y/theta/mapId, motion=True).

registry에도, `action_bridge`에도, `config.actions`에도 없다 → `step "localize"`를 쓰면 부팅 실패.

**해결: `manualStop`/`switchMap`이 이미 그랬듯 `action_bridge.py`에 `localize`를 브리지**한다.

---

## 3. 브리지 설계 (jibot 우선, contract 기반으로 vendor-portable)

핵심: 브리지 핸들러가 vendor 전용 `um_localize`가 아니라 **공통 contract 메서드
`_vehicle.localize(...)`**를 부르게 한다. 그러면 vendor 분기가 자동이다.

### 3.1 instant 핸들러 재사용 (primitive 추출 불필요 — 최종 채택안)

당초 "핵심 로직을 primitive로 추출" 계획이었으나, `manualMove`가 쓰는 더 단순한 선례를
그대로 따랐다: `core/motion_primitives.py`의 `_run_status_action(adapter, action, handler_name)`
이 completion-future를 등록하고 기존 instant 핸들러를 호출한 뒤 그 결과를 await한다. 그래서
`_handle_localize_instant_action`을 **분해하지 않고 그대로 재사용**한다.

- 추가: `core/motion_primitives.py::run_localize(adapter, action)` =
  `_run_status_action(adapter, action, "_handle_localize_instant_action")`.
- 기존 핸들러는 이미 contract 우선(`getattr(self._vehicle, "localize", None)` → 없으면
  `um_localize`)이고 재위치 side effect(`_set_last_node`, `_current_map_id`,
  `request_state_publish("localized")`)도 그 안에 있다. status 갱신도 핸들러 안에 남고,
  recipe 경로는 completion-future로 그 terminal status를 결과로 받는다.

### 3.1a 현재 위치 기본값 (핸들러 확장)

recipe가 좌표를 안 담고 heading만 바꾸도록, `_handle_localize_instant_action`의 `target=pose`
분기를 확장했다:

- `theta`는 여전히 필수(숫자).
- `x`,`y`가 **둘 다 비면** 로봇의 live 현재값(`self._vehicle._x/_y`)으로 채운다. 현재값이
  없으면(`None`) 명확히 FAILED. `x`/`y`가 주어지면 기존대로 그 값을 쓴다(회귀 보존).
- `mapId` 생략 시 기존 동작대로 현재 맵을 유지(`_current_map_id` 미변경).

### 3.2 registry 등록

`core/action_bridge.py`에 추가:
```python
ActionSpec("localize", handler=_localize, motion=True, label="localize")
```
- `motion=True`는 기존 `localize` 분류와 일치. **단 motion=True는 WebUI confirm-key 게이트를
  건다.** 이 recipe를 어떻게 트리거하는지(자동/수동)에 따라 게이트가 걸려도 되는지 의식적으로
  결정해야 한다. (문제되면 recipe의 `motion`은 별개 필드이므로 recipe 쪽에서 조정.)

### 3.3 recipe 정의 (`recipes.hcl` — 실제 반영본)

좌표를 안 담는다(§3.1a). `motion=false` — 재위치는 카를 움직이지 않으므로 주행 recipe가
아니고 WebUI 확인 키 게이트도 안 걸린다. cleanup 없음(재위치는 설비 자원을 쥐지 않음).

```hcl
recipe "localization1f" {                      # elevator로 1층 도착 후
  label = "재위치 — elevator로 1층 도착(현재 위치, heading 0)"
  motion = false
  step "localize" { parameters = { target = "pose", theta = 0 } }
}
recipe "localization2f" {                      # elevator로 상층 도착 후
  label = "재위치 — elevator로 상층 도착(현재 위치, heading -90)"
  motion = false
  step "localize" { parameters = { target = "pose", theta = -90 } }
}
```
FMS는 이름만으로 부르며 pose 파라미터가 필요 없다.

---

## 4. SEER로 바꿔도 코드 변경이 없는가 — 조사 결과

**결론: 브리지+recipe 엔진 코드는 이식 가능. 단 전제 1개(엔진 재사용)와 값 재측정은 남는다.**

### 근거

- 공통 contract가 이미 있다: `amr-client-contract/src/amr_client_contract/contract.py:188`
  `AmrClient.localize(target, goal, poseX, poseY, poseTh)`.
- **양쪽 client가 동일 시그니처로 구현**:
  - jibot: `jibot-client/src/jibot_client/client.py:942 um_localize(...)`.
  - seer: `seer-client/src/seer_client/client.py:373 localize(...)` → 내부적으로 SEER
    `reloc`(CONTROL 2002)으로 매핑(`commands.py:65`, `protocol.py:90`). seer-client는
    `amr_client_contract.AmrClient`를 구현한다고 명시(`__init__.py:5`).
- 어댑터 핸들러는 이미 vendor-neutral: `adapter_jibot.py:7416`이 `getattr(_vehicle,"localize")`
  우선 호출. §3.1이 이 패턴을 그대로 재사용.

### 전제/주의 (문서가 과장하지 않도록 명시)

1. **[핵심 전제] `adapter_seer`가 core recipe/action_registry 시스템을 재사용해야 한다.**
   지금 recipe/registry/action_bridge 전체가 `adapter_jibot.py`에만 배선돼 있다.
   `adapter_hexplorer.py`는 이 시스템을 **아예 안 쓴다(참조 0회)**. seer 어댑터를 hexplorer처럼
   core를 안 쓰게 만들면 recipe/브리지를 다시 배선해야 한다. → **이게 진짜 리스크이고,
   "client 명시"로는 해결되지 않는다.**
2. **pose 숫자(x/y/theta)는 vendor·맵 종속이라 재측정 필요.** jibot `um_localize(poseTh)` vs
   seer `reloc(angle=poseTh)`. SEER는 보통 radian. 같은 `theta=-90`을 다르게 해석할 수 있다.
   좌표계도 맵마다 다르다. 코드는 그대로여도 recipe 안 숫자는 새로 잡는다.

---

## 5. "recipe에 어느 client인지 명시" 아이디어 — 평가와 권장

사용자 제안: recipe에 client(vendor)를 명시하면 나중에 바꿀 일이 없다.

**권장: vendor 분리는 이미 있는 "robot별 recipes 파일"로 한다.** 공유 파일 안에 client 태그를
박는 것보다 낫다.

- recipe는 이미 **robot별 경로 로드**다: `core/registry.py:483 resolve_robot_path(robot,"recipes")`,
  `config/config.py:1087 load_recipes(recipes_path)`. robot 블록에 `recipes = "..."`를 주면 그
  파일을, 없으면 기본 `recipes.hcl`을 쓴다.
- 그래서: **지금** jibot은 `recipes.hcl`(jibot pose값). **나중** seer는 `recipes-seer.hcl`(seer
  pose값)을 만들고 seer robot이 가리키게 한다. **엔진·브리지 코드는 바이트 단위로 동일**하고
  pose 값만 파일별로 갈린다.
- 공유 파일 1개 안에 `client="jibot"` 필드를 넣으면, 그 필드를 robot의 vendor와 대조해 선택하는
  **새 로직**이 필요하고, robot 블록엔 아직 vendor 필드도 없다(`robots.hcl`은 ip/port/ezi만).
  → 오히려 미래 변경이 **더** 늘어난다. **YAGNI.**

**따라서 client 표기는 문서용(파일 헤더 주석 + recipe 이름)으로만.** 강제 필드는 `adapter_seer`가
이 시스템을 실제로 재사용하게 될 때 재검토.

---

## 6. 결정 사항 (사용자 확인 완료)

1. `x`, `y`, `mapId` = **실행시점 로봇 현재값 동적**. 저장소엔 고정값이 없고 런타임 스냅샷
   (`runtime/jibot-position.json`)뿐이라 하드코딩하면 stale. → 좌표를 안 담고 §3.1a로 채운다.
2. **theta = 1f=0 / 2f=-90** (도°, 지금 WebUI localize가 쓰는 값과 같은 단위).
3. **recipe motion=false** (재위치는 주행 아님). localize step의 ActionSpec은 motion=True로 두되
   recipe 카드는 확인 키 게이트가 안 걸린다. FMS 자동 호출이라 문제 없음.

주의(유지): "현재 위치"는 로봇의 현재 '추정' 위치다. localization이 크게 틀어진 상태면 틀어진
x/y에 다시 앵커된다 — 위치가 믿을 만할 때 heading만 보정하는 용도.

---

## 7. 구현 결과 (반영본)

- [x] `adapter_jibot.py`: `_handle_localize_instant_action` `target=pose` 분기 확장 — theta 필수,
      x/y 둘 다 비면 현재값(`_vehicle._x/_y`) 사용, 현재값 없으면 FAILED, x/y 있으면 기존대로.
- [x] `core/motion_primitives.py`: `run_localize` 추가(`_run_status_action`로 instant 핸들러 재사용).
- [x] `core/action_bridge.py`: `ActionSpec("localize", motion=True)` 등록(cancel 없음).
- [x] `recipes.hcl`: `localization1f`(theta 0)/`localization2f`(theta -90) 추가, 헤더에 client=jibot·
      현재값 동작 주석. 기존 잘못된 스텁(pioInit 복붙) 교체.
- [x] 테스트: `test_localize_current_pose.py`(현재값/명시값/theta필수), `test_action_bridge.py`
      (composable+bridge), `test_action_modules.py`(bridge 목록 갱신).

## 8. 검증 (실측)

- 부팅 검증: `get_config(recipes_path='config/recipes.hcl')` + `first_party_action_specs(config)`가
  예외 없이 통과. localization1f/2f가 recipe action으로 등록, steps=`[('localize', {target:pose,
  theta:0/-90})]`, `localize` composable(motion=True) 확인.
- **합성 경로(핵심)**: `test_localize_current_pose.py`의 통합 테스트가 실제 경로를 관찰한다 —
  `_action_registry.execute("localize")` → `run_localize` → `_run_status_action`(completion future)
  → 실제 `_handle_localize_instant_action`(RUNNING → `_run_on_adapter_loop` → terminal status) →
  future resolve. FINISHED + 현재값(1.5,2.5) 사용 확인. (analogy가 아니라 실측.)
- 엣지: `x=0,y=0`은 origin으로 localize(현재값 fallback에 안 먹힘) — `_blank()` 헬퍼가 숫자 0을
  '미지정'과 구분. theta 누락 시 FAILED, 명시 x/y 회귀 보존.
- 단위/회귀: `test_localize_current_pose`(5), `test_action_bridge` `test_action_modules` `test_recipes`
  `test_action_registry` `test_registry` `test_jibot_client_localize` → 91 passed.
  `test_adapter_jibot_v3_order`(localize FakeVehicle 홈, 핸들러 변경 검증) → **675 passed**.
  `test_local_instant_action`(motion/gating) → 71 passed. `test_web_server` `test_web_render` → 271 passed.
- 무관한 사전 실패(내 변경 전부터, 다른 세션/환경): `test_fleet_registry`(/private/var 심링크),
  shipped-recipe 3개(사이트 config). stash로 확인함.

## 9. 동시 세션 주의

작업 중 다른 세션들이 같은 트리를 수정 중이었다(`recipes.hcl`의 `airShower3l-4l`,
`adapter_jibot.py`의 `_refresh_pio_from_ezio`, `pio/__init__.py`, `web/render.py`,
`test_adapter_io_snapshot.py`). 내 변경과 영역이 겹치지 않으며, recipes.hcl은 편집 전 fresh read로
그들의 변경을 보존했다. 전체 스위트를 돌릴 때 저 파일들에서 나는 실패는 내 변경과 무관.

---

## 10. 후속 (2026-08-22): `node` 파라미터 — "현재 추정값" 대신 맵 절대 pose

§3.1a의 "x/y를 비우면 현재 위치" 결정이 실사용에서 걸렸다. 재위치 결과가 매번 달라져
"theta가 절대값이 아니다"로 보고됐다. 코드를 되짚은 결과:

- **theta는 처음부터 절대값이었다.** `_handle_localize_instant_action`이 `float(params["theta"])`를
  그대로 `um_localize(poseTh=)`에 싣는다. 상대 변환/오프셋은 어디에도 없다.
- **상대였던 것은 x/y다.** 둘 다 비면 `_vehicle._x/_y`(로봇의 '추정' 위치)로 앵커한다. 엘리베이터를
  타고 난 직후처럼 추정이 못 믿을 때가 이 recipe가 필요한 바로 그 순간이므로, 기준점이 매번 흔들렸다.
- **올바른 heading은 층이 아니라 진행 방향에 종속이다.** elevator 노드는 1_05(앞) → 2_01(카 안)
  → 3_01(상층)이고 좌표상 상행 +90°/하행 -90°다(1_05→2_01 = 89°, 2_01→1_05 = -91°).
  §6.2의 "1f=0 / 2f=-90"은 이 방향성을 반영하지 못한 값이었다.

### 추가한 것

`localize`에 `node` 파라미터. 맵의 Goal/GoalWithHeading/Dock 이름을 주면 핸들러가
`_map_nodes()`에서 그 노드의 **절대 (x, y, theta)** 를 읽어 앵커로 쓴다.

- 조회 순서는 **Goal/GoalWithHeading/Dock 먼저, PathPoint 그다음**이다. heading을 가진 것은
  Goal/Dock 뿐이다. PathPoint는 **위치만** 준다 — 이 사이트 맵의 PathPoint theta는 44개 전부
  0.00이라 heading을 조용히 0으로 만들어 버리기 때문이다(`_send_node_goto`
  adapter_jibot.py:4964와 같은 규칙). 그래서 PathPoint를 주면서 `theta`를 안 적으면 FAILED다.
  FMS 주행 그래프가 PathPoint 기준(`settings.nearest_node_mode`)이라 `p2` 같은 이름이 익숙하면
  그대로 쓸 수 있다. 카 안 자리는 Goal `2_01`과 PathPoint `p2`가 좌표까지 같다(16377 1666).
- 명시 `x`/`y`/`theta`는 node 값을 **필드 단위로 덮어쓴다**. 카 안 노드 2_01의 맵 theta는 0.00인데
  실제 heading은 방향에 달렸으므로, recipe는 위치를 맵에서 받고 heading만 적는다.
- `target != "pose"` + `node` = FAILED. `goal=`과 `node=`는 서로 다른 조회 테이블이라 하나를
  조용히 버리면 조작자가 요청하지 않은 곳으로 재위치된다.
- 없는 노드 = FAILED(설명에 노드 이름 포함). WebUI localize 폼에도 `node` 칸이 생겼다.

```hcl
recipe "localization1f" { step "localize" { parameters = { node = "p2", theta = -90 } } }
recipe "localization2f" { step "localize" { parameters = { node = "p2", theta = 90 } } }
```

`p2`(PathPoint)와 `2_01`(Goal)은 좌표가 같아(16377 1666) 결과가 동일하다. FMS 주행 그래프가
PathPoint 기준이라 `p2`로 적었다. 부수 효과로 `theta` 누락이 조용히 0으로 넘어가지 않고
FAILED로 드러난다 — PathPoint는 heading을 못 주기 때문이다.

### 남은 것

- **node는 "재위치 실행 시점에 로봇이 카 안에 있다"를 전제**한다(사용자 확인 완료). 카에서 내린
  뒤에 부른다면 1_05(1층)/3_01(상층)이어야 한다. 틀린 노드는 추정 위치를 1.5m 옮겨 놓는다.
- **로봇측 미확인**: urobot의 `UmLocalize(target=pose)`가 `poseTh`를 확정 적용하는지, scan-match
  초기 추정치(seed)로만 쓰는지. 후자라면 최종 heading은 레이저 정합 결과로 수렴하고 넣은 theta는
  힌트일 뿐이다. 실기에서 서로 다른 실제 heading 두 곳에서 같은 theta로 localize 후 결과를 비교하면
  1회로 판별된다.
- `_set_last_node(goal or "", 0)`은 손대지 않았다. node를 주면 신뢰할 수 있는 노드가 생기므로
  lastNodeId를 세울 수 있지만, x/y를 덮어쓴 경우와 얽혀 별도 판단이 필요하다.

### 검증

- TDD: 신규 8개(`tests/test_localize_node_pose.py`) + `test_registry.py` 1개를 먼저 RED로 확인 후 구현.
  오류 케이스는 `result_description`을 검증해 "theta 없음"으로 인한 무의미한 통과를 막았다.
- mutation 2회로 테스트 실효성 확인: theta override 제거 → 3 failed, node 좌표 조회 제거 → 4 failed.
- 부팅 검증 통과, `steps=[('localize', {node:p2, theta:-90/90})]`, localize composable=True.
- 출하 recipe 실측: 실제 `config/recipes.hcl`을 파싱해 그 파라미터를 `_action_registry.execute`로
  태우고, 로봇 추정 위치를 (999, 888)로 틀어둔 채 `um_localize(pose, None, 16377, 1666, ∓90)`이
  나가는 것을 확인했다 — 추정값을 무시하고 맵 좌표로 앵커한다.

### 부록: `p2`의 `postures`

`p2`는 이 맵에서 `postures`가 비어있지 않은 유일한 PathPoint다(값 `'180'`, 나머지 43개는 빈 값).
하필 엘리베이터 카 안 지점인데 진행 방향(±90°)과 맞지 않고, jibot-client는 `postures`를 파싱조차
하지 않는다(`_update_map_nodes`는 `pose`만 읽는다). 의미가 확인되면 heading 근거로 쓸 수 있는지
재검토할 것.
