import os
import re
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "update-jibot-adapter-over-ssh.sh"
SETUP_SCRIPT = REPO_ROOT / "scripts" / "setup-adaptor-service.sh"
SERVICES_SCRIPT = REPO_ROOT / "scripts" / "adaptor-services.sh"
OFFLINE_DEPS_SCRIPT = REPO_ROOT / "scripts" / "install-offline-python-deps.sh"
REQUIREMENTS = REPO_ROOT / "adaptor" / "requirements.txt"
POLKIT_RULE = REPO_ROOT / "scripts" / "systemd" / "amr-webui-polkit.rules"
WEBUI_UNIT = REPO_ROOT / "scripts" / "systemd" / "amr-webui.service"
ADAPTOR_UNIT = REPO_ROOT / "scripts" / "systemd" / "amr-adaptor.service"
TMPFILES_CONF = REPO_ROOT / "scripts" / "systemd" / "tmpfiles.d" / "amr-adaptor.conf"
SSH_MANUAL = REPO_ROOT / "docs" / "manual" / "jibot-adapter-ssh-update.md"
ROOT_README = REPO_ROOT / "README.md"
ADAPTOR_README = REPO_ROOT / "adaptor" / "readme.md"


def test_operator_docs_use_current_remote_dir_and_full_stack_restart_commands():
    positional_remote_dir = re.compile(
        r"scripts/update-jibot-adapter-over-ssh\.sh[^\n]*\b[\w.-]+@[\w.-]+\s+/"
    )
    standalone_restart = re.compile(r"(?<![-\w])--restart(?![-\w])")

    for path in (SSH_MANUAL, ROOT_README, ADAPTOR_README):
        text = path.read_text()
        assert "--remote-dir /home/ucore/adapter" in text, path
        assert standalone_restart.search(text), path
        assert not positional_remote_dir.search(text), path

    root_readme = ROOT_README.read_text()
    adaptor_readme = ADAPTOR_README.read_text()
    for text in (root_readme, adaptor_readme):
        assert "0개 또는 2개 이상" not in text
        assert "0/1/2+" not in text
        assert "0/1/2대" not in text
        assert "없거나 비어 있으면 시작에 실패" in text
        assert "명시적 `[[adapter.instances]]`와 의도적으로 지정한 JIBOT robot ID/수동 호환 경로" in text
        assert "# (2) WebUi 실행" not in text
        assert "systemd 관리 WebUI는 `amr-webui.service`" in text

    assert "setup-adaptor-service.sh --all" not in root_readme
    assert "setup-adaptor-service.sh --all" not in adaptor_readme
    assert "2개 이상 있으면 로봇별 유닛" not in root_readme
    assert "실행하려면 활성화" not in root_readme
    assert "Web UI도 기본으로 enable 후 start/restart" in root_readme
    assert not re.search(r"(?m)^adaptor/run-web\.sh$", root_readme)
    assert "명시적 `[[adapter.instances]]`용 systemd" not in root_readme
    assert "서비스는 등록 시 부팅 자동시작(enable)만 되고 바로 시작되지는 않는다" not in adaptor_readme
    assert "amr-adaptor@HN-SH6-TR-001.service  # 2대 이상" not in adaptor_readme
    assert "ucore@ubuntu:~/adapter$ run-web.sh" not in adaptor_readme
    assert "명시적 `[[adapter.instances]]`에만 사용" not in adaptor_readme
    assert "명시적 [[adapter.instances]] 인스턴스 템플릿" not in adaptor_readme
    assert "adaptor와 TUI가 함께 읽는 설정" not in adaptor_readme
    assert "TUI가 설치 대상 유닛" not in adaptor_readme
    assert "legacy 파일명" in adaptor_readme

    assert "adapter/Web UI/camera systemd 서비스를 등록합니다" not in root_readme
    assert "JIBOT adapter/WebUi/camera를 함께 등록하는 표준 설치 경로" not in adaptor_readme
    assert "카메라 필수 조건이 준비된 경우" in root_readme
    assert "카메라 필수 조건이 준비된 경우" in adaptor_readme

    ssh_manual = SSH_MANUAL.read_text()
    assert "코드와 `robots.hcl`을 같이 반영하고 adapter 재시작:" not in ssh_manual
    assert "코드와 `robots.hcl`을 같이 반영하고 adapter와 설치된 WebUi 재시작:" in ssh_manual


def test_update_script_uploads_setup_service_files():
    text = SCRIPT.read_text()

    assert "default: ~/adaptor" in text
    assert "remote_dir=\"$HOME/adaptor\"" in text
    assert "remote_dir_display='~/adaptor'" in text
    assert "default: ~/adapter" not in text
    assert 'remote_dir="$HOME/adapter"' not in text
    assert 'LOCAL_SCRIPTS_DIR="scripts"' in text
    assert 'mkdir -p "$STAGING_DIR/payload" "$STAGING_DIR/default_config"' in text
    assert 'cp "$LOCAL_ADAPTER_DIR/config/config.toml" "$STAGING_DIR/default_config/config.toml"' in text
    assert '-C "."' in text
    assert '"$LOCAL_JIBOT_SIMULATOR_DIR" "$LOCAL_SCRIPTS_DIR" | tar -C "$STAGING_DIR/payload" -xf -' in text
    assert '==> Uploading bundle to $host:$remote_dir_display' in text
    assert 'tar -C "$STAGING_DIR" --mtime=\'2000-01-01\' -cf - "${upload_items[@]}" | "${ssh_base[@]}" "$host" "$remote_command"' in text
    assert "ezi_io_input_test.py" in "\n".join(p.name for p in (REPO_ROOT / "scripts").glob("*.py"))
    assert OFFLINE_DEPS_SCRIPT.exists()
    assert 'find "$staging/payload/scripts" -maxdepth 1 -type f \\( -name \'*.sh\' -o -name \'*.py\' \\) -print0' in text


