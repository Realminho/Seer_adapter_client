import asyncio
import unittest


class EzioExtensionTest(unittest.TestCase):
    def test_start_sensor_service_schedules_tray_slot_polling(self):
        from extensions.ezio import start_sensor_service

        class Adapter:
            def __init__(self):
                self.calls = []
                self._ezi_io = None

            def _is_simulator(self):
                self.calls.append("simulator")
                return True

        async def scenario():
            adapter = Adapter()
            task = start_sensor_service(adapter, interval_sec=0.25)
            try:
                for _ in range(100):
                    if adapter.calls:
                        break
                    await asyncio.sleep(0.01)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertGreaterEqual(len(adapter.calls), 1)

        asyncio.run(scenario())

    def test_hardware_sensor_service_disabled_when_photo_sensor_action_disabled(self):
        from extensions.hardware import start_sensor_service

        class Registry:
            def has(self, action_type):
                self.action_type = action_type
                return False

        class Adapter:
            def _is_simulator(self):
                raise AssertionError("sensor polling should not start")

        registry = Registry()
        task = start_sensor_service(
            Adapter(),
            interval_sec=0.25,
            action_registry=registry,
        )

        self.assertIsNone(task)
        self.assertEqual(registry.action_type, "photoSensorRead")


if __name__ == "__main__":
    unittest.main()
