"""EZI IO 출력 비트 맵: 쓴 핀과 되읽는 핀이 같은지 잠근다.

output_bit()가 한 칸 밀리면 turn_off_output(n)이 옆 핀(n-1)을 끄고, 정작 n은
어떤 배지를 눌러도 꺼지지 않는다(WebUi에서 "마지막 켜진 출력이 안 꺼짐").
"""

import asyncio
import struct
import unittest

from utils.ezi_io import EZIIOClient

_FAS_GET_OUTPUT = 0xC5
_FAS_SET_OUTPUT = 0xC6


class FakeBoard(EZIIOClient):
    """UDP 대신 16출력 보드의 SetOutput/GetOutput만 흉내 내는 client."""

    def __init__(self, output_word: int = 0):
        super().__init__("127.0.0.1")
        self.output_word = output_word

    async def _send(self, frame_type, data=b""):
        if frame_type == _FAS_SET_OUTPUT:
            set_mask, reset_mask = struct.unpack("<II", data)
            self.output_word = (
                (self.output_word | set_mask) & ~reset_mask & 0xFFFFFFFF
            )
            return {"frame_type": frame_type, "status": 0, "data": b""}
        if frame_type == _FAS_GET_OUTPUT:
            return {
                "frame_type": frame_type,
                "status": 0,
                "data": struct.pack("<II", self.output_word, 0),
            }
        raise AssertionError(f"unexpected frame type 0x{frame_type:02X}")


_ALL_OUTPUTS_ON = 0xFFFF << 16


class EziIoOutputBitTest(unittest.TestCase):
    def test_turn_on_lights_only_the_requested_pin(self):
        for pin in range(16):
            with self.subTest(pin=pin):
                board = FakeBoard()
                resp = asyncio.run(self._turn_on_and_read(board, pin))
                self.assertEqual(resp["outputs"][pin], 1)
                self.assertEqual(sum(resp["outputs"]), 1)

    def test_turn_off_clears_only_the_requested_pin(self):
        # 모든 출력이 켜진 상태에서 한 핀만 끈다 — 밀린 비트 맵이면 이웃이 꺼진다.
        for pin in range(16):
            with self.subTest(pin=pin):
                board = FakeBoard(_ALL_OUTPUTS_ON)
                resp = asyncio.run(self._turn_off_and_read(board, pin))
                self.assertEqual(resp["outputs"][pin], 0)
                self.assertEqual(sum(resp["outputs"]), 15)

    def test_single_pin_read_back_confirms_the_write(self):
        # elevator/airshower의 get_output_pin polling loop가 빠져나올 수 있어야 한다.
        for pin in range(16):
            with self.subTest(pin=pin):
                board = FakeBoard()

                async def scenario():
                    await board.turn_on_output(pin)
                    on = await board.get_output_pin(pin)
                    await board.turn_off_output(pin)
                    return on, await board.get_output_pin(pin)

                self.assertEqual(asyncio.run(scenario()), (1, 0))

    def test_clear_all_outputs_clears_every_pin(self):
        board = FakeBoard(_ALL_OUTPUTS_ON)

        async def scenario():
            await board.clear_all_outputs()
            return await board.get_output()

        self.assertEqual(asyncio.run(scenario())["outputs"], [0] * 16)

    def test_output_bit_rejects_pins_outside_the_board(self):
        for pin in (-1, 16):
            with self.subTest(pin=pin):
                with self.assertRaises(ValueError):
                    EZIIOClient.output_bit(pin)

    @staticmethod
    async def _turn_on_and_read(board, pin):
        await board.turn_on_output(pin)
        return await board.get_output()

    @staticmethod
    async def _turn_off_and_read(board, pin):
        await board.turn_off_output(pin)
        return await board.get_output()


if __name__ == "__main__":
    unittest.main()