def test_setup_script_installs_web_video_server_from_uploaded_offline_debs_first():
    text = SETUP_SCRIPT.read_text()

    assert 'install_bundled_offline_debs "$pkg"' in text
    assert '"$REPO_ROOT/scripts/offline-debs/web-video-server"' in text
    assert 'mapfile -t offline_debs < <(find "$offline_deb_dir" -maxdepth 1 -type f -name \'*.deb\' | sort)' in text
    # `sort` is lexicographic, so the whole directory going to one `dpkg -i` can
    # order xboxdrv_0.8.8-10 before xboxdrv_0.8.8-2 and roll the robot back.
    # A robot already carrying a newer libusb-1.0-0 from focal-updates is the
    # same hazard, and fetch-offline-debs.sh only reads dists/$SUITE.
    assert 'run_root dpkg -i --refuse-downgrade "${offline_debs[@]}"' in text
    assert "Stage $label and its .deb dependencies there, then re-run setup." in text
    # The helper returns 1 when the bundle is stale, and setup runs under
    # `set -e`. This `|| true` is the only thing keeping a stale camera bundle
    # from killing setup mid-fleet-deployment, and the camera path runs on every
    # robot — unlike the xboxdrv path, which is gated on the binary.
    assert '"The JIBOT image may require ffmpeg and related libav packages in that directory." || true' in text


def test_setup_script_never_falls_back_to_apt_for_camera_package():
    text = SETUP_SCRIPT.read_text()

    assert "trying apt fallback" not in text
    assert "apt-get update" not in text
    assert "apt-get install -y \"$pkg\"" not in text
    assert "Stage missing .deb dependencies" in text


def test_update_script_preflights_web_video_offline_debs():
    text = SCRIPT.read_text()

    assert 'WEB_VIDEO_OFFLINE_DEB_DIR="$LOCAL_SCRIPTS_DIR/offline-debs/web-video-server"' in text
    assert "validate_web_video_offline_debs" in text
    assert "ffmpeg_*_arm64.deb" in text
    assert "libavcodec-dev_*_arm64.deb" in text
    assert "Do not rely on /var/cache/apt/archives" in text
    assert 'validate_web_video_offline_debs\n\nSTAGING_DIR="$(mktemp -d)"' in text
    assert text.index('validate_web_video_offline_debs\n\nSTAGING_DIR="$(mktemp -d)"') < text.index("Preparing upload bundle")


def test_update_script_logs_first_ssh_step_and_has_timeouts():
    text = SCRIPT.read_text()

    assert 'WARNING: remote target has no user part: $host' in text
    assert 'ucore@$host' in text
    assert 'SSH_CONNECT_TIMEOUT="${SSH_CONNECT_TIMEOUT:-10}"' in text
    assert 'SSH_CONTROL_MASTER="${SSH_CONTROL_MASTER:-1}"' in text
    assert '-o "ConnectTimeout=$SSH_CONNECT_TIMEOUT"' in text
    assert 'if [[ "$SSH_CONTROL_MASTER" == "1" ]]; then' in text
    assert '-o ControlMaster=auto' in text
    assert '-o ConnectionAttempts=1' in text
    assert '-o "ServerAliveInterval=$SSH_SERVER_ALIVE_INTERVAL"' in text
    assert '-o "ServerAliveCountMax=$SSH_SERVER_ALIVE_COUNT_MAX"' in text
    assert 'echo "[remote] preparing $remote_dir"' in text


def test_update_script_enables_legacy_jibot_ssh_crypto_by_default():
    text = SCRIPT.read_text()

    assert "-o KexAlgorithms=+diffie-hellman-group14-sha1" in text
    assert "-o Ciphers=+aes128-cbc" in text
    assert "-o HostKeyAlgorithms=+ssh-rsa" in text
    assert "-o PubkeyAcceptedAlgorithms=+ssh-rsa" in text


def test_update_script_chmods_only_uploaded_scripts():
    text = SCRIPT.read_text()

    assert 'find "$remote_dir/scripts" -maxdepth 1' not in text
    assert 'for runner in run-adapter.sh run-main.sh run-hexplorer.sh run_multi.py; do' in text
    assert '[[ -f "$remote_dir/$runner" ]] && chmod +x "$remote_dir/$runner"' in text
    assert 'find "$staging/payload/scripts" -maxdepth 1' in text
    assert 'rel="${uploaded_script#"$staging/payload/"}"' in text
    assert 'chmod +x "$remote_dir/$rel"' in text


def test_update_script_restarts_after_upload_with_tty():
    text = SCRIPT.read_text()

    assert 'echo "[remote] upload done."' in text
    assert 'restart_command="$(printf \\' in text
    assert 'echo "==> Restarting adapter on $host"' in text
    assert 'if [[ -r /dev/tty ]]; then' in text
    assert '"${ssh_base[@]}" -tt "$host" "$restart_command" < /dev/tty' in text
    assert "unit_patterns=(" in text
    assert "'jibot-adapter*.service'" in text
    assert 'no installed adapter systemd unit found' in text
    assert 'scripts/setup-adaptor-service.sh --jibot' in text
    assert 'elif sudo systemctl restart "$u"; then' in text
    assert text.index('elif sudo -n systemctl restart "$u"') < text.index('elif systemctl restart "$u"')
    assert 'FAILED during restart' in text


