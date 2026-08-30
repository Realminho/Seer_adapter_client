# JIBOT 수동 컨트롤 (조그 + 거리이동) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** JIBOT 어댑터에 수동 조그(UmDrive 덱맨 + 워치독)와 거리이동(move 라우트)을 VDA5050 instant action으로 추가하고, WebUI 전용 manual 페이지(화살표키/패드 + 거리입력/nudge + 오더 취소)로 노출한다.

**Architecture:** instant action이 본체(`manualDrive`/`manualMove`/`manualStop`/`enableMotor`), WebUI는 control.sock으로 같은 instant action을 보내는 프론트엔드. 조그는 누르는 동안 브라우저가 ~300ms heartbeat를 보내고 어댑터 워치독이 명령 단절 시 자동 `um_stop`. 거리이동은 `cmd:"move"` 라우트를 클라이언트가 구성해 실행. busy/오더 중에는 manualDrive/manualMove를 거부(manualStop은 항상 허용).

**Tech Stack:** Python(어댑터 3.10 호환 필수), asyncio, stdlib http.server(WebUI), unittest/pytest, jibot-client TCP, VDA5050.

## Global Constraints

- **로봇 Python 3.10 호환** — `datetime.UTC` 금지, `datetime.timezone.utc` 사용. `X | None` 타입 신문법은 런타임 평가되는 위치에서 피한다(어노테이션은 무방).
- **JIBOT 전용** — 수동 액션/페이지는 JIBOT 어댑터에서만. WebUI manual 페이지는 `manualDrive`를 노출하는 spec에서만 표시.
- **`watchdog_ms > heartbeat_ms`** — 기본 800 > 300. 정상 누름 중 오동작 정지 방지.
- **manualStop은 항상 허용** — busy/오더/disabled 무관하게 정지 가능(긴급 정지 경로). `_is_motion_instant_action`에 넣지 않는다.
- **fire-and-forget** — UmDrive/UmStop/move는 ack 없음. 핸들러는 "전송 성공"을 FINISHED로 본다.
- **WebUI 무-JS 원칙** — 거리이동/nudge/오더취소는 풀폼 POST. 조그 덱맨만 최소 inline JS.
- **검증 필요(실기)** — 가변 `move` 실행 경로는 `UmSetRoutes`+`UmRoutes`로 구현하되, 벤더 PDF(`jibot-client/robot_communication_protocol_english.pdf`)/실기로 확인 후 필요 시 `UmSchedulerThis`로 교체(국소 변경). `move_distance` 외 코드는 영향 없음.
- **커밋** — 각 task 끝에 커밋. 현재 브랜치 `jibot-client-refactor`에서 작업.

---

## File Structure

- `adaptor/config/config.py` — `ManualControlSettings` 추가, `Config`에 필드, 로더 파싱.
- `adaptor/config/config.toml` — `[manual_control]` 섹션.
- `jibot-client/src/jibot_client/client.py` — `build_move_route`(정적/순수) + `move_distance`(async).
- `adaptor/core/registry.py` — `_JIBOT_INSTANT_ACTIONS`에 4개 추가.
- `adaptor/adapter_jibot.py` — `SUPPORTED_INSTANT_ACTIONS` 추가, 디스패치 4분기, 핸들러 4종, 게이팅(`_manual_blocked_reason`, `_is_motion_instant_action` 갱신), 워치독, `__init__` 필드.
- `adaptor/web/server.py` — `_post_action` params 전달, `/manual` GET·POST 라우팅, `_post_manual`, `_json`.
- `adaptor/web/render.py` — `manual_page` + 헬퍼 + inline JS, control 페이지 링크.
- `adaptor/web/main.py` — manual 활성 플래그 와이어링(불필요 시 생략).
- `adaptor/tests/test_jibot_move_route.py` — 클라이언트 빌더/전송 테스트(adaptor 스위트, jibot_client import).
- `adaptor/tests/test_adapter_jibot_v3_order.py` — 어댑터 핸들러/워치독/게이팅 테스트 추가 + FakeVehicle 확장.
- `adaptor/tests/test_web_server.py` / `test_web_render.py` — web 라우팅/렌더 테스트.
- `docs/guide/web-ui.md` — manual 페이지 문서.

---

## Task 1: Config `[manual_control]` + `ManualControlSettings`

**Files:**
- Modify: `adaptor/config/config.py` (dataclass 추가 + `Config` 필드 + 로더 파싱)
- Modify: `adaptor/config/config.toml` (`[manual_control]` 섹션)
- Test: `adaptor/tests/test_config.py` (없으면 생성)

**Interfaces:**
- Produces: `config.config.ManualControlSettings` (dataclass), `Config.manual_control: ManualControlSettings`.
  Fields(+defaults): `enabled: bool=True`, `drive_trans: float=200`, `drive_rot: float=30`,
  `drive_speed: float=200`, `drive_lat: float=0`, `heartbeat_ms: int=300`, `watchdog_ms: int=800`,
  `step_distance_mm: int=500`, `default_move_speed: float=100`, `move_obs_avoid_dist: int=1000`,
  `move_side_avoid_dist: int=50`.

- [ ] **Step 1: Write the failing test**

`adaptor/tests/test_config.py` (파일 없으면 생성, 있으면 함수 추가):

```python
from config.config import ManualControlSettings


def test_manual_control_defaults():
    mc = ManualControlSettings()
    assert mc.enabled is True
    assert mc.drive_trans == 200
    assert mc.drive_rot == 30
    assert mc.drive_speed == 200
    assert mc.drive_lat == 0
    assert mc.heartbeat_ms == 300
    assert mc.watchdog_ms == 800
    assert mc.watchdog_ms > mc.heartbeat_ms
    assert mc.step_distance_mm == 500
    assert mc.default_move_speed == 100


def test_manual_control_overrides_from_dict():
    mc = ManualControlSettings(**{"enabled": False, "drive_speed": 120, "watchdog_ms": 1000})
    assert mc.enabled is False
    assert mc.drive_speed == 120
    assert mc.watchdog_ms == 1000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_config.py -q`
Expected: FAIL — `ImportError: cannot import name 'ManualControlSettings'`.

- [ ] **Step 3: Add the dataclass**

`adaptor/config/config.py` — `SoundSettings` 정의(line ~105) 바로 뒤에 추가:

```python
@dataclass
class ManualControlSettings:
    enabled: bool = True
    drive_trans: float = 200
    drive_rot: float = 30
    drive_speed: float = 200
    drive_lat: float = 0
    heartbeat_ms: int = 300
    watchdog_ms: int = 800
    step_distance_mm: int = 500
    default_move_speed: float = 100
    move_obs_avoid_dist: int = 1000
    move_side_avoid_dist: int = 50
```

