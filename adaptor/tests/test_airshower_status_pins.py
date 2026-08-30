"""에어샤워가 설비 상태를 어느 레지스터에서 읽는지 고정한다.

occupied/fun_working은 설비가 로봇에게 알려주는 신호라 EZI IO **입력**이다.
출력 레지스터를 읽으면 우리가 쓴 값만 보이므로 상태가 항상 0으로 나오고,
그러면 vacancy 대기는 검사 없이 즉시 통과하고 airflow 대기는 타임아웃까지
멈춰 선다. 문·층 핀 읽기는 자기가 쓴 값을 되읽는 확인이라 출력이 맞다.
"""
import asyncio
import copy
import unittest

from config.config import get_config
from utils.airshower import ASWorkflow


class _Ezi:
    """지정한 순서대로 입력 핀 값을 돌려주는 EZI IO 가짜."""

    def __init__(self, input_seq=None):
        self.input_seq = {pin: list(seq) for pin, seq in (input_seq or {}).items()}
        self.input_reads = []
        self.output_reads = []

    async def get_input_pin(self, pin):
        self.input_reads.append(pin)
        seq = self.input_seq.get(pin)
        if not seq:
            return 0
        return seq.pop(0) if len(seq) > 1 else seq[0]

    async def get_output_pin(self, pin):
        self.output_reads.append(pin)
        return 0


def _workflow(ezi):
    config = copy.deepcopy(get_config())
    config.air_shower_config.poll_interval_sec = 0.0
    return ASWorkflow(door_open_pin=0, action="ENTER", config_data=config, ezi_io=ezi)


class AirShowerStatusPinTest(unittest.TestCase):
    def test_vacancy_wait_reads_the_occupied_input_until_it_clears(self):
        config = get_config()
        occupied = config.air_shower_config.occupied
        ezi = _Ezi({occupied: [1, 1, 0]})
        wf = _workflow(ezi)

        self.assertTrue(asyncio.run(wf.handle_waiting_for_vacancy()))
        # 점유가 풀릴 때까지 실제로 기다렸다 (한 번 읽고 끝나지 않았다)
        self.assertGreater(ezi.input_reads.count(occupied), 1)
        # 출력 레지스터는 건드리지 않는다 — 거기엔 설비 상태가 없다
        self.assertEqual(ezi.output_reads, [])

    def test_airflow_wait_reads_the_fun_working_input_through_on_and_off(self):
        config = get_config()
        fun = config.air_shower_config.fun_working
        ezi = _Ezi({fun: [0, 1, 1, 0]})
        wf = _workflow(ezi)

        self.assertTrue(asyncio.run(wf.handle_waiting_for_airflow()))
        self.assertGreater(ezi.input_reads.count(fun), 1)
        self.assertEqual(ezi.output_reads, [])


if __name__ == "__main__":
    unittest.main()
