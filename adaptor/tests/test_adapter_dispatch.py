import io
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from config import adapter_dispatch
from config.adapter_dispatch import _fleet_help, _main, _python_bin, resolve_dispatch
from config.fleet import FleetError, load_fleet


def _write(tmp, name, body):
    path = Path(tmp) / name
    path.write_text(textwrap.dedent(body).strip() + "\n")
    return str(path)


class ResolveDispatchTest(unittest.TestCase):
    def test_default_no_instance_uses_single_robot_from_fleet(self):
        with TemporaryDirectory() as tmp:
            cfg = _write(tmp, "config.toml", '[adapter]\nvendor = "hexplorer"\n')
            robots = _write(
                tmp,
                "robots.hcl",
                'robot "HN-SH6-TR-002" {}\n',
            )
            self.assertEqual(
                resolve_dispatch(None, config_path=cfg, robots_path=robots),
                ("jibot", ["--robots", robots, "--robot", "HN-SH6-TR-002"]),
            )

    def test_default_no_instance_uses_run_multi_for_multiple_robots(self):
        with TemporaryDirectory() as tmp:
            cfg = _write(tmp, "config.toml", "[mqtt_broker]\nhost = \"x\"\n")
            robots = _write(
                tmp,
                "robots.hcl",
                'robot "HN-SH6-TR-001" {}\n\n'
                'robot "HN-SH6-TR-002" {}\n',
            )
            self.assertEqual(
                resolve_dispatch(None, config_path=cfg, robots_path=robots),
                ("jibot-multi", ["--robots", robots]),
            )

    def test_default_no_instance_ignores_disabled_robots(self):
        # 현장 로봇 전부를 한 robots.hcl에 적어 두고 기계마다 자기 블록만
        # 켜는 쓰임새다. 꺼진 블록까지 세면 한 대짜리 로봇이 multi로 뜬다.
        with TemporaryDirectory() as tmp:
            cfg = _write(tmp, "config.toml", "[mqtt_broker]\nhost = \"x\"\n")
            robots = _write(
                tmp,
                "robots.hcl",
                'robot "HN-SH6-TR-001" {\n  enabled = false\n}\n\n'
                'robot "HN-SH6-TR-002" {}\n',
            )
            self.assertEqual(
                resolve_dispatch(None, config_path=cfg, robots_path=robots),
                ("jibot", ["--robots", robots, "--robot", "HN-SH6-TR-002"]),
            )

    def test_default_no_instance_requires_fleet(self):
        with TemporaryDirectory() as tmp:
            cfg = _write(tmp, "config.toml", "[mqtt_broker]\nhost = \"x\"\n")
            with self.assertRaises(FleetError):
                resolve_dispatch(None, config_path=cfg, robots_path=str(Path(tmp) / "missing.hcl"))

    def test_named_instance_match(self):
        with TemporaryDirectory() as tmp:
            cfg = _write(
                tmp,
                "config.toml",
                """
                [adapter]
                vendor = "jibot"
                [[adapter.instances]]
                name = "line1"
                vendor = "hexplorer"
                config = "config/line1.toml"
                """,
            )
            self.assertEqual(
                resolve_dispatch("line1", config_path=cfg),
                ("hexplorer", ["--config", "config/line1.toml"]),
            )

    def test_unmatched_instance_is_jibot_robot(self):
        # 명시 인스턴스가 아니면 jibot fleet의 robot id(--robot)로 간주한다.
        with TemporaryDirectory() as tmp:
            cfg = _write(tmp, "config.toml", '[adapter]\nvendor = "jibot"\n')
            robots = _write(
                tmp,
                "robots.hcl",
                'robot "ROBOT-A" {}\n',
            )
            self.assertEqual(
                resolve_dispatch("ROBOT-A", config_path=cfg, robots_path=robots),
                ("jibot", ["--robots", robots, "--robot", "ROBOT-A"]),
            )


