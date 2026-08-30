"""EPR 적용 후 재검증이 **부팅 로더**를 통과하는지 보는 축.

문법 검사(hcl2.load)만으로는 부족하다는 것이 이 파일의 요지다. 값만 고칠 때는 드러나지
않았지만 recipe/extension 블록을 통째로 추가·삭제하면 "파싱은 되는데 부팅은 안 되는"
파일이 흔해진다. 그런 파일이 통과하면 적용은 성공으로 보고되고 다음 재시작에서 어댑터가
못 뜬다 — 원격에서 되돌릴 창구가 사라지는 실패다.
"""

import shutil
import sys
import unittest
from pathlib import Path

ADAPTOR_DIR = Path(__file__).resolve().parents[1]
if str(ADAPTOR_DIR) not in sys.path:
    sys.path.insert(0, str(ADAPTOR_DIR))

from adapter_jibot import _validate_hcl_on_disk, _validate_parameter_sources  # noqa: E402

CONFIG_DIR = ADAPTOR_DIR / "config"
SOURCES = ("config.toml", "extensions.hcl", "recipes.hcl", "robots.hcl")


class TestValidateParameterSources(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.paths = {}
        for name in SOURCES:
            src = CONFIG_DIR / name
            if not src.exists():
                continue
            dst = self.tmp / name
            shutil.copy2(src, dst)
            self.paths[name] = dst

    def test_untouched_config_is_valid(self):
        ok, message = _validate_parameter_sources(self.paths)
        self.assertTrue(ok, message)

    def test_duplicate_recipe_is_rejected(self):
        """문법은 멀쩡한데 부팅이 멈추는 대표 사례. 블록을 복제해 이름만 그대로 둔 경우다."""
        path = self.paths["recipes.hcl"]
        path.write_text(
            path.read_text(encoding="utf-8") + '\nrecipe "pioElevatorOpen1f" {\n  label = "dup"\n}\n',
            encoding="utf-8",
        )
        self.assertTrue(_validate_hcl_on_disk(path)[0], "문법 검사는 통과해야 이 테스트가 의미 있음")

        ok, message = _validate_parameter_sources(self.paths)
        self.assertFalse(ok)
        self.assertIn("duplicate recipe", message)

    def test_unknown_step_field_is_rejected(self):
        path = self.paths["recipes.hcl"]
        path.write_text(
            path.read_text(encoding="utf-8")
            + '\nrecipe "newRecipe" {\n  step "pioInit" {\n    nope = 1\n  }\n}\n',
            encoding="utf-8",
        )
        self.assertTrue(_validate_hcl_on_disk(path)[0])

        ok, message = _validate_parameter_sources(self.paths)
        self.assertFalse(ok)
        self.assertIn("unknown field", message)

    def test_broken_hcl_syntax_is_rejected(self):
        path = self.paths["recipes.hcl"]
        path.write_text('recipe "a" {\n  label = \n', encoding="utf-8")

        ok, _message = _validate_parameter_sources(self.paths)
        self.assertFalse(ok)

    def test_missing_optional_source_falls_back_to_default(self):
        """로봇에 recipes.hcl 이 없을 수 있다. 없는 경로를 넘기면 로더가 '파일 없음'으로 죽는다."""
        self.paths["recipes.hcl"].unlink()
        ok, message = _validate_parameter_sources(self.paths)
        self.assertTrue(ok, message)

    def test_state_action_registered_recipe_is_valid(self):
        path = self.paths["extensions.hcl"]
        path.write_text(
            path.read_text(encoding="utf-8")
            + '''
state_action "DRIVING" {
  start = { action = "pioElevatorOpen1f" }
  end = { action = "pioElevatorClose1f" }
}
''',
            encoding="utf-8",
        )
        ok, message = _validate_parameter_sources(self.paths)
        self.assertTrue(ok, message)

    def test_state_action_unregistered_action_is_rejected(self):
        path = self.paths["extensions.hcl"]
        path.write_text(
            path.read_text(encoding="utf-8")
            + '\nstate_action "DRIVING" { start = { action = "missingAction" } }\n',
            encoding="utf-8",
        )
        ok, message = _validate_parameter_sources(self.paths)
        self.assertFalse(ok)
        self.assertIn("unregistered action 'missingAction'", message)


if __name__ == "__main__":
    unittest.main()
