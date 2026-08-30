import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
ADAPTOR = REPO_ROOT / "adaptor"
SETUP_SCRIPT = SCRIPTS / "setup-adaptor-service.sh"
UNINSTALL_SCRIPT = SCRIPTS / "uninstall-adaptor-service.sh"
CONTROL_SCRIPT = SCRIPTS / "adaptor-services.sh"
SOUND_TEST_SCRIPT = SCRIPTS / "test-sound-devices.sh"
UPDATE_CONFIG_SCRIPT = SCRIPTS / "update-jibot-adapter-config.sh"


def test_default_service_ports_are_consistent():
    setup = SETUP_SCRIPT.read_text()
    camera_unit = (SCRIPTS / "systemd" / "amr-camera.service").read_text()
    run_web = (ADAPTOR / "run-web.sh").read_text()

    assert 'WEB_VIDEO_PORT="${WEB_VIDEO_PORT:-9001}"' in setup
    assert "_port:=9001" in camera_unit
    assert ".get('port',9000)" in run_web
    assert "echo 9000" in run_web


def test_setup_script_restarts_services_by_default_with_no_start_escape_hatch():
    text = SETUP_SCRIPT.read_text()

    assert "--no-start" in text
    assert "DO_START=1" in text
    assert "restart_installed_services()" in text
    assert 'run_root "$SYSTEMCTL_BIN" restart "$unit"' in text
    assert 'run_root "$SYSTEMCTL_BIN" restart amr-webui.service' in text


def test_uninstall_script_removes_setup_owned_system_integration():
    text = UNINSTALL_SCRIPT.read_text()

    assert "--dry-run" in text
    assert "'amr-adaptor@*.service'" in text
    for unit in (
        "amr-adaptor.service",
        "amr-adaptor@.service",
        "amr-webui.service",
        "amr-camera.service",
    ):
        assert f"/etc/systemd/system/{unit}" in text
    assert 'disable --now "$unit"' in text
    assert "/etc/sudoers.d/adaptor-tui" in text
    assert "/etc/polkit-1/rules.d/10-amr-webui.rules" in text
    assert "/etc/polkit-1/localauthority/50-local.d/10-amr-webui.pkla" in text
    assert "/etc/tmpfiles.d/amr-adaptor.conf" in text
    assert "/run/amr-adaptor" in text
    assert "daemon-reload" in text
    assert "reset-failed" in text


def test_uninstall_script_preserves_user_owned_install_side_effects():
    text = UNINSTALL_SCRIPT.read_text()

    assert "web-credentials.toml" not in text
    assert "disable-linger" not in text
    assert "rm -rf venv" not in text
    assert "rm -rf .venv" not in text


def test_adaptor_services_script_controls_all_service_groups():
    text = CONTROL_SCRIPT.read_text()

    assert "Usage:" in text
    assert "start|restart|stop|status" in text
    assert "amr-adaptor.service" in text
    assert 'amr-adaptor@${name}.service' in text
    assert 'amr-adaptor@${rid}.service' not in text
    assert "amr-webui.service" in text
    assert "amr-camera.service" in text
    assert 'systemctl_cmd start "$unit"' in text
    assert 'systemctl_cmd restart "$unit"' in text
    assert 'systemctl_cmd stop "$unit"' in text
    assert 'systemctl_cmd status "$unit" --no-pager' in text


def test_adaptor_services_script_delegates_install_and_remove():
    text = CONTROL_SCRIPT.read_text()

    assert 'install)' in text
    assert 'exec "$SCRIPT_DIR/setup-adaptor-service.sh" "$@"' in text
    assert 'remove)' in text
    assert 'exec "$SCRIPT_DIR/uninstall-adaptor-service.sh" "$@"' in text
    assert "install [setup options]" in text
    assert "remove [--dry-run]" in text


def test_run_scripts_export_xdg_runtime_dir_for_pulseaudio():
    # A boot-time system service has no login-session env, so XDG_RUNTIME_DIR is
    # unset and mplayer/pactl can't reach the user's PulseAudio (silent audio +
    # no-op volume). The launchers must default it to /run/user/<uid>.
    for name in ("run-adapter.sh", "run-main.sh"):
        text = (ADAPTOR / name).read_text()
        assert 'XDG_RUNTIME_DIR="/run/user/$(id -u)"' in text, name
        assert 'if [[ -z "${XDG_RUNTIME_DIR:-}" ]]; then' in text, name


