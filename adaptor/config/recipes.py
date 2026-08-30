"""recipes.hcl parser with strict startup validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from config.errors import ConfigError
from config.hcl import PARAM_PATTERN, HclError, blocks, load_hcl

DEFAULT_RECIPES_PATH = Path(__file__).resolve().parent / "recipes.hcl"


class RecipesError(ConfigError):
    """recipes.hcl is malformed."""


def _number(value: Any, field: str, owner: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise RecipesError(f"recipe '{owner}': {field} must be a non-negative number")
    return float(value)


def _steps(body: Dict[str, Any], kind: str, owner: str) -> List[Dict[str, Any]]:
    result = []
    for extension, raw in blocks(body, kind):
        if not extension.strip():
            raise RecipesError(f"recipe '{owner}': empty {kind} extension")
        unknown = set(raw) - {
            "parameters",
            "timeout_sec",
            "retry",
            "retry_delay_sec",
            "delay_sec",
        }
        if unknown:
            raise RecipesError(
                f"recipe '{owner}' {kind} '{extension}': unknown field(s): "
                f"{', '.join(sorted(unknown))}"
            )
        parameters = raw.get("parameters", {})
        if not isinstance(parameters, dict):
            raise RecipesError(
                f"recipe '{owner}' {kind} '{extension}': parameters must be an object"
            )
        retry = raw.get("retry", 0)
        if isinstance(retry, bool) or not isinstance(retry, int) or retry < 0:
            raise RecipesError(
                f"recipe '{owner}' {kind} '{extension}': retry must be a non-negative integer"
            )
        result.append(
            {
                "extension": extension,
                "parameters": dict(parameters),
                "timeout_sec": _number(
                    raw.get("timeout_sec", 0), f"{kind}.timeout_sec", owner
                ),
                "retry": retry,
                "retry_delay_sec": _number(
                    raw.get("retry_delay_sec", 0), f"{kind}.retry_delay_sec", owner
                ),
                "delay_sec": _number(
                    raw.get("delay_sec", 0), f"{kind}.delay_sec", owner
                ),
            }
        )
    return result


def load_recipes(path: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """Return normalized recipe dictionaries; a missing default file means none."""
    file_path = Path(path) if path is not None else DEFAULT_RECIPES_PATH
    if path is None and not file_path.exists():
        return []
    try:
        data = load_hcl(file_path)
        entries = blocks(data, "recipe")
    except HclError as exc:
        raise RecipesError(str(exc)) from exc

    result = []
    seen = set()
    for name, body in entries:
        if not name.strip():
            raise RecipesError(f"empty recipe name in {file_path}")
        if name in seen:
            raise RecipesError(f"duplicate recipe '{name}' in {file_path}")
        seen.add(name)
        unknown = set(body) - {
            "step",
            "cleanup",
            "enabled",
            "timeout_sec",
            "cleanup_timeout_sec",
            "motion",
            "label",
        }
        if unknown:
            raise RecipesError(
                f"recipe '{name}': unknown field(s): {', '.join(sorted(unknown))}"
            )
        enabled = body.get("enabled", True)
        motion = body.get("motion", False)
        if not isinstance(enabled, bool) or not isinstance(motion, bool):
            raise RecipesError(
                f"recipe '{name}': enabled and motion must be true or false"
            )
        steps = _steps(body, "step", name)
        if not steps:
            raise RecipesError(f"recipe '{name}' must contain at least one step")
        label = body.get("label", name)
        if not isinstance(label, str):
            raise RecipesError(f"recipe '{name}': label must be a string")
        cleanup_timeout = _number(
            body.get("cleanup_timeout_sec", 5),
            "cleanup_timeout_sec",
            name,
        )
        if cleanup_timeout <= 0:
            raise RecipesError(
                f"recipe '{name}': cleanup_timeout_sec must be greater than zero"
            )
        result.append(
            {
                "action_type": name,
                "steps": steps,
                "cleanup": _steps(body, "cleanup", name),
                "enabled": enabled,
                "timeout_sec": _number(body.get("timeout_sec", 0), "timeout_sec", name),
                "cleanup_timeout_sec": cleanup_timeout,
                "motion": motion,
                "label": label,
            }
        )
    return result


def _walk_variables(value: Any, found: List[str]) -> None:
    """값 안의 ``${var.NAME}`` 참조를 선언 순서대로 모은다."""
    if isinstance(value, str):
        for name in PARAM_PATTERN.findall(value):
            if name not in found:
                found.append(name)
        return
    if isinstance(value, dict):
        for item in value.values():
            _walk_variables(item, found)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _walk_variables(item, found)


def recipe_variables(recipe: Any) -> tuple:
    """recipe가 호출자에게서 받아야 하는 파라미터 이름을 돌려준다.

    recipe에는 파라미터 선언부가 없고 step 안에서 ``${var.X}``로 참조만 한다.
    WebUI 실행 폼이 입력칸을 만들려면 정의를 되짚어야 하므로, 모든 step과
    cleanup의 parameters를 재귀적으로 훑는다. 실행기(extensions/recipes)가
    문자열 전체 치환과 중간 삽입을 모두 처리하므로 여기서도 둘 다 잡는다.
    """
    found: List[str] = []
    for step in (*recipe.steps, *recipe.cleanup):
        _walk_variables(step.parameters, found)
    return tuple(found)

