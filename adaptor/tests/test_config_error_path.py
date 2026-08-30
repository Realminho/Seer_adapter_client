"""FATAL CONFIG_LOAD_FAILED가 가리키는 "고쳐야 할 파일" 경로.

ConfigErrorReporter는 이 경로를 errorReferences.configPath로 싣는다. 깨진 것이
extensions.hcl인데 config.toml을 가리키면, 운영자는 멀쩡한 파일을 열어 놓고
무엇이 문제인지 찾게 된다 — 살아남아서 보고하는 의미가 사라진다.
"""
import sys
import unittest
from pathlib import Path

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
if str(ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(ADAPTER_ROOT))

from config.config import DEFAULT_CONFIG_PATH
from config.errors import ConfigError
from config.extensions import DEFAULT_EXTENSIONS_PATH, ExtensionsError
from config.recipes import RecipesError
from main import config_error_path


class TestConfigErrorPath(unittest.TestCase):
    def test_extensions_error_points_at_the_extensions_file(self):
        path = config_error_path(
            ExtensionsError("moved key"),
            config_path="config/robot-a.toml",
            extensions_path="config/a-ext.hcl",
            recipes_path="config/a-recipes.hcl",
        )
        self.assertEqual(path, "config/a-ext.hcl")

    def test_recipes_error_points_at_the_recipes_file(self):
        path = config_error_path(
            RecipesError("bad step"),
            config_path="config/robot-a.toml",
            extensions_path="config/a-ext.hcl",
            recipes_path="config/a-recipes.hcl",
        )
        self.assertEqual(path, "config/a-recipes.hcl")

    def test_other_errors_still_point_at_the_config_toml(self):
        path = config_error_path(
            FileNotFoundError("no such file"),
            config_path="config/robot-a.toml",
            extensions_path="config/a-ext.hcl",
            recipes_path="config/a-recipes.hcl",
        )
        self.assertEqual(path, "config/robot-a.toml")

    def test_a_section_error_names_the_file_the_key_came_from(self):
        """섹션 검증은 어느 파일에서 온 키인지 이미 안다 — 그 답을 써야 한다.

        타입만 보고 되짚으면 robots.hcl이나 config.toml에서 온 키도
        extensions.hcl이라고 말하게 되어, 운영자가 멀쩡한 파일을 뒤진다.
        """
        path = config_error_path(
            ConfigError("[pio]에 모르는 키", path="config/robots.hcl"),
            config_path="config/robot-a.toml",
            extensions_path="config/a-ext.hcl",
            recipes_path="config/a-recipes.hcl",
        )
        self.assertEqual(path, "config/robots.hcl")

    def test_an_unset_extensions_path_names_the_default_file(self):
        """robots.hcl에 extensions = "..." 가 없으면 기본 파일을 쓴다.

        None을 그대로 실으면 configPath가 빠져 운영자에게 아무 단서도 안 남는다 —
        이 현장(.61)이 정확히 그 경우다.
        """
        path = config_error_path(
            ExtensionsError("moved key"),
            config_path=None,
            extensions_path=None,
            recipes_path=None,
        )
        self.assertEqual(path, str(DEFAULT_EXTENSIONS_PATH))


    def test_a_bare_error_without_a_config_path_still_names_a_file(self):
        """라벨 없는 KeyError/TypeError도 이제 이 경로로 온다.

        get_config_with_fallback이 예외 타입을 가리지 않게 되면서, 로봇별
        config가 따로 없는(--robot 한 대) 흔한 경우 config_path=None인 채로
        여기 닿는다. None을 그대로 실으면 build_config_error_state가
        configPath 참조 자체를 빼 버려 운영자에게 단서가 안 남는다.
        """
        path = config_error_path(
            KeyError("channel"),
            config_path=None,
            extensions_path=None,
            recipes_path=None,
        )
        self.assertEqual(path, str(DEFAULT_CONFIG_PATH))


if __name__ == "__main__":
    unittest.main()
