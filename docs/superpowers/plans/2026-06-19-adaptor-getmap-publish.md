# Adaptor getMap Publish (unified-amr-adaptor) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** unified-amr-adaptor가 `getMap` VDA5050 instant-action을 받으면 JIBOT 맵을 fetch → `uamap.core.v1`로 변환 → `requestId`/`generatedAt`를 실어 `amr/v3/{serial}/map` MQTT 토픽에 publish 한다.

**Architecture:** 핵심 로직을 테스트 가능한 모듈 `adaptor/amr_map_publish.py`에 둔다(순수 함수 + collaborator를 인자로 받는 async 함수). 큰 `adapter_jibot.py`에는 dispatch case 1개와 얇은 글루 메서드만 추가한다. 이렇게 하면 정확성은 fake vehicle/mqtt로 단위 테스트되고, Adapter 결합은 최소화된다.

**Tech Stack:** Python 3, asyncio, paho-mqtt, pytest. VDA5050 v3 메시지(`adaptor/protocol/vda5050_3_0/messages.py`).

> 이 plan은 **Phase A의 2번 plan**. 선행 Plan 1(wcs API diff/prune core) 완료. 후속: Plan 3(fabris requestMap+bridge), Plan 4(wcs subscriber+mutation), Plan 5(wcs-ui 팝업). 본 plan은 fake collaborator로 단위 검증 완결, 라이브 JIBOT 불필요.
> 상세 설계: spec 결정 D2(adaptor는 자기 VDA5050 namespace로 publish, prefix 수정 안 함)·D3(requestId/generatedAt/non-retained).

## Global Constraints

- **publish 토픽**: `self._mqtt.publish("map", payload, qos=1, retain=False)` → `cls_mqtt.publish`가 `topic_prefix`(`amr/{vda_version}/{serial}`)를 prepend → 최종 `amr/v3/{serial}/map`. **non-retained, QoS1**(D3).
- **adaptor publish prefix 수정 금지**(D2): `cls_mqtt.publish`의 `use_prefix` 미동작은 건드리지 않음. 자기 namespace 그대로 사용.
- **requestId/generatedAt echo**(D3): 발행 payload는 `from_jibot_snapshot` 결과(uamap.core.v1) + top-level `requestId`, `generatedAt`(ISO8601 UTC, `...Z`).
- **requestId 추출**: getMap action의 `action_parameters`에서 key `requestId` 우선, 없으면 `action.action_id` fallback.
- **테스트 비동기**: `pytest-asyncio` 의존 금지 — async 함수는 테스트에서 `asyncio.run(...)`으로 실행.
- **docstring**: 신규 함수/모듈에 docstring(영문 또는 영/한 혼용, 기존 `common_amr_map.py` 스타일).
- **커밋**: 본 plan은 구현·검증만. 커밋은 사용자 결정(브랜치 `jibot-client-refactor`에 무관한 미커밋 변경 다수 → 분리 필요). 절대 `git add -A`/`.` 금지, 대상 파일만.
- **테스트 명령**: `adaptor/` 디렉토리에서 `python -m pytest tests/<file> -v` (pyproject `pythonpath=["."]`, `testpaths=["tests"]`). 프로젝트가 venv/uv를 쓰면 그 활성화 하에 동일 실행.

## File Structure

- Create `adaptor/amr_map_publish.py` — `MAP_TOPIC`, `extract_request_id()`, `build_map_message()`, `get_map_and_publish()`.
- Create `adaptor/tests/test_amr_map_publish.py` — 단위 테스트(fake vehicle/mqtt, asyncio.run).
- Modify `adaptor/adapter_jibot.py` — `instant_actions_accept_procedure`에 `getMap` case + `_handle_get_map_instant_action()` 얇은 글루.

---

### Task 1: 테스트 가능한 코어 모듈 `amr_map_publish.py`

**Files:**
- Create: `adaptor/amr_map_publish.py`
- Test: `adaptor/tests/test_amr_map_publish.py`

**Interfaces:**
- Consumes: `from_jibot_snapshot(snapshot: dict, raw_ref=None) -> dict` from `common_amr_map.py`.
- Produces:
  - `MAP_TOPIC = "map"`
  - `extract_request_id(action_parameters, action_id) -> str`
  - `build_map_message(raw_map: dict, map_id: str, request_id: str, generated_at: str) -> dict`
  - `async get_map_and_publish(vehicle, mqtt, *, map_id, request_id, generated_at) -> bool`

