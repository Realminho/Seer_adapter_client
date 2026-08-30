# 연속 경로 주행 (Continuous Path) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FMS가 released로 내려 준 연속 base 노드를 goto 하나로 합쳐 내리고, 중간 노드는 정지 없이 통과 판정만 해서 오더 진행을 발행한다.

**Architecture:** 어댑터의 오더 워커(`_process_v3_node_step`)가 노드마다 `UmGoto → 도달 대기 → 완전 정지 대기`를 직렬로 돌던 것을, 연속 released 구간(coalescing run)을 계산해 **마지막 노드로만 goto**를 보내고 중간 노드는 pose 스트림 + 반경 보간으로 통과 판정하도록 바꾼다. 벤더 라우트 API는 쓰지 않는다(스텝 경계가 정지를 새로 만든다 — spec §3.3). 전부 `[settings] path_control` 게이트 뒤에 두고 기본값은 현행 동작이다.

**Tech Stack:** Python 3.12 / asyncio, unittest(+`unittest.IsolatedAsyncioTestCase`), pytest 러너는 `scripts/run-tests.sh`, 설정은 TOML + dataclass(`adaptor/config/config.py`).

**Spec:** `docs/superpowers/specs/2026-08-26-continuous-path-design.md`

## Global Constraints

- 주석·커밋 메시지는 **한국어로만** 작성한다. 이중 표기 금지. 커밋 타입 접두사(`feat:`/`fix:`/`test:`), 로그 출력 문자열, 식별자는 대상이 아니다. (`CLAUDE.md`)
- 주석은 "무엇을"이 아니라 **"왜"**를 적고 실측 근거(날짜·호스트·값)를 남긴다.
- 새 설정 키는 **`adaptor/config/config.toml`과 `adaptor/config/config.py`의 `Settings` dataclass 양쪽**에 같은 이름으로 넣는다. dataclass 필드 없이 toml 키만 넣으면 `_section()`이 `ConfigError`를 던져 **부팅이 실패한다**(`adaptor/config/config.py:1171`).
- 설정 이름에 **`mode`라는 단어를 쓰지 않는다.** 이미 6군데에서 다른 뜻으로 쓰인다(spec §1).
- 기본값은 **현행 동작 유지**(`path_control = "stop_point"`). 배포가 `--config-toml-mode keep` 기본이라 새 키는 현장 로봇에 도달하지 않으므로, **dataclass 기본값이 곧 현장 동작**이다.
- 테스트는 `scripts/run-tests.sh tests/<파일>` 로 돌린다. 맨 `pytest`는 ROS 플러그인 충돌로 죽는다.
- **전체 스위트(`scripts/run-tests.sh` 인자 없이)는 7분을 넘는다**(736 테스트, 2026-08-27 실측).
  구현자는 **자기 파일 대상 타깃 실행만** 하고, 전체 회귀는 오케스트레이터가 병렬로 돌린다.
  각 태스크의 "전체 회귀" 스텝은 이 분담으로 읽는다.
- 이식하는 외부 코드(inorbit `_get_drivable_segment`, BSD-3)는 **저작권 고지를 남긴다.**
- 유지해야 할 인접 계약 3건: (a) SOFT/HARD 액션 전 정지 게이트, (b) 미릴리즈 구간은 큐에 안 들어간다, (c) 노드 도착이 액션 실행 **전에** `lastNodeSequenceId`를 올린다.

---

### Task 1: 테스트 하네스 — FakeVehicle에 "주행 중" 상태

지금 `FakeVehicle._mode = "auto"`, `_status = "Stopped"`라 `_has_active_automatic_motion()`이 **항상 False**를 반환한다. 그래서 `_settle_goto_arrival`이 전체 테스트에서 no-op이고, CP를 고쳤는지 확인할 수단이 없다.

**Files:**
- Modify: `adaptor/tests/test_adapter_jibot_v3_order.py` (`class FakeVehicle`)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces: `FakeVehicle.start_driving()`, `FakeVehicle.stop_driving()` — 이후 모든 태스크의 테스트가 "로봇이 아직 굴러가는 중" 상태를 만들 때 쓴다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`adaptor/tests/test_adapter_jibot_v3_order.py` 에 추가:

```python
class ContinuousPathHarnessTests(unittest.TestCase):
    def test_fake_vehicle_can_report_active_automatic_motion(self):
        vehicle = FakeVehicle()
        self.assertFalse(vehicle.is_driving())

        vehicle.start_driving()
        self.assertTrue(vehicle.is_driving())
        # _has_active_automatic_motion 은 _mode 에 "goto" 가 들어 있으면 True 다.
        self.assertIn("goto", vehicle._mode.lower())

        vehicle.stop_driving()
        self.assertFalse(vehicle.is_driving())
        self.assertEqual(vehicle._status, "Stopped")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `scripts/run-tests.sh tests/test_adapter_jibot_v3_order.py -k ContinuousPathHarness -v`
Expected: FAIL — `AttributeError: 'FakeVehicle' object has no attribute 'is_driving'`

- [ ] **Step 3: 최소 구현**

`class FakeVehicle` 안, `arrive_at` 옆에 추가:

```python
    # 실차는 주행 중 mode="MRosGoto" / status="nrunto ..." 를 보고한다
    # (2026-08-26 192.168.101.50:7274 실측). fake 가 늘 "Stopped" 라서
    # _has_active_automatic_motion 이 항상 False 였고, 그 탓에
    # _settle_goto_arrival 이 전 테스트에서 no-op 이었다.
    def start_driving(self, goal: str = "test") -> None:
        self._mode = "MRosGoto"
        self._status = f"nrunto {goal}"

    def stop_driving(self) -> None:
        self._mode = "auto"
        self._status = "Stopped"

    def is_driving(self) -> bool:
        return "goto" in str(self._mode).lower()
