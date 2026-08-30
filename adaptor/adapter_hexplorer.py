"""Dobot Hexplorer VDA5050 adapter.

The adapter exposes a VDA5050 MQTT boundary to ACS and keeps Dobot ROS2
topic details inside the Hexplorer client.
"""

from __future__ import annotations

import asyncio
import queue
import sys
from pathlib import Path
from typing import Any

ADAPTER_ROOT = Path(__file__).resolve().parent
REPO_ROOT = ADAPTER_ROOT.parent
CLIENT_SRC = REPO_ROOT / "hexplorer-client" / "src"
if str(CLIENT_SRC) not in sys.path:
    sys.path.insert(0, str(CLIENT_SRC))

from config.config import get_config
from hexplorer_client.client import (
    HexplorerClient,
    HexplorerFieldMapping,
    HexplorerTopics,
)
from hexplorer_client.map_files import sync_hexplorer_maps
from protocol.vda5050_3_0.messages import (
    Connection,
    ConnectionState,
    EmergencyStop,
    Header,
    MobileRobotPosition,
    OperatingMode,
    PowerSupply,
    SafetyState,
    State,
)
from protocol.vda5050_3_0.transport import Vda5050V3Transport
from utils.mqtt_client import MQTTClient
from utils.helpers import get_timestamp

# Hexplorer has no BMS, so there is never a real state-of-charge to report.
# VDA5050 requires a numeric stateOfCharge (null is not allowed), so publish this
# out-of-range sentinel — the dashboard maps any negative SoC back to "unknown"
# (see core/monitor.py). config.hexplorer.default_battery_soc is retained for the
# deferred freshness-gated option (report the configured value only while the ROS
# state topic is live), to be wired up when the hardware is available again.
_BATTERY_CHARGE_UNKNOWN = -1.0


