import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from extensions.pio import pio_establish, pio_pair_until_go


class _Ezi:
    def __init__(self, go_states):
        self.go_states = iter(go_states)
        self.output_calls = []

    async def turn_on_output(self, pin):
        self.output_calls.append((pin, "on"))

    async def turn_off_output(self, pin):
        self.output_calls.append((pin, "off"))

    async def get_input_pin(self, _pin):
        return next(self.go_states)


def _adapter(go_states):
    return SimpleNamespace(_ezi_io=_Ezi(go_states))


def _link(timing):
    return {
        "media": 2,
        "station_id": "123456",
        "channel": 250,
        "bc_port": 0,
        "oht_number": "OHT123",
        "select_pin": 15,
        "go_pin": 15,
        "settle_sec": 0.0,
        "select_off_timing": timing,
        "pair_confirmation": "go",
        "select_off_delay_sec": 0.0,
        "poll_sec": 0.0,
    }


class SelectOffTimingTest(unittest.IsolatedAsyncioTestCase):
    async def test_bc_reply_confirmation_does_not_wait_for_go(self):
        adapter = _adapter([])
        link = _link("after_bc")
        link["pair_confirmation"] = "bc_reply"
        # checksum: sum(b"BC=OK") & 0xff == 0x5c
        with mock.patch(
            "extensions.pio.call_pio",
            new=mock.AsyncMock(return_value="[BC=OK5C]"),
        ):
            paired, reply, attempts = await pio_establish(
                adapter,
                link=link,
                wait_sec=0.01,
                pair_timeout_sec=1.0,
            )

        self.assertTrue(paired)
        self.assertEqual(reply, "[BC=OK5C]")
        self.assertEqual(attempts, 1)
        self.assertEqual(adapter._ezi_io.output_calls, [(15, "on"), (15, "off")])

    async def test_after_go_keeps_select_on_across_bc_retries(self):
        adapter = _adapter([False, True])
        with mock.patch(
            "extensions.pio.call_pio",
            new=mock.AsyncMock(return_value="[BC=OKF4]"),
        ) as call:
            paired, _reply, attempts = await pio_pair_until_go(
                adapter,
                link=_link("after_go"),
                wait_sec=0.01,
                pair_timeout_sec=1.0,
            )

        self.assertTrue(paired)
        self.assertEqual(attempts, 2)
        self.assertEqual(call.await_count, 2)
        self.assertEqual(adapter._ezi_io.output_calls, [(15, "on"), (15, "off")])

    async def test_after_bc_preserves_the_legacy_toggle_per_attempt(self):
        adapter = _adapter([False, True])
        with mock.patch(
            "extensions.pio.call_pio",
            new=mock.AsyncMock(return_value="[BC=OKF4]"),
        ):
            paired, _reply, attempts = await pio_pair_until_go(
                adapter,
                link=_link("after_bc"),
                wait_sec=0.01,
                pair_timeout_sec=1.0,
            )

        self.assertTrue(paired)
        self.assertEqual(attempts, 2)
        self.assertEqual(
            adapter._ezi_io.output_calls,
            [(15, "on"), (15, "off"), (15, "on"), (15, "off")],
        )


if __name__ == "__main__":
    unittest.main()