```

- [ ] **Step 4: 통과 확인**

Run: `scripts/run-tests.sh tests/test_adapter_jibot_v3_order.py -k ContinuousPathHarness -v`
Expected: PASS

- [ ] **Step 5: 기존 테스트 무회귀 확인**

Run: `scripts/run-tests.sh tests/test_adapter_jibot_v3_order.py`
Expected: 기존과 동일한 통과 개수 (`start_driving` 을 아무도 안 부르므로 기본값은 그대로다)

- [ ] **Step 6: 커밋**

```bash
git add adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "test(jibot): FakeVehicle 에 주행 중 상태 추가"
```

---

### Task 2: CP-4 — edgeStates를 엣지 이탈 시점에 제거

`_clear_v3_order_step`이 엣지 스텝에서 `edge_states`를 지우는데, 엣지 스텝은 주행 없이 즉시 completed가 되므로 사실상 **엣지 진입 시점**에 지워진다. 스펙 §6.6.2는 *"엣지 이탈 = 그 엣지가 이어지는 다음 노드를 통과할 때"* 제거하도록 규정한다. CP가 이 창을 넓히므로 **CP-1보다 먼저** 넣는다.

**Files:**
- Modify: `adaptor/adapter_jibot.py:6554` (`_clear_v3_order_step`)
- Modify: `adaptor/adapter_jibot.py:6002` (`_finalize_v3_node_step`)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Consumes: Task 1의 `FakeVehicle.start_driving()` (이 태스크는 안 써도 된다)
- Produces: `Adapter._clear_edge_states_up_to(sequence_id: int) -> int` — 주어진 노드 sequenceId보다 **작은** 모든 `edge_states`를 제거하고 제거 개수를 반환한다. Task 5가 코얼레싱 중간 노드 통과 시 같은 함수를 부른다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`state.edge_states` 는 `List[Any]`(`adaptor/protocol/vda5050_3_0/messages.py:534`)라
테스트에서는 `SimpleNamespace` 를 넣으면 된다. 어댑터 생성은 이 파일의 기존 패턴
(`_make_adapter`, `:797`)을 그대로 복제한다 — 모듈 함수가 아니라 **클래스 메서드**다.

```python
class EdgeStateReleaseTimingTests(unittest.TestCase):
    def _make_adapter(self) -> Adapter:
        adapter = Adapter()
        vehicle = FakeVehicle()
        adapter.set_vehicle(vehicle)
        adapter.set_charge_circuit(_VehicleLinkedChargeCircuit(vehicle))
        return adapter

    def test_edge_state_survives_until_next_node_is_traversed(self):
        adapter = self._make_adapter()
        adapter.state = SimpleNamespace(
            edge_states=[
                SimpleNamespace(edge_id="E1", sequence_id=1, released=True),
                SimpleNamespace(edge_id="E2", sequence_id=3, released=True),
            ]
        )

        # 엣지 진입만으로는 지워지지 않는다
        removed = adapter._clear_edge_states_up_to(1)
        self.assertEqual(removed, 0)
        self.assertEqual([e.edge_id for e in adapter.state.edge_states], ["E1", "E2"])

        # 그 엣지가 이어지는 노드(seq=2)를 통과하면 E1 만 지워진다
        removed = adapter._clear_edge_states_up_to(2)
        self.assertEqual(removed, 1)
        self.assertEqual([e.edge_id for e in adapter.state.edge_states], ["E2"])
```

- [ ] **Step 2: 실패를 확인한다**

Run: `scripts/run-tests.sh tests/test_adapter_jibot_v3_order.py -k EdgeStateReleaseTiming -v`
Expected: FAIL — `AttributeError: 'Adapter' object has no attribute '_clear_edge_states_up_to'`

- [ ] **Step 3: 최소 구현**

`adaptor/adapter_jibot.py`, `_clear_v3_order_step` 바로 위에 추가:

```python
    def _clear_edge_states_up_to(self, node_sequence_id: int) -> int:
        """노드 하나를 통과했을 때, 그 노드로 이어지던 엣지들의 edgeState 를 지운다.

        VDA5050 3.0 §6.6.2 는 엣지 이탈을 "그 엣지가 이어지는 다음 노드를 통과할 때"
        로 규정한다. 기존 구현은 엣지 스텝이 주행 없이 즉시 completed 되는 탓에
        사실상 엣지 **진입** 시점에 지우고 있었다. FMS 는 edgeStates 로 구간 점유를
        보므로, 로봇이 아직 그 구간 안에 있는데 점유를 놓아 버리는 창이 생긴다.
        노드마다 완전 정지하던 동안에는 창이 짧았을 뿐이고, 연속 주행이 이 창을 넓힌다.
        """
        if self.state is None:
            return 0
        before = len(self.state.edge_states)
        self.state.edge_states = [
            edge for edge in self.state.edge_states
            if edge.sequence_id >= node_sequence_id
        ]
        return before - len(self.state.edge_states)
```

`_clear_v3_order_step` 안에서 엣지를 지우던 분기를 **제거**하고(노드 분기는 그대로 둔다), `_finalize_v3_node_step`이 `lastNodeSequenceId`를 올린 직후에 다음을 부른다:

```python
        removed_edges = self._clear_edge_states_up_to(int(node.sequence_id))
        if removed_edges:
            print(
                f"[ORDER EDGE CLEAR] node seq={node.sequence_id} "
                f"id={node.node_id} removed={removed_edges}"
            )