def test_run_adapter_dispatches_multi_robot_fleet_to_run_multi():
    text = (ADAPTOR / "run-adapter.sh").read_text()

    assert 'DISPATCH_OUTPUT="$("$PYTHON_BIN" -m config.adapter_dispatch "$INSTANCE")"' in text
    assert "jibot-multi) LAUNCHER=\"./run-multi.sh\" ;;" in text


def test_setup_uses_an_already_installed_mise_python_without_the_flag():
    # --with-mise gates *downloading* an interpreter. A robot where mise is
    # already set up has one, but sudo strips ~/.local/share/mise/shims from
    # PATH and mise installs under the deploy user's home, so the plain PATH
    # probe misses it and setup used to die with "no Python >= 3.11".
    text = SETUP_SCRIPT.read_text()

    assert "mise_existing_python()" in text
    assert 'wdir="$(run_as_deploy_user "$mise" where "python@$min" 2>/dev/null || true)"' in text
    assert '"$home/.local/share/mise/shims/python3"' in text

    # Both fallback branches (uv present, and the stdlib-venv path) must consult
    # the installed mise before they consider the download-gating flag.
    body = text.split("repair_venv() {", 1)[1]
    assert body.count("mise_existing_python") == 2, "both uv and no-uv branches"
    before_each_flag_check = body.split("elif [[ $WITH_MISE -eq 1 ]]; then")[:-1]
    assert len(before_each_flag_check) == 2
    for chunk in before_each_flag_check:
        assert "mise_existing_python" in chunk


def test_setup_finds_a_mise_managed_uv():
    text = SETUP_SCRIPT.read_text()

    assert '"$home/.local/share/mise/shims/uv"' in text
    assert 'c="$(run_as_deploy_user "$mise" which uv 2>/dev/null || true)"' in text


def test_setup_warns_that_enabled_units_will_crash_loop_when_no_python_is_found():
    # repair_venv runs after the units are installed and enabled (locked by
    # test_setup_script_installs_and_enables_units_before_venv_repair), so a
    # die_no_python exit arms the adapter for the next boot with no .venv.
    text = SETUP_SCRIPT.read_text()
    body = text.split("die_no_python() {", 1)[1].split("\n}", 1)[0]

    assert "ENABLED but cannot start without" in body
    assert "crash-loop at boot" in body
    assert "systemctl disable --now amr-adaptor.service" in body
    assert "if [[ $DRY_RUN -eq 0 ]]; then" in body


def _staged_run_adapter(tmp_path, *, py_version, deps_ok):
    """Copy run-adapter.sh next to a stub `python3` of a chosen shape.

    The real launcher picks .venv/venvJIBOT when present, so it can only be
    exercised outside the repo checkout, with the fallback interpreter faked.
    """
    adapter = tmp_path / "adaptor"
    adapter.mkdir()
    (adapter / "run-adapter.sh").write_text((ADAPTOR / "run-adapter.sh").read_text())
    (adapter / "run-adapter.sh").chmod(0o755)
    (adapter / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.11"\n')

    major, minor = py_version.split(".")
    too_old = 0 if (int(major), int(minor)) >= (3, 11) else 1
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "python3"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'args="$*"\n'
        f'[[ "$args" == *"raise SystemExit"* ]] && exit {too_old}\n'
        f'[[ "$args" == *\'print("%d.%d"\'* ]] && {{ echo "{py_version}"; exit 0; }}\n'
        f'[[ "$args" == *hcl2* ]] && exit {0 if deps_ok else 1}\n'
        '[[ "$args" == *config.adapter_dispatch* ]] && { echo jibot; exit 0; }\n'
        "exit 0\n"
    )
    stub.chmod(0o755)

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "AMR_DISPATCH_PRINT": "1",
    }
    return subprocess.run(
        [str(adapter / "run-adapter.sh")],
        env=env,
        capture_output=True,
        text=True,
    )