def test_jibot_restart_adds_installed_webui_without_dropping_legacy_units():
    text = SCRIPT.read_text()
    assert "webui_unit=" in text
    assert "amr-webui.service" in text
    assert '[[ -n "$webui_unit" ]]' in text
    assert "'jibot-adapter*.service'" in text
    assert "'amr-adapter*.service'" in text
    assert "units=\"$(printf '%s\\n%s\\n' \"$units\" \"$webui_unit\" | sort -u)\"" in text


def test_jibot_restart_cycles_the_xboxdrv_bridge_it_overwrites():
    # The bridge runs scripts/amr-xboxdrv-run.sh straight out of the deployed
    # tree, and the upload rewrites that file in place. A running /bin/sh reads
    # its script by offset, so it must be stopped across the upload.
    text = SCRIPT.read_text()

    assert "xboxdrv_unit=" in text
    assert "amr-xboxdrv.service" in text
    assert '[[ -n "$xboxdrv_unit" ]]' in text


def test_update_script_normalizes_upload_tar_mtime_for_skewed_robot_clocks():
    text = SCRIPT.read_text()

    assert '--mtime=\'2000-01-01\'' in text


def test_update_script_keeps_remote_config_by_default():
    text = SCRIPT.read_text()

    assert 'CONFIG_TOML_MODE="${CONFIG_TOML_MODE:-keep}"' in text
    assert 'CONFIG_TOML_MODE=keep|overwrite|ask' in text
    assert 'ask)' in text


def test_update_script_exposes_config_modes_as_cli_options():
    text = SCRIPT.read_text()

    assert "--config-toml-mode keep|overwrite|ask" in text
    assert "--robots-hcl-mode keep|overwrite|ask" in text
    assert "--clean-remote" in text
    assert "--restart-cmd CMD" in text
    assert "--install-py-deps" in text
    assert "--no-install-py-deps" in text
    assert '-h|--help)' in text
    assert 'CONFIG_TOML_MODE="$2"' in text
    assert 'ROBOTS_HCL_MODE="$2"' in text
    # 기본값이 overwrite로 뒤집히면 현장 인벤토리가 조용히 덮어써진다.
    assert 'ROBOTS_HCL_MODE="${ROBOTS_HCL_MODE:-keep}"' in text
    assert "ROBOTS_HCL_MODE=keep|overwrite|ask" in text


def test_update_script_installs_python_deps_only_when_requested():
    text = SCRIPT.read_text()

    assert 'INSTALL_PY_DEPS="${INSTALL_PY_DEPS:-0}"' in text
    assert "--install-py-deps            Install Python deps from offline_packages after upload" in text
    assert "Install Python deps from offline_packages after upload (default)" not in text
    assert "INSTALL_PY_DEPS=1            Install Python deps from offline_packages after upload" in text
    assert 'if [[ "$INSTALL_PY_DEPS" == "1" ]]; then' in text


def test_update_script_treats_robots_hcl_as_remote_config():
    text = SCRIPT.read_text()

    assert 'cp "$LOCAL_ADAPTER_DIR/config/robots.hcl" "$STAGING_DIR/default_config/robots.hcl"' in text
    assert "--exclude='./config/robots.hcl'" in text
    assert "--exclude='config/robots.hcl'" in text
    assert 'preserved_robots="$staging/preserved-robots.hcl"' in text
    assert 'if [[ -f "$remote_dir/config/robots.hcl" ]]' in text
    assert 'cp "$preserved_robots" "$remote_dir/config/robots.hcl"' in text


def test_update_script_preserves_remote_extensions_hcl():
    text = SCRIPT.read_text()

    assert 'cp "$LOCAL_ADAPTER_DIR/config/extensions.hcl" "$STAGING_DIR/default_config/extensions.hcl"' in text
    assert "--exclude='./config/extensions.hcl'" in text
    assert "--exclude='config/extensions.hcl'" in text
    assert "! -name extensions.hcl" in text
    assert 'if [[ -f "$remote_dir/config/extensions.hcl" ]]' in text
    assert 'cp "$preserved_extensions" "$remote_dir/config/extensions.hcl"' in text
    assert 'cp "$staging/default_config/extensions.hcl" "$remote_dir/config/extensions.hcl"' in text


def test_update_script_can_overwrite_remote_extensions_and_recipes_hcl():
    """새 recipe/신호 이름은 두 파일이 함께 올라가야 동작한다.

    keep-only 시절에는 로봇에 파일이 이미 있으면 새 recipes.hcl과 그 recipe가
    참조하는 output_signals가 영원히 도착하지 못했다. 그러면 recipe는 실행 시
    'PIO signal ... is not declared'로 죽는다. 기본값은 여전히 keep이다 —
    현장이 손으로 고친 값을 조용히 덮지 않는다.
    """
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'EXTENSIONS_HCL_MODE="${EXTENSIONS_HCL_MODE:-keep}"' in text
    assert 'RECIPES_HCL_MODE="${RECIPES_HCL_MODE:-keep}"' in text
    assert "--extensions-hcl-mode keep|overwrite|ask" in text
    assert "--recipes-hcl-mode keep|overwrite|ask" in text
    assert 'EXTENSIONS_HCL_MODE="$2"' in text
    assert 'RECIPES_HCL_MODE="$2"' in text
    assert "EXTENSIONS_HCL_MODE=keep|overwrite|ask" in text
    assert "RECIPES_HCL_MODE=keep|overwrite|ask" in text
    # 원격에서 실제로 덮어쓰는 분기와, 모드가 원격까지 전달되는 경로.
    assert 'case "$EXTENSIONS_HCL_MODE" in' in text
    assert 'case "$RECIPES_HCL_MODE" in' in text
    assert "EXTENSIONS_HCL_MODE=%q" in text
    assert "RECIPES_HCL_MODE=%q" in text