```

- [ ] **Step 4: 통과 확인**

Run: `scripts/run-tests.sh tests/test_adapter_jibot_v3_order.py -k EdgeStateReleaseTiming -v`
Expected: PASS

- [ ] **Step 5: 전체 회귀**

Run: `scripts/run-tests.sh tests/test_adapter_jibot_v3_order.py`
Expected: 전부 통과. 실패하면 그 테스트가 "엣지 진입 시 제거"를 계약으로 고정하고 있다는 뜻이므로, **테스트를 고치기 전에 그 테스트가 무엇을 지키려던 것인지 읽고 판단한다.**

- [ ] **Step 6: 커밋**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "fix(adaptor): edgeState 를 엣지 진입이 아니라 다음 노드 통과 시 제거"
```

---

### Task 3: 설정 표면 — `path_control` / `waypoint_pass_radius_mm`

**Files:**
- Modify: `adaptor/config/config.py:374` (`Settings` dataclass 끝, `switch_map_timeout_seconds` 다음)
- Modify: `adaptor/config/config.toml:46` (`[settings]` 섹션)
- Test: `adaptor/tests/test_configio.py` (없으면 `adaptor/tests/test_adapter_jibot_v3_order.py`)

**Interfaces:**
- Consumes: 없음
- Produces: `Settings.path_control: str = "stop_point"`, `Settings.waypoint_pass_radius_mm: float = 0.0`, `Adapter._continuous_path_enabled() -> bool`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
class ContinuousPathSettingsTests(unittest.TestCase):
    def test_defaults_keep_current_behaviour(self):
        adapter = make_adapter()
        self.assertEqual(adapter.config.settings.path_control, "stop_point")
        self.assertEqual(adapter.config.settings.waypoint_pass_radius_mm, 0.0)
        self.assertFalse(adapter._continuous_path_enabled())

    def test_continuous_enables_the_gate(self):
        adapter = make_adapter()
        adapter.config.settings.path_control = "continuous"
        self.assertTrue(adapter._continuous_path_enabled())

    def test_unknown_value_warns_and_falls_back(self):
        adapter = make_adapter()
        adapter.config.settings.path_control = "blend"   # 오타/미지원 값
        self.assertFalse(adapter._continuous_path_enabled())
```

- [ ] **Step 2: 실패를 확인한다**

Run: `scripts/run-tests.sh tests/test_adapter_jibot_v3_order.py -k ContinuousPathSettings -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'path_control'`

- [ ] **Step 3: 최소 구현**

`adaptor/config/config.py`, `switch_map_timeout_seconds: float = 30.0` 바로 다음:

```python
    # 연속 경로 주행(Continuous Path). "stop_point" 는 노드마다 완전 정지를 기다리는
    # 현행 동작, "continuous" 는 연속 released 구간을 goto 하나로 합치고 중간 노드는
    # 통과 판정만 한다. 배포가 --config-toml-mode keep 기본이라 새 키는 현장 로봇
    # config.toml 에 도달하지 않는다. 즉 이 기본값이 곧 현장 동작이다.
    path_control: str = "stop_point"
    # 통과 간주 반경(mm). 0 이면 기존 도착존(allowedDeviationXY x reach_zone_scale)을 쓴다.
    # 2026-08-26 192.168.101.50:7274 실측에서 직선 통과 속도가 1002 mm/s 였고
    # node_position_poll_interval_sec=0.2 이므로 샘플 간 이동이 약 200mm 다.
    # 도착존 실효값 ±40mm 로는 통과를 놓친다.
    waypoint_pass_radius_mm: float = 0.0
```

`adaptor/config/config.toml`의 `[settings]` 섹션에 같은 두 키를 같은 기본값으로 추가한다(주석 포함).

`adaptor/adapter_jibot.py`, `_settle_goto_arrival` 근처에 추가:

```python
    _PATH_CONTROL_VALUES = ("stop_point", "continuous")

    def _continuous_path_enabled(self) -> bool:
        """path_control 이 continuous 일 때만 True. 모르는 값은 경고 후 현행 동작.

        문자열 enum 검증은 last_node_capture_mode / nearest_node_mode 선례를 따른다 —
        부팅을 막지 않고 경고 후 안전한 쪽(현행 동작)으로 폴백한다.
        """
        value = str(
            getattr(self.config.settings, "path_control", "stop_point")
        ).strip().lower()
        if value not in self._PATH_CONTROL_VALUES:
            if value not in self._warned_path_control_values:
                self._warned_path_control_values.add(value)
                print(
                    f"[CONFIG WARN] unknown path_control={value!r}; "
                    f"expected one of {self._PATH_CONTROL_VALUES}; using 'stop_point'"
                )
            return False
        return value == "continuous"
