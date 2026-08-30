import base64
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "change-jibot-network-over-ssh.sh"


def test_rejects_gateway_equal_to_robot_ip():
    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "ucore@192.168.3.221",
            "--ip",
            "172.16.2.61",
            "--gateway",
            "172.16.2.61",
            "--dry-run",
        ],
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 2
    assert "--gateway must not be the same" in result.stderr


def test_apply_pipeline_restarts_network_and_wifi_services():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "netplan apply" in script
    assert "systemctl restart NetworkManager.service" in script
    assert 'NMCLI_MODIFY=(connection modify uuid "$NM_WIFI_UUID")' in script
    assert 'connection.id "$NEW_SSID"' in script
    assert 'nmcli "${NMCLI_MODIFY[@]}"' in script
    assert 'nmcli connection up uuid "$NM_WIFI_UUID" ifname wlan0' in script
    assert '"$ACTUAL_SSID" == "$TARGET_SSID"' in script
    assert "systemctl restart wpa_supplicant.service" in script
    assert "wpa_cli -i wlan0 reconfigure" in script
    assert "systemctl restart wpa_supplicant@wlan0.service" not in script
    assert (
        "wpa_supplicant file already matches; continuing to synchronize "
        "the NetworkManager profile."
    ) in script
    assert "[change-jibot-network] detached apply scheduled." in script


def test_wifi_select_scans_remote_wlan0_and_passes_selected_credentials(tmp_path):
    calls_file = tmp_path / "ssh-calls.txt"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ssh = fake_bin / "ssh"
    fake_ssh.write_text(
        f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> {calls_file}
case "$*" in
  *"iw dev wlan0 scan"*)
    cat <<'SCAN'
        signal: -56.00 dBm
        SSID: TP_Link_AMR_5G
        signal: -48.00 dBm
        SSID: lab2m_guest
        signal: -60.00 dBm
        SSID: lab2m_guest
        signal: -75.00 dBm
        SSID:
SCAN
    ;;
  *"cat >"*|*"sudo env"*)
    cat >/dev/null || true
    ;;
  *"-O exit"*)
    ;;
esac
exit 0
""",
        encoding="utf-8",
    )
    fake_ssh.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "ucore@192.168.3.222",
            "--wifi-select",
            "--dry-run",
            "-y",
        ],
        input="2\nsecret-pass\n",
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "1) lab2m_guest" in result.stdout
    assert "2) TP_Link_AMR_5G" in result.stdout

    calls = calls_file.read_text(encoding="utf-8")
    selected_ssid = base64.b64encode(b"TP_Link_AMR_5G").decode("ascii")
    selected_psk = base64.b64encode(b"secret-pass").decode("ascii")
    assert f"NEW_SSID_B64='{selected_ssid}'" in calls
    assert f"NEW_PSK_B64='{selected_psk}'" in calls
