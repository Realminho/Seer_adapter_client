# Order Node Goto Rejection Diagnostic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the robot rejects or never acknowledges an order node's `UmGoto`, surface a `JIBOT_GOTO_REJECTED` FATAL error and hold the order, instead of waiting forever for an arrival that never happens.

**Architecture:** Fix the JIBOT client response filter so an `#CMD#:"error"` frame can be returned (opt-in). Then have the order worker await the `UmGoto` response, classify it with the existing `_jibot_command_rejection_reason`, and on rejection set a FATAL error + log + return `False` (soft hold; the FM re-sends to resume).

**Tech Stack:** Python 3.12, asyncio, `unittest` (offline tests with a `FakeVehicle`; no broker/robot).

Spec: `docs/superpowers/specs/2026-06-20-order-goto-rejection-diagnostic-design.md`

## Global Constraints

- `accept_errors` defaults to **`False`** — existing waiters (`get_map`, `get_battery_info`, status polls) must keep skipping stray `error` frames and falling back to `None`. No regression.
- New error: `errorType = JIBOT_GOTO_REJECTED`, `errorLevel = FATAL`.
- Goto ack timeout: **3.0s** (matches the order-action path).
- Recovery is **FM-driven**: on rejection the worker stops; it restarts only on order/orderUpdate. No stay-alive auto-retry loop.
- Reuse, do not reinvent: classify with the existing `_jibot_command_rejection_reason`; mirror the "filter same-type then append" pattern of `_set_jibot_node_unreached_error`.
- Out of scope: charge-route (`call_routes`) ack checking; `goto_node_position` on non-JIBOT vehicles; skipping goto when already in the reach zone.
- Run tests from the `adaptor/` directory: `cd adaptor && python -m pytest <path> -v`.

## File Structure

- `jibot-client/src/jibot_client/client.py` — add `accept_errors` opt-in to `wait_for_response` / `send_command_and_wait`. (Task 1)
- `adaptor/tests/test_jibot_client_wait_for_response.py` — client filter regression tests. (Task 1)
- `adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py` — add `JIBOT_GOTO_REJECTED` enum member. (Task 2)
- `adaptor/adapter_jibot.py` — `_await_goto_ack`, `_send_node_motion` return value, `_process_v3_node_step` wiring, `_set_jibot_goto_rejected_error`, `_clear_jibot_goto_rejected_error`, and `_dispatch_jibot_command` accept_errors. (Tasks 2 & 3)
- `adaptor/tests/test_adapter_jibot_v3_order.py` — `FakeVehicle.wait_for_response`, rejection/no-ack/accepted tests, `_dispatch` test. (Tasks 2 & 3)

---

### Task 1: Client returns `error` frames when asked (Step 0, foundational)

**Files:**
- Modify: `jibot-client/src/jibot_client/client.py` (`wait_for_response` ~`:498-527`, `send_command_and_wait` ~`:490-496`)
- Test: `adaptor/tests/test_jibot_client_wait_for_response.py`

**Interfaces:**
- Produces: `wait_for_response(command=None, timeout=3.0, accept_errors=False)` and `send_command_and_wait(command, gap=-1, timeout=3.0, accept_errors=False, **params)`. When `accept_errors=True`, a frame whose `#CMD#` equals the awaited command **or** equals `"error"` is returned.

- [ ] **Step 1: Write the failing tests**

Add these three methods to `WaitForResponseTimeoutTest` in `adaptor/tests/test_jibot_client_wait_for_response.py`:

