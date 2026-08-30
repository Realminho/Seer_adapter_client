"""Cross-platform localhost control transport for the drop-in WebUI.

The original Adapter uses a Unix-domain socket.  This runtime-only patch keeps
its request parser/dispatcher intact and replaces only the listening transport
with localhost TCP, allowing the unchanged WebUI engine to work on Windows as
well as Linux.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import time
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from .map_view import clear_active_route


def _request_has_action(request: dict, action_type: str) -> bool:
    payload = request.get("instantActions")
    actions = payload.get("actions") if isinstance(payload, dict) else None
    return bool(
        isinstance(actions, list)
        and any(
            isinstance(action, dict) and action.get("actionType") == action_type
            for action in actions
        )
    )


def _request_action_types(request: Mapping[str, Any]) -> tuple[str, ...]:
    payload = request.get("instantActions")
    actions = payload.get("actions") if isinstance(payload, Mapping) else None
    if not isinstance(actions, list):
        return ()
    return tuple(
        str(action.get("actionType", "") or "")
        for action in actions
        if isinstance(action, Mapping)
    )


def _request_created_at(request: Mapping[str, Any]) -> float:
    meta = request.get("meta")
    if not isinstance(meta, Mapping):
        return 0.0
    try:
        return float(meta.get("created_at", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _completed_order_is_retained(adapter: Any) -> bool:
    """True when VDA keeps the last order object but no order work remains."""

    if getattr(adapter, "order", None) is None:
        return False
    worker = getattr(adapter, "order_worker_task", None)
    if worker is not None and not worker.done():
        return False
    queue = getattr(adapter, "order_queue", None)
    if queue is not None and not queue.empty():
        return False
    if getattr(adapter, "current_order_step", None) is not None:
        return False
    state = getattr(adapter, "state", None)
    if state is not None:
        if getattr(state, "node_states", None) or getattr(state, "edge_states", None):
            return False
    active_background = getattr(adapter, "_active_order_background_action_tasks", None)
    if callable(active_background):
        try:
            if active_background():
                return False
        except Exception:
            return False
    return True


async def _prepare_manual_to_order_handoff(adapter: Any) -> bool:
    """Drain SEER manual velocity control before accepting an autonomous order.

    A manualDrive heartbeat is acknowledged before the SEER TCP request itself
    finishes.  On a slow wireless link, a later VDA order could therefore race a
    still-pending manualDrive/manualStop.  The old manualStop path also calls
    TASK_CANCEL, which can cancel the *new* order if it arrives late.  This
    barrier clears pending heartbeats, cancels the manual watchdog and waits for
    an explicit stop to complete before the order parser is invoked.
    """

    runner = getattr(adapter, "_seer_manual_drive_runner", None)
    pending = getattr(adapter, "_seer_manual_drive_pending", None)
    watchdog = getattr(adapter, "_manual_drive_watchdog", None)
    active = bool(getattr(adapter, "_manual_control_active", False))
    if not active and watchdog is None and pending is None and not (runner and not runner.done()):
        return False

    setattr(adapter, "_seer_manual_drive_pending", None)
    setattr(
        adapter,
        "_seer_manual_drive_generation",
        int(getattr(adapter, "_seer_manual_drive_generation", 0)) + 1,
    )
    cancel_watchdog = getattr(adapter, "_cancel_manual_drive_watchdog", None)
    if callable(cancel_watchdog):
        cancel_watchdog()
    adapter._manual_control_active = False

    vehicle = getattr(adapter, "_vehicle", None)
    if vehicle is not None:
        # This runs before the new order is accepted.  um_stop may therefore
        # cancel an old task safely, and the shared SEER CONTROL-port lock makes
        # it a drain barrier for any earlier manualDrive request.
        stop = getattr(vehicle, "um_stop", None)
        if callable(stop):
            await stop()
    print("[SEER MANUAL HANDOFF] manual control drained before VDA5050 order")
    return True


def _visible_emergency_active(adapter: Any) -> Any:
    state = getattr(adapter, "state", None)
    safety = getattr(state, "safety_state", None)
    emergency = getattr(safety, "e_stop", None)
    if emergency is None:
        return None
    token = str(getattr(emergency, "value", emergency) or "").upper()
    return token not in {"", "NONE"}


async def _wait_for_emergency_state_publish(
    adapter: Any, before_soft_emergency: bool, timeout: float = 2.0
) -> bool:
    """Wait until an emergency toggle is visible in the VDA state object.

    The original control endpoint acknowledges delivery as soon as an async
    action is scheduled.  Waiting here keeps the WebUI redirect from rendering
    the previous emergency state while the command has already reached SEER.
    """

    vehicle = getattr(adapter, "_vehicle", None)
    if vehicle is None:
        return False
    deadline = asyncio.get_running_loop().time() + max(0.0, float(timeout))
    publish_requested = False
    while asyncio.get_running_loop().time() < deadline:
        current_soft = bool(getattr(vehicle, "_soft_emergency", False))
        if current_soft != bool(before_soft_emergency):
            if not publish_requested:
                request_publish = getattr(adapter, "request_state_publish", None)
                if callable(request_publish):
                    request_publish("SEER emergency WebUI synchronization")
                publish_requested = True
            visible = _visible_emergency_active(adapter)
            expected = bool(getattr(vehicle, "_emergency", current_soft))
            if visible is not None and visible == expected:
                return True
        await asyncio.sleep(0.02)
    return False


class TcpControlSender:
    """Send complete VDA5050 messages to the local Adapter over localhost TCP.

    The transport is local-only, but the payload itself remains a normal
    VDA5050 ``order`` or ``instantActions`` object.  This keeps WebUI operation
    independent of MQTT availability while exercising the same Adapter parsers
    and order/action handlers used by production FMS messages.
    """

    def __init__(
        self,
        host: str,
        port: int,
        timeout: float = 2.0,
        trace_instant_actions: Optional[Callable[[Mapping[str, Any]], None]] = None,
        trace_message: Optional[Callable[[str, Mapping[str, Any]], None]] = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self._trace_instant_actions = trace_instant_actions
        self._trace_message = trace_message

    def send_vda(
        self, suffix: str, payload: Mapping[str, Any], meta: Mapping[str, Any] | None = None
    ) -> Tuple[bool, str]:
        if suffix not in {"order", "instantActions"}:
            return False, f"unsupported local VDA5050 topic: {suffix}"
        if self._trace_message is not None:
            try:
                self._trace_message(suffix, payload)
            except Exception:  # Tracing must never block an emergency/control action.
                pass
        elif suffix == "instantActions" and self._trace_instant_actions is not None:
            try:
                self._trace_instant_actions(payload)
            except Exception:  # Tracing must never block an emergency/control action.
                pass
        request = json.dumps(
            {suffix: dict(payload), "meta": dict(meta or {})},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            with socket.create_connection(
                (self.host, self.port), timeout=self.timeout
            ) as connection:
                connection.sendall(request)
                connection.shutdown(socket.SHUT_WR)
                chunks = []
                while True:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
            response: Dict[str, Any] = json.loads(b"".join(chunks).decode("utf-8"))
            delivered = bool(response.get("delivered"))
            return delivered, response.get("error") or (
                "delivered" if delivered else "rejected"
            )
        except (ConnectionRefusedError, TimeoutError):
            return False, "SEER adapter offline — not delivered"
        except (OSError, json.JSONDecodeError) as exc:
            return False, f"SEER command failed: {exc}"

    def send(self, payload: dict, meta: dict) -> Tuple[bool, str]:
        return self.send_vda("instantActions", payload, meta)

    def send_order(self, payload: Mapping[str, Any], meta: Mapping[str, Any] | None = None) -> Tuple[bool, str]:
        return self.send_vda("order", payload, meta)


def reserve_local_port() -> int:
    """Ask the OS for an available localhost port for one WebUI process."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def reserve_local_ports(count: int) -> tuple[int, ...]:
    """Reserve distinct localhost ports for a fleet in one atomic pass.

    Holding all probe sockets open until every port is selected prevents the OS
    from handing the same ephemeral port back to another member while the fleet
    is being assembled.  The sockets are released immediately afterwards so the
    Adapter processes can bind the ports normally.
    """

    wanted = max(0, int(count))
    probes = []
    try:
        for _ in range(wanted):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("127.0.0.1", 0))
            probes.append(probe)
        return tuple(int(probe.getsockname()[1]) for probe in probes)
    finally:
        for probe in probes:
            try:
                probe.close()
            except OSError:
                pass