- [ ] **Step 1: Write failing test** — `adaptor/tests/test_amr_map_publish.py`

```python
"""amr_map_publish 코어 단위 테스트(fake collaborator)."""
import asyncio
import unittest

from amr_map_publish import (
    MAP_TOPIC,
    build_map_message,
    extract_request_id,
    get_map_and_publish,
)

# 최소 JIBOT UmGetMap raw fixture(노드 2개)
RAW_MAP = {
    "Header": "umcl-map",
    "MapName": "lab2m",
    "MapRes": 20,
    "MinPose": "0 0",
    "MaxPose": "20000 10000",
    "Objs": {
        "Goal": [{"name": "F1_40", "pose": "5953 4854 0.00", "allowPassingThrough": False}],
        "PathPoint": [{"name": "p1", "pose": "1000 1000 0.00", "vertex": ""}],
    },
}


class FakeParam:
    def __init__(self, key, value):
        self.key = key
        self.value = value


class FakeVehicle:
    """async get_map + _map_raw 보유 fake."""
    def __init__(self, raw):
        self._map_raw = raw
        self.get_map_called = 0

    async def get_map(self):
        self.get_map_called += 1
        return {}


class FakeMqtt:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False, use_prefix=True):
        self.published.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})


class ExtractRequestIdTest(unittest.TestCase):
    def test_param_우선(self):
        params = [FakeParam("mapId", "lab2m"), FakeParam("requestId", "req-9")]
        self.assertEqual(extract_request_id(params, "act-1"), "req-9")

    def test_param_없으면_action_id_fallback(self):
        params = [FakeParam("mapId", "lab2m")]
        self.assertEqual(extract_request_id(params, "act-1"), "act-1")


class BuildMapMessageTest(unittest.TestCase):
    def test_uamap_변환_및_envelope(self):
        msg = build_map_message(RAW_MAP, map_id="lab2m", request_id="req-9", generated_at="2026-06-19T00:00:00Z")
        self.assertEqual(msg["schemaVersion"], "uamap.core.v1")
        self.assertEqual(msg["map"]["mapId"], "lab2m")
        self.assertEqual(msg["requestId"], "req-9")
        self.assertEqual(msg["generatedAt"], "2026-06-19T00:00:00Z")
        self.assertTrue(any(n["id"] == "F1_40" for n in msg["graph"]["nodes"]))


class GetMapAndPublishTest(unittest.TestCase):
    def test_fetch_후_map_토픽_publish(self):
        vehicle = FakeVehicle(RAW_MAP)
        mqtt = FakeMqtt()
        ok = asyncio.run(get_map_and_publish(vehicle, mqtt, map_id="lab2m", request_id="req-9", generated_at="2026-06-19T00:00:00Z"))
        self.assertTrue(ok)
        self.assertEqual(vehicle.get_map_called, 1)
        self.assertEqual(len(mqtt.published), 1)
        pub = mqtt.published[0]
        self.assertEqual(pub["topic"], MAP_TOPIC)
        self.assertEqual(pub["qos"], 1)
        self.assertFalse(pub["retain"])
        self.assertEqual(pub["payload"]["requestId"], "req-9")

    def test_raw_없으면_publish_안함(self):
        vehicle = FakeVehicle(None)
        mqtt = FakeMqtt()
        ok = asyncio.run(get_map_and_publish(vehicle, mqtt, map_id="lab2m", request_id="req-9", generated_at="t"))
        self.assertFalse(ok)
        self.assertEqual(len(mqtt.published), 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `adaptor/`): `python -m pytest tests/test_amr_map_publish.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'amr_map_publish'`.

- [ ] **Step 3: Create `adaptor/amr_map_publish.py`**

```python
"""getMap instant-action 처리: JIBOT 맵을 공통 맵으로 변환해 MQTT publish.

발행 토픽은 cls_mqtt가 prefix(amr/{vda_version}/{serial})를 붙여
amr/v3/{serial}/map 이 된다(non-retained). 페이로드는 uamap.core.v1 +
top-level requestId/generatedAt(요청-응답 correlation, spec D3).
"""
from typing import Any, Iterable, Optional

from common_amr_map import from_jibot_snapshot

# cls_mqtt가 prefix를 붙이므로 subtopic만 지정 → amr/v3/{serial}/map
MAP_TOPIC = "map"