def _remote_upload_script(tmp_path):
    """스크립트에 박혀 있는 원격 설치 본문을 그대로 꺼낸다.

    문자열 검사만으로는 "덮기 전에 백업한다"를 확인할 수 없다. 실제로 돌려서
    파일이 어떻게 남는지 본다.
    """
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    start = lines.index("read -r -d '' remote_upload_script <<'REMOTE_SCRIPT' || true")
    end = lines.index("REMOTE_SCRIPT", start)
    path = tmp_path / "remote.sh"
    path.write_text("\n".join(lines[start + 1:end]) + "\n", encoding="utf-8")
    return path


def _bundle(tmp_path):
    """업로드 번들(payload + 로컬 기본 설정)을 흉내낸다."""
    root = tmp_path / "bundle"
    (root / "payload").mkdir(parents=True)
    (root / "default_config").mkdir(parents=True)
    (root / "payload" / "main.py").write_text("print('adapter')\n")
    for name in ("config.toml", "robots.hcl", "extensions.hcl", "recipes.hcl"):
        (root / "default_config" / name).write_text(f"# shipped {name}\n")
    return root


def _robot_dir(tmp_path, name, *, seeded=True):
    """로봇 위의 ~/adaptor를 흉내낸다."""
    root = tmp_path / name
    (root / "config").mkdir(parents=True)
    if seeded:
        for item in ("config.toml", "robots.hcl", "extensions.hcl", "recipes.hcl"):
            (root / "config" / item).write_text(f"# robot {item}\n")
    return root


