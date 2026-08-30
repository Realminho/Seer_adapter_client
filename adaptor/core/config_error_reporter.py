"""Degraded "config error" reporter.

When the per-instance config TOML fails to load (missing file, bad TOML, missing
section/key), the adapter must not exit silently — operators would see nothing
but a dead unit. Instead it comes up in this degraded mode: connect to the MQTT
broker using a *fallback* config (base ``config.toml`` + the instance overrides,
which still carry the right serialNumber and broker), then publish a FATAL
``CONFIG_LOAD_FAILED`` state on a heartbeat so the misconfiguration is visible on
the FMS/WebUI and a human can fix it.

No vehicle / motor / IO is touched here — only MQTT. The full Adapter state
machine is deliberately bypassed because it depends on a live vehicle object.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import utils.helpers as utils
from protocol.vda5050_3_0.messages import (
    Connection,
    ConnectionState,
    EmergencyStop,
    Header,
    OperatingMode,
    PowerSupply,
    SafetyState,
    State,
)
from protocol.vda5050_3_0.transport import Vda5050V3Transport
from protocol.vda_2_0_0.vda5050_2_0_0_state import (
    Error,
    ErrorLevel,
    ErrorReference,
    ErrorType,
)


def build_config_error_state(
    *,
    serial_number: str,
    manufacturer: str,
    version: str,
    header_id: int,
    timestamp: str,
    config_path: Optional[str],
    reason: str,
) -> Dict[str, Any]:
    """Build a minimal VDA5050 v3 state dict carrying one FATAL config error.

    Telemetry (position, battery, nodes ...) is zero/empty: in config-error mode
    the adapter has no vehicle, so the only meaningful content is the error.
    """
    references: List[ErrorReference] = [ErrorReference("reason", reason)]
    if config_path:
        references.insert(0, ErrorReference("configPath", str(config_path)))

    error = Error(
        error_type=ErrorType.CONFIG_LOAD_FAILED,
        error_level=ErrorLevel.FATAL,
        error_references=references,
        error_description=reason,
    )

    state = State(
        header=Header(
            header_id=header_id,
            timestamp=timestamp,
            version=version,
            manufacturer=manufacturer,
            serial_number=serial_number,
        ),
        order_id="",
        order_update_id=0,
        last_node_id="",
        last_node_sequence_id=0,
        node_states=[],
        edge_states=[],
        driving=False,
        action_states=[],
        instant_action_states=[],
        power_supply=PowerSupply(state_of_charge=0.0, charging=False),
        operating_mode=OperatingMode.SERVICE,
        errors=[error.to_dict()],
        safety_state=SafetyState(
            active_emergency_stop=EmergencyStop.NONE,
            field_violation=False,
        ),
        information=[],
        loads=[],
    )
    return state.to_dict()


class ConfigErrorReporter:
    """Publishes connection + a FATAL config-error state heartbeat over MQTT.

    Reuses :class:`MQTTClient` + :class:`Vda5050V3Transport`. For tests a fake
    transport can be injected so no real broker is required.
    """

    def __init__(
        self,
        config: Any,
        config_path: Optional[str],
        error: BaseException,
        *,
        transport: Optional[Any] = None,
        mqtt_client: Optional[Any] = None,
    ) -> None:
        self._config = config
        self._config_path = config_path
        self._reason = str(error) or error.__class__.__name__
        self._header_id = 0

        if transport is not None:
            self._mqtt = mqtt_client
            self._vda3 = transport
        else:
            # Imported lazily so unit tests can inject a transport without paho.
            from utils.mqtt_client import MQTTClient

            self._mqtt = MQTTClient(config=config)
            self._vda3 = Vda5050V3Transport(self._mqtt)

    # -- payload helpers -------------------------------------------------
    def _serial(self) -> str:
        return self._config.vehicle.serial_number

    def _manufacturer(self) -> str:
        return getattr(self._config.vehicle, "manufacturer", "")

    def _version(self) -> str:
        return getattr(self._config.vehicle, "vda_full_version", "")

    def _next_header_id(self) -> int:
        self._header_id += 1
        return self._header_id

    def _connection(self, connection_state: ConnectionState) -> Connection:
        return Connection(
            header=Header(
                header_id=self._next_header_id(),
                timestamp=utils.get_timestamp(),
                version=self._version(),
                manufacturer=self._manufacturer(),
                serial_number=self._serial(),
            ),
            connection_state=connection_state,
        )

    # -- publish actions -------------------------------------------------
    def configure_last_will(self) -> None:
        """Retained OFFLINE last will so an ungraceful death still flips state."""
        self._vda3.set_connection_last_will(
            self._connection(ConnectionState.OFFLINE), qos=1, retain=True
        )

    def publish_connection_online(self) -> None:
        self._vda3.publish(
            "connection",
            self._connection(ConnectionState.ONLINE),
            qos=1,
            retain=True,
        )

    def publish_connection_offline(self) -> None:
        self._vda3.publish(
            "connection",
            self._connection(ConnectionState.OFFLINE),
            qos=1,
            retain=True,
        )

    def publish_error_state(self) -> None:
        state = build_config_error_state(
            serial_number=self._serial(),
            manufacturer=self._manufacturer(),
            version=self._version(),
            header_id=self._next_header_id(),
            timestamp=utils.get_timestamp(),
            config_path=self._config_path,
            reason=self._reason,
        )
        self._vda3.publish_state(state, qos=0)

    # -- lifecycle -------------------------------------------------------
    async def run(
        self,
        *,
        heartbeat_sec: float = 5.0,
        stop_event: Optional[asyncio.Event] = None,
    ) -> None:
        """Connect, announce ONLINE, then publish the error state until stopped.

        Stays alive (publishing the FATAL error on the heartbeat) so the
        misconfigured robot stays visible. Exits only on ``stop_event`` /
        cancellation, publishing OFFLINE on the way out.
        """
        # Arm the retained OFFLINE last will *before* connecting (paho requires
        # will_set before connect). This is transport-level, so it also runs
        # when a fake transport is injected for tests.
        self.configure_last_will()
        if self._mqtt is not None:
            self._mqtt.connect_background()

        self.publish_connection_online()
        try:
            while True:
                self.publish_error_state()
                if stop_event is not None:
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=heartbeat_sec)
                    except asyncio.TimeoutError:
                        continue
                    break
                await asyncio.sleep(heartbeat_sec)
        except asyncio.CancelledError:
            raise
        finally:
            try:
                self.publish_connection_offline()
            except Exception as exc:  # best-effort; we are shutting down
                print(f"[CONFIG ERROR MODE] OFFLINE publish failed (ignored): {exc}")
            if self._mqtt is not None:
                try:
                    self._mqtt.disconnect()
                except Exception as exc:
                    print(f"[CONFIG ERROR MODE] disconnect failed (ignored): {exc}")