def extract_request_id(action_parameters: Iterable[Any], action_id: str) -> str:
    """getMap action에서 requestId를 추출한다.

    action_parameters에 key 'requestId'가 있으면 그 value(str), 없으면 action_id.
    """
    for param in action_parameters or []:
        if getattr(param, "key", None) == "requestId" and getattr(param, "value", None) is not None:
            return str(param.value)
    return str(action_id)


def build_map_message(raw_map: dict, map_id: str, request_id: str, generated_at: str) -> dict:
    """JIBOT raw UmGetMap을 uamap.core.v1로 변환하고 correlation 필드를 덧붙인다.

    Args:
        raw_map: JIBOT UmGetMap 응답 dict(vehicle._map_raw).
        map_id: 맵 id.
        request_id: 요청 correlation id(echo).
        generated_at: 생성 시각(ISO8601 UTC, ...Z).

    Returns: uamap.core.v1 dict + top-level requestId/generatedAt.
    """
    common = from_jibot_snapshot({"raw": raw_map, "mapId": map_id}, raw_ref="runtime/jibot-map.json")
    common["requestId"] = request_id
    common["generatedAt"] = generated_at
    return common


async def get_map_and_publish(vehicle: Any, mqtt: Any, *, map_id: str, request_id: str, generated_at: str) -> bool:
    """JIBOT 맵을 fetch해 map 토픽에 publish 한다.

    vehicle.get_map()을 await한 뒤 vehicle._map_raw로 변환·발행.
    raw가 없으면 발행하지 않고 False 반환.
    """
    await vehicle.get_map()
    raw = getattr(vehicle, "_map_raw", None)
    if not raw:
        print(f"[getMap] no raw map for request {request_id}; skip publish")
        return False
    message = build_map_message(raw, map_id, request_id, generated_at)
    mqtt.publish(MAP_TOPIC, message, qos=1, retain=False)
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `adaptor/`): `python -m pytest tests/test_amr_map_publish.py -v`
Expected: PASS (6 tests).

> 커밋하지 않음. Phase 종료 시 사용자 결정.

---

### Task 2: `adapter_jibot.py`에 getMap 디스패치 + 글루 배선

**Files:**
- Modify: `adaptor/adapter_jibot.py`

**Interfaces:**
- Consumes: `extract_request_id`, `get_map_and_publish` (Task 1); 기존 `self._run_on_adapter_loop(coro_factory)`, `self._mqtt`, Adapter의 JIBOT vehicle 참조, `self.config`.
- Produces: `getMap` action 처리(dispatch + `_handle_get_map_instant_action`).

**DISCOVERY (구현 시작 전 파일에서 확인):**
1. `instant_actions_accept_procedure(self, instant_actions_request)` 내 `for action in ...: if action.action_type == "...":` dispatch 위치(약 line 2183-2261). 새 `elif action.action_type == "getMap":` 분기를 추가할 지점.
2. **Adapter가 보유한 JIBOT vehicle 객체의 attribute 이름**(예: `self._jibot`/`self._vehicle`/`self.vehicle` 등). `get_map`/`_map_raw`를 가진 객체. `robot_info_loop`에 넘기는 vehicle이 Adapter 어디에 보관되는지 확인해 정확한 `self.<attr>`로 참조.
3. `self.config`에서 활성 map id 경로(`self.config.settings.map_id`).
4. action 완료 상태 표기 방식(기존 `_handle_*_instant_action`들이 action을 FINISHED 등으로 표기/리포트하는 패턴) — 동일 패턴 사용.
5. import 위치: 파일 상단 import 블록에 `from amr_map_publish import extract_request_id, get_map_and_publish` 추가.

- [ ] **Step 1: Add dispatch case + handler**

`instant_actions_accept_procedure`의 dispatch 체인에 추가:
```python
            elif action.action_type == "getMap":
                self._handle_get_map_instant_action(action)
```

