import unittest

from protocol.vda5050_3_0.messages import ConnectionState, MobileRobotPosition


class HexplorerAdapterStateTest(unittest.TestCase):
    def test_build_state_matches_vda5050_v3_dataclasses(self) -> None:
        from adapter_hexplorer import HexplorerAdapter

        adapter = HexplorerAdapter(client=None)
        adapter.header_id = 7
        state = adapter._build_state(
            MobileRobotPosition(
                x=1.0,
                y=2.0,
                theta=0.5,
                map_id="lab2m",
                localized=True,
                localization_score=1.0,
            )
        )

        payload = state.to_dict()

        self.assertEqual(payload["headerId"], 7)
        self.assertEqual(payload["mobileRobotPosition"]["theta"], 0.5)
        # Hexplorer has no BMS — there is no real SoC to report, so it publishes
        # the -1 "unknown" sentinel rather than a misleading placeholder. The
        # dashboard maps any negative SoC back to "—". (Freshness-gated reporting
        # is deferred until the hardware is available again.)
        self.assertEqual(payload["powerSupply"]["stateOfCharge"], -1.0)
        self.assertEqual(payload["safetyState"]["activeEmergencyStop"], "NONE")
        self.assertIs(payload["driving"], False)
        self.assertEqual(payload["instantActionStates"], [])


class HexplorerAdapterConnectionTest(unittest.TestCase):
    """The Last Will is a retained CONNECTION_BROKEN, so ONLINE has to be
    re-asserted on every reconnect or ACS keeps seeing the robot as broken."""

    def _adapter_with_capture(self):
        from adapter_hexplorer import HexplorerAdapter

        adapter = HexplorerAdapter(client=None)
        published: list = []
        adapter.vda3.publish_connection = (
            lambda message, **kwargs: published.append(message.connection_state)
        )
        return adapter, published

    def test_broker_change_hook_is_wired_into_the_mqtt_client(self) -> None:
        """Without this the republish below is dead code — nothing calls it."""
        adapter, _published = self._adapter_with_capture()

        # Bound methods are rebuilt per attribute access, so compare by value.
        self.assertEqual(adapter.mqtt._on_connection_change, adapter._on_broker_change)

    def test_broker_reconnect_republishes_connection_online(self) -> None:
        adapter, published = self._adapter_with_capture()

        adapter._on_broker_change(True)   # first connect
        adapter._on_broker_change(False)  # link drops
        adapter._on_broker_change(True)   # paho reconnects

        self.assertEqual(
            published, [ConnectionState.ONLINE, ConnectionState.ONLINE]
        )

    def test_broker_reconnect_after_deliberate_offline_stays_offline(self) -> None:
        adapter, published = self._adapter_with_capture()

        adapter.publish_connection(ConnectionState.OFFLINE)  # graceful shutdown
        adapter._on_broker_change(True)

        self.assertEqual(published, [ConnectionState.OFFLINE])


if __name__ == "__main__":
    unittest.main()
