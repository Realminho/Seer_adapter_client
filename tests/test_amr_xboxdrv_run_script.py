"""Behaviour tests for the xboxdrv receiver supervisor.

The 8BitDo receiver re-enumerates between two USB product ids: the connected
id (310b) exists only while a controller is awake and paired, and it vanishes
when the controller sleeps. xboxdrv binds its USB handle once at startup, never
re-binds, and does not exit when the device disappears, so the virtual gamepad
survives as a dead husk. The supervisor must therefore follow the *device*, not
the process.
"""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import time

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "amr-xboxdrv-run.sh"

VENDOR = "2dc8"
PRODUCT = "310b"
POLL_SEC = "0.05"


def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _plug(sysfs: Path, product: str = PRODUCT, name: str = "1-1.2") -> Path:
    device = sysfs / name
    device.mkdir(parents=True, exist_ok=True)
    (device / "idVendor").write_text(f"{VENDOR}\n")
    (device / "idProduct").write_text(f"{product}\n")
    return device


def _unplug(device: Path) -> None:
    for child in device.iterdir():
        child.unlink()
    device.rmdir()


@pytest.fixture()
def harness(tmp_path):
    sysfs = tmp_path / "sysfs"
    sysfs.mkdir()
    args_file = tmp_path / "args"
    pid_file = tmp_path / "child.pid"
    fake = tmp_path / "fake-xboxdrv"
    fake.write_text(
        "#!/bin/sh\n"
        f'echo "$@" > "{args_file}"\n'
        f'echo $$ > "{pid_file}"\n'
        "exec sleep 60\n"
    )
    fake.chmod(0o755)

    processes: list[subprocess.Popen] = []

    def start() -> subprocess.Popen:
        env = dict(os.environ)
        env.update(
            AMR_XBOXDRV_SYSFS=str(sysfs),
            AMR_XBOXDRV_BIN=str(fake),
            AMR_XBOXDRV_POLL_SEC=POLL_SEC,
        )
        proc = subprocess.Popen(
            [str(SCRIPT)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        processes.append(proc)
        return proc

    yield {
        "sysfs": sysfs,
        "args_file": args_file,
        "pid_file": pid_file,
        "start": start,
    }

    for proc in processes:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def _child_pid(pid_file: Path) -> int:
    return int(pid_file.read_text().strip())


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_waits_for_receiver_before_launching_xboxdrv(harness):
    proc = harness["start"]()
    time.sleep(0.3)

    assert proc.poll() is None, "supervisor must keep waiting while the receiver is absent"
    assert not harness["args_file"].exists(), "xboxdrv must not start without the receiver"


def test_ignores_the_disconnected_product_id(harness):
    proc = harness["start"]()
    _plug(harness["sysfs"], product="3109")
    time.sleep(0.3)

    assert proc.poll() is None
    assert not harness["args_file"].exists(), "3109 is the controller-asleep id"


def test_launches_xboxdrv_when_the_receiver_appears(harness):
    harness["start"]()
    _plug(harness["sysfs"])

    assert _wait_until(harness["args_file"].exists), "xboxdrv should start once 310b appears"
    assert harness["args_file"].read_text().split() == [
        "--device-by-id",
        f"{VENDOR}:{PRODUCT}",
        "--type",
        "xbox360",
        "--silent",
    ]


def test_stops_xboxdrv_and_exits_when_the_receiver_disappears(harness):
    proc = harness["start"]()
    device = _plug(harness["sysfs"])
    assert _wait_until(harness["pid_file"].exists)
    child = _child_pid(harness["pid_file"])

    _unplug(device)

    assert _wait_until(lambda: proc.poll() is not None), "supervisor must exit so systemd restarts it"
    assert proc.returncode == 0
    assert _wait_until(lambda: not _alive(child)), "xboxdrv must not survive the receiver"


def test_stops_xboxdrv_when_the_receiver_re_enumerates_as_disconnected(harness):
    proc = harness["start"]()
    device = _plug(harness["sysfs"])
    assert _wait_until(harness["pid_file"].exists)
    child = _child_pid(harness["pid_file"])

    # The receiver keeps its bus path and only swaps the product id when the
    # controller sleeps, so watching the directory alone would miss this.
    (device / "idProduct").write_text("3109\n")

    assert _wait_until(lambda: proc.poll() is not None)
    assert _wait_until(lambda: not _alive(child))


def test_default_poll_interval_is_well_under_the_manual_watchdog():
    # The poll interval is the window in which a drive held as the controller
    # sleeps keeps being re-sent, because the virtual pad stays open and just
    # goes quiet. A one-second default would leave the robot moving for about
    # as long as manual_control.watchdog_ms allows in total.
    default = SCRIPT.read_text().split("AMR_XBOXDRV_POLL_SEC:-")[1].split("}")[0]

    assert 0 < float(default) <= 0.25


def test_terminates_child_on_sigterm(harness):
    proc = harness["start"]()
    _plug(harness["sysfs"])
    assert _wait_until(harness["pid_file"].exists)
    child = _child_pid(harness["pid_file"])

    proc.send_signal(signal.SIGTERM)

    assert _wait_until(lambda: proc.poll() is not None)
    assert _wait_until(lambda: not _alive(child)), "systemd stop must not orphan xboxdrv"