def install_dropin_control_server() -> None:
    """Patch the already importable Adapter class in memory, never on disk."""

    # The unchanged Adapter directory is added to sys.path by run_adapter.py.
    # Import by name at the runtime patch boundary so static analyzers do not
    # incorrectly require adapter_jibot to be an installed site-package.
    adapter_module = importlib.import_module("adapter_jibot")
    adapter_class = getattr(adapter_module, "Adapter")
    if getattr(adapter_class, "_seer_tcp_control_installed", False):
        return

    original_process_control_request = adapter_class._process_control_request
    original_manual_blocked_reason = getattr(
        adapter_class, "_manual_blocked_reason",
        lambda _self, *, owner_order_id=None: None,
    )
    original_manual_stop_handler = getattr(
        adapter_class, "_handle_manual_stop_instant_action",
        lambda _self, _action: None,
    )
    original_cancel_order_on_loop = getattr(
        adapter_class, "_cancel_order_on_loop", None
    )

    def manual_blocked_reason(self, *, owner_order_id=None):
        reason = original_manual_blocked_reason(
            self, owner_order_id=owner_order_id
        )
        if reason == "order in progress; cancel order first" and _completed_order_is_retained(self):
            # VDA5050 retains the last completed order snapshot.  That retained
            # object must not permanently lock out later operator jogging.
            return None
        return reason

    def handle_manual_drive(self, action):
        reason = self._manual_blocked_reason(
            owner_order_id=getattr(action, "_owner_order_id", None)
        )
        if reason:
            self._update_instant_action_status(
                action.action_id,
                adapter_module.ActionStatus.FAILED,
                result_description=reason,
            )
            return

        params = {p.key: p.value for p in action.action_parameters}
        mc = self.config.manual_control
        try:
            command = (
                float(params.get("trans", mc.drive_trans)),
                float(params.get("rot", mc.drive_rot)),
                float(params.get("speed", mc.drive_speed)),
                float(params.get("lat", mc.drive_lat)),
            )
        except (ValueError, TypeError):
            self._update_instant_action_status(
                action.action_id,
                adapter_module.ActionStatus.FAILED,
                result_description="manualDrive requires numeric trans/rot/speed/lat",
            )
            return

        # Heartbeats are a latest-value stream, not independent robot jobs.
        # A slow SEER link used to create one asyncio task for every heartbeat;
        # those tasks then piled up behind the CONTROL-port request lock until
        # the whole WebUI appeared dead.  Keep at most one request in flight and
        # one latest pending vector instead.
        self._update_instant_action_status(
            action.action_id,
            adapter_module.ActionStatus.FINISHED,
            result_description="manual drive heartbeat accepted",
        )

        def enqueue():
            self._seer_manual_drive_pending = command
            runner = getattr(self, "_seer_manual_drive_runner", None)
            if runner is not None and not runner.done():
                return

            async def run_latest():
                current_task = asyncio.current_task()
                self._seer_manual_drive_runner = current_task
                try:
                    while True:
                        latest = getattr(self, "_seer_manual_drive_pending", None)
                        self._seer_manual_drive_pending = None
                        if latest is None:
                            return
                        generation = int(
                            getattr(self, "_seer_manual_drive_generation", 0)
                        )
                        try:
                            await self._vehicle.um_drive(*latest)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            self._manual_control_active = False
                            print(f"[MANUAL DRIVE] um_drive failed: {exc}")
                            continue
                        if generation == int(
                            getattr(self, "_seer_manual_drive_generation", 0)
                        ):
                            self._manual_control_active = True
                            self._arm_manual_drive_watchdog()
                finally:
                    if getattr(self, "_seer_manual_drive_runner", None) is current_task:
                        self._seer_manual_drive_runner = None
                    # A heartbeat may have landed between the final empty check
                    # and cleanup.  Re-enter once so that latest value is not lost.
                    if getattr(self, "_seer_manual_drive_pending", None) is not None:
                        self._call_on_adapter_loop(enqueue)

            self._seer_manual_drive_runner = asyncio.create_task(run_latest())

        self._call_on_adapter_loop(enqueue)

    def handle_manual_stop(self, action):
        def invalidate_manual_stream():
            self._seer_manual_drive_pending = None
            self._seer_manual_drive_generation = int(
                getattr(self, "_seer_manual_drive_generation", 0)
            ) + 1
        self._call_on_adapter_loop(invalidate_manual_stream)
        return original_manual_stop_handler(self, action)

    def process_control_request(self, request):
        if "order" not in request:
            action_types = _request_action_types(request)
            if action_types and set(action_types).issubset({"manualDrive", "manualStop"}):
                created_at = _request_created_at(request)
                accepted_at = float(getattr(self, "_seer_last_webui_order_created_at", 0.0) or 0.0)
                if created_at and accepted_at and created_at <= accepted_at:
                    print(
                        "[SEER MANUAL STALE DROP] ignoring pre-order WebUI manual command "
                        f"actions={action_types} createdAt={created_at:.6f} orderAt={accepted_at:.6f}"
                    )
                    return {"delivered": True, "ignored": "stale manual command"}
            return original_process_control_request(self, request)
        try:
            order = adapter_module.Order.from_dict(request["order"])
        except Exception as exc:  # noqa: BLE001
            return {"delivered": False, "error": f"invalid order payload: {exc}"}
        expected_serial = str(getattr(self.config.vehicle, "serial_number", "") or "")
        topic = f"local/v3/{expected_serial}/order"
        if not self._is_command_for_this_robot(topic, order.serial_number):
            return {"delivered": False, "error": "VDA5050 serialNumber mismatch"}
        meta = request.get("meta", {})
        print(
            f"[CONTROL TCP ORDER] source_user={meta.get('source_user')} "
            f"confirmed={meta.get('confirmed')} orderId={order.order_id} "
            f"nodes={len(order.nodes)} edges={len(order.edges)}"
        )
        self._print_v3_order_preview(topic, order)
        created_at = _request_created_at(request) or time.time()
        self._seer_last_webui_order_created_at = created_at
        self._handle_v3_order(order)
        return {"delivered": True, "order_id": order.order_id}

    async def bind_control(self, serial):
        del serial
        host = os.getenv("SEER_CONTROL_IPC_HOST", "127.0.0.1")
        port = int(os.getenv("SEER_CONTROL_IPC_PORT", "0"))
        if port <= 0:
            raise RuntimeError("SEER_CONTROL_IPC_PORT is required for drop-in control")
        try:
            server = await asyncio.start_server(
                self._handle_control_conn, host=host, port=port
            )
            print(f"[SEER CONTROL TCP] listening on {host}:{port}")
            self._control_socket_error_logged = False
            return server
        except OSError as exc:
            if not getattr(self, "_control_socket_error_logged", False):
                print(f"[SEER CONTROL TCP FAILED] {host}:{port}: {exc}")
                self._control_socket_error_logged = True
            return None

    async def handle_control(self, reader, writer):
        try:
            raw = await reader.read()
            request = json.loads(raw.decode("utf-8"))
            wait_for_emergency = _request_has_action(
                request, "seerEmergencySwitch"
            )
            vehicle = getattr(self, "_vehicle", None)
            before_soft = bool(getattr(vehicle, "_soft_emergency", False))
            if "order" in request:
                await _prepare_manual_to_order_handoff(self)
            response = self._process_control_request(request)
            if response.get("delivered") and wait_for_emergency:
                await _wait_for_emergency_state_publish(self, before_soft)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            response = {"delivered": False, "error": f"bad request: {exc}"}
        except Exception as exc:  # noqa: BLE001
            response = {"delivered": False, "error": f"server error: {exc}"}
        try:
            writer.write(json.dumps(response).encode("utf-8"))
            await writer.drain()
        except Exception:  # noqa: BLE001
            pass
        finally:
            writer.close()


    async def cancel_order_on_loop(self, action_id, cancelled_order_id=""):
        """Run the original cancel, then retire only this Adapter's route marker."""

        if original_cancel_order_on_loop is None:
            return None
        try:
            return await original_cancel_order_on_loop(
                self, action_id, cancelled_order_id
            )
        finally:
            active_route_value = str(
                os.getenv("SEER_ACTIVE_ROUTE_PATH", "") or ""
            ).strip()
            if active_route_value:
                if clear_active_route(Path(active_route_value)):
                    print(
                        "[SEER ACTIVE ROUTE CLEAR] cancelOrder cleared "
                        f"{active_route_value}"
                    )

    adapter_class._manual_blocked_reason = manual_blocked_reason
    adapter_class._handle_manual_drive_instant_action = handle_manual_drive
    adapter_class._handle_manual_stop_instant_action = handle_manual_stop
    if original_cancel_order_on_loop is not None:
        adapter_class._cancel_order_on_loop = cancel_order_on_loop
    adapter_class._process_control_request = process_control_request
    adapter_class._bind_control_socket = bind_control
    adapter_class._handle_control_conn = handle_control
    adapter_class._seer_tcp_control_installed = True
