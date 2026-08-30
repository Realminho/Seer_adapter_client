# JIBOT Startup Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Report JIBOT as `STARTUP` until initial required telemetry is known, so missing startup battery data is not misreported as low battery.

**Architecture:** Track whether JIBOT has received its first battery value in the client. The adapter derives startup state from a small pending-reason checklist, reports `OperatingMode.STARTUP` while pending, uses a configurable fallback SOC only while battery is unknown, and exposes the pending reasons in `information`.

**Tech Stack:** Python, unittest, existing VDA5050 v2/v3 message dataclasses, existing adapter config TOML loader.

---

### Task 1: Battery Known Flag

**Files:**
- Modify: `jibot-client/src/jibot_client/client.py`
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

- [ ] **Step 1: Write the failing tests**

Add tests that create a `FakeVehicle` with `_battery = None` and `_battery_known = False`, then verify the adapter reports `STARTUP`, fallback SOC, and diagnostic references. Add a second test that flips `_battery_known = True` and verifies `AUTOMATIC` with the real SOC.

- [ ] **Step 2: Run tests to verify RED**

Run: `PYTHONPATH=adaptor:jibot-client/src python -m unittest adaptor.tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_startup_mode_uses_fallback_battery_until_first_battery_reading adaptor.tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_first_battery_reading_clears_startup_mode -v`

Expected: FAIL because the adapter still clamps unknown battery to `0.0` and does not report startup diagnostics.

- [ ] **Step 3: Implement minimal client state**

Change JIBOT startup battery state from `_battery = 0` to `_battery = None`, add `_battery_known = False`, and set `_battery_known = True` only when a status snapshot contains `battery`.

- [ ] **Step 4: Run focused tests**

Run the two focused tests again. Expected: still FAIL until adapter startup behavior is implemented.

### Task 2: Adapter Startup Derivation

**Files:**
- Modify: `adaptor/adapter_jibot.py`
- Modify: `adaptor/config/config.py`
- Modify: `adaptor/config/config.toml`
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

- [ ] **Step 1: Add config defaults**

Add `startup_battery_soc: float = 100.0` and `require_battery_before_ready: bool = True` to `JibotClientConfig`, and document them in `[jibot_client]`.

- [ ] **Step 2: Implement startup helpers**

Add `_startup_pending_reasons()`, `_is_startup_initializing()`, `_get_startup_battery_soc()`, and update `_derive_operating_mode()` so pending reasons produce `OperatingMode.STARTUP`; otherwise it returns `AUTOMATIC`.

- [ ] **Step 3: Use fallback battery only while unknown**

Update `_get_vehicle_battery_charge()` to return configured fallback SOC when battery is unknown. Keep real `0` as real `0` once `_battery_known` is true.

- [ ] **Step 4: Surface diagnostics**

Add `adapterInitializing`, `adapterInitPending`, and `jibotBatteryKnown` references to the existing `JIBOT_STATUS` information record.

- [ ] **Step 5: Run focused tests**

Run the two focused tests. Expected: PASS.

### Task 3: Regression Verification

**Files:**
- Verify: `adaptor/tests/test_adapter_jibot_v3_order.py`
- Verify: `adaptor/tests/test_configio.py`

- [ ] **Step 1: Run JIBOT adapter test module**

Run: `PYTHONPATH=adaptor:jibot-client/src python -m unittest adaptor.tests.test_adapter_jibot_v3_order -v`

Expected: PASS.

- [ ] **Step 2: Run config tests**

Run: `PYTHONPATH=adaptor:jibot-client/src python -m unittest adaptor.tests.test_configio -v`

Expected: PASS.
