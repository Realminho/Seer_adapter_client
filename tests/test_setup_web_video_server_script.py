from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "setup-web-video-server-on-onboard.sh"


def test_script_defaults_camera_port_to_9001():
    text = SCRIPT.read_text()

    assert 'WEB_VIDEO_PORT="${WEB_VIDEO_PORT:-9001}"' in text
    assert "WEB_VIDEO_PORT=9001" in text


def test_script_does_not_inherit_local_ros_distro_by_default():
    text = SCRIPT.read_text()

    assert 'REMOTE_ROS_DISTRO="${WEB_VIDEO_ROS_DISTRO:-}"' in text
    assert 'ROS_DISTRO="${ROS_DISTRO:-}"' not in text
    assert "WEB_VIDEO_ROS_DISTRO=<empty>" in text


def test_script_preserves_remote_installer_exit_status_after_cleanup():
    text = SCRIPT.read_text()

    assert "status=\\$?" in text
    assert "rm -f '${REMOTE_TMP}'" in text
    assert "exit \\$status" in text


def test_script_does_not_install_camera_package_from_apt():
    text = SCRIPT.read_text()

    assert "apt-get update" not in text
    assert "apt-get install" not in text
    assert "install the bundled offline debs first" in text


def test_script_does_not_pkill_itself_by_matching_script_name():
    text = SCRIPT.read_text()

    assert "pkill -f 'web_video_server'" not in text
    assert 'fuser -k "${WEB_VIDEO_PORT}/tcp"' in text
