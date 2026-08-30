"""Order motion must not be dispatched to a robot whose drive motor is off.

Field evidence (192.168.101.61, 2026-08-17 17:02:23): an order arrived while
UmGetMotorState reported motor=False. The adapter sent UmDock anyway; the robot
buzzed without moving. The operator switched the motor on by hand at 17:03:01
and nothing re-dispatched, so the robot sat at mode=Stop for another 2.5 minutes
until the order was cancelled.

These tests pin the fix: every order-motion dispatch powers the motor first and
waits for the robot to confirm it, exactly as it already leaves charging first.
"""

import asyncio

from adapter_jibot import Adapter
from config.config import MotionRule
from protocol.vda5050_3_0.messages import Node
from tests._stop_reason_harness import start_state
from tests.test_adapter_jibot_v3_order import FakeVehicle, OrderStep


class MotorVehicle(FakeVehicle):
    """FakeVehicle whose motor flag actually responds to enable_motor().

    ``powers_on=False`` models the robot refusing to arm (latched e-stop): the
    command is accepted over TCP but the polled flag never flips.
    """

    def __init__(self, *, motor_on: bool = False, powers_on: bool = True) -> None:
        super().__init__()
        self._motor_flag = 1 if motor_on else 0
        self._powers_on = powers_on
        self.calls: list = []

    async def enable_motor(self) -> None:
        self.enable_motor_calls += 1
        self.calls.append("enable_motor")
        if self._powers_on:
            self._motor_flag = 1

    async def goto_point(self, point: str, strict: bool = False) -> None:
        self.calls.append("goto_point")
        await super().goto_point(point, strict)

    async def goto_xyz(self, x: float, y: float, z: float, strict: bool = False) -> None:
        self.calls.append("goto_xyz")
        await super().goto_xyz(x, y, z, strict)

    async def um_dock(self, gap: int = -1, **params) -> None:
        self.calls.append("um_dock")
        await super().um_dock(gap=gap, **params)

    async def move_distance(self, distance, speed, **kwargs) -> None:
        self.calls.append("move_distance")
        await super().move_distance(distance, speed, **kwargs)


def make_adapter(vehicle: MotorVehicle) -> Adapter:
    adapter = Adapter()
    adapter.set_vehicle(vehicle)
    adapter.config.settings.node_position_poll_interval_sec = 0.01
    adapter.config.settings.order_motor_enable_timeout_sec = 0.3
    # 이 파일이 보는 것은 모터 가드가 주행 명령 앞에 오는지다. dock 후 충전 인계는
    # UmStop 을 덧붙여 확인하려는 명령 순서를 흐리고, MotorVehicle 은 릴레이 hold 를
    # 모르므로 인계가 항상 실패한다. 인계 자체는 test_dock_charge_handover.py 가 본다.
    adapter.config.dock.handover_charge_to_relay_after_dock = False
    return adapter


def _error_text(adapter: Adapter) -> str:
    """Flatten every published error to one string.

    The rejection reason travels in error_references (see
    _set_jibot_goto_rejected_error), not in error_description, so both are
    folded in here.
    """
    parts = []
    for error in adapter.state.errors:
        parts.append(str(getattr(error, "error_description", "")))
        for ref in getattr(error, "error_references", []) or []:
            parts.append(f"{getattr(ref, 'reference_key', '')}="
                         f"{getattr(ref, 'reference_value', '')}")
    return " ".join(parts)


def _node(node_id: str, *, x: float = 0.0, y: float = 0.0, sequence_id: int = 2) -> Node:
    return Node.from_dict({
        "nodeId": node_id,
        "sequenceId": sequence_id,
        "released": True,
        "nodePosition": {"x": x, "y": y, "theta": 0.0, "mapId": "lab2m"},
        "actions": [],
    })


def test_motor_off_is_enabled_before_the_goto():
    """The order's goto is preceded by enable_motor, not sent to a dead motor."""

    async def scenario():
        vehicle = MotorVehicle(motor_on=False)
        adapter = make_adapter(vehicle)
        task = await start_state(adapter)
        try:
            node = _node("N1")
            ok = await adapter._process_v3_node_step(
                OrderStep("node", node.sequence_id, node)
            )
            assert ok is True, "step should complete once the motor is powered"
            assert vehicle.enable_motor_calls == 1, vehicle.calls
            assert vehicle.calls[0] == "enable_motor", vehicle.calls
            assert any(c.startswith("goto") for c in vehicle.calls), vehicle.calls
        finally:
            task.cancel()

    asyncio.run(scenario())