```python
    def test_error_frame_returned_when_accept_errors(self) -> None:
        async def scenario() -> None:
            client = self._make_client()
            client._response_queue.put_nowait({"#CMD#": "error", "msg": "robot in stop mode"})
            result = await client.wait_for_response(
                command="UmGoto", timeout=0.05, accept_errors=True
            )
            self.assertEqual(result, {"#CMD#": "error", "msg": "robot in stop mode"})

        asyncio.run(scenario())

    def test_error_frame_skipped_and_requeued_by_default(self) -> None:
        async def scenario() -> None:
            client = self._make_client()
            client._response_queue.put_nowait({"#CMD#": "error", "msg": "x"})
            result = await client.wait_for_response(command="UmGoto", timeout=0.05)
            self.assertIsNone(result)
            # Default behavior unchanged: the error frame is left for other readers.
            self.assertEqual(
                client._response_queue.get_nowait(), {"#CMD#": "error", "msg": "x"}
            )

        asyncio.run(scenario())

    def test_accept_errors_still_returns_normal_match(self) -> None:
        async def scenario() -> None:
            client = self._make_client()
            client._response_queue.put_nowait({"#CMD#": "UmGoto", "result": "accepted"})
            result = await client.wait_for_response(
                command="UmGoto", timeout=0.05, accept_errors=True
            )
            self.assertEqual(result, {"#CMD#": "UmGoto", "result": "accepted"})

        asyncio.run(scenario())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd adaptor && python -m pytest tests/test_jibot_client_wait_for_response.py -v`
Expected: `test_error_frame_returned_when_accept_errors` FAILS — `wait_for_response()` got an unexpected keyword argument `accept_errors` (TypeError). The other two also error on the unexpected kwarg.

- [ ] **Step 3: Add the `accept_errors` parameter to `wait_for_response`**

In `jibot-client/src/jibot_client/client.py`, change the signature and the match condition:

```python
    async def wait_for_response(self, command=None, timeout=3.0, accept_errors=False):
```

and replace the match line inside the loop:

```python
                if command is None or response.get("#CMD#") == command:
                    return response
```

with:

```python
                if (
                    command is None
                    or response.get("#CMD#") == command
                    or (accept_errors and response.get("#CMD#") == "error")
                ):
                    return response
```

- [ ] **Step 4: Thread `accept_errors` through `send_command_and_wait`**

Change `send_command_and_wait` in the same file:

```python
    async def send_command_and_wait(self, command, gap=-1, timeout=3.0, accept_errors=False, **params):
        """Send a command and wait for the next matching #CMD# response.

        명령을 전송하고 같은 #CMD# 값을 가진 다음 응답을 기다린다.
        """
        await self.send_command(command, gap=gap, **params)
        return await self.wait_for_response(
            command=command, timeout=timeout, accept_errors=accept_errors
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd adaptor && python -m pytest tests/test_jibot_client_wait_for_response.py -v`
Expected: PASS (all methods, including the four pre-existing ones).

- [ ] **Step 6: Commit**

```bash
git add jibot-client/src/jibot_client/client.py adaptor/tests/test_jibot_client_wait_for_response.py
git commit -m "feat(jibot-client): opt-in accept_errors so UmGoto rejects are returned

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Order worker surfaces a rejected/unacknowledged node goto

**Files:**
- Modify: `adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py` (`ErrorType` enum, ~`:45`)
- Modify: `adaptor/adapter_jibot.py` (`_send_node_motion`, `_process_v3_node_step`, new helpers near `_set_jibot_node_unreached_error` ~`:1154`)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Consumes (Task 1): `vehicle.wait_for_response(command="UmGoto", accept_errors=True, timeout=3.0)`.
- Consumes (existing): `self._jibot_command_rejection_reason(command, response) -> Optional[str]` (returns `None` when accepted, a reason string on error/no-ack).
- Produces: `_send_node_motion(node) -> Optional[str]` (rejection reason or `None`); `_set_jibot_goto_rejected_error(node, reason)`; `_clear_jibot_goto_rejected_error()`.

- [ ] **Step 1: Add `wait_for_response` to the test `FakeVehicle`**

In `adaptor/tests/test_adapter_jibot_v3_order.py`, in `FakeVehicle.__init__`, add a default-accepted response (place it next to `self.goto_targets = []`):

```python
        self.goto_response: Optional[Dict[str, Any]] = {"#CMD#": "UmGoto", "result": "accepted"}