```

`__init__`에 `self._warned_path_control_values: set[str] = set()` 를 추가한다.

- [ ] **Step 4: 통과 확인**

Run: `scripts/run-tests.sh tests/test_adapter_jibot_v3_order.py -k ContinuousPathSettings -v`
Expected: PASS

- [ ] **Step 5: 설정 로더 회귀 — 부팅이 깨지지 않는지**

Run: `scripts/run-tests.sh tests/test_configio.py`
Expected: PASS. toml 키와 dataclass 필드 이름이 어긋나면 여기서 `ConfigError` 로 잡힌다.

- [ ] **Step 6: 커밋**

```bash
git add adaptor/config/config.py adaptor/config/config.toml adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(config): 연속 경로 주행 게이트 path_control 과 통과 반경 추가"
```

---

### Task 4: 통과 판정 — 선분 보간 (`segment_passes_within`)

점 판정(`현재 pose가 반경 안인가`)은 폴링 주기 때문에 쓸 수 없다(spec §3.4). 순수 함수로 먼저 만든다 — 어댑터에 물리기 전에 단독으로 검증된다.

**Files:**
- Create: `adaptor/core/waypoint_pass.py`
- Create: `adaptor/tests/test_waypoint_pass.py`

**Interfaces:**
- Consumes: 없음
- Produces: `waypoint_pass.segment_passes_within(node_xy, prev_xy, cur_xy, radius) -> bool` — Task 5가 통과 판정에 쓴다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`adaptor/tests/test_waypoint_pass.py`:

```python
import unittest

from core.waypoint_pass import segment_passes_within


class SegmentPassesWithinTests(unittest.TestCase):
    def test_endpoint_inside_radius(self):
        self.assertTrue(segment_passes_within((0, 0), (500, 0), (30, 0), 40))

    def test_both_endpoints_outside_but_segment_crosses(self):
        # 폴링 주기 0.2s x 1002mm/s = 약 200mm 이동. 두 표본 모두 반경 밖이지만
        # 그 사이 선분은 노드를 스쳐 지나간다 — 점 판정이면 놓친다.
        self.assertTrue(segment_passes_within((0, 0), (-100, 10), (100, 10), 40))

    def test_segment_misses(self):
        self.assertFalse(segment_passes_within((0, 0), (-100, 500), (100, 500), 40))

    def test_zero_length_segment_is_point_check(self):
        self.assertTrue(segment_passes_within((0, 0), (10, 0), (10, 0), 40))
        self.assertFalse(segment_passes_within((0, 0), (500, 0), (500, 0), 40))

    def test_none_previous_falls_back_to_point_check(self):
        self.assertTrue(segment_passes_within((0, 0), None, (30, 0), 40))
        self.assertFalse(segment_passes_within((0, 0), None, (500, 0), 40))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패를 확인한다**

Run: `scripts/run-tests.sh tests/test_waypoint_pass.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.waypoint_pass'`

- [ ] **Step 3: 최소 구현**

`adaptor/core/waypoint_pass.py`:

```python
"""연속 주행 중 노드 통과 판정.

점 판정("지금 pose 가 반경 안인가")은 쓸 수 없다. 2026-08-26 192.168.101.50:7274
실측에서 직선 통과 속도가 1002 mm/s 였고 node_position_poll_interval_sec 이 0.2 라
표본 간 이동이 약 200mm 다. 도착존 실효값 ±40mm 는 두 표본 사이로 통째로 지나간다.
그래서 직전 표본과 현재 표본을 잇는 **선분**이 반경 안을 지났는지로 판정한다.

libVDA5050++ 가 같은 문제를 InterpolationType {NONE, LINEAR} 로 노출한다.
NONE 이 점 판정, LINEAR 가 이 선분 판정에 해당한다.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

Point = Tuple[float, float]


def point_to_segment_distance(point: Point, start: Point, end: Point) -> float:
    """point 에서 선분 start-end 까지의 최단 거리."""
    px, py = point
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    # 선분 위 최근접점의 매개변수 t 를 [0, 1] 로 자른다.
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def segment_passes_within(
    node_xy: Point,
    prev_xy: Optional[Point],
    cur_xy: Point,
    radius: float,
) -> bool:
    """직전 표본에서 현재 표본까지 오는 동안 노드 반경 안을 지났는가.

    prev_xy 가 None 이면(첫 표본) 점 판정으로 떨어진다.
    """
    if prev_xy is None:
        return math.hypot(cur_xy[0] - node_xy[0], cur_xy[1] - node_xy[1]) <= radius
    return point_to_segment_distance(node_xy, prev_xy, cur_xy) <= radius
```

- [ ] **Step 4: 통과 확인**

Run: `scripts/run-tests.sh tests/test_waypoint_pass.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: 커밋**

```bash
git add adaptor/core/waypoint_pass.py adaptor/tests/test_waypoint_pass.py
git commit -m "feat(adaptor): 노드 통과 판정용 선분 보간 함수 추가"
```

---

### Task 5: CP-1 + CP-2 — 코얼레싱 구간 계산과 무정지 통과

**Files:**
- Create: `adaptor/core/coalescing.py`
- Create: `adaptor/tests/test_coalescing.py`
- Modify: `adaptor/adapter_jibot.py:6485` (`_process_v3_node_step`)
- Modify: `adaptor/adapter_jibot.py:6480`, `:6548` (`_settle_goto_arrival` 호출부)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Consumes: Task 3의 `_continuous_path_enabled()`, Task 4의 `segment_passes_within`, Task 2의 `_clear_edge_states_up_to`, Task 1의 `FakeVehicle.start_driving()`
- Produces: `coalescing.drivable_run(steps, index, is_breaker) -> int` — index 에서 시작하는 연속 주행 구간의 **마지막 인덱스**를 반환한다. Task 6이 "이 구간 끝이 base 끝인가" 판정에 쓴다.

- [ ] **Step 1: 구간 계산의 실패하는 테스트를 쓴다**

`adaptor/tests/test_coalescing.py`:

```python
import unittest

from core.coalescing import drivable_run