- [ ] **Step 4: Run test to verify dataclass passes**

Run: `cd adaptor && python -m pytest tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Wire into `Config` and the loader**

`adaptor/config/config.py`, 메인 `Config` 데이터클래스(필드 `sound_settings: SoundSettings`가 있는 곳, line ~225)에 필드 추가:

```python
    manual_control: ManualControlSettings
```

로더에서 `sound_settings = SoundSettings(**config_dict["sound_settings"])`(line ~305) 바로 뒤에 추가:

```python
    manual_control = ManualControlSettings(**config_dict.get("manual_control", {}))
```

그리고 `Config(...)` 생성 호출(line ~324 부근, `sound_settings=sound_settings,` 줄 옆)에 추가:

```python
        manual_control=manual_control,
```

- [ ] **Step 6: Add the toml section**

`adaptor/config/config.toml` 끝에 추가:

```toml
[manual_control]
enabled = true
drive_trans = 200
drive_rot = 30
drive_speed = 200
drive_lat = 0
heartbeat_ms = 300
watchdog_ms = 800
step_distance_mm = 500
default_move_speed = 100
move_obs_avoid_dist = 1000
move_side_avoid_dist = 50
```

- [ ] **Step 7: Run the full config + a smoke import**

Run: `cd adaptor && python -m pytest tests/test_config.py -q && python -c "from config.config import get_config; print(get_config().manual_control.watchdog_ms)"`
Expected: PASS, prints `800`.

- [ ] **Step 8: Commit**

```bash
git add adaptor/config/config.py adaptor/config/config.toml adaptor/tests/test_config.py
git commit -m "feat(config): [manual_control] settings for JIBOT manual control"
```

---

## Task 2: Client `build_move_route` (pure builder)

**Files:**
- Modify: `jibot-client/src/jibot_client/client.py` (`JIBOT`에 staticmethod 추가)
- Test: `adaptor/tests/test_jibot_move_route.py` (생성)

**Interfaces:**
- Produces: `JIBOT.build_move_route(distance, speed, *, obs_avoid_dist=1000, side_avoid_dist=50, flag=1, io=1, use_io=False, note=1, route_name="manual_move", step_key="a") -> dict` returning `{route_name: {step_key: {"cmd": "move", "distance": int(distance), "speed": int(speed), "flag": flag, "io": io, "obs_avoid_dist": obs_avoid_dist, "side_avoid_dist": side_avoid_dist, "use_io": use_io, "note": note}}}`.

- [ ] **Step 1: Write the failing test**

`adaptor/tests/test_jibot_move_route.py` (생성):

```python
from jibot_client.client import JIBOT


def test_build_move_route_matches_observed_shape():
    route = JIBOT.build_move_route(-2500, 200)
    assert route == {
        "manual_move": {
            "a": {
                "cmd": "move",
                "distance": -2500,
                "speed": 200,
                "flag": 1,
                "io": 1,
                "obs_avoid_dist": 1000,
                "side_avoid_dist": 50,
                "use_io": False,
                "note": 1,
            }
        }
    }


