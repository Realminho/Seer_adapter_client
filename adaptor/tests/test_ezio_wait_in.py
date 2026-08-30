"""ezioWaitIn 조건 대기 단위 테스트 (하드웨어 없이).

recipe가 문·설비 신호를 확인하려면 "읽고 끝"이 아니라 "기대 상태가 될 때까지
제한 시간 안에 기다렸다가 아니면 실패"가 필요하다. ezioReadIn은 전자만 한다.
"""

import asyncio
from types import SimpleNamespace

from config.config import EziConfig
from extensions.ezio import execute_ezio_action


def _action(**params):
    return SimpleNamespace(
        action_id="wait",
        action_type="ezioWaitIn",
        action_parameters=[
            SimpleNamespace(key=key, value=value) for key, value in params.items()
        ],
    )


class _FakeEziIo:
    """get_input()을 부를 때마다 준비된 bit 배열을 차례로 돌려준다."""

    def __init__(self, *reads):
        self._reads = list(reads)
        self.calls = 0
        self.last_error = ""

    async def get_input(self):
        self.calls += 1
        bits = self._reads[min(self.calls - 1, len(self._reads) - 1)]
        return {"inputs": list(bits)}


def _bits(*on_indexes):
    bits = [0] * 16
    for index in on_indexes:
        bits[index] = 1
    return bits


def _adapter(ezi_io):
    return SimpleNamespace(
        _ezi_io=ezi_io,
        state=None,
        config=SimpleNamespace(ezi_config=EziConfig()),
        _note_ezio=lambda **_kwargs: None,
    )


def test_wait_in_finishes_when_the_input_reaches_the_expected_state():
    ezi_io = _FakeEziIo(_bits(), _bits(), _bits(4))
    result = asyncio.run(
        execute_ezio_action(
            _adapter(ezi_io), _action(index=4, state="on", timeoutSec=5)
        )
    )
    assert result["ok"] is True
    assert "in4=on" in result["message"]
    assert ezi_io.calls == 3  # 기대 상태가 될 때까지 다시 읽었다


def test_wait_in_fails_with_timeout_when_the_input_never_arrives():
    ezi_io = _FakeEziIo(_bits())
    result = asyncio.run(
        execute_ezio_action(
            _adapter(ezi_io), _action(index=4, state="on", timeoutSec=0.05)
        )
    )
    assert result["ok"] is False
    assert result["failedReason"] == "timeout"
    assert "in4=on" in result["message"]
    assert result["inputs"]["5"] == "off"  # 표시용 사전은 1부터 센다


def test_wait_in_uses_the_same_pin_numbering_as_ezio_write_out():
    """index는 ezioWriteOut과 같은 0-based EZI IO 점 번호다.

    extensions.hcl의 open_door_pin = 4가 그대로 들어와야 하므로 표시용 1-based
    번호와 섞이면 옆 핀을 보게 된다.
    """
    result = asyncio.run(
        execute_ezio_action(
            _adapter(_FakeEziIo(_bits(4))),
            _action(index=3, state="on", timeoutSec=0.05),
        )
    )
    assert result["ok"] is False

    result = asyncio.run(
        execute_ezio_action(
            _adapter(_FakeEziIo(_bits(4))),
            _action(index=4, state="on", timeoutSec=0.05),
        )
    )
    assert result["ok"] is True


def test_wait_in_falls_back_to_the_configured_default_timeout():
    ezi_io = _FakeEziIo(_bits())
    adapter = _adapter(ezi_io)
    adapter.config.ezi_config.wait_in_default_timeout_sec = 0.05
    result = asyncio.run(execute_ezio_action(adapter, _action(index=4, state="on")))
    assert result["ok"] is False
    assert "0.05s" in result["message"]


def test_wait_in_reports_a_read_failure_instead_of_waiting_out_the_timeout():
    """읽기 자체가 깨지면 제한 시간을 다 쓰고 timeout이라고 보고하면 안 된다."""

    class _Broken:
        last_error = "socket closed"

        async def get_input(self):
            return {}

    result = asyncio.run(
        execute_ezio_action(
            _adapter(_Broken()), _action(index=4, state="on", timeoutSec=5)
        )
    )
    assert result["ok"] is False
    assert result["failedReason"] != "timeout"
    assert "socket closed" in result["message"]