새 메서드(기존 `_handle_*_instant_action`들 근처에 추가). 정확한 vehicle attr은 DISCOVERY 2에서 확인한 이름으로 치환:
```python
    def _handle_get_map_instant_action(self, action: Any) -> None:
        """getMap instant-action 처리: requestId/mapId 추출 후 비동기 fetch+publish 스케줄.

        실제 맵 fetch·발행은 adapter event loop에서 비동기로 수행한다.
        """
        import datetime

        request_id = extract_request_id(getattr(action, "action_parameters", []), getattr(action, "action_id", ""))
        map_id = self.config.settings.map_id
        for param in getattr(action, "action_parameters", []) or []:
            if getattr(param, "key", None) == "mapId" and getattr(param, "value", None):
                map_id = str(param.value)
                break
        generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

        vehicle = self._jibot  # DISCOVERY 2: 실제 JIBOT vehicle attribute로 치환
        self._run_on_adapter_loop(
            lambda: get_map_and_publish(vehicle, self._mqtt, map_id=map_id, request_id=request_id, generated_at=generated_at)
        )
        # 기존 _handle_*와 동일하게 action 수락/완료 상태 표기(DISCOVERY 4 패턴)
```

- [ ] **Step 2: 구문/임포트 검증 (대상 파일 컴파일)**

Run (from `adaptor/`): `python -c "import ast,sys; ast.parse(open('adapter_jibot.py').read()); print('syntax ok')"`
Expected: `syntax ok`. (전체 import 체인 실행이 무거우면 ast 파싱으로 구문만 확인. 가능하면 `python -c "import adapter_jibot"`도 시도)

- [ ] **Step 3: 회귀 — 코어 + 인접 테스트 재실행**

Run (from `adaptor/`): `python -m pytest tests/test_amr_map_publish.py tests/test_common_amr_map.py -v`
Expected: 모두 PASS(코어 6 + 기존 common map 테스트).

> 커밋하지 않음.

---

### Task 3: 전체 adaptor 테스트 검증

- [ ] **Step 1: 전체 스위트**

Run (from `adaptor/`): `python -m pytest tests/ -q`
Expected: 신규 포함 전부 PASS(기존 테스트 회귀 0). 실패 시 원인(신규 코드 관련만) 수정 후 재실행.

- [ ] **Step 2: 변경 파일 확인(커밋 준비, add는 사용자 승인 후)**

Run: `cd /home/lab2m-llm1/workspaces/unified-amr-adaptor && git status --short -- adaptor/amr_map_publish.py adaptor/tests/test_amr_map_publish.py adaptor/adapter_jibot.py`
> 커밋은 사용자 결정. 대상 파일만 명시 add(브랜치의 무관 jibot-client-refactor 변경과 섞지 말 것).

---

## Self-Review

**1. Spec coverage (D2·D3):**
- D2 adaptor 자기 namespace publish + prefix 미수정: `MAP_TOPIC="map"` + cls_mqtt prefix, publish 수정 없음 ✓
- D3 requestId/generatedAt echo + non-retained: `build_map_message` envelope + `qos=1, retain=False` ✓ / requestId 추출(param→action_id fallback) ✓
- getMap fetch→convert→publish: Task 1 `get_map_and_publish` + Task 2 dispatch ✓
- 후속(setMap/apply=Plan 3+ / wcs subscribe=Plan 4)은 범위 밖 — 상단 명시 ✓

**2. Placeholder scan:** Task 1은 완전 코드. Task 2는 `self._jibot` 한 곳이 DISCOVERY 의존(대형 파일의 실제 attr 확인 필요) — placeholder가 아니라 "파일에서 확인할 정확한 1개 심볼"로 명시·범위 한정. dispatch/handler 로직은 완전.

**3. Type consistency:** `extract_request_id(action_parameters, action_id)`·`get_map_and_publish(vehicle, mqtt, *, map_id, request_id, generated_at)`·`build_map_message(raw_map, map_id, request_id, generated_at)` 시그니처가 Task 1 정의와 Task 2 호출에서 일치. `from_jibot_snapshot({"raw":..., "mapId":...})` 입력 형태는 기존 테스트(`test_common_amr_map.py`)의 snapshot 형태와 일치.

**검증 리스크(실행 시 확인):** Task 2의 vehicle attribute 실제 이름, action 완료 상태 표기 패턴 — 구현자가 파일에서 확인(DISCOVERY). `python -m pytest` 실행 환경(venv/uv) — 프로젝트 표준 활성화 하에 실행.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-06-19-adaptor-getmap-publish.md`. Subagent-Driven으로 실행: Task1(코어, 경량 모델) → 리뷰 → Task2(Adapter 배선, 표준 모델: DISCOVERY 필요) → 리뷰 → Task3 검증. 커밋은 사용자 결정.
