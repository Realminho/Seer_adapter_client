"""Stream JIBOT BMS voltage/current from ROS /jrobot_status into the vehicle.

UmGetBatteryInfo over TCP 7273 is a firmware stub (returns zeros) on this robot;
the real BMS truth is only on ROS topic /jrobot_status (jarvis_msgs/RobotStatus).
The adapter core stays ROS-free: this listener keeps ONE `rostopic echo -p`
subprocess alive (system ROS env / python 3.8) and parses its CSV stdout. The
parsed values feed the existing VDA5050 batteryState/powerSupply fields via
vehicle.set_bms(). See docs/reference/jibot-charging-dock-bms.md.
"""

import asyncio
import csv
from typing import Optional, Tuple


def find_bms_columns(header: str) -> Tuple[Optional[int], Optional[int]]:
    """Map a `rostopic echo -p` CSV header to (voltage_idx, current_idx).

    Column names look like `field.bms_voltage`; match by suffix so a prefix or
    nesting change does not break it. Either index is None when absent.
    """
    voltage_idx = current_idx = None
    for i, name in enumerate(c.strip() for c in header.split(",")):
        if name.endswith("bms_voltage"):
            voltage_idx = i
        elif name.endswith("bms_current"):
            current_idx = i
    return voltage_idx, current_idx


def parse_bms_row(row: str, voltage_idx, current_idx) -> Optional[Tuple[float, float]]:
    """Extract (voltage, current) floats from one CSV data row, or None."""
    if voltage_idx is None or current_idx is None:
        return None
    parts = [p.strip() for p in row.split(",")]
    if len(parts) <= max(voltage_idx, current_idx):
        return None
    try:
        return float(parts[voltage_idx]), float(parts[current_idx])
    except ValueError:
        return None


SAFETY_FIELDS = (
    "system_status",
    "system_error_code",
    "motor_enable",
    "motor_enable_status",
    "hmi_estop",
    "bumpe_stop",
    "pc_estop",
    "motor_error",
    "pc_enable",
    "charge",
    "l_status",
    "l_error",
    "r_status",
    "r_error",
    "lift_status",
    "rotate_status",
)


def find_robot_status_columns(header):
    """Map a `rostopic echo -p` CSV header to {safety_field: column index}.

    Matches a column when its leaf name equals the field exactly or ends with
    '.' + field (full dot-segment match). Raw suffix matching would collide
    (e.g., r_error wrongly matching field.motor_error). Only SAFETY_FIELDS
    are returned.
    """
    columns = {}
    names = next(csv.reader([header]))
    for i, name in enumerate(col.strip() for col in names):
        for field in SAFETY_FIELDS:
            # full dot-segment match: leaf name이 정확히 field거나 ".field"로 끝남.
            # 단순 endswith는 r_error가 field.motor_error("...r_error")에 오매칭됨.
            if name == field or name.endswith("." + field):
                columns[field] = i
    return columns


def parse_robot_safety_row(row, columns):
    """Extract {safety_field: value-string} from one CSV data row.

    Uses the csv module so a comma/quote inside system_status is handled.
    Returns {} when the row is too short for the mapped columns.
    """
    if not columns:
        return {}
    parts = next(csv.reader([row]))
    if len(parts) <= max(columns.values()):
        return {}
    return {field: parts[idx].strip() for field, idx in columns.items()}


class BmsRosListener:
    """Keeps one `rostopic echo -p` child alive and caches BMS values on the
    vehicle. The adapter stays ROS-free: ROS lives only in the child process."""

    def __init__(self, vehicle, cfg, spawn=None, sleep=None):
        self._vehicle = vehicle
        self._cfg = cfg
        # Spawn default is resolved lazily in _run_once (Task 5) so this class is
        # constructible before _default_spawn exists / without a real ROS env.
        self._spawn = spawn
        self._sleep = sleep or asyncio.sleep

    async def _consume(self, stdout):
        voltage_idx = current_idx = None
        safety_columns = {}
        while True:
            try:
                raw = await asyncio.wait_for(
                    stdout.readline(), timeout=self._cfg.stale_after_sec
                )
            except asyncio.TimeoutError:
                self._vehicle.clear_bms()  # no fresh row within the window
                self._vehicle.clear_robot_safety()
                continue
            if not raw:
                self._vehicle.clear_robot_safety()
                return  # EOF: child exited
            line = (
                raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray))
                else str(raw)
            ).strip()
            if not line:
                continue
            if line.startswith("%time"):
                # CSV 헤더(재시작 시 재등장). 단일 BMS 컬럼에 의존하지 않는다.
                voltage_idx, current_idx = find_bms_columns(line)
                safety_columns = find_robot_status_columns(line)
                continue
            parsed = parse_bms_row(line, voltage_idx, current_idx)
            if parsed is not None:
                self._vehicle.set_bms(parsed[0], parsed[1])
            safety = parse_robot_safety_row(line, safety_columns)
            if safety:
                self._vehicle.set_robot_safety(safety)

    def command(self):
        prefix = (
            f"source {self._cfg.ros_setup}; "
            f"export ROS_MASTER_URI={self._cfg.ros_master_uri}; "
        )
        inner = f"exec rostopic echo -p {self._cfg.topic}"
        return ["bash", "-lc", prefix + inner]

    async def _default_spawn(self):
        return await asyncio.create_subprocess_exec(
            *self.command(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def _terminate(self, proc):
        try:
            if proc.returncode is None:
                proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=3)
        except Exception:
            pass

    async def _run_once(self):
        spawn = self._spawn or self._default_spawn  # lazy default (see __init__)
        proc = await spawn()
        try:
            await self._consume(proc.stdout)
        finally:
            await self._terminate(proc)

    async def run(self):
        """Supervise the child forever. Never raises out of normal operation:
        any error is logged and retried after a backoff. Cancellation
        (adapter shutdown) propagates normally."""
        if not self._cfg.enabled:
            return
        while True:
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # spawn failure, decode error, etc.
                print(f"[BMS ROS] listener error: {exc}")
            self._vehicle.clear_bms()
            await self._sleep(self._cfg.restart_backoff_sec)


def start_bms_listener(vehicle, cfg, simulator):
    """Create the listener task, or None when it should not run.

    Returns None under --simulator (no ROS) or when disabled in config.
    """
    if simulator or not cfg.enabled:
        return None
    listener = BmsRosListener(vehicle, cfg)
    return asyncio.create_task(listener.run())