def test_build_move_route_coerces_to_int_and_honors_opts():
    route = JIBOT.build_move_route(
        500.0, 100.0, obs_avoid_dist=800, side_avoid_dist=30, route_name="r", step_key="s"
    )
    step = route["r"]["s"]
    assert step["distance"] == 500 and isinstance(step["distance"], int)
    assert step["speed"] == 100 and isinstance(step["speed"], int)
    assert step["obs_avoid_dist"] == 800
    assert step["side_avoid_dist"] == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_jibot_move_route.py -q`
Expected: FAIL — `AttributeError: type object 'JIBOT' has no attribute 'build_move_route'`.

- [ ] **Step 3: Implement the static builder**

`jibot-client/src/jibot_client/client.py` — `goto_xyz`(line ~693) 뒤, `disable_motor` 앞에 추가:

```python
    @staticmethod
    def build_move_route(
        distance,
        speed,
        *,
        obs_avoid_dist=1000,
        side_avoid_dist=50,
        flag=1,
        io=1,
        use_io=False,
        note=1,
        route_name="manual_move",
        step_key="a",
    ):
        """Build a one-step ``cmd:"move"`` route (relative-distance jog).

        ``move`` is not a top-level Um* command; it only runs as a route step.
        Shape mirrors the observed routes_ali.json "move" route. Verify the
        exact run path (UmSetRoutes+UmRoutes vs UmSchedulerThis) on the robot.
        """
        return {
            route_name: {
                step_key: {
                    "cmd": "move",
                    "distance": int(distance),
                    "speed": int(speed),
                    "flag": flag,
                    "io": io,
                    "obs_avoid_dist": obs_avoid_dist,
                    "side_avoid_dist": side_avoid_dist,
                    "use_io": use_io,
                    "note": note,
                }
            }
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_jibot_move_route.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add jibot-client/src/jibot_client/client.py adaptor/tests/test_jibot_move_route.py
git commit -m "feat(jibot-client): build_move_route pure builder for relative move"
```

---

## Task 3: Client `move_distance` (async send)

**Files:**
- Modify: `jibot-client/src/jibot_client/client.py` (`move_distance` async)
- Test: `adaptor/tests/test_jibot_move_route.py` (함수 추가)

**Interfaces:**
- Consumes: `JIBOT.build_move_route(...)` (Task 2), `JIBOT.um_set_routes(routes)`, `JIBOT.call_routes(name, key, id)` (기존).
- Produces: `async JIBOT.move_distance(distance, speed, *, obs_avoid_dist=1000, side_avoid_dist=50, flag=1, io=1, use_io=False, route_name="manual_move", step_key="a", gap=-1) -> None` — `um_set_routes`로 라우트 업로드 후 `call_routes`로 트리거.

- [ ] **Step 1: Write the failing test**

`adaptor/tests/test_jibot_move_route.py` 에 추가:

```python
import asyncio


def test_move_distance_uploads_then_triggers():
    j = JIBOT(robot_ip="127.0.0.1")
    calls = []

    async def fake_send(command, gap=-1, **params):
        calls.append((command, params))

    j.send_command = fake_send
    asyncio.run(j.move_distance(-2500, 200))

    assert calls[0][0] == "UmSetRoutes"
    assert calls[0][1]["routes"] == JIBOT.build_move_route(-2500, 200)
    assert calls[1][0] == "UmRoutes"
    assert calls[1][1]["routes"] == "manual_move"
    assert calls[1][1]["key"] == "a"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_jibot_move_route.py::test_move_distance_uploads_then_triggers -q`
Expected: FAIL — `AttributeError: 'JIBOT' object has no attribute 'move_distance'`.

- [ ] **Step 3: Implement `move_distance`**

`jibot-client/src/jibot_client/client.py` — `goto_xyz` 근처(HIGH LEVEL이 아닌 wrapper 영역, `um_set_routes`가 정의된 line ~915 뒤)에 추가:

```python
    async def move_distance(
        self,
        distance,
        speed,
        *,
        obs_avoid_dist=1000,
        side_avoid_dist=50,
        flag=1,
        io=1,
        use_io=False,
        route_name="manual_move",
        step_key="a",
        gap=-1,
    ):
        """Run a relative-distance move via an ad-hoc one-step route.

        Uploads the route (UmSetRoutes) then triggers it (UmRoutes). NOTE: the
        exact variable-distance run path needs robot verification; if UmRoutes
        only triggers pre-stored routes, switch to UmSchedulerThis here (local
        change). distance is in mm (signed: +forward / -backward).
        """
        route = self.build_move_route(
            distance,
            speed,
            obs_avoid_dist=obs_avoid_dist,
            side_avoid_dist=side_avoid_dist,
            flag=flag,
            io=io,
            use_io=use_io,
            route_name=route_name,
            step_key=step_key,
        )
        await self.um_set_routes(route, gap=gap)
        await self.call_routes(route_name, step_key)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_jibot_move_route.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add jibot-client/src/jibot_client/client.py adaptor/tests/test_jibot_move_route.py
git commit -m "feat(jibot-client): move_distance executes relative move route"
```

---

## Task 4: Registry + factsheet exposure

**Files:**
- Modify: `adaptor/core/registry.py` (`_JIBOT_INSTANT_ACTIONS`)
- Modify: `adaptor/adapter_jibot.py` (`SUPPORTED_INSTANT_ACTIONS`)
- Test: `adaptor/tests/test_registry.py` + `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Produces: registry/factsheet에 actionType `manualDrive`, `manualMove`, `manualStop`, `enableMotor` 노출.
  registry `motion`: manualDrive=True, manualMove=True, manualStop=False, enableMotor=True.

- [ ] **Step 1: Write the failing test (registry)**

`adaptor/tests/test_registry.py` 에 추가(기존 import/헬퍼 재사용; 없으면 `from core.registry import build_registry, _JIBOT_INSTANT_ACTIONS`):

```python
def test_jibot_instant_actions_include_manual_control():
    from core.registry import _JIBOT_INSTANT_ACTIONS
    by_type = {a.action_type: a for a in _JIBOT_INSTANT_ACTIONS}
    assert "manualDrive" in by_type and by_type["manualDrive"].motion is True
    assert "manualMove" in by_type and by_type["manualMove"].motion is True
    assert "manualStop" in by_type and by_type["manualStop"].motion is False
    assert "enableMotor" in by_type and by_type["enableMotor"].motion is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_registry.py::test_jibot_instant_actions_include_manual_control -q`
Expected: FAIL — assertion (manualDrive not in by_type).

- [ ] **Step 3: Add to `_JIBOT_INSTANT_ACTIONS`**

`adaptor/core/registry.py` — `_JIBOT_INSTANT_ACTIONS` 튜플(line ~187), `clampStop` 항목 뒤에 추가:

```python
    InstantAction("manualDrive", "Manual drive (jog)", motion=True),
    InstantAction("manualMove", "Manual move (distance)", motion=True),
    InstantAction("manualStop", "Manual stop", motion=False),
    InstantAction("enableMotor", "Enable motor", motion=True),
```

- [ ] **Step 4: Run registry test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_registry.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing test (factsheet)**

`adaptor/tests/test_adapter_jibot_v3_order.py` 에 추가(기존 `_make_adapter` 사용하는 테스트 클래스 안):

```python
    def test_factsheet_advertises_manual_actions(self) -> None:
        adapter = self._make_adapter()
        factsheet = adapter._build_factsheet()
        types = {a["actionType"] for a in factsheet["agvActions"]}
        for t in ("manualDrive", "manualMove", "manualStop", "enableMotor"):
            self.assertIn(t, types)
```

- [ ] **Step 6: Run factsheet test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k factsheet_advertises_manual -q`
Expected: FAIL — manual types missing.

- [ ] **Step 7: Add to `SUPPORTED_INSTANT_ACTIONS`**

`adaptor/adapter_jibot.py` — `SUPPORTED_INSTANT_ACTIONS` 튜플(line ~1993)에 문자열 추가(끝부분):

```python
        "manualDrive",
        "manualMove",
        "manualStop",
        "enableMotor",
```

- [ ] **Step 8: Run factsheet test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k factsheet_advertises_manual -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add adaptor/core/registry.py adaptor/adapter_jibot.py adaptor/tests/test_registry.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(jibot): expose manual* instant actions in registry + factsheet"
```

---

## Task 5: Adapter `manualStop` + `enableMotor` handlers (+ FakeVehicle 확장)

**Files:**
- Modify: `adaptor/adapter_jibot.py` (`__init__` 필드, 디스패치 2분기, 핸들러 2종, `_cancel_manual_drive_watchdog`)
- Modify: `adaptor/tests/test_adapter_jibot_v3_order.py` (FakeVehicle 확장 + 테스트)

**Interfaces:**
- Consumes: `self._vehicle.um_stop()`, `self._vehicle.enable_motor()`, `_run_on_adapter_loop`, `_update_instant_action_status`.
- Produces: `_handle_manual_stop_instant_action(action)`, `_handle_enable_motor_instant_action(action)`,
  `_cancel_manual_drive_watchdog()`, `self._manual_drive_watchdog: Optional[asyncio.TimerHandle]`.
- FakeVehicle 신규 카운터: `um_stop_calls`, `drive_calls`, `move_distance_calls`, `enable_motor_calls`.

- [ ] **Step 1: Extend FakeVehicle (test scaffolding)**

`adaptor/tests/test_adapter_jibot_v3_order.py` `FakeVehicle.__init__` 끝(line ~143, `self.is_simulator = False` 뒤)에 추가:

```python
        self.um_stop_calls = 0
        self.drive_calls = []
        self.move_distance_calls = []
        self.enable_motor_calls = 0
```

기존 `FakeVehicle.um_stop`(line ~184)에 카운터 한 줄 추가(기존 동작 유지):

```python
    async def um_stop(self, gap: int = -1) -> None:
        self.um_stop_calls += 1
        self.stop_charge_calls += 1
        self._charging = False
        self._status = "Stopped"
```

`FakeVehicle.stop_motion` 아래(line ~199)에 신규 메서드 추가:

```python
    async def um_drive(self, trans, rot, speed, lat, gap: int = -1) -> None:
        self.drive_calls.append((trans, rot, speed, lat))

    async def move_distance(self, distance, speed, **kwargs) -> None:
        self.move_distance_calls.append((distance, speed, kwargs))

    async def enable_motor(self) -> None:
        self.enable_motor_calls += 1
```

- [ ] **Step 2: Write the failing tests**

같은 파일, 핸들러 테스트 클래스에 추가:

```python
    def _manual_action(self, action_type, action_id="m1", params=None):
        return InstantActions.from_dict({
            "headerId": 1,
            "timestamp": "2026-06-10T00:00:00.000Z",
            "version": "3.0.0",
            "manufacturer": "jibot",
            "serialNumber": "HN-SH6-TR-001",
            "actions": [{
                "actionType": action_type,
                "actionId": action_id,
                "blockingType": "NONE",
                "actionParameters": [
                    {"key": k, "value": v} for k, v in (params or {}).items()
                ],
            }],
        })

    def test_manual_stop_calls_um_stop_even_while_busy(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter._work_in_progress = "loading"  # busy
                adapter.instant_actions_accept_procedure(self._manual_action("manualStop"))
                await asyncio.sleep(0.02)
                self.assertGreaterEqual(adapter._vehicle.um_stop_calls, 1)
            finally:
                state_task.cancel()
        asyncio.run(scenario())

    def test_enable_motor_calls_vehicle(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                adapter.instant_actions_accept_procedure(self._manual_action("enableMotor"))
                await asyncio.sleep(0.02)
                self.assertEqual(adapter._vehicle.enable_motor_calls, 1)
            finally:
                state_task.cancel()
        asyncio.run(scenario())
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k "manual_stop_calls or enable_motor_calls" -q`
Expected: FAIL — manualStop/enableMotor unsupported (현재 else 분기 → FAILED, vehicle 미호출).

- [ ] **Step 4: Add `__init__` field**

`adaptor/adapter_jibot.py` `__init__`에서 `self._work_in_progress` 초기화(line ~141) 근처에 추가:

```python
        self._manual_drive_watchdog: Optional[asyncio.TimerHandle] = None
```

- [ ] **Step 5: Add dispatch branches**

`instant_actions_accept_procedure`의 if/elif 체인(line ~3658), `cancelOrder` 분기 부근에 추가(순서 무관, 단 work-busy 차단 분기 아래):

```python
            elif action.action_type == "manualStop":
                self._handle_manual_stop_instant_action(action)
            elif action.action_type == "enableMotor":
                self._handle_enable_motor_instant_action(action)
```

- [ ] **Step 6: Add handlers + watchdog-cancel**

클램프 핸들러 인근(line ~3750 부근)에 추가:

```python
    def _cancel_manual_drive_watchdog(self) -> None:
        if self._manual_drive_watchdog is not None:
            self._manual_drive_watchdog.cancel()
            self._manual_drive_watchdog = None

    def _handle_manual_stop_instant_action(self, action: Any) -> None:
        self._update_instant_action_status(
            action.action_id, ActionStatus.FINISHED, result_description="manual stop"
        )

        async def _run() -> None:
            self._cancel_manual_drive_watchdog()
            await self._vehicle.um_stop()

        self._run_on_adapter_loop(_run)

    def _handle_enable_motor_instant_action(self, action: Any) -> None:
        if not self.config.manual_control.enabled:
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED,
                result_description="manual control disabled",
            )
            return
        self._update_instant_action_status(action.action_id, ActionStatus.RUNNING)

        async def _run() -> None:
            try:
                await self._vehicle.enable_motor()
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FINISHED,
                    result_description="motor enabled",
                )
            except Exception as exc:
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FAILED,
                    result_description=f"enableMotor failed: {exc}",
                )

        self._run_on_adapter_loop(_run)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k "manual_stop_calls or enable_motor_calls" -q`
Expected: PASS (2 passed).

- [ ] **Step 8: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(jibot): manualStop + enableMotor instant action handlers"
```

---

## Task 6: Adapter `manualDrive` + watchdog + gating

**Files:**
- Modify: `adaptor/adapter_jibot.py` (`_manual_blocked_reason`, `_is_motion_instant_action` 갱신, manualDrive 핸들러 + 워치독 arm/timeout, 디스패치 분기)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Consumes: `self._vehicle.um_drive(trans, rot, speed, lat)`, config `manual_control`, `self.order`, `self._work_in_progress`, `self._loop`.
- Produces: `_handle_manual_drive_instant_action(action)`, `_manual_blocked_reason() -> Optional[str]`,
  `_arm_manual_drive_watchdog()`, `async _manual_drive_timeout()`. `_is_motion_instant_action` now also True for manualDrive/manualMove.

- [ ] **Step 1: Write the failing tests**

`adaptor/tests/test_adapter_jibot_v3_order.py` 에 추가:

```python
    def test_manual_drive_sends_um_drive_with_lat(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                adapter.config.manual_control.drive_lat = 0
                adapter.order = None
                adapter.instant_actions_accept_procedure(
                    self._manual_action("manualDrive", params={"trans": 150, "rot": 0, "speed": 150})
                )
                await asyncio.sleep(0.02)
                self.assertEqual(adapter._vehicle.drive_calls, [(150.0, 0.0, 150.0, 0.0)])
            finally:
                state_task.cancel()
        asyncio.run(scenario())

    def test_manual_drive_watchdog_auto_stops(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                adapter.config.manual_control.watchdog_ms = 30
                adapter.order = None
                adapter.instant_actions_accept_procedure(self._manual_action("manualDrive"))
                await asyncio.sleep(0.01)
                self.assertTrue(adapter._vehicle.drive_calls)
                self.assertEqual(adapter._vehicle.um_stop_calls, 0)
                await asyncio.sleep(0.06)  # > watchdog_ms
                self.assertGreaterEqual(adapter._vehicle.um_stop_calls, 1)
            finally:
                state_task.cancel()
        asyncio.run(scenario())

    def test_manual_drive_rejected_while_order_active(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                adapter.order = _one_node_order("o1")  # active order
                adapter.instant_actions_accept_procedure(self._manual_action("manualDrive"))
                await asyncio.sleep(0.02)
                self.assertEqual(adapter._vehicle.drive_calls, [])
                # after cancelOrder clears self.order, manual is allowed
                adapter.order = None
                adapter.instant_actions_accept_procedure(self._manual_action("manualDrive", action_id="m2"))
                await asyncio.sleep(0.02)
                self.assertTrue(adapter._vehicle.drive_calls)
            finally:
                state_task.cancel()
        asyncio.run(scenario())

    def test_manual_drive_rejected_when_disabled(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = False
                adapter.order = None
                adapter.instant_actions_accept_procedure(self._manual_action("manualDrive"))
                await asyncio.sleep(0.02)
                self.assertEqual(adapter._vehicle.drive_calls, [])
            finally:
                state_task.cancel()
        asyncio.run(scenario())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k manual_drive -q`
Expected: FAIL — manualDrive unsupported / no drive_calls.

- [ ] **Step 3: Add gating helper + update motion classification**

`adaptor/adapter_jibot.py` `_is_motion_instant_action`(line ~4324) 수정:

```python
    def _is_motion_instant_action(self, action: Any) -> bool:
        return (
            action.action_type == "startCharging"
            or action.action_type in ("manualDrive", "manualMove")
            or self._is_jibot_command_instant_action(action)
        )
```

같은 클래스에 추가:

```python
    def _manual_blocked_reason(self) -> Optional[str]:
        """Return why manual drive/move is blocked, or None if allowed."""
        if not self.config.manual_control.enabled:
            return "manual control disabled"
        if self._work_in_progress is not None:
            return f"busy with {self._work_in_progress} work"
        if self.order is not None:
            return "order in progress; cancel order first"
        return None
```

- [ ] **Step 4: Add manualDrive handler + watchdog**

```python
    def _arm_manual_drive_watchdog(self) -> None:
        if self._loop is None or not self._loop.is_running():
            return
        if self._manual_drive_watchdog is not None:
            self._manual_drive_watchdog.cancel()
        timeout = float(self.config.manual_control.watchdog_ms) / 1000.0
        self._manual_drive_watchdog = self._loop.call_later(
            timeout, lambda: self._run_on_adapter_loop(self._manual_drive_timeout)
        )

    async def _manual_drive_timeout(self) -> None:
        self._manual_drive_watchdog = None
        await self._vehicle.um_stop()
        print("[MANUAL DRIVE WATCHDOG] auto-stop (no heartbeat)")

    def _handle_manual_drive_instant_action(self, action: Any) -> None:
        reason = self._manual_blocked_reason()
        if reason:
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED, result_description=reason
            )
            return
        params = {p.key: p.value for p in action.action_parameters}
        mc = self.config.manual_control
        trans = float(params.get("trans", mc.drive_trans))
        rot = float(params.get("rot", mc.drive_rot))
        speed = float(params.get("speed", mc.drive_speed))
        lat = float(params.get("lat", mc.drive_lat))
        self._update_instant_action_status(
            action.action_id, ActionStatus.FINISHED, result_description="manual drive"
        )

        async def _run() -> None:
            await self._vehicle.um_drive(trans, rot, speed, lat)
            self._arm_manual_drive_watchdog()

        self._run_on_adapter_loop(_run)
```

- [ ] **Step 5: Add dispatch branch**

`instant_actions_accept_procedure` 체인에 추가(manualStop 분기 옆):

```python
            elif action.action_type == "manualDrive":
                self._handle_manual_drive_instant_action(action)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k manual_drive -q`
Expected: PASS (4 passed).

- [ ] **Step 7: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(jibot): manualDrive jog + deadman watchdog + busy/order gating"
```

---

## Task 7: Adapter `manualMove` handler

**Files:**
- Modify: `adaptor/adapter_jibot.py` (manualMove 핸들러 + 디스패치 분기)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Consumes: `self._vehicle.move_distance(distance, speed, obs_avoid_dist=..., side_avoid_dist=...)`, `_manual_blocked_reason`.
- Produces: `_handle_manual_move_instant_action(action)`.

- [ ] **Step 1: Write the failing tests**

```python
    def test_manual_move_calls_move_distance(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                adapter.order = None
                adapter.instant_actions_accept_procedure(
                    self._manual_action("manualMove", params={"distance": -2500, "speed": 200})
                )
                await asyncio.sleep(0.02)
                self.assertEqual(len(adapter._vehicle.move_distance_calls), 1)
                distance, speed, kwargs = adapter._vehicle.move_distance_calls[0]
                self.assertEqual(distance, -2500.0)
                self.assertEqual(speed, 200.0)
            finally:
                state_task.cancel()
        asyncio.run(scenario())

    def test_manual_move_requires_distance(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                adapter.order = None
                adapter.instant_actions_accept_procedure(self._manual_action("manualMove"))
                await asyncio.sleep(0.02)
                self.assertEqual(adapter._vehicle.move_distance_calls, [])
            finally:
                state_task.cancel()
        asyncio.run(scenario())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k manual_move -q`
Expected: FAIL — manualMove unsupported.

- [ ] **Step 3: Add handler**

```python
    def _handle_manual_move_instant_action(self, action: Any) -> None:
        reason = self._manual_blocked_reason()
        if reason:
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED, result_description=reason
            )
            return
        params = {p.key: p.value for p in action.action_parameters}
        mc = self.config.manual_control
        try:
            distance = float(params["distance"])
        except (KeyError, ValueError, TypeError):
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED,
                result_description="manualMove requires numeric distance",
            )
            return
        speed = float(params.get("speed", mc.default_move_speed))
        self._update_instant_action_status(action.action_id, ActionStatus.RUNNING)

        async def _run() -> None:
            try:
                await self._vehicle.move_distance(
                    distance, speed,
                    obs_avoid_dist=mc.move_obs_avoid_dist,
                    side_avoid_dist=mc.move_side_avoid_dist,
                )
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FINISHED,
                    result_description=f"move {distance}mm @ {speed}",
                )
            except Exception as exc:
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FAILED,
                    result_description=f"manualMove failed: {exc}",
                )

        self._run_on_adapter_loop(_run)
```

- [ ] **Step 4: Add dispatch branch**

```python
            elif action.action_type == "manualMove":
                self._handle_manual_move_instant_action(action)
```

- [ ] **Step 5: Run tests + full adapter suite**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -q`
Expected: PASS (전체 통과 — 기존 테스트 회귀 없음).

- [ ] **Step 6: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(jibot): manualMove distance instant action handler"
```

---

## Task 8: Web `_post_action` params + `/manual` endpoint

**Files:**
- Modify: `adaptor/web/server.py` (`_post_action` params, `_json`, `/manual` POST 라우팅, `_post_manual`)
- Test: `adaptor/tests/test_web_server.py`

**Interfaces:**
- Consumes: `control.build_instant_actions(parameters=...)` (기존), `self._senders[key].send(payload, meta)`, `_confirmed`, `_audit`, `_next_header_id`.
- Produces: POST `/adapter/<key>/manual` → `_post_manual`. `_post_action`는 form의 `distance`/`speed`를 parameters로 전달. handler `_json(code, obj)`.

- [ ] **Step 1: Write the failing tests**

`adaptor/tests/test_web_server.py` (기존 fake-sender 하니스 사용; 패턴은 기존 `_post_action` 테스트 참조). 다음을 추가:

```python
    def test_post_action_forwards_distance_speed_params(self):
        ui, sender = self._make_ui_with_recording_sender()  # 기존 헬퍼(없으면 생성)
        h = self._post(ui, "/adapter/jibot/action", {
            "csrf_token": ui._csrf, "action_type": "manualMove",
            "confirm": "on", "distance": "-2500", "speed": "200",
        })
        payload = sender.sent[-1][0]
        params = {p["key"]: p["value"] for p in payload["actions"][0]["actionParameters"]}
        assert params == {"distance": "-2500", "speed": "200"}

    def test_post_manual_returns_json_and_requires_armed(self):
        ui, sender = self._make_ui_with_recording_sender()
        # armed 누락 → 거부, 미전송
        h = self._post(ui, "/adapter/jibot/manual", {
            "csrf_token": ui._csrf, "action_type": "manualDrive", "confirm": "on",
        })
        assert h.status == 400
        assert sender.sent == []
        # armed=on → 전송, JSON
        h2 = self._post(ui, "/adapter/jibot/manual", {
            "csrf_token": ui._csrf, "action_type": "manualDrive",
            "confirm": "on", "armed": "on", "trans": "150",
        })
        assert h2.status == 200
        assert h2.json["delivered"] is True
        assert sender.sent[-1][0]["actions"][0]["actionType"] == "manualDrive"
```

> 참고: 기존 test_web_server.py에 fake handler/sender 헬퍼가 없으면, 기존 `_post_action`/`_post_goto` 테스트가 쓰는 패턴(가짜 핸들러로 `_html`/`_redirect`/`_json` 캡처, fake sender가 `send()`에서 `(True, "delivered")` 반환하며 payload 기록)을 따라 `_make_ui_with_recording_sender`, `_post`를 구성한다. `_post`는 `_dispatch_post(h, form)` 호출 후 핸들러가 기록한 status/json/redirect를 노출한다.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd adaptor && python -m pytest tests/test_web_server.py -k "forwards_distance or post_manual" -q`
Expected: FAIL — params 미전달 / `/manual` 라우트 없음.

- [ ] **Step 3: Forward params in `_post_action`**

`adaptor/web/server.py` `_post_action`(line ~564) — `build_instant_actions(...)` 호출에 parameters 추가. action_type/confirm/csrf/return_to/verb 외의 form 키를 parameters로 전달:

```python
        param_keys = [k for k in form
                      if k not in ("csrf_token", "action_type", "confirm", "return_to", "verb")]
        parameters = [(k, form[k]) for k in param_keys] or None
        payload = control.build_instant_actions(
            header_id=self._next_header_id(key),
            timestamp=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            version=spec.vda_full_version,
            manufacturer=spec.manufacturer,
            serial_number=spec.serial,
            action_type=action_type,
            action_id=str(uuid.uuid4()),
            parameters=parameters,
        )
```

- [ ] **Step 4: Add `_json` handler helper**

`adaptor/web/server.py` 핸들러 클래스(`_html` 정의 근처, line ~238)에 추가:

```python
            def _json(self, code: int, obj: dict):
                import json as _jsonlib
                data = _jsonlib.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
```

- [ ] **Step 5: Add `/manual` POST route + handler**

`_dispatch_post`(line ~388) 에 추가(action 라우트 옆):

```python
        if len(parts) == 3 and parts[0] == "adapter" and parts[2] == "manual":
            self._post_manual(h, parts[1], form)
            return
```

`_post_action` 뒤에 핸들러 추가:

```python
    def _post_manual(self, h, key: str, form: dict):
        """Deadman/jog endpoint: returns small JSON, requires armed flag."""
        spec = self._specs.get(key)
        if spec is None:
            h._json(404, {"delivered": False, "error": "unknown adapter"})
            return
        action_type = form.get("action_type", "")
        action = next((a for a in spec.instant_actions if a.action_type == action_type), None)
        if action is None:
            self._audit(h, key, f"manual:{action_type[:40]!r}", "rejected:unknown")
            h._json(400, {"delivered": False, "error": "unknown action"})
            return
        # manualStop is always allowed; drive/move require armed + confirm.
        if action_type != "manualStop":
            if form.get("armed") not in ("on", "1", "true"):
                self._audit(h, key, f"manual:{action_type}", "rejected:unarmed")
                h._json(400, {"delivered": False, "error": "not armed"})
                return
            if action.motion and not _confirmed(form):
                h._json(400, {"delivered": False, "error": "confirm required"})
                return
        from core import control
        param_keys = [k for k in form
                      if k not in ("csrf_token", "action_type", "confirm", "armed", "return_to", "verb")]
        parameters = [(k, form[k]) for k in param_keys] or None
        payload = control.build_instant_actions(
            header_id=self._next_header_id(key),
            timestamp=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            version=spec.vda_full_version,
            manufacturer=spec.manufacturer,
            serial_number=spec.serial,
            action_type=action_type,
            action_id=str(uuid.uuid4()),
            parameters=parameters,
        )
        meta = {
            "confirmed": _confirmed(form),
            "source_user": self._auth_user(h.headers.get("Authorization")),
            "created_at": time.time(),
        }
        delivered, text = self._senders[key].send(payload, meta)
        # heartbeat 비감사: manualDrive 반복은 audit하지 않음(시작/정지/이동만 감사).
        if action_type in ("manualMove", "manualStop"):
            self._audit(h, key, f"manual:{action_type}", f"delivered={delivered}")
        h._json(200 if delivered else 502, {"delivered": delivered, "text": text})
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd adaptor && python -m pytest tests/test_web_server.py -k "forwards_distance or post_manual" -q`
Expected: PASS.

- [ ] **Step 7: Run full web server suite (regression)**

Run: `cd adaptor && python -m pytest tests/test_web_server.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add adaptor/web/server.py adaptor/tests/test_web_server.py
git commit -m "feat(web): forward action params + /manual deadman JSON endpoint"
```

---

## Task 9: Web manual page render + GET route + link

**Files:**
- Modify: `adaptor/web/render.py` (`manual_page` + 헬퍼 + inline JS, control 페이지 링크)
- Modify: `adaptor/web/server.py` (`/manual` GET 라우트)
- Test: `adaptor/tests/test_web_render.py`

**Interfaces:**
- Consumes: `render.page`, `render.esc`, `render._csrf_field`, `render._command_form`, `render._command_group`, `spec.instant_actions`, snapshot(모터 상태/오더), `_csrf`.
- Produces: `render.manual_page(spec, csrf, q, snapshot=None) -> str`. GET `/adapter/<key>/manual`. `render._manual_enabled(spec)` 헬퍼(spec에 manualDrive 있으면 True).

- [ ] **Step 1: Write the failing tests**

`adaptor/tests/test_web_render.py` 에 추가(기존 spec fixture 사용):

```python
def test_manual_page_renders_for_jibot_spec(jibot_spec):
    html = render.manual_page(jibot_spec, "csrf-token", {})
    assert "Manual control" in html or "수동" in html
    assert "manualDrive" in html       # jog pad posts manualDrive
    assert "manualMove" in html        # distance move
    assert "manualStop" in html        # stop
    assert "cancelOrder" in html       # order cancel affordance
    assert 'name="armed"' in html      # arm toggle
    assert "/adapter/" in html and "/manual" in html  # heartbeat endpoint
    assert "ArrowUp" in html           # keyboard arrows in inline JS


def test_manual_enabled_false_for_non_jibot(hexplorer_spec):
    assert render._manual_enabled(hexplorer_spec) is False
```

> `jibot_spec`/`hexplorer_spec` fixture가 없으면 기존 test_web_render.py의 spec 생성 패턴(또는 `core.registry`의 `_jibot_spec`/hexplorer spec)을 재사용한다. 핵심: jibot spec은 `instant_actions`에 `manualDrive` 포함, hexplorer는 미포함.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd adaptor && python -m pytest tests/test_web_render.py -k manual -q`
Expected: FAIL — `AttributeError: module 'render' has no attribute 'manual_page'`.

- [ ] **Step 3: Add `_manual_enabled` + `manual_page`**

`adaptor/web/render.py` 끝부분(control 관련 헬퍼 인근)에 추가:

```python
def _manual_enabled(spec) -> bool:
    return any(getattr(a, "action_type", "") == "manualDrive" for a in spec.instant_actions)


_MANUAL_JS = """
<script>
(function(){
  var armed=false, beat=null, dir=null;
  var key=document.body.getAttribute('data-key');
  var csrf=document.getElementById('mc-csrf').value;
  function arm(){armed=document.getElementById('mc-arm').checked;}
  function send(type, extra){
    var b=new URLSearchParams();
    b.set('csrf_token',csrf); b.set('action_type',type);
    b.set('confirm','on'); if(armed) b.set('armed','on');
    if(extra){for(var k in extra) b.set(k, extra[k]);}
    return fetch('/adapter/'+encodeURIComponent(key)+'/manual',
      {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:b.toString()});
  }
  function startDir(d){
    if(!armed||dir===d) return; dir=d;
    var m={up:{trans:1},down:{trans:-1},left:{rot:1},right:{rot:-1}}[d]||{};
    var p={}; if('trans' in m)p.trans=(m.trans>0?'':'-')+Math.abs(0); // server uses config defaults
    // send a tick now and on interval; server fills speeds from config
    var payload={}; if('trans' in m)payload.trans=m.trans*1; if('rot' in m)payload.rot=m.rot*1;
    send('manualDrive', payload);
    if(beat) clearInterval(beat);
    beat=setInterval(function(){ if(armed&&dir) send('manualDrive', payload); }, %HEARTBEAT%);
  }
  function stop(){ dir=null; if(beat){clearInterval(beat); beat=null;} send('manualStop'); }
  window.addEventListener('load', function(){
    document.getElementById('mc-arm').addEventListener('change', arm); arm();
    var map={'mc-up':'up','mc-down':'down','mc-left':'left','mc-right':'right'};
    Object.keys(map).forEach(function(id){
      var el=document.getElementById(id); if(!el) return;
      el.addEventListener('mousedown', function(){startDir(map[id]);});
      el.addEventListener('mouseup', stop); el.addEventListener('mouseleave', stop);
      el.addEventListener('touchstart', function(e){e.preventDefault();startDir(map[id]);});
      el.addEventListener('touchend', stop);
    });
    var keys={ArrowUp:'up',ArrowDown:'down',ArrowLeft:'left',ArrowRight:'right'};
    document.addEventListener('keydown', function(e){ if(keys[e.key]){e.preventDefault(); startDir(keys[e.key]);} });
    document.addEventListener('keyup', function(e){ if(keys[e.key]){e.preventDefault(); stop();} });
    window.addEventListener('blur', stop);
    document.addEventListener('visibilitychange', function(){ if(document.hidden) stop(); });
  });
})();
</script>
"""


def manual_page(spec, csrf: str, q: dict, snapshot=None) -> str:
    key = esc(spec.key)
    ret = f"/adapter/{key}/manual"
    arm = (
        '<label class="confirm"><input type="checkbox" id="mc-arm" name="armed"> '
        'Arm (수동모드 무장)</label>'
    )
    pad = (
        '<div class="manual-pad">'
        '<button type="button" id="mc-up" class="btn">▲</button>'
        '<div class="manual-row">'
        '<button type="button" id="mc-left" class="btn">◀</button>'
        '<button type="button" id="mc-right" class="btn">▶</button></div>'
        '<button type="button" id="mc-down" class="btn">▼</button>'
        '<p class="muted">누르는 동안 주행, 떼면 정지 (키보드 화살표키도 동작)</p></div>'
    )
    move = _command_form(
        ret, csrf, ret,
        '<input type="hidden" name="action_type" value="manualMove">'
        '<input type="hidden" name="armed" value="on">',
        title="Move distance", desc="상대 거리 이동 (mm, 음수=후진)", label="Go",
        variant="primary", confirm=True,
        fields='<span class="command-fields">'
               '<input type="number" name="distance" placeholder="distance mm" required>'
               '<input type="number" name="speed" placeholder="speed"></span>',
    )
    stop = _command_form(
        ret, csrf, ret, '<input type="hidden" name="action_type" value="manualStop">',
        title="Stop", desc="즉시 정지 (UmStop)", label="STOP", variant="danger",
    )
    motor = _command_form(
        ret, csrf, ret, '<input type="hidden" name="action_type" value="enableMotor">',
        title="Enable motor", desc="모터 OFF면 이동이 무시될 수 있음", label="motor ON",
        variant="warning", confirm=True,
    )
    cancel = _command_form(
        f"/adapter/{key}/action", csrf, ret,
        '<input type="hidden" name="action_type" value="cancelOrder">',
        title="Cancel order", desc="수동 전환 전 진행 중 오더 취소", label="cancel order",
        variant="warning", confirm=True,
    )
    motor_state = _motor_label(snapshot) if snapshot is not None else "모터 상태 미상"
    body = (
        f'<div class="manual" data-key="{key}">'
        f'<input type="hidden" id="mc-csrf" value="{esc(csrf)}">'
        f'{_flash(q)}'
        f'<p><a href="/adapter/{key}/control">← 제어</a></p>'
        f'<section class="command-group"><div class="command-head"><h2>Manual control (jog)</h2></div>'
        f'<div class="command-body">{arm}{pad}<p class="muted">{esc(motor_state)}</p></div></section>'
        f'{_command_group("Move (distance)", move)}'
        f'{_command_group("Safety", stop + motor)}'
        f'{_command_group("Order", cancel)}'
        f'</div>'
        + _MANUAL_JS.replace("%HEARTBEAT%", "300")
    )
    return page(f"{spec.display_name} — manual", body, current="")
```

> `data-key`는 `<div class="manual">`에 두지만 JS는 `document.body.getAttribute('data-key')`를 읽으므로, body에도 키를 싣기 위해 `manual_page`의 wrapper를 body로 쓰거나 JS의 셀렉터를 `.manual`로 바꾼다. 단순화를 위해 JS 첫 줄을 `var key=document.querySelector('.manual').getAttribute('data-key');`로 교체한다.

- [ ] **Step 4: Fix the JS key selector**

위 `_MANUAL_JS`에서 `var key=document.body.getAttribute('data-key');` 를 다음으로 교체:

```javascript
  var key=document.querySelector('.manual').getAttribute('data-key');
```

- [ ] **Step 5: Add GET route**

`adaptor/web/server.py` `_dispatch_get`(line ~360, control 라우트 뒤)에 추가:

```python
        if len(parts) == 3 and parts[0] == "adapter" and parts[2] == "manual":
            spec = self._specs.get(parts[1])
            if spec is None or not render._manual_enabled(spec):
                h._html(404, render.page("not found", "<p>manual control unavailable</p>"))
                return
            snap = self._monitor_snapshot(spec.key)
            h._html(200, render.manual_page(spec, self._csrf, q, snapshot=snap))
            return
```

- [ ] **Step 6: Link from control page**

`adaptor/web/render.py` `control_page` 본문에 manual 링크 추가(`_manual_enabled(spec)`일 때만). control_page가 호출하는 본문 빌더(`_control_forms` 결과 인근)에서, JIBOT일 때 상단에 한 줄:

```python
    manual_link = (
        f'<p><a class="btn" href="/adapter/{esc(spec.key)}/manual">수동 컨트롤 (jog)</a></p>'
        if _manual_enabled(spec) else ""
    )
```

그리고 control_page가 만드는 body 문자열 앞에 `manual_link`를 끼워 넣는다(정확한 삽입 지점은 control_page 본문 구성 직전).

- [ ] **Step 7: Run render tests + full web suite**

Run: `cd adaptor && python -m pytest tests/test_web_render.py tests/test_web_server.py -q`
Expected: PASS.

- [ ] **Step 8: Manual smoke (optional, no robot needed)**

Run: `cd adaptor && python -c "from web import render; from core.registry import build_registry; from config.config import get_config; specs=build_registry(get_config()); s=[x for x in specs if any(a.action_type=='manualDrive' for a in x.instant_actions)][0]; print('manualDrive' in render.manual_page(s,'t',{}))"`
Expected: prints `True`.

- [ ] **Step 9: Commit**

```bash
git add adaptor/web/render.py adaptor/web/server.py adaptor/tests/test_web_render.py
git commit -m "feat(web): JIBOT manual control page (jog pad + distance + order cancel)"
```

---

## Task 10: Docs — web-ui.md

**Files:**
- Modify: `docs/guide/web-ui.md`

- [ ] **Step 1: Add a manual control section**

`docs/guide/web-ui.md` §5(화면) 안 "제어" 뒤에 추가:

```markdown
### 수동 컨트롤 ( `/adapter/<key>/manual` ) — JIBOT 전용

운영자가 로봇을 직접 운전한다. 두 가지 방식:
- **조그(덱맨)**: 화살표키 또는 화면 ▲▼◀▶ 패드를 **누르는 동안** 주행, **떼면 즉시 정지**.
  내부적으로 `manualDrive`(UmDrive 연속 속도) instant action을 ~300ms마다 보내고, 어댑터
  워치독이 명령 단절 시 자동 정지한다. **Arm 토글을 켜야** 동작한다.
- **거리 이동**: distance(±mm)·speed 입력 후 Go → `manualMove`(상대 거리 이동, 자동 정지).

**Stop** 버튼(`manualStop`)은 항상 동작한다. 모터가 OFF면 이동이 무시될 수 있어 **모터 ON**
버튼(`enableMotor`)과 상태 표시를 둔다. **오더/work 진행 중에는 조그/거리이동이 거부**되므로,
**오더 취소**(`cancelOrder`) 버튼으로 먼저 중단한 뒤 수동으로 전환한다.

> 안전: WebUi는 소프트웨어 게이트일 뿐 E-stop/안전 PLC를 대체하지 않는다.
```

- [ ] **Step 2: Commit**

```bash
git add docs/guide/web-ui.md
git commit -m "docs(web-ui): document JIBOT manual control page"
```

---

## Self-Review

**1. Spec coverage:**
- 조그 UmDrive 덱맨 → Task 6. 거리이동 move → Task 2/3/7. 양쪽(instant action+WebUI) → Task 4/8/9.
- 워치독 → Task 6. 다층 게이팅(enabled/work/order, manualStop 항상 허용) → Task 5/6. 모터 상태+ON → Task 9/5.
- params 전달(#5) → Task 8. heartbeat 비감사(#6) → Task 8. lat(#2) → Task 1/6. 미구현 명시(#1) → 배경. WebUI 오더 취소(#8) → Task 9.
- config → Task 1. 문서 → Task 10. 모든 spec 요구에 task 대응.

**2. Placeholder scan:** 모든 코드 step에 실제 코드 포함. "검증 필요"는 placeholder가 아니라 명시된 실기 확인 항목(Global Constraints + Task 3 주석). 없음.

**3. Type consistency:** `build_move_route`/`move_distance` 시그니처 Task 2↔3↔7 일치. `_manual_blocked_reason`/`_arm_manual_drive_watchdog`/`_manual_drive_timeout`/`_cancel_manual_drive_watchdog` Task 5↔6 일치. FakeVehicle 카운터(`um_stop_calls`/`drive_calls`/`move_distance_calls`/`enable_motor_calls`) Task 5에서 정의, 6/7에서 사용. registry actionType ↔ 디스패치 ↔ render 문자열 일치(`manualDrive`/`manualMove`/`manualStop`/`enableMotor`/`cancelOrder`).

## 리스크
- 가변 `move` 실행 경로(UmSetRoutes+UmRoutes vs UmSchedulerThis)는 실기 검증 항목 — Task 3에 국한, 교체 시 한 메서드만 변경.
- `_post_manual`/`manual_page` 테스트는 기존 test_web_server/test_web_render 하니스에 의존 — 헬퍼 부재 시 기존 패턴으로 구성(각 Task에 명시).
