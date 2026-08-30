"""adapter io.json snapshot/write 단위 테스트 (하드웨어 없이)."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from core import ipc_paths
from extensions.ezio import execute_ezio_action
from extensions.pio import execute_pio_action, pio_disconnect, pio_read_inputs
from extensions.pio import pio_write_output


def _bare_adapter():
    """Adapter 인스턴스를 __init__ 없이 만들고 테스트에 필요한 속성만 세팅."""
    from adapter_jibot import Adapter
    a = Adapter.__new__(Adapter)
    # IO 캐시 필드 초기값
    a._ezi_io = object()          # not None -> ezio configured
    a._ezio_inputs = None
    a._ezio_inputs_at = None
    a._ezio_outputs = None
    a._ezio_outputs_at = None
    a._ezio_connected = False
    a._ezio_board = ""
    a._ezio_error = ""
    a._pio_connected = False
    a._pio_client = None
    a._pio_client_factory = None
    a._pio_inputs = None
    a._pio_inputs_at = None
    a._pio_outputs = None
    a._pio_outputs_at = None
    a._pio_error = ""
    a._io_last_write = 0.0
    a.state = None            # read_ezio_input_bits -> clear_ezio_input_error
    # config stub
    a.config = mock.Mock()
    a.config.vehicle.serial_number = "S1"
    a.config.ezi_config.ezi_io = "10.8.8.87"
    a.config.pio_config.pio_serial_port = "/dev/ttyUSB0"
    a.config.pio_config.pio_baudrate = 19200
    # Mock은 어떤 속성이든 만들어 주므로 핀 맵은 진짜 값으로 덮어 둔다.
    a.config.pio_config.input_pins = [0, 1, 2, 3, 4, 5, 6, 7]
    a.config.pio_config.output_pin_map = {i: i - 1 for i in range(1, 9)}
    a._is_simulator = lambda: False
    return a


class AdapterIoSnapshotTest(unittest.TestCase):
    def test_note_ezio_sets_timestamps(self):
        a = _bare_adapter()
        a._note_ezio(connected=True, inputs=[1, 0, 1])
        self.assertTrue(a._ezio_connected)
        self.assertEqual(a._ezio_inputs, [1, 0, 1])
        self.assertIsNotNone(a._ezio_inputs_at)
        self.assertIsNone(a._ezio_outputs_at)  # outputs not touched

    def test_note_pio_inputs_sets_timestamp_and_keeps_on_disconnect(self):
        a = _bare_adapter()
        a._note_pio(connected=True, inputs={"1": "on"})
        self.assertEqual(a._pio_inputs, {"1": "on"})
        self.assertIsNotNone(a._pio_inputs_at)
        a._note_pio(connected=False)          # disconnect
        self.assertFalse(a._pio_connected)
        self.assertEqual(a._pio_inputs, {"1": "on"})  # inputs retained

    def test_note_pio_outputs_sets_timestamp_and_keeps_on_disconnect(self):
        a = _bare_adapter()
        a._note_pio(connected=True, outputs={"2": "on"})
        self.assertEqual(a._pio_outputs, {"2": "on"})
        self.assertIsNotNone(a._pio_outputs_at)
        a._note_pio(connected=False)          # disconnect
        self.assertEqual(a._pio_outputs, {"2": "on"})  # outputs retained

    def test_pio_write_output_records_merged_state(self):
        """출력은 EZI IO로 나가고, 커맨드한 점들이 누적 보존된다."""
        import asyncio

        a = _bare_adapter()
        a.config.pio_config.output_pins = [0, 1, 2, 3, 4, 5, 6, 7]
        pins = {}

        class _Ezi:
            async def turn_on_output(self, n):
                pins[n] = 1

            async def turn_off_output(self, n):
                pins[n] = 0

            async def get_output(self):
                return {"outputs": [pins.get(i, 0) for i in range(16)]}

        a._ezi_io = _Ezi()
        asyncio.run(pio_write_output(a, 2, "on"))
        asyncio.run(pio_write_output(a, 5, "off"))
        # both commanded indices are retained (merge, not clobber)
        self.assertEqual(a._pio_outputs, {"2": "on", "5": "off"})
        # index 2 -> pin 1, index 5 -> pin 4
        self.assertEqual(pins, {1: 1, 4: 0})

    def test_snapshot_dict_marks_configured(self):
        a = _bare_adapter()
        a._note_ezio(connected=True, inputs=[1] * 16)
        a._note_pio(connected=True, outputs={"3": "on"})
        d = a._io_snapshot_dict()
        self.assertTrue(d["ezio"]["configured"])
        self.assertEqual(d["ezio"]["ip"], "10.8.8.87")
        self.assertTrue(d["pio"]["configured"])
        self.assertEqual(d["pio"]["baudrate"], 19200)
        self.assertEqual(d["pio"]["outputs"], {"3": "on"})
        self.assertIsNotNone(d["pio"]["outputs_updated_at"])

    def test_snapshot_dict_ezio_unconfigured_when_client_none(self):
        a = _bare_adapter()
        a._ezi_io = None
        d = a._io_snapshot_dict()
        self.assertFalse(d["ezio"]["configured"])

    def test_write_io_file_atomic(self):
        a = _bare_adapter()
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "io.json"
            with mock.patch.object(ipc_paths, "io_path", return_value=target), \
                 mock.patch.object(ipc_paths, "ensure_runtime_dir"):
                a._note_ezio(connected=True, inputs=[0] * 16)
                a._write_io_file()
                data = json.loads(target.read_text())
                self.assertIn("updated_at", data)
                self.assertEqual(data["ezio"]["inputs"], [0] * 16)

    def test_throttle_writes_only_after_interval(self):
        import adapter_jibot as mod
        a = _bare_adapter()
        a._ezi_io = mock.Mock()
        calls = {"write": 0, "output": 0}
        a._write_io_file = lambda: calls.__setitem__("write", calls["write"] + 1)
        # get_output / get_board_info coroutines
        async def _out():
            calls["output"] += 1
            return {"outputs": [0] * 16}
        async def _board():
            return {"description": "EZI-IO X"}
        a._ezi_io.get_output = _out
        a._ezi_io.get_board_info = _board

        import asyncio
        # 첫 호출(now=100): interval 경과 -> write 1, output 1
        asyncio.run(a._io_throttle_tick(100.0))
        # 두번째(now=101, <2.5초): skip
        asyncio.run(a._io_throttle_tick(101.0))
        # 세번째(now=103, >=2.5초): write 2, output 2
        asyncio.run(a._io_throttle_tick(103.0))
        self.assertEqual(calls["write"], 2)
        self.assertEqual(calls["output"], 2)
        self.assertEqual(a._ezio_board, "EZI-IO X")  # 1회 캐시


    def test_pio_read_inputs_notes_cache(self):
        """입력은 EZI IO digital input에서 온다 (in 1~8 -> pin 0~7)."""
        import asyncio

        a = _bare_adapter()
        a.config.pio_config.input_pins = [0, 1, 2, 3, 4, 5, 6, 7]
        bits = [0] * 16
        bits[0] = 1

        class _Ezi:
            async def get_input(self):
                return {"inputs": list(bits)}

        a._ezi_io = _Ezi()
        inputs = asyncio.run(pio_read_inputs(a, object()))
        self.assertEqual(inputs, {"1": "on", "2": "off", "3": "off", "4": "off", "5": "off", "6": "off", "7": "off", "8": "off"})
        self.assertEqual(a._pio_inputs, inputs)   # cached via _note_pio

    def test_io_throttle_tick_mirrors_ezio_bits_into_pio(self):
        """PIO in/out은 EZI IO 레지스터의 다른 이름이다. pio* 액션이 돌지 않아도
        주기 tick이 표시 값을 따라가야 한다."""
        import asyncio

        a = _bare_adapter()
        a._write_io_file = lambda: None
        bits = [0] * 16
        bits[2] = 1                      # EZI IO in2 -> PIO in 3
        a._note_ezio(connected=True, inputs=bits)

        class _Ezi:
            async def get_output(self):
                outs = [0] * 16
                outs[3] = 1              # EZI IO out3 -> PIO out 4
                return {"outputs": outs}

            async def get_board_info(self):
                return {"description": "EZI-IO X"}

        a._ezi_io = _Ezi()
        asyncio.run(a._io_throttle_tick(100.0))

        self.assertEqual(a._pio_inputs["3"], "on")
        self.assertEqual(a._pio_inputs["1"], "off")
        self.assertEqual(a._pio_inputs_at, a._ezio_inputs_at)
        self.assertEqual(a._pio_outputs["4"], "on")
        self.assertEqual(a._pio_outputs["1"], "off")
        self.assertEqual(a._pio_outputs_at, a._ezio_outputs_at)

    def test_pio_mirror_keeps_the_ezio_timestamp_when_the_read_stops(self):
        """EZI IO 읽기가 끊기면 PIO 값도 같이 늙어야 한다. 여기서 now를 찍으면
        stale 표시가 거짓말을 한다."""
        import asyncio

        a = _bare_adapter()
        a._write_io_file = lambda: None
        a._note_ezio(connected=True, inputs=[0] * 16)
        frozen_at = a._ezio_inputs_at

        class _Ezi:
            async def get_output(self):
                raise RuntimeError("board down")

            async def get_board_info(self):
                raise RuntimeError("board down")

        a._ezi_io = _Ezi()
        asyncio.run(a._io_throttle_tick(100.0))
        asyncio.run(a._io_throttle_tick(200.0))

        self.assertEqual(a._pio_inputs_at, frozen_at)   # 다시 찍지 않는다
        self.assertIsNone(a._pio_outputs_at)            # 출력은 읽힌 적이 없다

    def test_pio_mirror_keeps_last_values_when_the_pin_map_misses(self):
        """핀 맵이 통째로 어긋나면 옮길 점이 하나도 없다. 빈 dict를 그대로 넣으면
        방금 명령한 출력 표시가 지워지고 시각만 새로 찍힌다."""
        import asyncio

        a = _bare_adapter()
        a._write_io_file = lambda: None
        a.config.pio_config.input_pins = [30]            # 읽은 비트 밖
        a.config.pio_config.output_pin_map = {1: 20}     # 읽은 비트 밖
        a._note_pio(connected=True, inputs={"1": "on"}, outputs={"2": "on"})
        inputs_at, outputs_at = a._pio_inputs_at, a._pio_outputs_at
        a._note_ezio(connected=True, inputs=[0] * 16)

        class _Ezi:
            async def get_output(self):
                return {"outputs": [0] * 16}

            async def get_board_info(self):
                return {"description": "EZI-IO X"}

        a._ezi_io = _Ezi()
        asyncio.run(a._io_throttle_tick(100.0))

        self.assertEqual(a._pio_inputs, {"1": "on"})
        self.assertEqual(a._pio_inputs_at, inputs_at)
        self.assertEqual(a._pio_outputs, {"2": "on"})
        self.assertEqual(a._pio_outputs_at, outputs_at)

    def test_pio_disconnect_marks_disconnected_keeps_inputs(self):
        import asyncio
        a = _bare_adapter()
        a._note_pio(connected=True, inputs={"1": "on"})
        a._pio_client = mock.Mock(close=lambda: None)
        asyncio.run(pio_disconnect(a))
        self.assertFalse(a._pio_connected)
        self.assertEqual(a._pio_inputs, {"1": "on"})

    def test_execute_pio_action_exception_notes_error(self):
        import asyncio
        a = _bare_adapter()
        bad = mock.Mock(action_type="pioReadIn")
        bad.action_parameters = []
        def _boom(*args, **kwargs):
            raise RuntimeError("serial down")
        class _Ezi:
            async def get_input(self):
                _boom()

        a._ezi_io = _Ezi()
        result = asyncio.run(execute_pio_action(a, bad))
        self.assertFalse(result["ok"])
        self.assertIn("serial down", a._pio_error)

    def test_ezio_write_out_on_sets_output_and_refreshes_cache(self):
        import asyncio
        a = _bare_adapter()
        calls = []
        class FakeEzi:
            @staticmethod
            def output_bit(n):
                return 1 << (16 + n)
            async def turn_on_output(self, n):
                calls.append(("on", n))
            async def turn_off_output(self, n):
                calls.append(("off", n))
            async def get_output(self):
                return {"outputs": [0, 0, 0, 1] + [0] * 12}
        a._ezi_io = FakeEzi()
        action = mock.Mock(action_type="ezioWriteOut")
        action.action_parameters = [
            mock.Mock(key="index", value="3"),
            mock.Mock(key="state", value="on"),
        ]
        result = asyncio.run(execute_ezio_action(a, action))
        self.assertTrue(result["ok"])
        self.assertEqual(calls, [("on", 3)])              # 0-indexed pin 3
        self.assertEqual(a._ezio_outputs, [0, 0, 0, 1] + [0] * 12)  # cache refreshed

    def test_ezio_write_out_off_calls_turn_off(self):
        import asyncio
        a = _bare_adapter()
        calls = []
        class FakeEzi:
            async def turn_on_output(self, n):
                calls.append(("on", n))
            async def turn_off_output(self, n):
                calls.append(("off", n))
            async def get_output(self):
                return {"outputs": [0] * 16}
        a._ezi_io = FakeEzi()
        action = mock.Mock(action_type="ezioWriteOut")
        action.action_parameters = [
            mock.Mock(key="index", value="5"),
            mock.Mock(key="state", value="off"),
        ]
        result = asyncio.run(execute_ezio_action(a, action))
        self.assertTrue(result["ok"])
        self.assertEqual(calls, [("off", 5)])

    def test_ezio_write_out_uninitialized_fails(self):
        import asyncio
        a = _bare_adapter()
        a._ezi_io = None
        action = mock.Mock(action_type="ezioWriteOut")
        action.action_parameters = [
            mock.Mock(key="index", value="1"),
            mock.Mock(key="state", value="on"),
        ]
        result = asyncio.run(execute_ezio_action(a, action))
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
