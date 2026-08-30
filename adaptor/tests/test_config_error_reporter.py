"""Tests for the config-error fallback reporter.

When the per-instance config TOML fails to load, the adapter must not die
silently. It comes up in a degraded "config error" mode that still reaches the
MQTT broker (using a fallback config) and publishes a FATAL CONFIG_LOAD_FAILED
state so operators see the misconfiguration on the FMS/WebUI and can respond.
"""
import asyncio
import json
import sys
import unittest
from pathlib import Path

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
if str(ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(ADAPTER_ROOT))

from core.config_error_reporter import (
    ConfigErrorReporter,
    build_config_error_state,
)


class _FakeTransport:
    """Records publish()/set_connection_last_will() calls (no real MQTT)."""

    def __init__(self):
        self.published = []  # (topic, payload, qos, retain)
        self.last_will = None  # (payload, qos, retain)

    def publish(self, topic, message, qos=0, retain=False, use_prefix=True):
        payload = message.to_dict() if hasattr(message, "to_dict") else message
        self.published.append((topic, payload, qos, retain))

    def publish_state(self, state, qos=0):
        self.publish("state", state, qos=qos, retain=False)

    def set_connection_last_will(self, connection, qos=0, retain=True):
        payload = connection.to_dict() if hasattr(connection, "to_dict") else connection
        self.last_will = (payload, qos, retain)


def _json_roundtrip(payload):
    """Mirror what MQTTClient.publish does before sending."""
    return json.loads(json.dumps(payload))


class TestBuildConfigErrorState(unittest.TestCase):
    def test_carries_single_fatal_config_load_failed_error(self):
        state = build_config_error_state(
            serial_number="HN-SH6-TR-002",
            manufacturer="HANBACK",
            version="3.0.0",
            header_id=7,
            timestamp="2026-06-25T00:00:00Z",
            config_path="config/HN-SH6-TR-002.toml",
            reason="[Errno 2] No such file or directory: 'config/HN-SH6-TR-002.toml'",
        )
        payload = _json_roundtrip(state)

        self.assertEqual(payload["serialNumber"], "HN-SH6-TR-002")
        self.assertEqual(payload["manufacturer"], "HANBACK")
        self.assertEqual(payload["headerId"], 7)
        self.assertEqual(len(payload["errors"]), 1)
        err = payload["errors"][0]
        self.assertEqual(err["errorType"], "CONFIG_LOAD_FAILED")
        self.assertEqual(err["errorLevel"], "FATAL")
        self.assertIn(err["errorDescription"], state["errors"][0]["errorDescription"])
        refs = {r["referenceKey"]: r["referenceValue"] for r in err["errorReferences"]}
        self.assertEqual(refs["configPath"], "config/HN-SH6-TR-002.toml")

    def test_state_has_required_vda5050_fields(self):
        payload = _json_roundtrip(
            build_config_error_state(
                serial_number="S1",
                manufacturer="M",
                version="3.0.0",
                header_id=0,
                timestamp="t",
                config_path="c.toml",
                reason="boom",
            )
        )
        # Required top-level VDA5050 state fields must be present so a strict
        # FMS/WebUI parser does not choke on the error heartbeat.
        for key in (
            "operatingMode",
            "safetyState",
            "powerSupply",
            "nodeStates",
            "edgeStates",
            "actionStates",
            "driving",
        ):
            self.assertIn(key, payload)


class TestConfigErrorReporter(unittest.TestCase):
    def _config(self):
        class _Broker:
            host = "192.168.2.61"
            port = 11883
            vda_interface = "amr"

        class _Vehicle:
            serial_number = "HN-SH6-TR-002"
            manufacturer = "HANBACK"
            vda_version = "v3"
            vda_full_version = "3.0.0"

        class _Config:
            mqtt_broker = _Broker()
            vehicle = _Vehicle()

        return _Config()

    def test_publishes_connection_online_then_error_state(self):
        transport = _FakeTransport()
        reporter = ConfigErrorReporter(
            config=self._config(),
            config_path="config/HN-SH6-TR-002.toml",
            error=FileNotFoundError(
                2, "No such file or directory", "config/HN-SH6-TR-002.toml"
            ),
            transport=transport,
        )

        reporter.publish_connection_online()
        reporter.publish_error_state()

        topics = [t for (t, _p, _q, _r) in transport.published]
        self.assertIn("connection", topics)
        self.assertIn("state", topics)

        conn = next(p for (t, p, _q, _r) in transport.published if t == "connection")
        self.assertEqual(conn["connectionState"], "ONLINE")

        st = next(p for (t, p, _q, _r) in transport.published if t == "state")
        self.assertEqual(st["errors"][0]["errorType"], "CONFIG_LOAD_FAILED")
        self.assertEqual(st["serialNumber"], "HN-SH6-TR-002")

    def test_header_id_increments_across_state_heartbeats(self):
        transport = _FakeTransport()
        reporter = ConfigErrorReporter(
            config=self._config(),
            config_path="c.toml",
            error=ValueError("bad toml"),
            transport=transport,
        )

        reporter.publish_error_state()
        reporter.publish_error_state()

        states = [p for (t, p, _q, _r) in transport.published if t == "state"]
        self.assertEqual(len(states), 2)
        self.assertLess(states[0]["headerId"], states[1]["headerId"])

    def test_configures_offline_last_will(self):
        transport = _FakeTransport()
        reporter = ConfigErrorReporter(
            config=self._config(),
            config_path="c.toml",
            error=ValueError("bad toml"),
            transport=transport,
        )
        reporter.configure_last_will()
        self.assertIsNotNone(transport.last_will)
        payload, _qos, retain = transport.last_will
        self.assertEqual(payload["connectionState"], "OFFLINE")
        self.assertTrue(retain)

    def test_run_publishes_then_offline_on_stop(self):
        transport = _FakeTransport()
        reporter = ConfigErrorReporter(
            config=self._config(),
            config_path="c.toml",
            error=ValueError("bad toml"),
            transport=transport,
        )

        async def drive():
            stop = asyncio.Event()
            task = asyncio.create_task(
                reporter.run(heartbeat_sec=0.01, stop_event=stop)
            )
            await asyncio.sleep(0.05)
            stop.set()
            await task

        asyncio.run(drive())

        conn_states = [
            p["connectionState"]
            for (t, p, _q, _r) in transport.published
            if t == "connection"
        ]
        # First ONLINE, last OFFLINE (graceful shutdown).
        self.assertEqual(conn_states[0], "ONLINE")
        self.assertEqual(conn_states[-1], "OFFLINE")
        # At least one error state went out while running.
        self.assertTrue(
            any(t == "state" for (t, _p, _q, _r) in transport.published)
        )
        # run() must arm the retained OFFLINE last will so an ungraceful death
        # (SIGKILL / power loss) still flips the robot OFFLINE for operators.
        self.assertIsNotNone(transport.last_will)
        self.assertEqual(transport.last_will[0]["connectionState"], "OFFLINE")


if __name__ == "__main__":
    unittest.main()
