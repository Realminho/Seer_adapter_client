"""하드웨어 없는 ``--simulator`` 실행용 가짜 EZI IO / PIO 직렬 보드.

실장비가 없는 개발 장비에서는 ``ezi_io`` 주소가 비어 있고, 그러면 pio*/ezio*
action은 전부 "EZI IO is not initialized"로 첫 step에서 끝난다. 문 하나 눌러
보려면 보드가 필요한 셈이라 recipe의 순서·cleanup·timeout을 확인할 방법이 없다.

여기 두 클래스는 실기 규칙만 흉내낸다:

* 출력은 쓴 비트에서 그대로 되읽힌다. FAS_SetOutput/FAS_GetOutput은 output n을
  비트 ``16 + n``에 두므로(``utils.ezi_io.EZIIOClient.output_bit``) 가짜도 같은
  규약을 쓴다 — 어긋나면 ``pioWriteOut``의 read-back 검증이 실기와 다르게 통과한다.
* 설비 pairing은 "SELECT가 올라가 있는 동안 BC가 오면 성립, BC 없이 SELECT만
  토글하면 해제"다. ``extensions.pio``의 ``pio_pair``/``pio_unpair``가 실제로
  밟는 절차이고, GO 입력이 그 상태를 따라간다.
* 출력 n은 입력 n으로 되돌아온다. ``utils/elevator.py``가 floor pin을 출력으로
  걸고 같은 번호의 입력으로 도착을 확인하기 때문이다. GO 입력만은 예외로
  pairing 상태를 따른다 (기본 설정에서 SELECT 출력과 GO 입력이 둘 다 15번이다).

흉내내지 않는 것: 설비가 스스로 올리는 신호(문 열림 확인, 점유, airflow)와 EZI
모터(clamp)다. 그 값들은 저장소에 확정된 게 없어 짐작해 넣으면 "엉뚱한 점을 보고
통과"하게 된다. 필요해지면 SimulatedFacility에 명시적으로 추가한다.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

OUTPUT_SHIFT = 16
PIN_COUNT = 16


class SimulatedFacility:
    """EZI IO 쪽과 PIO 직렬 쪽이 공유하는 설비 상태.

    두 클래스가 같은 인스턴스를 보기 때문에 "SELECT를 올린 채 BC" 순서가 맞아야만
    pairing이 성립한다. 순서를 헷갈리면 실기처럼 GO가 올라오지 않는다.
    """

    def __init__(self, *, select_pin: int = 15, go_pin: int = 15) -> None:
        self.select_pin = int(select_pin)
        self.go_pin = int(go_pin)
        self.paired = False
        self.bc_count = 0
        # SELECT가 올라간 뒤 BC가 왔는지 판정하기 위한 표식.
        self._bc_count_at_select: Optional[int] = None

    def select_raised(self) -> None:
        self._bc_count_at_select = self.bc_count

    def select_lowered(self) -> None:
        if self._bc_count_at_select is None:
            return
        got_bc = self.bc_count > self._bc_count_at_select
        self._bc_count_at_select = None
        # BC를 받았으면 pairing, BC 없이 SELECT만 토글했으면 해제.
        self.paired = bool(got_bc)

    def bc_received(self) -> None:
        self.bc_count += 1


class SimulatedEZIIO:
    """``utils.ezi_io.EZIIOClient``와 같은 표면을 가진 가짜 IO 보드."""

    def __init__(
        self,
        facility: Optional[SimulatedFacility] = None,
        *,
        echo_outputs_to_inputs: bool = True,
    ) -> None:
        self.facility = facility if facility is not None else SimulatedFacility()
        self.echo_outputs_to_inputs = echo_outputs_to_inputs
        self.outputs: List[int] = [0] * PIN_COUNT
        self.inputs: List[int] = [0] * PIN_COUNT
        # 검증용 기록: (pin, "on"/"off") 순서.
        self.output_history: List[Tuple[int, str]] = []
        self.last_error = ""
        self.closed = False

    # --- lifecycle ---------------------------------------------------------
    async def connect(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    async def get_board_info(self) -> Dict[str, Any]:
        return {
            "status": 0,
            "slave_type": 0,
            "description": "simulated EZI IO board (no hardware)",
        }

    # --- outputs -----------------------------------------------------------
    @staticmethod
    def output_bit(n: int) -> int:
        if not 0 <= n <= 15:
            raise ValueError("Output pin must be 0~15")
        return 1 << (OUTPUT_SHIFT + n)

    async def set_output(self, set_mask: int = 0, reset_mask: int = 0) -> int:
        # 실기와 같은 순서로 적용한다: reset 먼저, 그 다음 set.
        for pin in range(PIN_COUNT):
            bit = 1 << (OUTPUT_SHIFT + pin)
            if reset_mask & bit:
                self._apply_output(pin, 0)
        for pin in range(PIN_COUNT):
            bit = 1 << (OUTPUT_SHIFT + pin)
            if set_mask & bit:
                self._apply_output(pin, 1)
        return 0

    def _apply_output(self, pin: int, value: int) -> None:
        previous = self.outputs[pin]
        self.outputs[pin] = value
        if pin == self.facility.select_pin:
            if value and not previous:
                self.facility.select_raised()
            elif previous and not value:
                self.facility.select_lowered()

    async def turn_on_output(self, n: int) -> int:
        result = await self.set_output(set_mask=self.output_bit(n))
        self.output_history.append((int(n), "on"))
        return result

    async def turn_off_output(self, n: int) -> int:
        result = await self.set_output(reset_mask=self.output_bit(n))
        self.output_history.append((int(n), "off"))
        return result

    async def clear_all_outputs(self) -> int:
        return await self.set_output(reset_mask=0xFFFF << OUTPUT_SHIFT)

    async def get_output(self) -> Dict[str, Any]:
        output_raw = 0
        for pin, value in enumerate(self.outputs):
            if value:
                output_raw |= 1 << (OUTPUT_SHIFT + pin)
        return {
            "comm_status": 0,
            "output_raw": output_raw,
            "run_stop_raw": 0,
            "outputs": list(self.outputs),
            "run_stop": [0] * PIN_COUNT,
        }

    async def get_output_pin(self, pin: int) -> int:
        if not 0 <= pin <= 15:
            raise ValueError("Output pin must be 0~15")
        return (await self.get_output())["outputs"][pin]

    async def get_run_stop_pin(self, pin: int) -> int:
        if not 0 <= pin <= 15:
            raise ValueError("Run/Stop pin must be 0~15")
        return 0

    # --- inputs ------------------------------------------------------------
    def _input_bits(self) -> List[int]:
        bits = list(self.inputs)
        if self.echo_outputs_to_inputs:
            for pin, value in enumerate(self.outputs):
                if value:
                    bits[pin] = 1
        # GO는 echo가 아니라 pairing 상태다. SELECT 출력과 같은 번호일 수 있으므로
        # echo 뒤에 덮어써야 한다.
        bits[self.facility.go_pin] = 1 if self.facility.paired else 0
        return bits

    async def get_input(self) -> Dict[str, Any]:
        bits = self._input_bits()
        input_raw = 0
        for pin, value in enumerate(bits):
            if value:
                input_raw |= 1 << pin
        return {
            "comm_status": 0,
            "input_raw": input_raw,
            "latch_raw": 0,
            "inputs": bits,
            "latches": [0] * PIN_COUNT,
        }

    async def get_input_pin(self, pin: int) -> int:
        return self._input_bits()[int(pin)]


class SimulatedPIOMaster:
    """``utils.pio.PIOMaster``와 같은 표면을 가진 가짜 직렬 master.

    직렬 PIO는 BC pairing 전용이다 (설비로 나가는 신호선은 EZI IO가 구동한다).
    그래서 여기서 하는 일은 "포트를 연 척하고 BC를 설비 상태에 알리는 것"뿐이다.
    """

    def __init__(self, facility: Optional[SimulatedFacility] = None) -> None:
        self.facility = facility if facility is not None else SimulatedFacility()
        self.ser = SimpleNamespace(is_open=False)
        self.bc_calls: List[Tuple[Any, ...]] = []

    def connect(self) -> None:
        self.ser = SimpleNamespace(is_open=True)

    def close(self) -> None:
        self.ser = SimpleNamespace(is_open=False)

    def __enter__(self) -> "SimulatedPIOMaster":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    @staticmethod
    def checksum_hex(payload: str) -> str:
        return f"{sum(payload.encode()) & 0xFF:02X}"

    def make_frame(self, payload: str, head: str = "<", tail: str = ">") -> str:
        return f"{head}{payload}{self.checksum_hex(payload)}{tail}"

    def send_bc(
        self,
        media: Any,
        station_id: Any,
        channel: Any,
        port: Any,
        oht_num: Any,
        wait_sec: Optional[float] = None,
    ) -> str:
        self.bc_calls.append((media, station_id, channel, port, oht_num, wait_sec))
        # 포트가 닫혀 있으면 실기처럼 아무 응답도 없다.
        if not bool(getattr(self.ser, "is_open", False)):
            return ""
        self.facility.bc_received()
        return self.make_frame("BC=OK")

    def send_raw(self, data: Any, wait_sec: Optional[float] = None) -> str:
        if not bool(getattr(self.ser, "is_open", False)):
            return ""
        return self.make_frame("OK")

    def send_channel(self, channel: Any, wait_sec: Optional[float] = None) -> str:
        return self.send_raw(f"C={channel}", wait_sec)

    def monitor_data(self, channel: Any, wait_sec: Optional[float] = None) -> str:
        # 입력은 EZI IO에서 읽는다 (extensions.ezio.read_ezio_input_bits).
        # 직렬 monitor는 런타임 경로가 아니므로 형식만 맞춘 빈 프레임을 준다.
        if not bool(getattr(self.ser, "is_open", False)):
            return ""
        return self.make_frame("IN=00000000")


def make_simulated_io(ezi_config: Any) -> Tuple[SimulatedEZIIO, SimulatedPIOMaster]:
    """설비 상태를 공유하는 (가짜 EZI IO, 가짜 PIO master) 한 쌍을 만든다.

    SELECT 출력·GO 입력 번호는 ``extension "ezi"``의 select/go에서 가져온다.
    두 클래스가 같은 SimulatedFacility를 보기 때문에 pairing 순서가 맞아야
    GO가 올라온다.
    """
    facility = SimulatedFacility(
        select_pin=int(getattr(ezi_config, "select", 15)),
        go_pin=int(getattr(ezi_config, "go", 15)),
    )
    return SimulatedEZIIO(facility), SimulatedPIOMaster(facility)
