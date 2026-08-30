"""No-hardware replacements for JIBOT-only EZI devices in a SEER process.

The shared Adapter creates external EZI IO/motor clients unconditionally.
SEER exposes its own IO and motor APIs, so opening those JIBOT UDP devices is
both unnecessary and makes equipment-free simulation fail.  These classes
only satisfy the shared Adapter's construction and status interfaces; SEER IO
continues to go through :class:`seer_client.client.SeerClient`.
"""

from __future__ import annotations

import sys
import types
from typing import Any


class SeerNullEziIo:
    """In-memory EZI-shaped object used only by the shared Adapter shell."""

    def __init__(self, ip: str = "", port: int = 3002, timeout: float = 2.0):
        self.addr = (ip, port)
        self.timeout = timeout
        self.last_error = ""
        self._inputs = [0] * 16
        self._outputs = [0] * 16

    async def connect(self):
        return None

    async def get_board_info(self):
        return {
            "status": 0,
            "slave_type": 0,
            "description": "SEER drop-in: JIBOT EZI IO disabled",
        }

    async def get_input(self):
        return {
            "comm_status": 0,
            "input_raw": 0,
            "latch_raw": 0,
            "inputs": list(self._inputs),
            "latches": [0] * 16,
        }

    async def get_input_pin(self, pin: int):
        return self._inputs[int(pin)]

    async def get_output(self):
        return {
            "comm_status": 0,
            "output_raw": sum(value << (16 + index) for index, value in enumerate(self._outputs)),
            "outputs": list(self._outputs),
            "run_stop": [0] * 16,
        }

    async def turn_on_output(self, pin: int):
        self._outputs[int(pin)] = 1
        return {"status": 0}

    async def turn_off_output(self, pin: int):
        self._outputs[int(pin)] = 0
        return {"status": 0}

    async def clear_all_outputs(self):
        self._outputs = [0] * 16
        return {"status": 0}

    def close(self):
        return None


class SeerNullEziMotor:
    """Non-moving EZI motor shell; real SEER motor control uses OTHER TCP."""

    def __init__(self, ip: str = "", *args: Any, **kwargs: Any):
        del args, kwargs
        self.addr = (ip, 3002)
        self.servo_on = False
        self.position = 0

    async def get_board_info(self):
        return {
            "status": 0,
            "description": "SEER drop-in: JIBOT EZI motor disabled",
        }

    async def servo_enable(self, enabled: bool):
        self.servo_on = bool(enabled)
        return {"status": 0}

    async def move_single_axis_abs_pos(self, position: int, speed: int):
        del speed
        self.position = int(position)
        return {"status": 0}

    async def goto_limit_minus(self, speed: int):
        del speed
        return {"status": 0}

    async def goto_limit_plus(self, speed: int):
        del speed
        return {"status": 0}

    async def goto_origin(self):
        self.position = 0
        return {"status": 0}

    async def move_stop(self):
        return {"status": 0}

    async def emergency_stop(self):
        return {"status": 0}

    async def alarm_reset(self):
        return {"status": 0}

    async def clear_position(self):
        self.position = 0
        return {"status": 0}

    async def initialized_open_close_encoder_position(self, origin_encoder_offset=0):
        offset = int(origin_encoder_offset)
        return {"open_gripper": offset, "close_gripper": -offset}

    async def get_actual_position(self):
        return self.position

    async def get_axis_status(self):
        return {"servo_on": self.servo_on, "in_position": True}

    async def is_origin_sensor_on(self):
        return self.position == 0

    async def is_origin_done(self):
        return self.position == 0

    async def is_in_position(self):
        return True

    async def is_motion_done(self):
        return True

    async def is_limit_plus_done(self):
        return False

    async def is_limit_minus_done(self):
        return False

    async def is_error_all(self):
        return False

    def close(self):
        return None


def install_dropin_device_modules() -> None:
    """Install EZI-shaped modules before original ``main()`` imports them."""

    io_module = types.ModuleType("utils.ezi_io")
    io_module.EZIIOClient = SeerNullEziIo
    motor_module = types.ModuleType("utils.ezi_motor")
    motor_module.EziMotorClient = SeerNullEziMotor
    sys.modules["utils.ezi_io"] = io_module
    sys.modules["utils.ezi_motor"] = motor_module

    already_loaded = sys.modules.get("adapter_jibot")
    if already_loaded is not None:
        already_loaded.EZIIOClient = SeerNullEziIo
        already_loaded.EziMotorClient = SeerNullEziMotor