class HexplorerAdapter:
    def __init__(self, client: HexplorerClient | None = None, *, config_path: str | None = None) -> None:
        self.config = get_config(config_path=config_path)
        topics = HexplorerTopics(
            robot_command=self.config.hexplorer.robot_command_topic,
            robot_state=self.config.hexplorer.robot_state_topic,
            velocity_command=self.config.hexplorer.velocity_command_topic,
            camera_info=self.config.hexplorer.camera_info_topic,
        )
        field_mapping = HexplorerFieldMapping(
            position_field=self.config.hexplorer.position_field,
            orientation_field=self.config.hexplorer.orientation_field,
            mode_field=self.config.hexplorer.mode_field,
            mode_index=self.config.hexplorer.mode_index,
            command_target_state_field=(
                self.config.hexplorer.command_target_state_field
            ),
            orientation_quaternion_order=(
                self.config.hexplorer.orientation_quaternion_order
            ),
        )
        self.client = client or HexplorerClient(topics, field_mapping)
        # True once OFFLINE was published on purpose (graceful shutdown), which
        # suppresses the reconnect-time ONLINE re-assert.
        self._connection_offline_intent = False
        self._connection_republish_error_logged = False
        self.mqtt = MQTTClient(on_connection_change=self._on_broker_change)
        self.vda3 = Vda5050V3Transport(self.mqtt)
        self.header_id = 0
        # queue.Queue (not asyncio.Queue) because handle_vda_message runs on the
        # paho MQTT network thread; asyncio.Queue is not thread-safe.
        self.command_queue: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()

    def connect_mqtt(self) -> None:
        self.mqtt.connect()

    def _on_broker_change(self, connected: bool) -> None:
        """Re-assert connectionState=ONLINE on every successful (re)connect.

        The Last Will is a *retained* CONNECTION_BROKEN, so a dropped link
        leaves that stuck on the broker for this serial. paho reconnects
        underneath and MQTTClient._resubscribe_all() re-arms instantActions, but
        ONLINE was published once in run() only — nothing overwrote the retained
        message, so ACS kept seeing the robot as broken for the rest of the
        process lifetime.
        LWT가 retain=True CONNECTION_BROKEN이라 링크가 끊기면 브로커에 그 값이
        남는다. paho가 재접속해도 덮어쓰는 주체가 없어 ACS에는 계속 끊긴 것으로
        보였다. 접속이 살아날 때마다 ONLINE을 다시 선언한다.
        """
        if not connected or self._connection_offline_intent:
            return
        try:
            self.publish_connection(ConnectionState.ONLINE)
        except Exception as exc:  # noqa: BLE001
            # Runs on the paho network thread, which does not suppress callback
            # exceptions: one escaping here kills that thread and with it the
            # auto-reconnect this method exists to support.
            # paho 네트워크 스레드에서 돈다. 예외가 새면 스레드가 죽어 자동
            # 재접속 자체가 멈춘다.
            if not self._connection_republish_error_logged:
                print(f"[VDA5050 CONNECTION REPUBLISH FAILED] {exc}")
                self._connection_republish_error_logged = True

    def disconnect_mqtt(self) -> None:
        self.mqtt.disconnect()

    async def run(self) -> None:
        self.client.start()
        # Register the MQTT last will before connecting so the broker reports
        # CONNECTION_BROKEN to ACS if this process dies or the link drops.
        self.set_connection_last_will()
        self.connect_mqtt()
        self.publish_connection(ConnectionState.ONLINE)
        self.vda3.subscribe_instant_actions(self.handle_vda_message, qos=0)

        while True:
            await asyncio.to_thread(self.client.spin_once, 0.05)
            await self._drain_command_queue()
            self.publish_state()
            await asyncio.sleep(float(self.config.settings.state_publish_delay))

    def _build_connection(self, state: ConnectionState) -> Connection:
        self.header_id += 1
        return Connection(
            header=Header(
                header_id=self.header_id,
                timestamp=get_timestamp(),
                version=self.config.vehicle.vda_full_version,
                manufacturer="dobot",
                serial_number=self.config.vehicle.serial_number,
            ),
            connection_state=state,
        )

    def publish_connection(self, state: ConnectionState) -> None:
        # Remember a deliberate OFFLINE so a reconnect racing shutdown does not
        # flip the retained message back to ONLINE.
        self._connection_offline_intent = state == ConnectionState.OFFLINE
        self.vda3.publish_connection(
            self._build_connection(state), qos=1, retain=True
        )

    def set_connection_last_will(self) -> None:
        self.vda3.set_connection_last_will(
            self._build_connection(ConnectionState.CONNECTION_BROKEN),
            qos=1,
            retain=True,
        )

    def handle_vda_message(self, topic: str, payload: Any) -> None:
        topic_type = topic.rsplit("/", 1)[-1]
        if topic_type == "instantActions":
            self._handle_instant_actions(payload)

    def _handle_instant_actions(self, payload: Any) -> None:
        if hasattr(payload, "to_dict"):
            payload = payload.to_dict()
        actions = payload.get("actions", []) if isinstance(payload, dict) else []

        for action in actions:
            action_type = action.get("actionType")
            params = {
                param.get("key"): param.get("value")
                for param in action.get("actionParameters", [])
            }
            if action_type == "standUp":
                self.command_queue.put_nowait(
                    (
                        "setRobotState",
                        {"state": self.config.hexplorer.stand_up_state},
                    )
                )
            elif action_type == "standDown":
                self.command_queue.put_nowait(
                    (
                        "setRobotState",
                        {"state": self.config.hexplorer.stand_down_state},
                    )
                )
            elif action_type == "walkMode":
                self.command_queue.put_nowait(
                    (
                        "setRobotState",
                        {"state": self.config.hexplorer.walk_mode_state},
                    )
                )
            elif action_type == "stop":
                self.command_queue.put_nowait(
                    ("velocity", {"x": 0.0, "y": 0.0, "yaw": 0.0})
                )
            elif action_type == "velocity":
                self.command_queue.put_nowait(
                    (
                        "velocity",
                        {
                            "x": float(params.get("x", 0.0)),
                            "y": float(params.get("y", 0.0)),
                            "yaw": float(params.get("yaw", 0.0)),
                        },
                    )
                )
            elif action_type == "syncHexplorerMap":
                include_names = params.get("includeNames", ["record"])
                if isinstance(include_names, str):
                    include_names = [include_names]
                self.command_queue.put_nowait(
                    (
                        "syncHexplorerMap",
                        {
                            "sourceDir": params.get(
                                "sourceDir",
                                self.config.hexplorer.map_source_dir,
                            ),
                            "targetDir": params.get(
                                "targetDir",
                                self.config.hexplorer.map_target_dir,
                            ),
                            "backupDir": params.get(
                                "backupDir",
                                self.config.hexplorer.map_backup_dir,
                            ),
                            "includeNames": tuple(include_names),
                        },
                    )
                )
            elif action_type == "getCameraInfo":
                self.command_queue.put_nowait(("getCameraInfo", {}))

    async def _drain_command_queue(self) -> None:
        while True:
            try:
                command, params = self.command_queue.get_nowait()
            except queue.Empty:
                break
            if command == "setRobotState":
                self.client.set_robot_state(int(params["state"]))
            elif command == "velocity":
                self.client.publish_velocity(
                    float(params["x"]),
                    float(params["y"]),
                    float(params["yaw"]),
                )
            elif command == "syncHexplorerMap":
                result = sync_hexplorer_maps(
                    source_dir=Path(params["sourceDir"]),
                    target_dir=Path(params["targetDir"]),
                    backup_root=Path(params["backupDir"]),
                    include_names=tuple(params["includeNames"]),
                )
                print(
                    f"[HEXPLORER MAP] copied={result.copied} "
                    f"backup={result.backup_dir}"
                )
            elif command == "getCameraInfo":
                print(f"[HEXPLORER CAMERA] {self.client.snapshot.camera_info}")

    def publish_state(self) -> None:
        self.header_id += 1
        snapshot = self.client.snapshot
        state = self._build_state(
            MobileRobotPosition(
                x=snapshot.x,
                y=snapshot.y,
                theta=snapshot.theta,
                map_id=self.config.settings.map_id,
                localized=snapshot.localized,
                localization_score=1.0 if snapshot.localized else 0.0,
            )
        )
        self.vda3.publish_state(state, qos=0)

    def _build_state(self, position: MobileRobotPosition) -> State:
        return State(
            header=Header(
                header_id=self.header_id,
                timestamp=get_timestamp(),
                version=self.config.vehicle.vda_full_version,
                manufacturer="dobot",
                serial_number=self.config.vehicle.serial_number,
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
            power_supply=PowerSupply(
                state_of_charge=_BATTERY_CHARGE_UNKNOWN,
                charging=False,
            ),
            operating_mode=OperatingMode.AUTOMATIC,
            errors=[],
            safety_state=SafetyState(
                active_emergency_stop=EmergencyStop.NONE,
                field_violation=False,
            ),
            information=[],
            mobile_robot_position=position,
        )