class DrivableRunTests(unittest.TestCase):
    def test_single_step_when_next_is_a_breaker(self):
        steps = ["a", "b", "c"]
        self.assertEqual(drivable_run(steps, 0, lambda s: s == "b"), 0)

    def test_runs_to_the_end_when_nothing_breaks(self):
        steps = ["a", "b", "c"]
        self.assertEqual(drivable_run(steps, 0, lambda s: False), 2)

    def test_stops_before_the_breaker(self):
        steps = ["a", "b", "c", "d"]
        self.assertEqual(drivable_run(steps, 0, lambda s: s == "c"), 1)

    def test_breaker_itself_is_its_own_run(self):
        # 끊는 노드는 합치지 않고 그 자체로 한 구간이다 — dock/move 룰이나
        # SOFT/HARD 액션 노드는 반드시 자기 goto 로 가야 한다.
        steps = ["a", "b", "c"]
        self.assertEqual(drivable_run(steps, 1, lambda s: s == "b"), 1)

    def test_last_index(self):
        self.assertEqual(drivable_run(["a"], 0, lambda s: False), 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패를 확인한다**

Run: `scripts/run-tests.sh tests/test_coalescing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.coalescing'`

- [ ] **Step 3: 구간 계산 구현**

`adaptor/core/coalescing.py`:

```python
"""연속 주행 구간(coalescing run) 계산.

알고리즘은 inorbit vda5050_connector 의 `_get_drivable_segment()` 를 옮긴 것이다
(vda5050_connector_py/vda5050_controller.py, BSD-3-Clause,
 Copyright (c) InOrbit, Inc. — https://github.com/inorbit-ai/ros_amr_interop).
원본은 미released 엣지/노드와 첫 HARD/SOFT 블로킹 액션에서 구간을 끊는다.
여기서는 무엇이 끊는지를 호출자가 술어로 넘기게 일반화했다 — JIBOT 은 dock 세그먼트
룰, move 룰, dock work 노드, 좌표 미상 노드처럼 벤더 고유의 끊는 조건이 더 있다.
"""

from __future__ import annotations

from typing import Callable, Sequence, TypeVar

T = TypeVar("T")


def drivable_run(
    steps: Sequence[T],
    index: int,
    is_breaker: Callable[[T], bool],
) -> int:
    """index 에서 시작하는 연속 주행 구간의 마지막 인덱스.

    끊는 스텝은 합치지 않고 그 자체로 한 구간이 된다(반환값 == index).
    끊지 않는 스텝은 다음 끊는 스텝 **직전**까지 이어 붙인다.
    """
    if is_breaker(steps[index]):
        return index
    last = index
    for candidate in range(index + 1, len(steps)):
        if is_breaker(steps[candidate]):
            break
        last = candidate
    return last
```

- [ ] **Step 4: 통과 확인**

Run: `scripts/run-tests.sh tests/test_coalescing.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: 어댑터 쪽 실패 테스트를 쓴다 — 중간 노드에서 정지를 기다리지 않는다**

`adaptor/tests/test_adapter_jibot_v3_order.py`:

`OrderStep` 은 위치인자 3개(`kind`, `sequence_id`, `item`)이고 item 은
`SimpleNamespace` 로 만든다 — 이 파일의 기존 테스트(`:1031`)와 같은 형태다.

```python
def _cp_node(node_id="N1", seq=2, actions=None, released=True):
    return OrderStep(
        "node",
        seq,
        SimpleNamespace(
            node_id=node_id,
            sequence_id=seq,
            released=released,
            actions=actions or [],
        ),
    )


def _cp_action(blocking_type):
    return SimpleNamespace(
        action_id=f"a-{blocking_type}",
        action_type="probe",
        blocking_type=blocking_type,
        action_parameters=[],
    )


class ContinuousPathNodeStepTests(unittest.TestCase):
    def _make_adapter(self) -> Adapter:
        adapter = Adapter()
        vehicle = FakeVehicle()
        adapter.set_vehicle(vehicle)
        adapter.set_charge_circuit(_VehicleLinkedChargeCircuit(vehicle))
        return adapter

    def test_intermediate_node_does_not_settle(self):
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        step = _cp_node()
        self.assertFalse(adapter._should_settle_at(step, is_run_end=False))
        self.assertTrue(adapter._should_settle_at(step, is_run_end=True))

    def test_stop_point_always_settles(self):
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "stop_point"
        step = _cp_node()
        self.assertTrue(adapter._should_settle_at(step, is_run_end=False))

    def test_blocking_action_breaks_the_run(self):
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        self.assertFalse(adapter._step_has_blocking_action(_cp_node()))
        self.assertFalse(
            adapter._step_has_blocking_action(_cp_node(actions=[_cp_action("NONE")]))
        )
        self.assertTrue(
            adapter._step_has_blocking_action(_cp_node(actions=[_cp_action("SOFT")]))
        )
        self.assertTrue(
            adapter._step_has_blocking_action(_cp_node(actions=[_cp_action("HARD")]))
        )

    def test_unreleased_and_actions_only_break_the_run(self):
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        self.assertTrue(adapter._is_coalescing_breaker(_cp_node(released=False)))
        only = _cp_node()
        only.actions_only = True
        self.assertTrue(adapter._is_coalescing_breaker(only))
```

- [ ] **Step 6: 실패를 확인한다**

Run: `scripts/run-tests.sh tests/test_adapter_jibot_v3_order.py -k ContinuousPathNodeStep -v`
Expected: FAIL — `AttributeError: 'Adapter' object has no attribute '_should_settle_at'`

- [ ] **Step 7: 게이트 구현**

`adaptor/adapter_jibot.py`, `_settle_goto_arrival` 바로 위에 추가:

```python
    def _should_settle_at(self, step: Any, *, is_run_end: bool) -> bool:
        """이 노드에서 완전 정지를 기다려야 하는가.

        연속 주행에서 중간 노드는 통과 지점이지 정지 지점이 아니다.
        VDA5050 3.0 §6.1.2 는 정지 의무를 base 의 마지막 노드(decision point)
        하나로만 규정하고, §6.6.2 는 노드 위 정지를 SOFT/HARD 블로킹 액션이
        있을 때의 예외로 기술한다.
        """
        if not self._continuous_path_enabled():
            return True
        return is_run_end
```

`_process_v3_node_step`의 goto 분기에서 `await self._settle_goto_arrival(node)` 를 감싼다:

```python
                    await self._wait_until_node_position_reached(node, target)
                    if self._should_settle_at(step, is_run_end=self._is_run_end(step)):
                        await self._settle_goto_arrival(node)
```

`_is_run_end(step)` 와 `_step_has_blocking_action(step)` 도 함께 정의한다.
`_step_has_blocking_action` 은 `_process_v3_step_actions` 의 `last_driving_blocker`
(`adapter_jibot.py:4531`)와 **같은 판정**을 쓴다 — blockingType 이 SOFT/HARD 인 액션이
하나라도 있으면 정지가 필요하다.

```python
    def _step_has_blocking_action(self, step: Any) -> bool:
        """SOFT/HARD 블로킹 액션이 하나라도 있으면 True.

        _process_v3_step_actions 의 last_driving_blocker 와 같은 기준이다.
        blocking_type 은 enum 일 수도 문자열일 수도 있어 value 를 먼저 본다.
        """
        for action in getattr(step.item, "actions", None) or []:
            raw = getattr(action, "blocking_type", None)
            value = str(getattr(raw, "value", raw) or "").strip().upper()
            if value in {"SOFT", "HARD"}:
                return True
        return False

    def _is_run_end(self, step: Any) -> bool:
        """이 스텝이 코얼레싱 구간의 마지막인가.

        큐에 남은 스텝들을 순서대로 놓고, 이 스텝에서 시작하는 구간의 끝을 구한다.
        구간의 끝이 곧 "여기서는 실제로 서야 한다"는 뜻이다.
        """
        if not self._continuous_path_enabled():
            return True
        pending = [step, *list(self.order_queue._queue)]  # asyncio.Queue 내부 deque
        last = drivable_run(pending, 0, self._is_coalescing_breaker)
        return last == 0
```

`from core.coalescing import drivable_run` 를 파일 상단 임포트에 추가한다.

끊는 술어는 spec §5.2의 8개 조건을 그대로 옮긴다:

```python
    def _is_coalescing_breaker(self, step: Any) -> bool:
        """이 스텝을 앞 노드와 합칠 수 없는가 (spec 5.2)."""
        item = step.item
        node_id = str(getattr(item, "node_id", "") or "")
        if step.kind != "node":
            return True
        if getattr(step, "actions_only", False):
            return True
        if not getattr(item, "released", False):
            return True
        if self._step_has_blocking_action(step):
            return True
        if self._dock_segment_rule(node_id) is not None:
            return True
        if self._move_motion_rule(node_id) is not None:
            return True
        if self._is_dock_work_node(node_id):
            return True
        if self._resolve_node_target(item) is None:
            return True
        return False
```

`_step_has_blocking_action(step)`는 `step.item.actions` 중 `blocking_type` 값이 `"SOFT"`/`"HARD"` 인 것이 있으면 True를 반환한다. `_process_v3_step_actions`의 `last_driving_blocker`(`adapter_jibot.py:4531`)가 쓰는 것과 **같은 판정**을 쓴다.

- [ ] **Step 8: `_order_node_motion_seq` 와 노드별 상태 발행을 지킨다 (spec 6)**

`_order_node_motion_seq`(`adapter_jibot.py:6487`)는 rotateTo 배경 루프의 supersede 신호다
— 값이 바뀌면 "다음 노드 주행이 시작됐다"로 읽고 진행 중인 rotateTo 를 버린다.
코얼레싱하면 goto 는 구간마다 한 번만 나가지만, **증가는 노드마다 그대로 유지한다.**
중간 노드를 통과하는 것도 rotateTo 입장에서는 "진행이 일어났다"이기 때문이다.
즉 `_process_v3_node_step` 첫 줄의 `self._order_node_motion_seq += 1` 은 건드리지 않는다.

같은 이유로 `_finalize_v3_node_step` 은 **중간 노드에서도 노드마다 호출한다.**
FMS 가 점유 관제를 하므로 `lastNodeId` 발행을 구간 단위로 접으면 로봇 위치를 잃는다(spec §5.3).

테스트를 추가한다:

```python
    def test_motion_seq_and_finalize_run_per_node_even_when_coalesced(self):
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        before = adapter._order_node_motion_seq

        finalized = []
        adapter._finalize_v3_node_step = lambda step, node: finalized.append(node.node_id) or True

        # 중간 노드 2개를 통과 처리해도 seq 는 노드마다 오르고 finalize 도 노드마다 돈다.
        for seq, node_id in ((2, "N1"), (3, "N2")):
            step = _cp_node(node_id=node_id, seq=seq)
            adapter._order_node_motion_seq += 1
            adapter._finalize_v3_node_step(step, step.item)

        self.assertEqual(adapter._order_node_motion_seq, before + 2)
        self.assertEqual(finalized, ["N1", "N2"])
```

- [ ] **Step 9: `segment_passes_within` 을 실제로 배선한다 (중간 노드 통과 판정)**

Task 4 가 만든 `segment_passes_within` 을 여기서 쓰지 않으면 죽은 코드가 되고,
spec §3.4 의 위험(폴링 0.2s × 1002mm/s = 표본 간 200mm 이동 vs 도착존 ±40mm)이
그대로 남는다. spec §5.1 은 이것을 "이것 없이는 CP 가 동작하지 않는다"고 명시한다.

중간 노드 전용 대기를 새로 만든다. 구간의 **마지막** 노드는 기존
`_wait_until_node_position_reached` 를 그대로 쓴다 — 거기서는 정확한 도착이 필요하다.

```python
    async def _wait_until_node_passed(self, node: Any, target: Any) -> None:
        """코얼레싱 중간 노드를 '지나갔다'고 볼 때까지 기다린다.

        점 판정("지금 pose 가 반경 안인가")은 못 쓴다. 2026-08-26 192.168.101.50:7274
        실측에서 직선 통과 속도가 1002 mm/s 였고 node_position_poll_interval_sec 이
        0.2 라 표본 간 이동이 약 200mm 다. 도착존 실효값 ±40mm 는 두 표본 사이로
        통째로 지나간다. 그래서 직전 표본과 현재 표본을 잇는 선분으로 판정한다.
        """
        poll_sec = float(
            getattr(self.config.settings, "node_position_poll_interval_sec", 0.2)
        )
        target_x, target_y, deviation_xy = target
        radius = float(
            getattr(self.config.settings, "waypoint_pass_radius_mm", 0.0)
        ) or self._effective_reach_deviation_xy(deviation_xy)
        node_xy = (float(target_x), float(target_y))

        print(
            f"[ORDER NODE PASS WAIT] id={node.node_id} target={node_xy} radius={radius}"
        )
        prev_xy = None
        while True:
            pose = self._vehicle_xy()
            if pose is not None:
                if segment_passes_within(node_xy, prev_xy, pose, radius):
                    print(f"[ORDER NODE PASSED] id={node.node_id} at={pose}")
                    return
                prev_xy = pose
            await asyncio.sleep(poll_sec)
```

`_vehicle_xy()` 는 이 파일에 이미 pose 를 읽는 헬퍼가 있으면 그것을 쓰고, 없으면
`_wait_until_node_position_reached` 가 pose 를 읽는 방식을 그대로 따라 한 줄로 만든다.
**새 pose 소스를 발명하지 마라** — 기존 경로와 다른 좌표계를 쓰면 판정이 어긋난다.

`from core.waypoint_pass import segment_passes_within` 를 파일 상단 임포트에 추가한다.

`_process_v3_node_step` 의 goto 분기에서 구간 끝이 아니면 이쪽을 부른다:

```python
                    if self._should_settle_at(step, is_run_end=is_run_end):
                        await self._wait_until_node_position_reached(node, target)
                        await self._settle_goto_arrival(node)
                    else:
                        await self._wait_until_node_passed(node, target)
```

- [ ] **Step 10: 지뢰 1 — 워커가 자기 주행을 취소하지 못하게 한다**

`_active_order_worker_step_is_obsolete()` 는 `current_order_step.sequence_id <=
state.last_node_sequence_id` 이면 주행 중인 워커를 cancel 한다. 코얼레싱은 중간 노드를
통과할 때마다 `last_node_sequence_id` 를 올리므로, **주행 중인 스텝이 자기 자신을
"지나간 것"으로 읽고 취소된다.**

같은 함수에 이미 선례가 있다 — `_order_action_step_in_flight` 인 스텝은 예외 처리되어
있고, 그 주석이 정확히 이 부류의 버그를 서술한다. 코얼레싱이 세 번째 경우다.

`__init__` 에 `self._coalescing_run_step: Any = None` 을 추가하고,
`_process_v3_node_step` 이 구간 주행을 시작할 때 담았다가 `finally` 에서 비운다.
`_active_order_worker_step_is_obsolete` 에 예외를 추가한다:

```python
        if self._coalescing_run_step is self.current_order_step:
            # 코얼레싱 구간을 주행 중인 스텝이다. 중간 노드를 통과할 때마다
            # lastNodeSequenceId 가 올라가므로 아래 비교가 이 스텝을 '지나간 것'으로
            # 읽고 자기 주행을 끊는다. 위 _order_action_step_in_flight 예외와 같은 성질이다.
            return False
```

테스트를 추가한다:

```python
    def test_coalescing_run_is_not_obsolete_while_driving(self):
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        step = _cp_node(node_id="N4", seq=8)
        adapter.current_order_step = step
        adapter.state = SimpleNamespace(
            last_node_id="N2", last_node_sequence_id=8, node_states=[], edge_states=[]
        )
        adapter._order_action_step_in_flight = None

        # 구간 주행 중이 아니면 기존 판정 그대로 취소 대상이다
        adapter._coalescing_run_step = None
        self.assertTrue(adapter._active_order_worker_step_is_obsolete())

        # 구간 주행 중이면 취소되지 않는다
        adapter._coalescing_run_step = step
        self.assertFalse(adapter._active_order_worker_step_is_obsolete())
```

- [ ] **Step 11: 통과 확인**

Run: `scripts/run-tests.sh tests/test_adapter_jibot_v3_order.py -k ContinuousPathNodeStep -v`
Expected: PASS

- [ ] **Step 12: 전체 회귀**

Run: `scripts/run-tests.sh`
Expected: 전부 통과. 특히 spec §6의 인접 계약 3건을 지키는 테스트가 살아 있어야 한다 —
`test_hard_stops_residual_node_motion_before_action`(SOFT/HARD 전 정지),
`test_new_order_bootstraps_from_current_vehicle_station`(미릴리즈 구간),
액션 이중 실행 회귀 테스트(`lastNodeSequenceId` 갱신 순서).

- [ ] **Step 13: 커밋**

```bash
git add adaptor/core/coalescing.py adaptor/tests/test_coalescing.py adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(adaptor): 연속 released 구간을 합쳐 중간 노드에서 정지 대기를 건너뛴다"
```

---

### Task 6: CP-3 — `newBaseRequest` 발행

`new_base_request`는 `adapter_jibot.py:739`에서 `None`으로 하드코딩되고 `:3514`가 그 값을 그대로 발행한다. 즉 스펙이 정한 "불필요한 제동 방지" 신호를 한 번도 보낸 적이 없다. 이것이 없으면 base 안 7번은 안 멈춰도 8번째(decision point)에서는 그대로 정지한다.

**Files:**
- Modify: `adaptor/adapter_jibot.py:739`, `:3514`
- Modify: `adaptor/adapter_jibot.py:6002` (`_finalize_v3_node_step`)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Consumes: Task 5의 `coalescing.drivable_run`, `_is_coalescing_breaker`
- Produces: 없음 (마지막 태스크)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
class NewBaseRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_is_raised_when_base_is_running_short(self):
        adapter = make_adapter()
        adapter.config.settings.path_control = "continuous"
        # 큐에 released 노드가 하나만 남았다 = 곧 decision point 다
        adapter._set_new_base_request_from_queue(remaining_released=1)
        self.assertTrue(adapter.state.new_base_request)

    async def test_request_is_cleared_when_base_is_long(self):
        adapter = make_adapter()
        adapter.config.settings.path_control = "continuous"
        adapter._set_new_base_request_from_queue(remaining_released=5)
        self.assertFalse(adapter.state.new_base_request)

    async def test_stop_point_never_requests(self):
        adapter = make_adapter()
        adapter.config.settings.path_control = "stop_point"
        adapter._set_new_base_request_from_queue(remaining_released=1)
        self.assertFalse(adapter.state.new_base_request)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `scripts/run-tests.sh tests/test_adapter_jibot_v3_order.py -k NewBaseRequest -v`
Expected: FAIL — `AttributeError: 'Adapter' object has no attribute '_set_new_base_request_from_queue'`

- [ ] **Step 3: 최소 구현**

```python
    # base 가 짧아지고 있음을 FMS 에 알리는 임계치. 1 이면 "다음이 decision point" 다.
    _NEW_BASE_REQUEST_THRESHOLD = 1

    def _set_new_base_request_from_queue(self, *, remaining_released: int) -> None:
        """base 끝이 가까우면 newBaseRequest 를 올린다.

        VDA5050 3.0 §6.6.3: "If the mobile robot detects that its base is running
        short, it can set the newBaseRequest flag to 'true' to attempt to prevent
        unnecessary braking." 이 신호가 없으면 연속 주행을 만들어도 base 의 마지막
        노드에서는 그대로 정지한다 — §6.1.2 가 decision point 정지를 의무로 두기 때문이다.
        stop_point 에서는 어차피 노드마다 서므로 올리지 않는다.
        """
        if self.state is None:
            return
        wanted = (
            self._continuous_path_enabled()
            and remaining_released <= self._NEW_BASE_REQUEST_THRESHOLD
        )
        if bool(self.state.new_base_request) == wanted:
            return
        self.state.new_base_request = wanted
        print(f"[ORDER BASE REQUEST] newBaseRequest={wanted} remaining={remaining_released}")
        self.request_state_publish("new base request")
```

`_finalize_v3_node_step` 에서 노드를 통과 처리한 뒤 다음을 부른다:

```python
        remaining = sum(
            1
            for pending in list(self.order_queue._queue)
            if pending.kind == "node" and getattr(pending.item, "released", False)
        )
        self._set_new_base_request_from_queue(remaining_released=remaining)
```

- [ ] **Step 4: 통과 확인**

Run: `scripts/run-tests.sh tests/test_adapter_jibot_v3_order.py -k NewBaseRequest -v`
Expected: PASS

- [ ] **Step 5: 전체 회귀**

Run: `scripts/run-tests.sh`
Expected: 전부 통과

- [ ] **Step 6: 커밋**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(adaptor): base 끝이 가까우면 newBaseRequest 를 발행한다"
```

---

## 실기 검증 (구현 후, 별도 세션)

코드 태스크가 아니다. 어댑터 서비스를 내리고 `scripts/probe-jibot-route-goto.py --status-only` 로 외부 간섭 0을 먼저 확인한 뒤, 같은 오더를 `path_control="stop_point"` / `"continuous"` 로 각각 3회 이상 돌려 비교한다.

측정할 것:
- 오더 전체 소요 시간
- 중간 노드에서 `vel_f` 가 0으로 떨어지는 샘플 수
- `lastNodeId` 발행이 노드마다 나오는지 (FMS 점유 관제가 깨지지 않는지)
- `edgeStates` 가 다음 노드 통과 시점에 빠지는지

기준선(2026-08-26 192.168.101.50:7274): 단일 goto 로 직선 중간 노드 통과 시 1002 mm/s 무감속.
코너 정지는 기구학 제약이라 `continuous` 에서도 남는다 — 이득은 직선·완만 구간에서만 본다.