def test_motor_off_is_enabled_before_um_dock():
    """The dock-work UmDock path is order motion too; it needs the same guard."""

    async def scenario():
        vehicle = MotorVehicle(motor_on=False)
        adapter = make_adapter(vehicle)
        adapter.config.dock.nodes = ["1_01CH"]
        adapter.config.dock.stop_charging_on_arrival = False
        task = await start_state(adapter)
        try:
            node = _node("1_01CH")

            async def finish_docking():
                await asyncio.sleep(0.05)
                vehicle._charging = True
                vehicle._status = adapter.config.jibot_status.charging

            asyncio.create_task(finish_docking())
            await adapter._process_v3_node_step(
                OrderStep("node", node.sequence_id, node)
            )

            assert vehicle.enable_motor_calls == 1, vehicle.calls
            assert vehicle.calls == ["enable_motor", "um_dock"], vehicle.calls
        finally:
            task.cancel()

    asyncio.run(scenario())


def test_motor_off_is_enabled_before_the_dock_seat():
    """The directional dock-segment seat (_run_dock) also dispatches motion."""

    async def scenario():
        vehicle = MotorVehicle(motor_on=False)
        adapter = make_adapter(vehicle)
        adapter.config.motion_rules = [MotionRule(to="CHARGE_C", mode="dock")]
        task = await start_state(adapter)
        try:
            node = _node("CHARGE_C")

            async def finish_docking():
                await asyncio.sleep(0.05)
                vehicle._charging = True
                vehicle._status = adapter.config.jibot_status.charging

            asyncio.create_task(finish_docking())
            await adapter._process_v3_node_step(
                OrderStep("node", node.sequence_id, node)
            )

            assert vehicle.enable_motor_calls == 1, vehicle.calls
            assert vehicle.calls == ["enable_motor", "um_dock"], vehicle.calls
        finally:
            task.cancel()

    asyncio.run(scenario())


def test_motor_off_is_enabled_before_a_move_segment():
    """mode="move" relative segments dispatch motion and need the guard too."""

    async def scenario():
        vehicle = MotorVehicle(motor_on=False)
        adapter = make_adapter(vehicle)
        adapter.config.motion_rules = [
            MotionRule(to="N1", mode="move", distance=100.0)
        ]
        adapter.config.settings.move_start_timeout_sec = 0.3
        adapter.config.settings.move_stall_timeout_sec = 0.05
        task = await start_state(adapter)
        try:
            node = _node("N1", x=100.0)
            await adapter._process_v3_node_step(
                OrderStep("node", node.sequence_id, node)
            )

            assert vehicle.enable_motor_calls == 1, vehicle.calls
            assert vehicle.calls[0] == "enable_motor", vehicle.calls
            assert "move_distance" in vehicle.calls, vehicle.calls
        finally:
            task.cancel()

    asyncio.run(scenario())


def test_running_motor_is_left_alone():
    """A powered motor is never re-commanded — the guard is a no-op then."""

    async def scenario():
        vehicle = MotorVehicle(motor_on=True)
        adapter = make_adapter(vehicle)
        task = await start_state(adapter)
        try:
            node = _node("N1")
            ok = await adapter._process_v3_node_step(
                OrderStep("node", node.sequence_id, node)
            )
            assert ok is True
            assert vehicle.enable_motor_calls == 0, vehicle.calls
        finally:
            task.cancel()

    asyncio.run(scenario())


def test_motor_that_never_powers_on_fails_the_step_instead_of_hanging():
    """A motor that will not arm rejects the node rather than buzzing forever.

    This is the field symptom's escape hatch: the FMS gets a reason instead of a
    robot that stands still with an order it silently cannot execute.
    """

    async def scenario():
        vehicle = MotorVehicle(motor_on=False, powers_on=False)
        adapter = make_adapter(vehicle)
        task = await start_state(adapter)
        try:
            node = _node("N1")
            ok = await adapter._process_v3_node_step(
                OrderStep("node", node.sequence_id, node)
            )
            assert ok is False, "the step must fail, not complete"
            assert vehicle.enable_motor_calls == 1, vehicle.calls
            assert not any(c.startswith("goto") for c in vehicle.calls), vehicle.calls

            errors = _error_text(adapter)
            assert "motor did not power on" in errors, errors
        finally:
            task.cancel()

    asyncio.run(scenario())