def test_run_adapter_refuses_to_start_on_an_interpreter_below_the_floor(tmp_path):
    # The robot regression: no venv -> system python3.8 -> adapter_dispatch dies
    # with "No module named 'hcl2'". run-main.sh guards this, but run-adapter.sh
    # runs Python first, so the guard has to live here too.
    result = _staged_run_adapter(tmp_path, py_version="3.8", deps_ok=False)

    assert result.returncode == 1
    assert "is Python 3.8 but the adapter needs >= 3.11" in result.stderr
    assert "No .venv or venvJIBOT here" in result.stderr
    assert "setup-adaptor-service.sh" in result.stderr
    assert "run-main.sh" not in result.stdout


def test_run_adapter_reports_missing_dependencies_instead_of_a_traceback(tmp_path):
    result = _staged_run_adapter(tmp_path, py_version="3.11", deps_ok=False)

    assert result.returncode == 1
    assert "missing adapter dependencies" in result.stderr
    assert "install-offline-python-deps.sh" in result.stderr


def test_run_adapter_dispatches_when_the_interpreter_is_usable(tmp_path):
    result = _staged_run_adapter(tmp_path, py_version="3.11", deps_ok=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "./run-main.sh"


def test_run_multi_stops_all_children_when_one_child_exits():
    text = (ADAPTOR / "run_multi.py").read_text()

    assert "proc.poll()" in text
    assert "exited; stopping all adapters" in text
    assert "for proc in procs:\n        proc.wait()" not in text


def test_setup_script_enables_journald_only_when_needed():
    text = SETUP_SCRIPT.read_text()

    assert "configure_journald_logging()" in text
    assert '"$SYSTEMCTL_BIN" is-enabled "$unit"' in text
    assert '[[ ! -d /var/log/journal ]]' in text
    assert 'id -nG "$DEPLOY_USER"' in text
    assert 'usermod -aG systemd-journal "$DEPLOY_USER"' in text
    assert "systemd-journald.service" in text
    assert "systemd-journald.socket" in text
    assert "systemd-journald-dev-log.socket" in text
    assert 'unmask "${journald_units[@]}"' in text
    assert 'systemd-tmpfiles --create --prefix /var/log/journal' in text


def test_arm_watchdog_script_sets_and_applies_watchdog():
    # Arms the systemd hardware watchdog (bounds the recurring .222 hard-lockup
    # downtime). Must set both keys, apply via daemon-reexec, back up the conf,
    # support dry-run/revert, and verify it actually armed.
    text = (SCRIPTS / "arm-watchdog.sh").read_text()
    assert "RuntimeWatchdogSec" in text
    assert "RebootWatchdogSec" in text
    assert "/etc/systemd/system.conf" in text
    assert "daemon-reexec" in text
    assert "--dry-run" in text
    assert "--revert" in text
    assert ".amr-watchdog.bak" in text          # keeps a pre-arm backup
    assert "RuntimeWatchdogUSec" in text         # verifies the result


def test_urobot_watchdog_boot_delay_extends_start_timeout():
    # ExecStartPre counts against TimeoutStartSec. The vendor urobot unit on
    # bot .222 has a short start timeout, so a 30s boot delay must raise it or
    # systemd kills /bin/sleep before urobot even starts.
    text = (SCRIPTS / "robot-host" / "install-urobot-watchdog.sh").read_text()
    assert "START_TIMEOUT" in text
    assert "TimeoutStartSec=${START_TIMEOUT}" in text
    assert "ExecStartPre=/bin/sleep ${BOOT_DELAY}" in text


def test_setup_script_retires_legacy_misspelled_amr_adapter_unit():
    # A prior install used the "amr-adapter" (…ER) spelling; the canonical unit is
    # "amr-adaptor" (…OR). Both ended up enabled, so TWO adapter processes ran and
    # both subscribed to MQTT — every WebUI instant action was handled twice, and
    # the two instances raced on singleton hardware: clamp servo_enable timed out
    # ("no response from EZI motor") and audio was flaky. migrate_legacy_units must
    # stop/disable the legacy "…ER" units (static + template instances) and remove
    # their unit files, while NEVER touching the canonical "…OR" unit.
    text = SETUP_SCRIPT.read_text()
    assert "amr-adapter.service" in text            # legacy static unit handled
    assert "amr-adapter@" in text                   # legacy template/instances handled
    assert "/etc/systemd/system/amr-adapter.service" in text   # legacy file removed
    # Safety: the canonical unit is installed via /etc/systemd/system/$name, so its
    # literal path never appears — if it did, someone added it to an rm list.
    assert "/etc/systemd/system/amr-adaptor.service" not in text


def test_setup_script_enables_linger_for_deploy_user():
    # enable-linger makes /run/user/<uid> (and the user's pulse) exist at boot so
    # OS sound works before anyone logs in.
    text = SETUP_SCRIPT.read_text()
    assert 'enable-linger' in text
    assert '"$DEPLOY_USER"' in text


def test_setup_installs_the_xboxdrv_bridge_with_an_opt_out():
    text = SETUP_SCRIPT.read_text()

    assert "--no-xboxdrv" in text
    assert "DO_XBOXDRV=1" in text
    assert "DO_XBOXDRV=0" in text
    assert "install_amr_xboxdrv_service" in text
    # Robots without the receiver have no xboxdrv binary; installing the unit
    # there would just crash-loop, so the install is gated on the binary.
    assert "command -v xboxdrv" in text
    assert "run_root usermod -aG input" in text
    assert 'run_root "$SYSTEMCTL_BIN" enable amr-xboxdrv.service' in text


def test_setup_skips_the_bridge_when_the_kernel_driver_can_bind_the_receiver():
    # The bridge exists for kernels that cannot drive the receiver themselves.
    # Where xpad is available it claims the same USB interface, so xboxdrv would
    # either crash-loop on a busy interface or take the working /dev/input/jsN
    # path away from the runtime. Skip by default, with an explicit override.
    text = SETUP_SCRIPT.read_text()

    assert "xpad_driver_available()" in text
    assert "[[ -d /sys/module/xpad ]]" in text
    assert "modinfo xpad" in text
    assert "--with-xboxdrv" in text
    assert "FORCE_XBOXDRV=1" in text
    assert "$FORCE_XBOXDRV -eq 0 ]] && xpad_driver_available" in text
    # The skip must come before the unit is installed, not after.
    gate = text.index("xpad_driver_available; then")
    assert gate < text.index('install_unit "amr-xboxdrv.service"')


def test_setup_installs_xboxdrv_from_the_bundled_offline_debs():
    # The robot has no internet, so apt cannot supply xboxdrv. The .deb bundle
    # rides along with the scripts/ directory the update script uploads.
    text = SETUP_SCRIPT.read_text()

    assert 'install_bundled_offline_debs "xboxdrv"' in text
    assert '"$REPO_ROOT/scripts/offline-debs/xboxdrv"' in text
    # The helper returns non-zero on failure and setup runs under `set -e`, so a
    # missing deb must not abort the rest of the install. `|| bundle_failed=1`
    # terminates an OR-list, which keeps `set -e` quiet exactly like `|| true`
    # did — while keeping the status instead of throwing it away.
    assert 'scripts/fetch-offline-debs.sh and re-run it on the build machine." || bundle_failed=1' in text
    assert "local bundle_failed=0" in text
    # The binary is re-checked after the install attempt, so a failed dpkg run
    # still degrades to the old skip rather than enabling a crash-looping unit.
    assert text.count("command -v xboxdrv") >= 2
    # ...but the re-check alone is NOT enough, and this is the load-bearing part:
    # `dpkg -i` on a package with an unsatisfied Depends still unpacks it and
    # only refuses to configure it (no Pre-Depends here to block the unpack), so
    # /usr/bin/xboxdrv is on disk and `command -v xboxdrv` succeeds even though
    # dpkg exited 1. Enabling the unit then crash-loops every RestartSec=2 in
    # the dynamic linker on a robot apt cannot repair. The dpkg status must gate
    # the unit too.
    assert (
        "if [[ $DRY_RUN -eq 0 ]] && "
        "{ [[ $bundle_failed -eq 1 ]] || ! command -v xboxdrv >/dev/null 2>&1; }; then"
    ) in text
    # The deb install itself is deliberately NOT wrapped in a $DRY_RUN guard:
    # install_bundled_offline_debs prints its own [dry-run] line and returns 0,
    # and guarding here would silently drop that from the dry-run preview.
    assert (
        'if ! command -v xboxdrv >/dev/null 2>&1; then\n'
        '    echo "==> amr-xboxdrv.service (offline deb bundle)"'
    ) in text
    # Installing xboxdrv on a host that will skip the bridge anyway is waste, so
    # the xpad gate runs first.
    assert text.index("xpad_driver_available; then") < text.index('install_bundled_offline_debs "xboxdrv"')


def test_setup_renders_the_deployed_scripts_dir_into_units():
    # The bridge runs the repo's own supervisor copy, so an over-ssh update
    # refreshes it; the unit must not be left pointing at an unrendered path.
    text = SETUP_SCRIPT.read_text()

    assert "__SCRIPTSDIR__" in text
    assert 's|__SCRIPTSDIR__|$SCRIPT_DIR|g' in text
    assert '*"__SCRIPTSDIR__"*' in text


def test_setup_restarts_the_xboxdrv_bridge_it_installed():
    text = SETUP_SCRIPT.read_text()

    assert "XBOXDRV_SERVICE_AVAILABLE=1" in text
    assert 'run_root "$SYSTEMCTL_BIN" restart amr-xboxdrv.service' in text


def test_setup_sudoers_allows_xboxdrv_bridge_control_without_password():
    text = SETUP_SCRIPT.read_text()
    sudoers = text[text.index("install_sudoers() {"):text.index("\n}\n\ninstall_webui_credentials")]

    assert 'units+=("amr-xboxdrv.service")' in sudoers


def test_adaptor_services_script_controls_the_xboxdrv_bridge():
    text = CONTROL_SCRIPT.read_text()

    assert 'if optional_unit_available "amr-xboxdrv.service"; then' in text
    assert 'units+=("amr-xboxdrv.service")' in text
    # The bridge has to lead: the adapter binds the virtual gamepad by name and
    # only re-scans on its retry loop.
    assert text.index('units+=("amr-xboxdrv.service")') < text.index('units+=("amr-webui.service")')


def test_uninstall_removes_the_xboxdrv_bridge():
    text = UNINSTALL_SCRIPT.read_text()

    assert "amr-xboxdrv.service" in text
    assert "/etc/systemd/system/amr-xboxdrv.service" in text


def test_convenience_wrappers_delegate_to_control_script():
    expected = {
        "start-adaptor-services.sh": "start",
        "restart-adaptor-services.sh": "restart",
        "stop-adaptor-services.sh": "stop",
        "status-adaptor-services.sh": "status",
    }

    for name, action in expected.items():
        text = (SCRIPTS / name).read_text()
        assert "adaptor-services.sh" in text
        assert f'"{action}" "$@"' in text


def test_sound_device_probe_script_exercises_each_pulseaudio_sink():
    text = SOUND_TEST_SCRIPT.read_text()

    assert "pactl list short sinks" in text
    assert "sink_name=" in text
    assert "PULSE_SINK=" in text
    assert "-noconsolecontrols" in text
    assert "</dev/null" in text
    assert '"$PLAYER" -really-quiet -noconsolecontrols -ao "pulse::${sink_name}"' in text
    assert "--track" in text
    assert "--seconds" in text
    assert "copy this into config/config.toml" in text


def test_sound_device_probe_script_can_prepare_rt5651_routes():
    text = SOUND_TEST_SCRIPT.read_text()

    assert "--prepare-rt5651" in text
    assert "--show-mixer" in text
    assert "--direct-alsa" in text
    assert "RT5651_ROUTE_SWITCHES" in text
    assert "OUT MIXL DAC L1" in text
    assert "OUT MIXR DAC R1" in text
    assert "LOUT MIX DAC L1" in text
    assert "LOUT MIX OUTVOL R" in text
    assert "HPO MIX HPVOL" in text
    assert "amixer -c \"$ALSA_CARD\" set" in text
    assert "speaker-test -D \"plughw:${ALSA_CARD},0\"" in text


def test_update_adapter_config_rejects_vehicle_ip_config_edits():
    proc = subprocess.run(
        ["bash", str(UPDATE_CONFIG_SCRIPT), "--vehicle-ip", "192.168.3.223", "--dry-run"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 2
    assert "config/robots.hcl" in proc.stderr