class MissingFleetMessageTest(unittest.TestCase):
    """A missing robots.hcl must fail legibly, not as a traceback.

    systemd restarts this every few seconds, so a stack trace buries the one
    line an operator can act on under an ever-growing journal.
    """

    def _run_main(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = _main(["adapter_dispatch"])
        return code, out.getvalue(), err.getvalue()

    def test_missing_fleet_prints_guidance_to_stderr_and_exits_nonzero(self):
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "config" / "robots.hcl"
            with patch.object(adapter_dispatch, "DEFAULT_FLEET_PATH", missing):
                code, out, err = self._run_main()

        self.assertEqual(code, 1)
        # stdout is parsed by run-adapter.sh; nothing may leak into it.
        self.assertEqual(out, "")
        self.assertIn("기동할 수 없습니다", err)
        self.assertNotIn("Traceback", err)

    def test_main_dispatches_from_explicit_fleet_path(self):
        with TemporaryDirectory() as tmp:
            fleet = _write(
                tmp,
                "simulator-robots.hcl",
                'robot "SIM-1" {}\nrobot "SIM-2" {}\n',
            )
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = _main(["adapter_dispatch", "", fleet])

        self.assertEqual(code, 0)
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(
            out.getvalue().splitlines(),
            ["jibot-multi", "--robots", fleet],
        )

    def test_guidance_converts_robots_toml_when_the_robot_still_has_one(self):
        with TemporaryDirectory() as tmp:
            fleet = Path(tmp) / "robots.hcl"
            (Path(tmp) / "robots.toml").write_text("[[robot]]\nid = \"R\"\n")
            lines = "\n".join(_fleet_help(fleet))

        self.assertIn("convert-robots-toml-to-hcl.py", lines)
        # Copying the example would overwrite the robot's only identity source.
        self.assertNotIn("robots.hcl.example", lines)

    def test_guidance_falls_back_to_the_example_without_a_robots_toml(self):
        with TemporaryDirectory() as tmp:
            lines = "\n".join(_fleet_help(Path(tmp) / "robots.hcl"))

        self.assertIn("robots.hcl.example", lines)
        self.assertNotIn("convert-robots-toml-to-hcl.py", lines)

    def test_guidance_tells_the_operator_which_block_to_enable(self):
        """The example documents the multi-robot layout with two blocks.

        Enabling both puts two adapters on one onboard PC, dialling the
        example's placeholder IPs. This text is the operator's only prompt at
        the moment they copy the file.
        """
        with TemporaryDirectory() as tmp:
            lines = "\n".join(_fleet_help(Path(tmp) / "robots.hcl"))

        self.assertIn("enabled = false", lines)
        self.assertIn("run_multi.py", lines)
        self.assertIn("자리표시자", lines)

    def test_example_has_two_blocks_with_exactly_one_enabled(self):
        # Guard the guidance against the example drifting: trimmed to one block
        # (advice contradicts the file) or shipped with both enabled (a verbatim
        # copy then starts two adapters, which is what the flag prevents).
        example = Path(adapter_dispatch.__file__).with_name("robots.hcl.example")
        fleet = load_fleet(example, include_disabled=True)

        self.assertGreaterEqual(len(fleet), 2)
        self.assertEqual(len([r for r in fleet if r.get("enabled", True)]), 1)

    def test_printed_interpreter_exists_on_this_install(self):
        """Naming a venv the robot lacks turns the advice into a second failure.

        run-adapter.sh probes .venv then venvJIBOT then python3; the printed
        command must resolve the same way.
        """
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "robots.toml").write_text("[[robot]]\nid = \"R\"\n")
            command = "\n".join(_fleet_help(Path(tmp) / "robots.hcl"))

        chosen = _python_bin()
        self.assertIn(chosen, command)
        if chosen != "python3":
            self.assertTrue((adapter_dispatch._ADAPTER_ROOT / chosen).is_file())

    def test_python_bin_falls_back_when_no_venv_is_present(self):
        with TemporaryDirectory() as tmp:
            with patch.object(adapter_dispatch, "_ADAPTER_ROOT", Path(tmp)):
                self.assertEqual(_python_bin(), "python3")

    def test_python_bin_prefers_venv_jibot_when_dot_venv_is_absent(self):
        with TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "venvJIBOT" / "bin"
            legacy.mkdir(parents=True)
            (legacy / "python").write_text("")
            with patch.object(adapter_dispatch, "_ADAPTER_ROOT", Path(tmp)):
                self.assertEqual(_python_bin(), "venvJIBOT/bin/python")


if __name__ == "__main__":
    unittest.main()
