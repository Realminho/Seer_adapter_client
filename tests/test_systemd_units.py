import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = REPO_ROOT / "scripts" / "systemd"


def test_amr_adapter_units_exist_and_use_dispatcher():
    base = (SYSTEMD / "amr-adaptor.service").read_text()
    assert "run-adapter.sh" in base
    assert "KillSignal=SIGINT" in base
    assert "CPUAccounting=yes" in base
    assert "Environment=PYTHONUNBUFFERED=1" in base
    assert "StandardOutput=journal" in base
    assert "StandardError=journal" in base

    tmpl = (SYSTEMD / "amr-adaptor@.service").read_text()
    assert "run-adapter.sh --instance %i" in tmpl
    assert "KillSignal=SIGINT" in tmpl
    assert "TimeoutStopSec=15" in tmpl
    assert "CPUAccounting=yes" in tmpl
    assert "Environment=PYTHONUNBUFFERED=1" in tmpl
    assert "StandardOutput=journal" in tmpl
    assert "StandardError=journal" in tmpl


def test_old_adapter_units_removed():
    for stale in ("jibot-adapter.service", "jibot-adapter@.service", "hexplorer-adapter.service"):
        assert not (SYSTEMD / stale).exists(), f"{stale} should be deleted"


def test_xboxdrv_bridge_retries_and_starts_before_adapter():
    unit = (SYSTEMD / "amr-xboxdrv.service").read_text()
    assert "ExecStartPre=-/sbin/modprobe uinput" in unit
    # The supervisor owns the xboxdrv lifetime; the unit must not spawn xboxdrv
    # directly or it will keep a dead USB handle when the controller sleeps.
    # It runs the deployed copy so an over-ssh update ships supervisor fixes
    # without re-running setup.
    assert "ExecStart=__SCRIPTSDIR__/amr-xboxdrv-run.sh" in unit
    assert "xboxdrv --device-by-id" not in unit
    assert "Restart=always" in unit
    assert "Before=amr-adaptor.service" in unit
    # xboxdrv inherits SIGINT as ignored from the supervisor shell, so a SIGINT
    # stop would reach only the supervisor and leave the bridge behind.
    assert "KillSignal=SIGINT" not in unit


def test_xboxdrv_supervisor_script_is_installable():
    script = REPO_ROOT / "scripts" / "amr-xboxdrv-run.sh"
    assert script.exists()
    assert os.access(script, os.X_OK), "script must be executable to install with -m 0755"
    assert script.read_text().startswith("#!/bin/sh\n")
