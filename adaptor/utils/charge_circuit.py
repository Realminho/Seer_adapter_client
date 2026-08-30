"""Charge-relay control for JIBOT in-place charging.

In-place charge = hold the charge relay closed by repeatedly publishing
ROS /jcmd jarvis_msgs/Cmd{cmd:3, arg_int8:1} (CloseChargingCircuit); arg_int8:0
opens it. The relay only stays closed while the assertion is held, so the
production implementation keeps a `rostopic pub -r` subprocess alive.

The adapter core stays ROS-free: only SubprocessChargeCircuit touches ROS, and
only via subprocess. NullChargeCircuit is the no-op default (simulator / disabled).
"""

import atexit
import subprocess


class ChargeCircuit:
    """Interface: hold/release the charge relay."""

    @property
    def is_holding(self) -> bool:
        raise NotImplementedError

    def start_hold(self) -> None:
        raise NotImplementedError

    def stop_hold(self) -> None:
        raise NotImplementedError


class NullChargeCircuit(ChargeCircuit):
    """No-op circuit used when in-place charge is disabled (simulator/non-JIBOT)."""

    def __init__(self) -> None:
        self._holding = False

    @property
    def is_holding(self) -> bool:
        return self._holding

    def start_hold(self) -> None:
        self._holding = True

    def stop_hold(self) -> None:
        self._holding = False


class FakeChargeCircuit(ChargeCircuit):
    """Test double that records calls."""

    def __init__(self) -> None:
        self._holding = False
        self.start_calls = 0
        self.stop_calls = 0

    @property
    def is_holding(self) -> bool:
        return self._holding

    def start_hold(self) -> None:
        if self._holding:
            return
        self._holding = True
        self.start_calls += 1

    def stop_hold(self) -> None:
        if not self._holding:
            return
        self._holding = False
        self.stop_calls += 1


# jarvis_msgs/Cmd YAML for cmd:3 (charge relay). arg_int8 1=close(on), 0=open(off).
_CLOSE_MSG = '{cmd: 3, data: {arg_int8: 1, arg_int32: 0, arg_str: ""}}'
_OPEN_MSG = '{cmd: 3, data: {arg_int8: 0, arg_int32: 0, arg_str: ""}}'


def _default_spawn(cmd):
    return subprocess.Popen(cmd)


class SubprocessChargeCircuit(ChargeCircuit):
    """Holds the charge relay by keeping a `rostopic pub -r` subprocess alive."""

    def __init__(self, cfg, spawn=None):
        self._cfg = cfg
        self._spawn = spawn or _default_spawn
        self._proc = None
        # Safety net: kill an orphaned hold publisher on process exit so the
        # relay assertion stops (relay opens) however the adapter shuts down.
        atexit.register(self._terminate_on_exit)

    def _terminate_on_exit(self):
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    @property
    def is_holding(self) -> bool:
        return self._proc is not None

    def _ros_prefix(self) -> str:
        return (
            f"source {self._cfg.ros_setup}; "
            f"export ROS_MASTER_URI={self._cfg.ros_master_uri}; "
        )

    def hold_command(self):
        inner = (
            f"exec rostopic pub -r {self._cfg.publish_rate_hz} "
            f"{self._cfg.jcmd_topic} jarvis_msgs/Cmd '{_CLOSE_MSG}'"
        )
        return ["bash", "-lc", self._ros_prefix() + inner]

    def off_command(self):
        inner = (
            f"rostopic pub -1 {self._cfg.jcmd_topic} jarvis_msgs/Cmd '{_OPEN_MSG}'"
        )
        return ["bash", "-lc", self._ros_prefix() + inner]

    def start_hold(self) -> None:
        if self._proc is not None:
            return
        self._proc = self._spawn(self.hold_command())

    def stop_hold(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=getattr(self._cfg, "terminate_timeout_sec", 3.0))
            except Exception as exc:
                print(f"[CHARGE CIRCUIT] terminate hold failed: {exc}")
        # Best-effort single open (relay OFF).
        off = None
        try:
            off = self._spawn(self.off_command())
            off.wait(timeout=getattr(self._cfg, "off_timeout_sec", 15.0))
        except subprocess.TimeoutExpired:
            # `rostopic pub -1` latches for ~3s on top of sourcing the ROS env
            # and registering a node, so a busy board can overrun the budget
            # with the publish still in flight -- on .61 (2026-08-20 00:31) the
            # message did go out, 1.5s after we had given up on it. Leaving the
            # process behind orphans something that still talks to /jcmd, so
            # kill it, and say the relay state is unknown rather than reporting
            # this as a plain failure: it may well have opened.
            print("[CHARGE CIRCUIT] open-relay publish timed out; relay state unknown")
            self._kill(off)
        except Exception as exc:
            print(f"[CHARGE CIRCUIT] open-relay publish failed: {exc}")
            self._kill(off)

    @staticmethod
    def _kill(proc):
        if proc is None:
            return
        try:
            proc.kill()
            proc.wait(timeout=2.0)
        except Exception:
            pass