```

and add this method to `FakeVehicle` (next to `goto_xyz`):

```python
    async def wait_for_response(self, command=None, timeout=3.0, accept_errors=False):
        return self.goto_response
```

Also extend the test imports — change:

```python
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionState, ActionStatus, ErrorType
```

to:

```python
from protocol.vda_2_0_0.vda5050_2_0_0_state import (
    ActionState,
    ActionStatus,
    Error,
    ErrorLevel,
    ErrorType,
)
```

- [ ] **Step 2: Write the failing tests**

Add these three methods to the order-worker test class (the class that defines `_make_adapter` / `_start_state`):

```python
    def test_order_node_goto_rejected_sets_fatal_error_and_holds(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._mode = "Stop"
            vehicle._status = "Stopped"
            vehicle.goto_response = {"#CMD#": "error", "msg": "robot in stop mode"}
            state_task = await self._start_state(adapter)

            order = Order.from_dict(
                {
                    "headerId": 50,
                    "timestamp": "2026-06-20T00:00:00.000Z",
                    "version": "3.0.0",
                    "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-001",
                    "orderId": "goto-reject",
                    "orderUpdateId": 0,
                    "nodes": [
                        {
                            "nodeId": "N1",
                            "sequenceId": 0,
                            "released": True,
                            "nodePosition": {
                                "x": 5000.0,
                                "y": 5000.0,
                                "theta": 0.0,
                                "mapId": "lab2m",
                                "allowedDeviationXY": 5.0,
                            },
                            "actions": [],
                        }
                    ],
                    "edges": [],
                }
            )

            adapter._handle_v3_order(order)
            await asyncio.wait_for(adapter.order_worker_task, timeout=1.0)

            rejected = [
                e
                for e in adapter.state.errors
                if e.error_type == ErrorType.JIBOT_GOTO_REJECTED
            ]
            self.assertEqual(len(rejected), 1)
            self.assertEqual(rejected[0].error_level, ErrorLevel.FATAL)
            refs = {
                r.reference_key: r.reference_value
                for r in rejected[0].error_references
            }
            self.assertEqual(refs["nodeId"], "N1")
            self.assertEqual(refs["command"], "UmGoto")
            self.assertEqual(refs["jibotMode"], "Stop")
            self.assertIn("robot in stop mode", refs["reason"])
            # Soft hold: the node is NOT cleared.
            self.assertTrue(adapter.state.node_states)

            state_task.cancel()

        asyncio.run(scenario())

    def test_order_node_goto_no_ack_sets_error(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle.goto_response = None  # robot stayed silent
            state_task = await self._start_state(adapter)

            order = Order.from_dict(
                {
                    "headerId": 51,
                    "timestamp": "2026-06-20T00:00:00.000Z",
                    "version": "3.0.0",
                    "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-001",
                    "orderId": "goto-noack",
                    "orderUpdateId": 0,
                    "nodes": [
                        {
                            "nodeId": "N1",
                            "sequenceId": 0,
                            "released": True,
                            "nodePosition": {
                                "x": 5000.0,
                                "y": 5000.0,
                                "theta": 0.0,
                                "mapId": "lab2m",
                                "allowedDeviationXY": 5.0,
                            },
                            "actions": [],
                        }
                    ],
                    "edges": [],
                }
            )

            adapter._handle_v3_order(order)
            await asyncio.wait_for(adapter.order_worker_task, timeout=1.0)

            rejected = [
                e
                for e in adapter.state.errors
                if e.error_type == ErrorType.JIBOT_GOTO_REJECTED
            ]
            self.assertEqual(len(rejected), 1)
            refs = {
                r.reference_key: r.reference_value
                for r in rejected[0].error_references
            }
            self.assertIn("no robot acknowledgement", refs["reason"])

            state_task.cancel()

        asyncio.run(scenario())

    def test_order_node_goto_accepted_clears_stale_reject_and_completes(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)

            # Pre-seed a stale rejection error from a previous attempt.
            adapter.state.errors.append(
                Error(
                    error_type=ErrorType.JIBOT_GOTO_REJECTED,
                    error_level=ErrorLevel.FATAL,
                    error_references=[],
                    error_description="stale",
                )
            )

            order = Order.from_dict(
                {
                    "headerId": 52,
                    "timestamp": "2026-06-20T00:00:00.000Z",
                    "version": "3.0.0",
                    "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-001",
                    "orderId": "goto-accept",
                    "orderUpdateId": 0,
                    "nodes": [
                        {
                            "nodeId": "N1",
                            "sequenceId": 0,
                            "released": True,
                            "nodePosition": {
                                "x": 5000.0,
                                "y": 5000.0,
                                "theta": 0.0,
                                "mapId": "lab2m",
                                "allowedDeviationXY": 5.0,
                            },
                            "actions": [],
                        }
                    ],
                    "edges": [],
                }
            )

            adapter._handle_v3_order(order)
            for _ in range(100):
                if vehicle.goto_targets:
                    break
                await asyncio.sleep(0.01)
            # Report arrival so the accepted goto completes.
            vehicle._station = "N1"
            vehicle._status = "Stopped"
            await asyncio.wait_for(adapter.order_worker_task, timeout=1.0)

            self.assertEqual(adapter.state.last_node_id, "N1")
            self.assertEqual(
                [
                    e
                    for e in adapter.state.errors
                    if e.error_type == ErrorType.JIBOT_GOTO_REJECTED
                ],
                [],
            )

            state_task.cancel()

        asyncio.run(scenario())
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k "goto_rejected or goto_no_ack or goto_accepted_clears" -v`
Expected: FAIL — `AttributeError: JIBOT_GOTO_REJECTED` (enum member missing) on the first two; the third fails because the stale error is never cleared.

- [ ] **Step 4: Add the `JIBOT_GOTO_REJECTED` enum member**

In `adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py`, add the member directly after `JIBOT_NODE_UNREACHED`:

```python
    JIBOT_NODE_UNREACHED = "JIBOT_NODE_UNREACHED"  # JIBOT stopped before the target node was reached
    JIBOT_GOTO_REJECTED = "JIBOT_GOTO_REJECTED"  # JIBOT rejected or did not acknowledge a node goto
    EZI_IO_INPUT_FAILED = "EZI_IO_INPUT_FAILED"  # Adapter could not read EZI IO inputs
```

- [ ] **Step 5: Add the error set/clear helpers**

In `adaptor/adapter_jibot.py`, add these two methods immediately after `_set_jibot_node_unreached_error` (so they sit beside the pattern they mirror):

```python
    def _set_jibot_goto_rejected_error(self, node: Any, reason: str) -> None:
        """Surface a rejected/unacknowledged node goto as a FATAL error.

        Mirrors _set_jibot_node_unreached_error: the prior same-type error is
        filtered out before appending, so repeated rejections do not stack.
        """
        if self.state is None:
            return

        self.state.errors = [
            error
            for error in self.state.errors
            if getattr(error, "error_type", None) != ErrorType.JIBOT_GOTO_REJECTED
        ]
        self.state.errors.append(
            Error(
                error_type=ErrorType.JIBOT_GOTO_REJECTED,
                error_level=ErrorLevel.FATAL,
                error_references=[
                    ErrorReference("nodeId", str(getattr(node, "node_id", "") or "")),
                    ErrorReference("sequenceId", str(getattr(node, "sequence_id", "") or "")),
                    ErrorReference("command", "UmGoto"),
                    ErrorReference("reason", reason),
                    ErrorReference("jibotMode", str(getattr(self._vehicle, "_mode", "") or "")),
                    ErrorReference("jibotStatus", str(getattr(self._vehicle, "_status", "") or "")),
                ],
                error_description=(
                    "JIBOT rejected or did not acknowledge the order node goto; "
                    "the order is held until the fleet manager re-sends it."
                ),
            )
        )
        self.request_state_publish("goto rejected")

    def _clear_jibot_goto_rejected_error(self) -> None:
        """Drop a stale JIBOT_GOTO_REJECTED error once a goto is accepted."""
        if self.state is None:
            return

        before = len(self.state.errors)
        self.state.errors = [
            error
            for error in self.state.errors
            if getattr(error, "error_type", None) != ErrorType.JIBOT_GOTO_REJECTED
        ]
        if len(self.state.errors) != before:
            self.request_state_publish("goto rejected cleared")
```

- [ ] **Step 6: Make `_send_node_motion` await the goto ack and return a rejection reason**

In `adaptor/adapter_jibot.py`, change the `_send_node_motion` signature and add the ack wait. The method currently returns `None` implicitly; make it return `Optional[str]`.

Change the signature line:

```python
    async def _send_node_motion(self, node: Any) -> None:
```

to:

```python
    async def _send_node_motion(self, node: Any) -> Optional[str]:
```

In the simulator branch, change the bare `return` after the `goto_node_position`/`goto_xyz` emission. Replace:

```python
            else:
                await self._vehicle.goto_xyz(position.x, position.y, position.theta)
            return
```

with:

```python
            else:
                await self._vehicle.goto_xyz(position.x, position.y, position.theta)
            return await self._await_goto_ack(node)
```

In the charge-route branch, make the `return` explicit (charge routes are not UmGoto, so no ack check). Replace:

```python
            await self._vehicle.call_routes(route_name, "a")
            return
```

with:

```python
            await self._vehicle.call_routes(route_name, "a")
            return None
```

Change the final two lines of the method:

```python
        print(f"[ORDER NODE GOTO_POINT] seq={node.sequence_id} id={node.node_id}")
        await self._vehicle.goto_point(node.node_id)
```

to:

```python
        print(f"[ORDER NODE GOTO_POINT] seq={node.sequence_id} id={node.node_id}")
        await self._vehicle.goto_point(node.node_id)
        return await self._await_goto_ack(node)
```

Then add the `_await_goto_ack` helper immediately after `_send_node_motion`:

```python
    async def _await_goto_ack(self, node: Any) -> Optional[str]:
        """Wait for the robot's UmGoto response; return a rejection reason or None.

        The goto itself was already transmitted by the caller. A vehicle without
        wait_for_response (e.g. some simulators) cannot report a result, so the
        check is skipped (returns None) rather than failing the order.
        """
        wait_for_response = getattr(self._vehicle, "wait_for_response", None)
        if not callable(wait_for_response):
            return None
        response = await wait_for_response(
            command="UmGoto", accept_errors=True, timeout=3.0
        )
        return self._jibot_command_rejection_reason("UmGoto", response)
```

- [ ] **Step 7: Wire the rejection into `_process_v3_node_step`**

In `adaptor/adapter_jibot.py`, in `_process_v3_node_step`, replace:

```python
        await self._send_node_motion(node)

        target = self._resolve_node_target(node)
```

with:

```python
        rejection_reason = await self._send_node_motion(node)
        if rejection_reason is not None:
            self._set_jibot_goto_rejected_error(node, rejection_reason)
            print(
                f"[ORDER NODE GOTO REJECTED] id={node.node_id} "
                f"seq={node.sequence_id} command=UmGoto reason={rejection_reason}"
            )
            return False
        self._clear_jibot_goto_rejected_error()

        target = self._resolve_node_target(node)
```

- [ ] **Step 8: Run the new tests to verify they pass**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k "goto_rejected or goto_no_ack or goto_accepted_clears" -v`
Expected: PASS (all three).

- [ ] **Step 9: Run the full order-worker suite to check for regressions**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -v`
Expected: PASS (all pre-existing tests still green — `FakeVehicle.wait_for_response` defaults to accepted, so accepted goto behavior is unchanged).

- [ ] **Step 10: Commit**

```bash
git add adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(adaptor): hold order on rejected/unacknowledged node goto

Await the UmGoto response, classify with _jibot_command_rejection_reason,
and on rejection raise JIBOT_GOTO_REJECTED (FATAL) + log + return False so
the worker soft-holds the order. Clear the stale error once accepted.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Fix the existing order-action UmGoto path to catch explicit rejects

**Files:**
- Modify: `adaptor/adapter_jibot.py` (`_dispatch_jibot_command` ~`:3441-3459`)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Consumes (Task 1): `vehicle.send_command_and_wait(..., accept_errors=True)`.
- Produces: order-action UmGoto now receives `#CMD#:"error"` frames, so `_jibot_command_rejection_reason` reports the real reason instead of `"no robot acknowledgement"`.

- [ ] **Step 1: Write the failing test**

Add to the order-worker test class in `adaptor/tests/test_adapter_jibot_v3_order.py`:

```python
    def test_dispatch_umgoto_requests_error_frames(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            captured: Dict[str, Any] = {}

            async def fake_send_command_and_wait(
                command, gap=-1, timeout=3.0, accept_errors=False, **params
            ):
                captured["command"] = command
                captured["accept_errors"] = accept_errors
                return {"#CMD#": "error", "msg": "denied"}

            vehicle.send_command_and_wait = fake_send_command_and_wait

            response = await adapter._dispatch_jibot_command(
                "UmGoto",
                gap=-1,
                params={"target": "goal", "goal": "N1"},
                timeout=3.0,
            )
            reason = adapter._jibot_command_rejection_reason("UmGoto", response)

            self.assertTrue(captured["accept_errors"])
            self.assertIsNotNone(reason)
            self.assertIn("denied", reason)

        asyncio.run(scenario())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k dispatch_umgoto_requests_error_frames -v`
Expected: FAIL — `captured["accept_errors"]` is `False` (the current `_dispatch_jibot_command` does not pass `accept_errors`).

- [ ] **Step 3: Pass `accept_errors=True` on the UmGoto dispatch branch**

In `adaptor/adapter_jibot.py`, in `_dispatch_jibot_command`, change:

```python
        if command == "UmGoto":
            return await self._vehicle.send_command_and_wait(
                command,
                gap=gap,
                timeout=timeout,
                **params,
            )
```

to:

```python
        if command == "UmGoto":
            return await self._vehicle.send_command_and_wait(
                command,
                gap=gap,
                timeout=timeout,
                accept_errors=True,
                **params,
            )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k dispatch_umgoto_requests_error_frames -v`
Expected: PASS.

- [ ] **Step 5: Run the full adapter + client suites**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py tests/test_jibot_client_wait_for_response.py -v`
Expected: PASS (whole suite green).

- [ ] **Step 6: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "fix(adaptor): order-action UmGoto reads explicit reject frames

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Notes for the implementer

- The order worker treats `return False` from `_process_v3_node_step` as "step not complete": it requeues the step and stops the worker (`_process_v3_order_queue`). That is the intended soft hold. The worker restarts only when the FM sends an order/orderUpdate (`_schedule_v3_order_worker`); there is no auto-retry by design.
- `_jibot_command_rejection_reason(command, response)` already returns `None` for an accepted response, the formatted error for `#CMD#:"error"` / `state:false` / `success:false` / a reject-ish `status`, and `"UmGoto no robot acknowledgement"` when `response is None`. Do not duplicate that logic.
- `Error`, `ErrorLevel`, `ErrorReference`, `ErrorType` are already imported in `adapter_jibot.py` (used by `_set_jibot_node_unreached_error`); no new imports needed there.
- Simulator path: if the simulator vehicle lacks `wait_for_response`, `_await_goto_ack` skips the check (returns `None`). This keeps simulator orders working; rejection detection for non-JIBOT vehicles is out of scope.