def _install(remote_script, robot_dir, bundle, *, extensions_mode, recipes_mode):
    payload = subprocess.run(
        ["tar", "-C", str(bundle), "-cf", "-", "."],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    env = {
        **os.environ,
        "CONFIG_TOML_MODE": "keep",
        "ROBOTS_HCL_MODE": "keep",
        "EXTENSIONS_HCL_MODE": extensions_mode,
        "RECIPES_HCL_MODE": recipes_mode,
        "CLEAN_REMOTE": "0",
        "INSTALL_PY_DEPS": "0",
        "COPY_VENV": "0",
        "REMOTE_ADAPTER_DIR_ARG": str(robot_dir),
    }
    done = subprocess.run(
        ["bash", str(remote_script)],
        input=payload,
        env=env,
        capture_output=True,
    )
    assert done.returncode == 0, done.stderr.decode()
    return done.stdout.decode()


def _backups(robot_dir, name):
    return sorted((robot_dir / "config").glob(f"{name}.bak-*"))


def test_keep_mode_leaves_the_robots_own_config_files_alone(tmp_path):
    remote = _remote_upload_script(tmp_path)
    robot = _robot_dir(tmp_path, "keep")

    _install(remote, robot, _bundle(tmp_path), extensions_mode="keep", recipes_mode="keep")

    assert (robot / "config" / "extensions.hcl").read_text() == "# robot extensions.hcl\n"
    assert (robot / "config" / "recipes.hcl").read_text() == "# robot recipes.hcl\n"
    assert _backups(robot, "extensions.hcl") == []


def test_overwrite_installs_the_shipped_config_and_backs_up_the_robots_own(tmp_path):
    """덮어쓰기는 되돌릴 수 없다 — 로봇에만 있던 현장 값이 사라진다.

    2026-08-18에 .62가 구세대 recipe 이름을 갖고 있어 엘리베이터 액션이 통째로
    UNSUPPORTED로 떨어졌다. 그때 필요한 게 이 덮어쓰기였고, 동시에 그 로봇의
    손으로 맞춘 extensions.hcl이 유일한 사본이었다.
    """
    remote = _remote_upload_script(tmp_path)
    robot = _robot_dir(tmp_path, "overwrite")

    _install(
        remote, robot, _bundle(tmp_path), extensions_mode="overwrite", recipes_mode="overwrite"
    )

    assert (robot / "config" / "extensions.hcl").read_text() == "# shipped extensions.hcl\n"
    assert (robot / "config" / "recipes.hcl").read_text() == "# shipped recipes.hcl\n"
    for name in ("extensions.hcl", "recipes.hcl"):
        backups = _backups(robot, name)
        assert len(backups) == 1, name
        assert backups[0].read_text() == f"# robot {name}\n"
    # robots.hcl은 이 로봇만의 하드웨어 값(USB 포트 등)과 enabled를 갖는다.
    # 함께 덮이면 그게 사라진다.
    assert (robot / "config" / "robots.hcl").read_text() == "# robot robots.hcl\n"
    assert (robot / "config" / "config.toml").read_text() == "# robot config.toml\n"


def test_overwrite_on_a_fresh_robot_leaves_no_stray_backup(tmp_path):
    remote = _remote_upload_script(tmp_path)
    robot = _robot_dir(tmp_path, "fresh", seeded=False)

    _install(
        remote, robot, _bundle(tmp_path), extensions_mode="overwrite", recipes_mode="overwrite"
    )

    assert (robot / "config" / "extensions.hcl").read_text() == "# shipped extensions.hcl\n"
    assert _backups(robot, "extensions.hcl") == []


def _modes_sent_to_remote(tmp_path, *args):
    """스텁 ssh로 원격에 전달된 두 모드 문자열만 꺼내 본다."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "ssh.log"
    stub = bin_dir / "ssh"
    stub.write_text(
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$SSH_STUB_LOG"\nexit 0\n'
    )
    stub.chmod(0o755)
    log.write_text("")
    # 스텁은 원격 설치를 흉내내지 않으므로 스크립트 자체는 실패로 끝난다.
    subprocess.run(
        ["bash", str(SCRIPT), *args, "--no-restart", "ucore@example.invalid"],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "SSH_STUB_LOG": str(log),
            "SSH_CONTROL_MASTER": "0",
        },
        capture_output=True,
    )
    found = re.search(
        r"EXTENSIONS_HCL_MODE=(\w+) RECIPES_HCL_MODE=(\w+)", log.read_text()
    )
    assert found, "the script never handed the config modes to the remote"
    return found.groups()


def test_configure_device_sends_overwrite_for_extensions_and_recipes(tmp_path):
    assert _modes_sent_to_remote(tmp_path, "--configure-device") == (
        "overwrite",
        "overwrite",
    )


def test_a_plain_deploy_still_keeps_both_config_files(tmp_path):
    assert _modes_sent_to_remote(tmp_path) == ("keep", "keep")


def test_update_script_documents_configure_device():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "--configure-device" in text
    assert "CONFIGURE_DEVICE=1" in text
    assert "backed up config/" in text
    assert "--configure-device" in SSH_MANUAL.read_text(encoding="utf-8")


def test_update_script_preserves_optional_remote_recipes_hcl():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "--exclude='./config/recipes.hcl'" in text
    assert "--exclude='config/recipes.hcl'" in text
    assert "! -name recipes.hcl" in text
    assert 'if [[ -f "$remote_dir/config/recipes.hcl" ]]' in text
    assert 'cp "$preserved_recipes" "$remote_dir/config/recipes.hcl"' in text


def test_update_script_converts_robots_toml_when_the_robot_has_no_hcl():
    """A pre-migration robot must not be left without robots.hcl.

    Default keep-mode used to leave it absent, which crash-loops the unit. The
    robot's own robots.toml is converted rather than installing the build
    machine's robots.hcl, which names a different robot and may be simulator.
    """
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'elif [[ -f "$remote_dir/config/robots.toml" ]]; then' in text
    assert '"$remote_dir/scripts/convert-robots-toml-to-hcl.py"' in text
    # Interpreter probed like run-adapter.sh, not hardcoded to one venv.
    assert 'convert_py=python3' in text
    assert '[[ -x "$remote_dir/venvJIBOT/bin/python" ]]' in text
    assert '[[ -x "$remote_dir/.venv/bin/python" ]]' in text
    # A half-written robots.hcl would replace clear guidance with a syntax error.
    assert 'rm -f "$remote_dir/config/robots.hcl"' in text


def test_update_script_seeds_recipes_hcl_when_the_robot_has_none():
    """Without this the shipped recipes.hcl can never reach a robot: it is
    rsync-excluded, so a robot that has never had one stays at zero recipes."""
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'cp "$LOCAL_ADAPTER_DIR/config/recipes.hcl" "$STAGING_DIR/default_config/recipes.hcl"' in text
    assert 'cp "$staging/default_config/recipes.hcl" "$remote_dir/config/recipes.hcl"' in text


def test_update_script_excludes_runtime_and_local_env_from_adapter_tar():
    text = SCRIPT.read_text()

    assert "--exclude='./runtime'" in text
    assert "--exclude='runtime'" in text
    assert "--exclude='.venv'" in text
    assert "--exclude='venvJIBOT'" in text
    assert "--exclude='.pytest_cache'" in text


def test_update_script_preserves_remote_mise_toml():
    # mise.toml pins the robot's Python (e.g. 3.12 on the dev box); uploading it
    # would flip the remote's pinned Python version on every deploy. Treat it like
    # config.toml/robots.hcl: never overwrite the remote copy.
    text = SCRIPT.read_text()

    assert "--exclude='./mise.toml'" in text
    assert "--exclude='mise.toml'" in text


def test_update_script_treats_sounds_as_managed_adapter_content():
    text = SCRIPT.read_text()

    assert "--exclude='sounds'" not in text
    assert "--exclude='./sounds'" not in text
    assert "    sounds \\" in text


def test_jibot_clean_removes_module_and_web_source_trees_before_extract():
    text = SCRIPT.read_text()
    clean = text[text.index('echo "[remote] removing managed adapter files"'):text.index('echo "[remote] extracting upload bundle"')]
    for managed in ("core", "extensions", "web"):
        assert f"    {managed} \\" in clean


def test_setup_script_supports_flat_remote_adapter_layout():
    text = SETUP_SCRIPT.read_text()

    assert 'if [[ -f "$REPO_ROOT/run-main.sh" ]]; then' in text
    assert 'ADAPTER_DIR="$REPO_ROOT"' in text
    assert 'ADAPTER_DIR="$REPO_ROOT/adaptor"' in text
    assert text.index('ADAPTER_DIR="$REPO_ROOT"') < text.index('ADAPTER_DIR="$REPO_ROOT/adaptor"')


def test_setup_script_installs_and_enables_units_before_venv_repair():
    text = SETUP_SCRIPT.read_text()

    assert text.index('install_unit "amr-adaptor.service"') < text.index('[[ $DO_VENV -eq 1 ]] && repair_venv')
    assert text.index('run_root "$SYSTEMCTL_BIN" enable "$unit"') < text.index('[[ $DO_VENV -eq 1 ]] && repair_venv')
    assert text.index('run_root "$SYSTEMCTL_BIN" enable amr-webui.service') < text.index('[[ $DO_VENV -eq 1 ]] && repair_venv')
    assert text.index('[[ $DO_VENV -eq 1 ]] && repair_venv') < text.index('restart_installed_services()')


def test_setup_script_accepts_crlf_config_vendor():
    text = SETUP_SCRIPT.read_text()

    assert 'gsub(/["' in text
    assert '\\r]/,"")' in text


def test_setup_script_defaults_to_jibot_only():
    text = SETUP_SCRIPT.read_text()

    assert "DO_HEXPLORER=1\nfi\n\n# The deploy user" not in text


def test_setup_script_reloads_systemd_after_webui_unit_install():
    text = SETUP_SCRIPT.read_text()

    assert 'getent passwd "$DEPLOY_USER"' in text
    assert 'Template placeholders were not fully rendered for $name' in text
    assert '==> systemctl daemon-reload (WebUi/camera units)' in text
    assert 'run_root "$SYSTEMCTL_BIN" daemon-reload' in text
    assert text.count('run_root "$SYSTEMCTL_BIN" daemon-reload') == 3


def test_setup_sudoers_allows_webui_restart_without_password():
    # amr-webui.service is not polkit-managed (the WebUi can't restart itself),
    # so the over-ssh deploy's `--restart-cmd 'sudo systemctl restart amr-webui.service'`
    # needs a NOPASSWD sudoers entry; otherwise the restart prompts for a password
    # over the non-interactive SSH session and the new code never loads.
    text = SETUP_SCRIPT.read_text()

    assert 'units+=("amr-webui.service")' in text


def test_setup_sudoers_allows_camera_control_without_password():
    text = SETUP_SCRIPT.read_text()
    sudoers = text[text.index("install_sudoers() {"):text.index("\n}\n\ninstall_webui_credentials")]

    assert 'units+=("amr-camera.service")' in sudoers


def test_setup_sudoers_grants_only_exact_adapter_units():
    text = SETUP_SCRIPT.read_text()
    sudoers = text[text.index("install_sudoers() {"):text.index("\n}\n\ninstall_webui_credentials")]

    assert "amr-adaptor@*.service" not in sudoers
    assert 'for unit in ${JIBOT_UNITS[@]+"${JIBOT_UNITS[@]}"}; do' in sudoers
    assert 'units+=("$unit")' in sudoers


def test_setup_sudoers_escapes_exact_unit_arguments(tmp_path):
    text = SETUP_SCRIPT.read_text()
    helper_start = text.index("sudoers_escape_arg() {")
    helper_end = text.index("\n}\n\ninstall_sudoers", helper_start) + 2
    helper = text[helper_start:helper_end]

    def escape(value):
        result = subprocess.run(
            ["bash", "-c", f'{helper}\nsudoers_escape_arg "$1"', "bash", value],
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout

    assert escape("amr-adaptor@line:1.service") == r"amr-adaptor@line\:1.service"
    assert escape(r"one\two,three:four=five") == r"one\\two\,three\:four\=five"

    sudoers = text[text.index("install_sudoers() {"):text.index("\n}\n\ninstall_webui_credentials")]
    assert 'escaped_unit="$(sudoers_escape_arg "$unit")"' in sudoers
    assert 'cmds+="$SYSTEMCTL_BIN $verb $escaped_unit"' in sudoers

    visudo = shutil.which("visudo")
    if visudo:
        rule = tmp_path / "sudoers"
        rule.write_text(
            "Cmnd_Alias TEST_ESCAPING = /usr/bin/systemctl restart "
            f'{escape("amr-adaptor@line:1.service")}\n'
            "test-user ALL=(root) NOPASSWD: TEST_ESCAPING\n"
        )
        parsed = subprocess.run([visudo, "-cf", rule], text=True, capture_output=True)
        assert parsed.returncode == 0, parsed.stderr


def test_setup_installs_sudoers_after_jibot_units_are_populated():
    text = SETUP_SCRIPT.read_text()
    sudoers_call = '\n  install_sudoers || echo "  (continuing without the sudoers rule; service control will prompt for a password)"'

    assert text.index('JIBOT_UNITS=()') < text.index(sudoers_call)
    assert text.index('JIBOT_UNITS+=("amr-adaptor.service")') < text.index(sudoers_call)


def test_setup_default_dry_run_sudoers_uses_exact_units():
    result = subprocess.run(
        [
            str(SETUP_SCRIPT),
            "--jibot",
            "--dry-run",
            "--no-venv",
            "--no-camera",
            "--no-start",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    sudoers = result.stdout[result.stdout.index("Cmnd_Alias ADAPTOR_TUI_CTL"):]

    for unit in "amr-adaptor.service", "amr-webui.service", "amr-camera.service":
        assert f"systemctl restart {unit}" in sudoers
    assert "amr-adaptor@*.service" not in sudoers


def test_service_helper_treats_webui_and_camera_as_optional():
    text = SERVICES_SCRIPT.read_text()
    assert 'if optional_unit_available "amr-webui.service"; then' in text
    assert 'if optional_unit_available "amr-camera.service"; then' in text


def test_service_helper_skips_missing_optional_units_but_dry_run_includes_them(tmp_path):
    fake_systemctl = tmp_path / "systemctl"
    fake_systemctl.write_text("#!/usr/bin/env bash\nexit 0\n")
    fake_systemctl.chmod(0o755)
    env = os.environ.copy()
    env.update(SYSTEMCTL_BIN=str(fake_systemctl), ADAPTER_DIR=str(tmp_path))

    status = subprocess.run(
        [SERVICES_SCRIPT, "status", "--no-sudo"], env=env, text=True, capture_output=True
    )
    assert status.returncode == 0, status.stderr
    assert "==> status amr-adaptor.service" in status.stdout
    assert "amr-webui.service" not in status.stdout
    assert "amr-camera.service" not in status.stdout

    dry_run = subprocess.run(
        [SERVICES_SCRIPT, "restart", "--dry-run", "--no-sudo"],
        env=env,
        text=True,
        capture_output=True,
    )
    assert dry_run.returncode == 0, dry_run.stderr
    for unit in "amr-adaptor.service", "amr-webui.service", "amr-camera.service":
        assert f"==> restart {unit}" in dry_run.stdout


def test_active_setup_text_does_not_point_to_removed_tui():
    setup = SETUP_SCRIPT.read_text()
    unit = ADAPTOR_UNIT.read_text()
    assert "Launch the TUI:" not in setup
    assert "Inside the TUI" not in setup
    assert "scripts/adaptor-tui.sh" not in setup
    assert "scripts/adaptor-tui.sh" not in unit
    assert "AMR service control" in setup


def test_setup_script_creates_default_webui_credentials_when_missing():
    text = SETUP_SCRIPT.read_text()

    assert 'WEBUI_DEFAULT_USER="admin"' in text
    assert 'WEBUI_DEFAULT_PASSWORD="labtomarket1231"' in text
    assert 'install_webui_credentials' in text
    assert 'username = "$WEBUI_DEFAULT_USER"' in text
    assert 'password = "$WEBUI_DEFAULT_PASSWORD"' in text
    assert 'chmod 600 "$file"' in text


def test_python38_webui_tomli_dependency_is_installed():
    requirements = REQUIREMENTS.read_text()
    setup = SETUP_SCRIPT.read_text()
    update = SCRIPT.read_text()
    offline_deps = OFFLINE_DEPS_SCRIPT.read_text()

    assert "tomli==2.4.1" in requirements
    assert "import paho.mqtt.client; import tomli" in setup
    assert '"$venv/bin/python" -m ensurepip --upgrade' in setup
    assert '"$python_bin" -m ensurepip --upgrade' in update
    assert "install_pure_python_wheels_without_pip" in setup
    assert 'if [[ -x "$ADAPTER_DIR/.venv/bin/python" ]]' in offline_deps
    assert "if [[ -x .venv/bin/python ]]" in update
    # py2.py3-none-any(pyserial 등)까지 잡으려면 py3로 좁히면 안 된다 —
    # test_offline_wheel_glob_covers_py2py3_wheels가 그 경계를 지킨다.
    assert '"*-none-any.whl"' in setup
    assert '"*-none-any.whl"' in update
    assert '"*-none-any.whl"' in offline_deps
    assert "offline deps ok" in offline_deps


def test_webui_polkit_allows_camera_service_control():
    text = POLKIT_RULE.read_text()

    assert "__MANAGED_UNITS__" in text
    assert "__JIBOT_TEMPLATE_RULE__" in text
    assert '"org.freedesktop.login1.reboot"' in text
    assert '"org.freedesktop.login1.reboot-multiple-sessions"' in text
    assert '"org.freedesktop.login1.reboot-ignore-inhibit"' in text


def test_setup_script_renders_webui_polkit_units_from_selected_targets():
    text = SETUP_SCRIPT.read_text()

    assert "render_webui_polkit" in text
    assert 'local polkit_units=("amr-adaptor.service" "urobot.service")' in text
    assert '[[ $DO_CAMERA -eq 1 ]] && polkit_units+=("amr-camera.service")' in text
    # 구 어댑터 유닛은 polkit 목록에 없다.
    assert "jibot-adapter.service" not in text.split("migrate_legacy_units")[0]


def test_setup_script_dry_run_omits_hexplorer_by_default():
    result = subprocess.run(
        [
            str(SETUP_SCRIPT),
            "--dry-run",
            "--no-venv",
            "--no-sudoers",
            "--no-camera",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert "install_unit \"hexplorer-adapter.service\"" not in result.stdout
    assert "__MANAGED_UNITS__" not in result.stdout
    assert "__JIBOT_TEMPLATE_RULE__" not in result.stdout


def _setup_dry_run_with_polkit_version(version, tmp_path):
    """Run the setup script in dry-run with a fake ``pkaction`` on PATH that
    reports ``version``, so the polkit-format branch can be exercised on any
    host. Returns captured stdout."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    pkaction = fake_bin / "pkaction"
    pkaction.write_text(f'#!/bin/sh\necho "pkaction version {version}"\n')
    pkaction.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        [
            str(SETUP_SCRIPT),
            "--jibot",
            "--dry-run",
            "--no-venv",
            "--no-sudoers",
            "--no-camera",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=True,
    )
    return result.stdout


def test_setup_installs_pkla_for_old_polkit(tmp_path):
    # polkit < 0.106 (e.g. the jibot onboard / Ubuntu 16.04) ignores JS rules.d,
    # so the pklocalauthority (.pkla) form must be installed instead.
    out = _setup_dry_run_with_polkit_version("0.105", tmp_path)

    assert "polkit 0.105 (<0.106): pklocalauthority rule" in out
    assert "/etc/polkit-1/localauthority/50-local.d/10-amr-webui.pkla" in out
    assert "Identity=unix-user:" in out
    assert "Action=org.freedesktop.systemd1.manage-units;org.freedesktop.systemd1.manage-unit-files" in out
    assert "ResultAny=yes" in out
    # the JS rule must NOT be the chosen format on old polkit
    assert "polkit.addRule(function" not in out


def test_setup_installs_js_rules_for_modern_polkit(tmp_path):
    # polkit >= 0.106 keeps the original JS rules.d/*.rules method.
    out = _setup_dry_run_with_polkit_version("0.120", tmp_path)

    assert "polkit 0.120 (>=0.106): JS rule" in out
    assert "/etc/polkit-1/rules.d/10-amr-webui.rules" in out
    assert "polkit.addRule(function" in out
    # the pkla form must not be rendered on modern polkit
    assert "localauthority/50-local.d/10-amr-webui.pkla" not in out
    assert "ResultAny=yes" not in out


def test_webui_service_runs_as_deploy_user_without_extra_account():
    text = WEBUI_UNIT.read_text()

    assert "User=__USER__" in text
    assert "User=amr-webui" not in text


def test_setup_script_renders_webui_polkit_for_deploy_user():
    text = SETUP_SCRIPT.read_text()

    assert 'sed "s|__USER__|$DEPLOY_USER|g" "$SYSTEMD_SRC/amr-webui-polkit.rules"' in text
    assert "sudo useradd --system --no-create-home amr-webui" not in text


def test_setup_script_defaults_to_single_adapter_service():
    text = SETUP_SCRIPT.read_text()

    assert 'no config adapter instances; using single amr-adaptor.service' in text
    assert 'JIBOT_UNITS+=("amr-adaptor.service")' in text
    assert 'JIBOT_UNITS+=("amr-adaptor@${rid}.service")' not in text
    assert 'ROBOT_COUNT=' not in text


def test_setup_script_installs_camera_service_with_webui():
    text = SETUP_SCRIPT.read_text()

    assert "install_amr_camera_service" in text
    assert "amr-camera.service" in text
    assert "Description=AMR camera (ROS image topics over HTTP)" in text
    assert 'pkg="ros-${distro}-web-video-server"' in text
    assert 'rospack find web_video_server' in text
    assert 'CAMERA_SERVICE_AVAILABLE=1' in text
    assert "sudo systemctl enable --now amr-camera.service" not in text


def test_amr_adaptor_tmpfiles_template_declares_run_root():
    text = TMPFILES_CONF.read_text()
    assert "d /run/amr-adaptor 0750 __USER__" in text


def test_setup_script_installs_amr_adaptor_tmpfiles_for_deploy_user():
    text = SETUP_SCRIPT.read_text()
    assert "install_amr_tmpfiles" in text
    assert 'sed "s|__USER__|$DEPLOY_USER|g" "$src"' in text
    assert "/etc/tmpfiles.d/amr-adaptor.conf" in text
    assert "systemd-tmpfiles --create" in text


def test_setup_migrates_old_unit_names():
    text = SETUP_SCRIPT.read_text()
    assert "migrate_legacy_units" in text
    for stale in ("jibot-adapter.service", "hexplorer-adapter.service", "web_video_server.service"):
        assert stale in text  # 마이그레이션 대상으로 언급


def test_setup_enables_config_adapter_instances():
    text = SETUP_SCRIPT.read_text()
    # config.toml [[adapter.instances]]의 이름으로 amr-adaptor@<name>.service를 enable.
    assert "adapter.instances" in text
    assert 'JIBOT_UNITS+=("amr-adaptor@${name}.service")' in text


def test_offline_wheel_glob_covers_py2py3_wheels(tmp_path):
    """pyserial 휠은 py2.py3-none-any다. py3-none-any만 glob하면 requirements에
    선언해도 오프라인 설치에서 조용히 빠져 pioInit이 ModuleNotFoundError로 죽는다.
    반대로 *.whl로 넓히면 native 휠을 purelib에 풀어버리므로 그것도 막는다."""
    (tmp_path / "pyserial-3.5-py2.py3-none-any.whl").touch()
    (tmp_path / "paho_mqtt-2.1.0-py3-none-any.whl").touch()
    (tmp_path / "regex-2024.11.6-cp311-cp311-manylinux_2_17_aarch64.whl").touch()

    for script in (SCRIPT, SETUP_SCRIPT, OFFLINE_DEPS_SCRIPT):
        match = re.search(
            r'wheel_dir\.glob\("([^"]+)"\)', script.read_text(encoding="utf-8")
        )
        assert match, f"{script.name}: wheel glob not found"
        names = {path.name for path in tmp_path.glob(match.group(1))}
        assert "pyserial-3.5-py2.py3-none-any.whl" in names, script.name
        assert "paho_mqtt-2.1.0-py3-none-any.whl" in names, script.name
        assert not [name for name in names if "manylinux" in name], script.name