def test_latched_safety_stop_is_not_force_armed():
    """An e-stopped robot is not re-armed behind the operator's back.

    /jrobot_status says a human latched this stop. Powering the motor back on
    would undo a deliberate safety action, so the node fails with the reason
    instead.
    """

    async def scenario():
        vehicle = MotorVehicle(motor_on=False)
        adapter = make_adapter(vehicle)
        adapter._derive_jibot_stop_reason = lambda: "EMERGENCY"
        task = await start_state(adapter)
        try:
            node = _node("N1")
            ok = await adapter._process_v3_node_step(
                OrderStep("node", node.sequence_id, node)
            )
            assert ok is False
            assert vehicle.enable_motor_calls == 0, vehicle.calls
            assert vehicle.calls == [], vehicle.calls

            errors = _error_text(adapter)
            assert "latched by EMERGENCY" in errors, errors
        finally:
            task.cancel()

    asyncio.run(scenario())


def test_unknown_motor_state_dispatches_as_before():
    """Never polled (flag None) is unknown, not off — do not command the motor."""

    async def scenario():
        vehicle = MotorVehicle(motor_on=False)
        vehicle._motor_flag = None
        adapter = make_adapter(vehicle)
        task = await start_state(adapter)
        try:
            node = _node("N1")
            ok = await adapter._process_v3_node_step(
                OrderStep("node", node.sequence_id, node)
            )
            assert ok is True
            assert vehicle.enable_motor_calls == 0, vehicle.calls
        finally:
            task.cancel()

    asyncio.run(scenario())


def test_motor_flag_shapes_are_read_the_same_way():
    """The flag decides whether a motor is commanded, so pin every shape.

    UmGetMotorState answers with a JSON bool today; an int or string payload
    must not silently read as "on" and disable the guard.
    """
    adapter = Adapter()
    vehicle = MotorVehicle()
    adapter.set_vehicle(vehicle)

    for value in (False, 0, "0", "false", "False", "off", " OFF "):
        vehicle._motor_flag = value
        assert adapter._is_motor_power_off() is True, repr(value)

    # On, or unreadable — either way, not ours to touch.
    for value in (True, 1, "1", "true", None, ""):
        vehicle._motor_flag = value
        assert adapter._is_motor_power_off() is False, repr(value)


def test_auto_enable_can_be_switched_off():
    """order_motor_auto_enable = false restores the pre-fix dispatch."""

    async def scenario():
        vehicle = MotorVehicle(motor_on=False)
        adapter = make_adapter(vehicle)
        adapter.config.settings.order_motor_auto_enable = False
        task = await start_state(adapter)
        try:
            node = _node("N1")
            ok = await adapter._process_v3_node_step(
                OrderStep("node", node.sequence_id, node)
            )
            assert ok is True
            assert vehicle.enable_motor_calls == 0, vehicle.calls
            assert any(c.startswith("goto") for c in vehicle.calls), vehicle.calls
        finally:
            task.cancel()

    asyncio.run(scenario())


def test_stall_retry_powers_the_motor_back_on():
    """A motor switched off mid-order is powered again by the goto retry.

    The guard belongs on the retry dispatch as well, so the recovery that
    already exists for a joystick-stolen goto also covers a motor cut.
    """

    async def scenario():
        vehicle = MotorVehicle(motor_on=False)
        adapter = make_adapter(vehicle)
        task = await start_state(adapter)
        try:
            node = _node("N1", x=5000.0)
            await adapter._resend_node_goto(node)

            assert vehicle.enable_motor_calls == 1, vehicle.calls
            assert vehicle.calls[0] == "enable_motor", vehicle.calls
            assert any(c.startswith("goto") for c in vehicle.calls), vehicle.calls
        finally:
            task.cancel()

    asyncio.run(scenario())
