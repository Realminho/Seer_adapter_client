import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "update-hexplorer-adapter-over-ssh.sh"


def test_help_documents_restart_options():
    result = subprocess.run(
        [str(SCRIPT), "--help"], cwd=REPO_ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert result.returncode == 0
    assert "--restart" in result.stdout
    assert "--no-restart" in result.stdout
    assert "--restart-cmd CMD" in result.stdout


def test_restart_cmd_equals_requires_nonempty_value():
    result = subprocess.run(
        [str(SCRIPT), "--restart-cmd="], cwd=REPO_ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert result.returncode == 2
    assert "Missing value for --restart-cmd" in result.stderr


def test_restart_cmd_separate_requires_nonempty_value():
    result = subprocess.run(
        [str(SCRIPT), "--restart-cmd", ""], cwd=REPO_ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert result.returncode == 2
    assert "Missing value for --restart-cmd" in result.stderr


def test_uploads_service_helper_and_uses_it_for_restart():
    text = SCRIPT.read_text()
    commands = " ".join(text.replace("\\\n", " ").replace('"', "").split())
    assert 'LOCAL_SERVICE_HELPER="scripts/adaptor-services.sh"' in text
    assert 'mkdir -p $remote_dir_expr/scripts' in text
    assert 'cat > $remote_dir_expr/scripts/adaptor-services.sh' in text
    assert 'chmod +x $remote_dir_expr/scripts/adaptor-services.sh' in text
    assert '< "$LOCAL_SERVICE_HELPER"' in text
    assert 'tar -C . -cf - scripts |' not in commands
    assert 'cp -R scripts ' not in commands
    assert 'elif [[ "$RESTART_ADAPTER" == "1" ]]; then' in text
    assert '"cd $remote_dir_expr && scripts/adaptor-services.sh restart"' in text


def test_restart_uses_tty_when_available_and_keeps_headless_fallback():
    text = SCRIPT.read_text()
    assert 'restart_command=""' in text
    assert 'restart_command="$RESTART_CMD"' in text
    assert 'restart_command="cd $remote_dir_expr && scripts/adaptor-services.sh restart"' in text
    assert '[[ -r /dev/tty ]]' not in text
    assert 'if { exec 3</dev/tty; } 2>/dev/null; then' in text
    assert '"${ssh_base[@]}" -tt "$REMOTE_HOST" "$restart_command" <&3' in text
    assert 'exec 3<&-' in text
    assert '"${ssh_base[@]}" "$REMOTE_HOST" "$restart_command"' in text


def test_restart_falls_back_to_plain_ssh_without_controlling_tty(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ssh_log = tmp_path / "ssh.log"
    fake_ssh = fake_bin / "ssh"
    fake_ssh.write_text(
        r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$SSH_LOG"
case "$*" in
  *test\ -f*config/config.toml*) exit 1 ;;
esac
cat >/dev/null
"""
    )
    fake_ssh.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["SSH_LOG"] = str(ssh_log)
    env["INCLUDE_OFFLINE_PACKAGES"] = "0"
    result = subprocess.run(
        [str(SCRIPT), "--restart", "robot@example"],
        cwd=REPO_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    assert result.returncode == 0, result.stderr
    restart_calls = [
        line for line in ssh_log.read_text().splitlines()
        if "scripts/adaptor-services.sh restart" in line
    ]
    assert restart_calls
    assert all("-tt" not in call.split() for call in restart_calls)


def test_service_helper_is_validated_before_remote_mutation():
    text = SCRIPT.read_text()
    validation = '[[ -r "$LOCAL_SERVICE_HELPER" ]]'
    first_remote_mutation = '"${ssh_base[@]}" "$REMOTE_HOST" "mkdir -p $remote_dir_expr"'

    assert validation in text
    assert text.index(validation) < text.index(first_remote_mutation)


def test_clean_removes_module_and_web_source_trees():
    text = SCRIPT.read_text()
    clean = text[text.index("rm -rf \\"):text.index("if [ -d config ]")]
    for managed in ("core", "extensions", "web"):
        assert managed in clean
