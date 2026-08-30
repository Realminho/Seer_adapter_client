import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate-extension-recipes.py"
PYTHON = ROOT / "adaptor" / ".venv" / "bin" / "python"


def test_validator_checks_complete_robot_configuration(tmp_path):
    recipes = tmp_path / "recipes.hcl"
    recipes.write_text(
        '''recipe "stopOnly" {
          step "manualStop" {}
        }''',
        encoding="utf-8",
    )
    robots = tmp_path / "robots.hcl"
    robots.write_text(
        textwrap.dedent(
            f'''
            robot "R1" {{
              simulator = true
              extensions = "{(ROOT / "adaptor/config/extensions.hcl").resolve()}"
              recipes = "{recipes}"
            }}
            '''
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(PYTHON), str(SCRIPT), "--robots", str(robots), "--robot", "R1"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "OK robot=R1" in result.stdout
    assert "enabled recipes=stopOnly" in result.stdout


def test_validator_returns_nonzero_for_missing_recipe_reference(tmp_path):
    recipes = tmp_path / "recipes.hcl"
    recipes.write_text(
        'recipe "bad" { step "doesNotExist" {} }\n',
        encoding="utf-8",
    )
    robots = tmp_path / "robots.hcl"
    robots.write_text(
        f'''robot "R1" {{
          extensions = "{(ROOT / "adaptor/config/extensions.hcl").resolve()}"
          recipes = "{recipes}"
        }}\n''',
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(PYTHON), str(SCRIPT), "--robots", str(robots), "--robot", "R1"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "doesNotExist" in result.stderr
