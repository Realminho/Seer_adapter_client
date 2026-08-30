from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


SEER_CLIENT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SEER_CLIENT_ROOT.parent
for source in (SEER_CLIENT_ROOT / "src", REPO_ROOT / "adaptor"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from seer_client.hcl_config import ensure_hcl_files  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.block_program import decode_program  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.map_view import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    recipe_route_picker_payload,
    render_fleet_map_card,
    write_map_cache,
)
from seer_client.recipe_builder import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    BUILDER_EXAMPLES,
    RecipeBuilderError,
    build_recipe_hcl,
    delete_recipe,
    managed_recipe_definition,
    managed_recipe_names,
    render_builder_page,
    save_recipe,
)
from config.recipes import load_recipes  # pyright: ignore[reportMissingImports]  # noqa: E402


def _program(name: str = "seerBuiltAction") -> str:
    return json.dumps(
        {
            "name": name,
            "label": "SEER Built Action",
            "blocks": [
                {
                    "kind": "translate",
                    "delay_sec": "0.25",
                    "values": {
                        "distance_m": "0.2",
                        "linear_speed_mps": "0.05",
                        "lateral": "forward",
                    },
                    "variables": {"distance_m": "travel_distance"},
                },
                {
                    "kind": "rotate",
                    "values": {"angle_deg": "90", "angular_speed_deg_s": "5"},
                    "variables": {"angle_deg": "turn_angle"},
                },
                {
                    "kind": "path_nav",
                    "delay_sec": "0.1",
                    "values": {
                        "id": "LM4",
                        "source_id": "LM1",
                        "route_points": "LM1, LM5, LM4",
                        "task_id": "",
                    },
                    "variables": {"id": "target_id"},
                },
                {
                    "kind": "set_do",
                    "values": {"id": "0", "status": "off"},
                    "variables": {},
                },
                {
                    "kind": "wait",
                    "values": {"seconds": ""},
                    "variables": {"seconds": "wait_seconds"},
                },
            ],
        }
    )


class RecipeBuilderTests(unittest.TestCase):
    def test_block_program_generates_safe_hcl_variables_and_defaults(self) -> None:
        block, recipe = build_recipe_hcl(_program())
        self.assertEqual(recipe["variables"], ("target_id", "travel_distance", "turn_angle", "wait_seconds"))
        self.assertIn('step "seerTranslate"', block)
        self.assertIn('distance_m = "${var.travel_distance}"', block)
        self.assertIn("default_distance_m = 0.2", block)
        self.assertIn("delay_sec = 0.25", block)
        self.assertIn('step "seerTurn"', block)
        self.assertIn('route_points = ["LM1","LM5","LM4"]', block)
        self.assertIn("delay_sec = 0.1", block)
        self.assertIn("default_seconds = 1", block)
        self.assertNotIn("subprocess", block)
        self.assertNotIn("command =", block)

    def test_save_preserves_hand_written_hcl_and_replaces_managed_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = ensure_hcl_files(Path(temp_dir))
            before = paths.recipes.read_text(encoding="utf-8")
            calls = []

            def validate(candidate: Path):
                calls.append(candidate.read_text(encoding="utf-8"))
                return True, "ok"

            save_recipe(paths.recipes, _program(), validate=validate)
            first = paths.recipes.read_text(encoding="utf-8")
            save_recipe(paths.recipes, _program(), validate=validate)
            second = paths.recipes.read_text(encoding="utf-8")
            self.assertTrue(first.startswith(before.rstrip()))
            self.assertEqual(first, second)
            self.assertEqual(managed_recipe_names(second), ("seerBuiltAction",))
            self.assertEqual(len(calls), 2)

    def test_saved_recipe_can_be_loaded_edited_and_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = ensure_hcl_files(Path(temp_dir))
            original = paths.recipes.read_text(encoding="utf-8")
            validations = []

            def validate(candidate: Path):
                validations.append(candidate.read_text(encoding="utf-8"))
                return True, "ok"

            save_recipe(paths.recipes, _program(), validate=validate)
            saved = paths.recipes.read_text(encoding="utf-8")
            definition = managed_recipe_definition(saved, "seerBuiltAction")
            self.assertEqual(definition["label"], "SEER Built Action")
            self.assertEqual(definition["blocks"][0]["kind"], "translate")
            self.assertEqual(
                definition["blocks"][0]["variables"]["distance_m"],
                "travel_distance",
            )
            self.assertEqual(definition["blocks"][2]["values"]["route_points"], "LM1, LM5, LM4")
            self.assertEqual(definition["blocks"][0]["delay_sec"], 0.25)
            self.assertEqual(definition["blocks"][2]["delay_sec"], 0.1)

            definition["label"] = "Edited Action"
            definition["blocks"][0]["values"]["linear_speed_mps"] = 0.08
            save_recipe(paths.recipes, json.dumps(definition), validate=validate)
            edited = managed_recipe_definition(
                paths.recipes.read_text(encoding="utf-8"), "seerBuiltAction"
            )
            self.assertEqual(edited["label"], "Edited Action")
            self.assertEqual(edited["blocks"][0]["values"]["linear_speed_mps"], 0.08)
            self.assertEqual(edited["blocks"][0]["delay_sec"], 0.25)

            self.assertEqual(
                delete_recipe(paths.recipes, "seerBuiltAction", validate=validate),
                "seerBuiltAction",
            )
            remaining = paths.recipes.read_text(encoding="utf-8")
            self.assertEqual(managed_recipe_names(remaining), ())
            self.assertEqual(remaining.rstrip(), original.rstrip())
            self.assertEqual(len(validations), 3)

    def test_saved_recipe_can_be_renamed_without_leaving_old_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = ensure_hcl_files(Path(temp_dir))
            original = paths.recipes.read_text(encoding="utf-8")
            validated = []

            def validate(candidate: Path):
                validated.append(candidate.read_text(encoding="utf-8"))
                return True, "ok"

            save_recipe(paths.recipes, _program(), validate=validate)
            definition = managed_recipe_definition(
                paths.recipes.read_text(encoding="utf-8"), "seerBuiltAction"
            )
            definition["name"] = "renamedAmrAction"
            definition["label"] = "Renamed AMR Action"
            renamed = save_recipe(
                paths.recipes,
                json.dumps(definition),
                original_name="seerBuiltAction",
                validate=validate,
            )

            content = paths.recipes.read_text(encoding="utf-8")
            self.assertEqual(renamed["name"], "renamedAmrAction")
            self.assertEqual(managed_recipe_names(content), ("renamedAmrAction",))
            self.assertNotIn('recipe "seerBuiltAction"', content)
            self.assertEqual(
                managed_recipe_definition(content, "renamedAmrAction")["label"],
                "Renamed AMR Action",
            )
            self.assertTrue(content.startswith(original.rstrip()))
            self.assertEqual(len(validated), 2)

    def test_rename_rejects_existing_recipe_name_without_changing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = ensure_hcl_files(Path(temp_dir))
            save_recipe(paths.recipes, _program("firstAction"))
            save_recipe(paths.recipes, _program("secondAction"))
            before = paths.recipes.read_text(encoding="utf-8")
            definition = managed_recipe_definition(before, "firstAction")
            definition["name"] = "secondAction"

            with self.assertRaisesRegex(RecipeBuilderError, "이미 있습니다"):
                save_recipe(
                    paths.recipes,
                    json.dumps(definition),
                    original_name="firstAction",
                )
            self.assertEqual(paths.recipes.read_text(encoding="utf-8"), before)

    def test_legacy_managed_recipe_without_metadata_remains_editable(self) -> None:
        block, _recipe = build_recipe_hcl(_program())
        legacy = "\n".join(
            line for line in block.splitlines() if not line.startswith("# SEER_BLOCK_DEFINITION ")
        )
        definition = managed_recipe_definition(legacy, "seerBuiltAction")
        self.assertEqual(definition["blocks"][1]["kind"], "rotate")
        self.assertEqual(definition["blocks"][1]["variables"]["angle_deg"], "turn_angle")

    def test_legacy_escaped_route_string_remains_editable(self) -> None:
        block, _recipe = build_recipe_hcl(_program())
        block = block.replace(
            'route_points = ["LM1","LM5","LM4"]',
            'route_points = "[\\\"LM1\\\",\\\"LM5\\\",\\\"LM4\\\"]"',
        )
        block = "\n".join(
            line for line in block.splitlines() if not line.startswith("# SEER_BLOCK_DEFINITION ")
        )
        definition = managed_recipe_definition(block, "seerBuiltAction")
        self.assertEqual(
            definition["blocks"][2]["values"]["route_points"],
            "LM1, LM5, LM4",
        )

    def test_builder_page_exposes_edit_delete_and_high_contrast_management_title(self) -> None:
        class Render:
            @staticmethod
            def _flash(_q):
                return ""

            @staticmethod
            def page(_title, body, **_kwargs):
                return body

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "recipes.hcl"
            path.write_text("", encoding="utf-8")
            save_recipe(path, _program())
            page = render_builder_page(
                Render,
                "csrf",
                {"edit": "seerBuiltAction"},
                path,
            )
            self.assertIn("seerBuiltAction 편집 중", page)
            self.assertIn("Recipe 변경 저장 및 적용", page)
            self.assertIn('name="recipe_name" required', page)
            self.assertNotIn('name="recipe_name" required readonly', page)
            self.assertIn('name="original_recipe_name" value="seerBuiltAction"', page)
            self.assertIn("Recipe 이름과 화면 표시 이름을 모두 변경", page)
            self.assertIn("?delete=seerBuiltAction", page)
            self.assertIn(".seer-builder-panel h2,.seer-managed-item strong{color:#f3f8fc}", page)

    def test_builder_path_block_includes_small_map_route_picker(self) -> None:
        class Render:
            @staticmethod
            def _flash(_q):
                return ""

            @staticmethod
            def page(_title, body, **_kwargs):
                return body

        raw_map = {
            "header": {"mapName": "route-map", "minPos": {"x": 0, "y": 0}, "maxPos": {"x": 3, "y": 2}},
            "normalPosList": [],
            "advancedPointList": [
                {"instanceName": "LM1", "pos": {"x": 0.0, "y": 0.0}},
                {"instanceName": "LM2", "pos": {"x": 1.0, "y": 1.0}},
                {"instanceName": "LM3", "pos": {"x": 2.0, "y": 0.0}},
            ],
            "advancedCurveList": [
                {
                    "className": "BezierPath",
                    "startPos": {"instanceName": "LM1", "pos": {"x": 0.0, "y": 0.0}},
                    "endPos": {"instanceName": "LM2", "pos": {"x": 1.0, "y": 1.0}},
                    "controlPos1": {"x": 0.25, "y": 0.25},
                    "controlPos2": {"x": 0.75, "y": 0.75},
                },
                {
                    "className": "BezierPath",
                    "startPos": {"instanceName": "LM2", "pos": {"x": 1.0, "y": 1.0}},
                    "endPos": {"instanceName": "LM3", "pos": {"x": 2.0, "y": 0.0}},
                    "controlPos1": {"x": 1.25, "y": 0.75},
                    "controlPos2": {"x": 1.75, "y": 0.25},
                },
            ],
            "advancedLineList": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache = root / "seer-map.json"
            recipes = root / "recipes.hcl"
            recipes.write_text("", encoding="utf-8")
            write_map_cache(cache, raw_map)
            snapshot = SimpleNamespace(x=0.0, y=0.0, last_node_id="LM1")
            payload = recipe_route_picker_payload(
                cache, snapshot, position_unit="m"
            )
            self.assertEqual(payload["current_point"], "LM1")
            self.assertEqual(len(payload["points"]), 3)
            self.assertEqual(len(payload["edges"]), 2)
            page = render_builder_page(
                Render,
                "csrf",
                {},
                recipes,
                map_cache_path=cache,
                map_snapshot=snapshot,
                map_position_unit="m",
                map_robot_key="seer:a",
                map_robot_options=(("seer:a", "AMR-A"), ("seer:b", "AMR-B")),
            )
            self.assertIn("지도에서 지정 경로 선택", page)
            self.assertIn('id="seer-route-picker-map"', page)
            self.assertIn('aria-label="지도 축소"', page)
            self.assertIn('aria-label="지도 확대"', page)
            self.assertIn("data-route-map-fit", page)
            self.assertIn('id="seer-route-point-size"', page)
            self.assertIn('min="35" max="200"', page)
            self.assertIn('id="seer-route-point-label-size"', page)
            self.assertIn("data-seer-route-point-marker", page)
            self.assertIn('data-seer-route-point-base-radius="16"', page)
            self.assertIn("data-seer-route-point-label", page)
            self.assertIn('data-seer-route-label-base="11"', page)
            self.assertIn('dominant-baseline="central"', page)
            self.assertIn("data-seer-route-fixed-marker", page)
            self.assertIn("var inverseScale=1/routeView.scale", page)
            self.assertIn("scale('+inverseScale.toFixed(5)+')", page)
            self.assertIn("seer-route-map-point:", page)
            self.assertIn("seer-route-map-label:", page)
            self.assertIn('id="seer-route-base-paths"', page)
            self.assertIn('id="seer-route-highlight-paths"', page)
            self.assertIn('id="seer-route-layer-order"', page)
            self.assertIn('value="above">노란선 위', page)
            self.assertIn('value="below">노란선 아래', page)
            self.assertIn("seer-route-layer-order:", page)
            self.assertIn("insertBefore(highlight,base)", page)
            self.assertIn("createElementNS('http://www.w3.org/2000/svg','path')", page)
            self.assertIn("pathData.search(/\\s[QC]\\s/)", page)
            self.assertIn("stroke-linecap:round", page)
            self.assertIn("stroke-linejoin:round", page)
            self.assertIn("LM 원", page)
            self.assertIn("LM 글자", page)
            self.assertIn("휠 확대·축소 · 드래그 이동", page)
            self.assertIn("setupRouteMapNavigation", page)
            self.assertIn("Math.max(.5,Math.min(6", page)
            self.assertIn("이 블록 완료 후 추가 대기 (초)", page)
            self.assertIn("목적지 도착 확인 즉시 다음 블록 실행", page)
            self.assertIn("주행시간과 속도에는 영향을 주지 않습니다", page)
            self.assertIn("data-step-delay", page)
            self.assertIn('"current_point": "LM1"', page)
            self.assertIn("Recipe / 실행 AMR", page)
            self.assertIn('data-seer-amr-label-key="seer:a"', page)
            self.assertIn("현재 Recipe / 실행 대상 AMR", page)
            self.assertIn("최대 5개 후보", page)

    def test_invalid_or_shadowing_recipe_is_rejected(self) -> None:
        broken = json.loads(_program("bad"))
        broken["blocks"][0]["variables"]["distance_m"] = "1bad"
        with self.assertRaises(RecipeBuilderError):
            build_recipe_hcl(json.dumps(broken))
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "recipes.hcl"
            path.write_text('recipe "seerBuiltAction" {\n  step "seerWait" { parameters = { seconds = 1 } }\n}\n', encoding="utf-8")
            with self.assertRaises(RecipeBuilderError):
                save_recipe(path, _program())

    def test_step_delay_defaults_to_zero_and_rejects_invalid_values(self) -> None:
        default_program = json.loads(_program("defaultDelay"))
        for block in default_program["blocks"]:
            block.pop("delay_sec", None)
        hcl, recipe = build_recipe_hcl(json.dumps(default_program))
        self.assertTrue(all(delay == 0.0 for _spec, _params, delay in recipe["blocks"]))
        self.assertEqual(
            len(re.findall(r"^\s+delay_sec\s*=\s*0\s*$", hcl, re.MULTILINE)),
            len(default_program["blocks"]),
        )

    def test_entry_style_nested_repeat_and_condition_compile_to_safe_program(self) -> None:
        definition = {
            "name": "nestedProgram",
            "label": "Nested Program",
            "blocks": [
                {
                    "kind": "repeat",
                    "values": {"count": "2"},
                    "variables": {"count": "repeat_count"},
                    "children": [
                        {
                            "kind": "set_do",
                            "values": {"id": "1", "status": "on"},
                            "variables": {},
                        }
                    ],
                },
                {
                    "kind": "if_else",
                    "values": {
                        "source": "battery",
                        "channel": "0",
                        "operator": ">=",
                        "expected": "50",
                    },
                    "variables": {"expected": "minimum_battery"},
                    "children": [
                        {"kind": "wait", "values": {"seconds": "0"}, "variables": {}}
                    ],
                    "else_children": [
                        {
                            "kind": "set_do",
                            "values": {"id": "1", "status": "off"},
                            "variables": {},
                        }
                    ],
                },
            ],
        }
        hcl, recipe = build_recipe_hcl(json.dumps(definition))
        self.assertTrue(recipe["structured"])
        self.assertEqual(recipe["variables"], ("minimum_battery", "repeat_count"))
        self.assertEqual(hcl.count('step "seerBlockProgram"'), 1)
        self.assertIn("declared_variables", hcl)
        loaded = managed_recipe_definition(hcl, "nestedProgram")
        self.assertEqual(loaded["blocks"][0]["kind"], "repeat")
        self.assertEqual(loaded["blocks"][0]["children"][0]["kind"], "set_do")
        self.assertEqual(loaded["blocks"][1]["else_children"][0]["kind"], "set_do")

    def test_builder_page_exposes_entry_categories_drag_nested_zones_and_zoom(self) -> None:
        class Render:
            @staticmethod
            def _flash(_q):
                return ""

            @staticmethod
            def page(_title, body, **_kwargs):
                return body

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "recipes.hcl"
            path.write_text("", encoding="utf-8")
            page = render_builder_page(Render, "csrf", {}, path)
        self.assertIn("블록 카테고리", page)
        self.assertIn("블록 꾸러미", page)
        self.assertIn("블록 조립소", page)
        self.assertIn('data-entry-add-block="repeat"', page)
        self.assertIn('data-entry-add-block="if_else"', page)
        self.assertIn('data-child-zone=', page)
        self.assertIn('data-entry-zoom="10"', page)
        self.assertIn("draggable=\"true\"", page)
        self.assertIn(".seer-route-tool .btn{border-color:#d9a72e;color:#3b2a00!important", page)
        self.assertIn(".seer-builder-map-selector .is-current{border-color:#31b9d2;background:#d8f3f8!important;color:#07354a!important", page)
        self.assertIn(".seer-block-dropzone>.seer-program-block::before{display:none}", page)
        self.assertIn(".seer-control-block>.seer-block-dropzone::before{content:""", page)

    def test_builder_page_exposes_entry_style_inline_variable_math_and_conditions(self) -> None:
        class Render:
            @staticmethod
            def _flash(_q):
                return ""

            @staticmethod
            def page(_title, body, **_kwargs):
                return body

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "recipes.hcl"
            path.write_text("", encoding="utf-8")
            page = render_builder_page(Render, "csrf", {}, path)

        self.assertIn("result가 4가 될 때까지 반복", page)
        self.assertIn("seer-entry-sentence", page)
        self.assertIn("seer-entry-boolean", page)
        self.assertIn("inlineSentence(kind,schema)", page)
        self.assertIn("conditionSentence(schema,'이 될 때까지 반복하기')", page)
        self.assertIn("Actions 실행값", page)
        self.assertIn("변수·계산·판단·반복 조건은 엔트리처럼 블록 표면에서 바로 입력", page)
        self.assertIn("'==':'='", page)
        self.assertIn("multiply:'×'", page)
        self.assertIn("padding:3px 10px 3px 14px", page)
        self.assertIn("border-radius:14px 16px 12px 12px", page)
        self.assertIn("linear-gradient(180deg,rgba(255,255,255,.14),rgba(255,255,255,.04))", page)
        self.assertIn('.seer-program-block[data-kind="repeat"] .seer-entry-sentence', page)
        self.assertIn('id="seer-runtime-monitor"', page)
        self.assertIn('id="seer-runtime-follow"', page)
        self.assertIn('id="seer-runtime-vars"', page)
        self.assertIn('id="seer-runtime-condition"', page)
        self.assertIn('data-runtime-chip', page)
        self.assertIn("assignRuntimeTraceIds()", page)
        self.assertIn("/recipe-builder/runtime?", page)
        self.assertIn("is-runtime-current", page)
        self.assertIn("실행 따라가기", page)
        self.assertIn('data-builder-control="run"', page)
        self.assertIn('data-builder-control="pause"', page)
        self.assertIn('data-builder-control="resume"', page)
        self.assertIn("runtimePausedRequested=status==='running'&&!!(state&&state.paused)", page)
        self.assertIn("runtimePausedRequested?'일시정지'", page)
        self.assertIn('data-builder-control="cancel"', page)
        self.assertIn("prepareBuilderNativeSubmit", page)
        self.assertIn("data-seer-builder-save-form", page)
        self.assertNotIn("saveBuilderWithoutReload", page)
        self.assertNotIn("body.set('ajax','1')", page)
        self.assertIn("is-runtime-locked", page)
        self.assertIn("outline-width:4px", page)
        self.assertIn("field-sizing:content", page)
        self.assertIn(".seer-entry-boolean{flex:0 0 auto;width:max-content;min-width:max-content", page)
        self.assertIn("padding-right:18px", page)
        self.assertIn("column-gap:4px;row-gap:4px", page)
        self.assertIn("grid-template-columns:max-content max-content", page)
        self.assertIn("function naturalFlexChildrenWidth(node)", page)
        self.assertIn("function naturalBlockWidth(block)", page)
        self.assertIn("titleWidth=naturalFlexChildrenWidth(title)", page)
        self.assertNotIn("title.scrollWidth+actionWidth", page)
        self.assertIn(".seer-block-title{flex:0 1 auto}", page)
        self.assertIn("function syncAdaptiveBlockWidths()", page)
        self.assertIn("block.style.width=Math.ceil(width)+'px'", page)
        self.assertIn("--seer-lane-width", page)
        self.assertIn("padding-right:62px", page)
        self.assertIn("width:56px;min-width:56px;max-width:56px", page)
        self.assertIn("sizeEntryControl(control)", page)
        self.assertIn("sizeConditionCapsule(capsule)", page)
        self.assertIn("capsule.style.width='max-content'", page)
        self.assertIn("--seer-entry-control-width", page)
        self.assertIn("max-width:none!important", page)
        self.assertIn("entryTextWidth(control,text)", page)
        self.assertIn("control.tagName==='SELECT'", page)
        self.assertIn("min-width:0!important", page)
        self.assertNotIn("min-width:72px;max-width:150px", page)
        self.assertIn("조건식 전체를 한 줄로 표시", page)
        self.assertIn("실제 픽셀 폭에 맞춰 각각 자동으로", page)
        self.assertIn("자동완성 목록이 붙은 입력칸과 드롭다운 화살표가 차지하는 폭까지 포함해 계산", page)
        self.assertIn("바깥 블록 자체도 함께 넓어지고", page)
        self.assertIn("실행 흐름/함수 영역도 그 블록 폭에 맞춰 확장", page)
        self.assertIn("변수명을 지우면 입력칸·육각형·블록·작업 영역이 즉시 함께 줄어듭니다", page)
        self.assertIn("삭제할수록 오히려 넓어지는 현상이 생기지 않습니다", page)
        self.assertIn("실행 중에는 블록 편집과 저장이 잠깁니다", page)
        self.assertIn("새 회차가 시작될 때", page)

    def test_counter_until_four_example_compiles_as_variable_condition_loop(self) -> None:
        example = next(item for item in BUILDER_EXAMPLES if item["key"] == "counter_until_four")
        hcl, recipe = build_recipe_hcl(json.dumps(example["definition"]))
        program = tuple(recipe["program"])

        self.assertIn('step "seerBlockProgram"', hcl)
        self.assertEqual(program[0]["kind"], "set_variable")
        self.assertEqual(program[0]["parameters"], {"name": "result", "value": "0"})
        self.assertEqual(program[1]["kind"], "repeat_until")
        self.assertEqual(program[1]["condition"]["source"], "variable")
        self.assertEqual(program[1]["condition"]["variable_name"], "result")
        self.assertEqual(program[1]["condition"]["operator"], "==")
        self.assertEqual(program[1]["condition"]["expected"], "4")
        self.assertEqual(program[1]["children"][0]["kind"], "change_variable")
        self.assertEqual(program[1]["children"][0]["parameters"]["name"], "result")
        self.assertEqual(program[1]["children"][0]["parameters"]["amount"], 1.0)

    def test_builder_page_includes_manual_and_editable_compiling_examples(self) -> None:
        class Render:
            @staticmethod
            def _flash(_q):
                return ""

            @staticmethod
            def page(_title, body, **_kwargs):
                return body

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "recipes.hcl"
            path.write_text("", encoding="utf-8")
            page = render_builder_page(Render, "csrf", {}, path)

        self.assertIn("Block Builder 사용 매뉴얼", page)
        self.assertIn('data-open-builder-manual', page)
        self.assertIn('id="seer-builder-manual-modal"', page)
        self.assertIn('role="dialog"', page)
        self.assertIn('aria-hidden="true"', page)
        self.assertIn('class="seer-manual-map"', page)
        self.assertIn("setBuilderManualOpen", page)
        self.assertIn("event.target===manualModal", page)
        self.assertIn("event.key==='Escape'", page)
        self.assertIn(".seer-entry-pack{padding:9px;background:#10202c", page)
        self.assertNotIn("background:#eef3f6", page)
        for chapter in range(1, 9):
            self.assertIn(f'id="seer-manual-{chapter}"', page)
        self.assertNotIn('<details class="seer-learning-panel"><summary>📘', page)
        self.assertIn("예제 블록코딩", page)
        self.assertIn("예제나 매뉴얼 버튼은 자동 저장·실행하지 않습니다", page)
        self.assertEqual(page.count('data-load-builder-example="'), len(BUILDER_EXAMPLES))
        self.assertIn('id="seer-builder-examples"', page)
        self.assertIn("loadBuilderExample", page)
        self.assertIn("현재 조립 중인 블록을 지우고", page)
        self.assertIn("original.remove()", page)
        self.assertIn("Recipe 저장 및 적용", page)

        for example in BUILDER_EXAMPLES:
            hcl, recipe = build_recipe_hcl(json.dumps(example["definition"]))
            self.assertIn(f'recipe "{recipe["name"]}"', hcl)

    def test_builder_separates_free_flow_and_function_lanes_with_dynamic_calls(self) -> None:
        class Render:
            @staticmethod
            def _flash(_q):
                return ""

            @staticmethod
            def page(_title, body, **_kwargs):
                return body

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "recipes.hcl"
            path.write_text("", encoding="utf-8")
            page = render_builder_page(Render, "csrf", {}, path)

        self.assertIn('id="seer-flow-lane"', page)
        self.assertIn('id="seer-function-lane"', page)
        self.assertIn('data-entry-view="both"', page)
        self.assertIn('data-entry-view="flow"', page)
        self.assertIn('data-entry-view="functions"', page)
        self.assertIn('id="seer-custom-function-palette"', page)
        self.assertIn('data-entry-function="', page)
        self.assertIn("placeTopBlock", page)
        self.assertIn("node.layout={x:Number(block.dataset.layoutX", page)
        self.assertIn(
            "serializeZone(functionLane).concat(serializeZone(flowLane))",
            page,
        )
        self.assertIn("snapFlowBlock", page)
        self.assertIn("SNAP_DISTANCE=42", page)
        self.assertIn("node.layout.connected=true", page)
        self.assertIn("var flowBlocks=directBlocks(flowLane),options=", page)
        self.assertIn("wanted=Number(sequence.value)", page)
        self.assertIn("seer-drag-visual", page)
        self.assertIn("seer-drag-group-visual", page)
        self.assertIn("prepareDragClone", page)
        self.assertIn("showDragVisual(dragBlock,event,scale,dragGroup)", page)
        self.assertIn("members.forEach(function(member){member.classList.add('is-drag-source');})", page)
        self.assertIn("setDragImage", page)
        self.assertIn("(v('source_id')||'SELF_POSITION')+' → '+v('id')", page)

    def test_free_layout_coordinates_survive_save_edit_and_are_validated(self) -> None:
        definition = {
            "name": "freeLayoutAction",
            "label": "Free Layout Action",
            "blocks": [
                {
                    "kind": "define_function",
                    "layout": {"x": 34.5, "y": 72},
                    "values": {"function_name": "Demo"},
                    "variables": {},
                    "children": [
                        {"kind": "wait", "values": {"seconds": "0"}, "variables": {}}
                    ],
                },
                {
                    "kind": "wait",
                    "layout": {"x": 140, "y": 180},
                    "values": {"seconds": "0"},
                    "variables": {},
                },
                {
                    "kind": "repeat",
                    "layout": {"x": 140, "y": 216.25, "connected": True},
                    "values": {"count": "2"},
                    "variables": {},
                    "children": [
                        {
                            "kind": "call_function",
                            "values": {"function_name": "Demo"},
                            "variables": {},
                        }
                    ],
                },
            ],
        }
        hcl, recipe = build_recipe_hcl(json.dumps(definition))
        self.assertTrue(recipe["structured"])
        loaded = managed_recipe_definition(hcl, "freeLayoutAction")
        self.assertEqual(loaded["blocks"][0]["layout"], {"x": 34.5, "y": 72.0})
        self.assertEqual(loaded["blocks"][1]["layout"], {"x": 140.0, "y": 180.0})
        self.assertEqual(
            loaded["blocks"][2]["layout"],
            {"x": 140.0, "y": 216.25, "connected": True},
        )
        self.assertEqual(
            loaded["blocks"][2]["children"][0]["values"]["function_name"],
            "Demo",
        )

        invalid = json.loads(json.dumps(definition))
        invalid["blocks"][2]["layout"]["x"] = -1
        with self.assertRaisesRegex(RecipeBuilderError, "배치 좌표"):
            build_recipe_hcl(json.dumps(invalid))

        invalid = json.loads(json.dumps(definition))
        invalid["blocks"][2]["layout"]["connected"] = "yes"
        with self.assertRaisesRegex(RecipeBuilderError, "연결 정보"):
            build_recipe_hcl(json.dumps(invalid))

    def test_every_connected_top_level_block_survives_recipe_edit_round_trip(self) -> None:
        definition = {
            "name": "connectedChainAction",
            "label": "Connected Chain Action",
            "blocks": [
                {
                    "kind": "wait",
                    "layout": {"x": 80, "y": 60},
                    "values": {"seconds": "0.1"},
                    "variables": {},
                },
                *[
                    {
                        "kind": "wait",
                        "layout": {
                            "x": 80,
                            "y": 60 + index * 42,
                            "connected": True,
                        },
                        "values": {"seconds": str(index / 10)},
                        "variables": {},
                    }
                    for index in range(1, 6)
                ],
            ],
        }
        hcl, _recipe = build_recipe_hcl(json.dumps(definition))
        loaded = managed_recipe_definition(hcl, "connectedChainAction")

        self.assertNotIn("connected", loaded["blocks"][0]["layout"])
        self.assertTrue(all(block["layout"]["connected"] for block in loaded["blocks"][1:]))

        class Render:
            @staticmethod
            def _flash(_q):
                return ""

            @staticmethod
            def page(_title, body, **_kwargs):
                return body

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "recipes.hcl"
            path.write_text(hcl, encoding="utf-8")
            page = render_builder_page(Render, "csrf", {"edit": "connectedChainAction"}, path)

        self.assertIn("if(block.dataset.connected!=='true')return", page)
        self.assertIn("setTopCoordinates(block,expectedX,expectedY)", page)
        self.assertIn("refreshSnapMarkers();ensureLaneHeight(flowLane);ensureLaneHeight(functionLane);", page)
        self.assertNotIn("sameY)block.classList.add('is-snapped-after');else block.dataset.connected='false'", page)

        invalid = json.loads(_program("badDelay"))
        invalid["blocks"][2]["delay_sec"] = "-0.1"
        with self.assertRaises(RecipeBuilderError):
            build_recipe_hcl(json.dumps(invalid))

    def test_block_summary_expands_horizontally_without_ellipsis(self) -> None:
        class Render:
            @staticmethod
            def _flash(_q):
                return ""

            @staticmethod
            def page(_title, body, **_kwargs):
                return body

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "recipes.hcl"
            path.write_text("", encoding="utf-8")
            page = render_builder_page(Render, "csrf", {}, path)

        self.assertIn(".seer-top-lane>.seer-program-block{position:absolute!important;width:max-content;min-width:360px;max-width:none", page)
        self.assertIn(".seer-block-dropzone>.seer-program-block{position:relative!important;left:auto!important;top:auto!important;width:max-content!important", page)
        self.assertIn(".seer-block-summary{min-width:max-content;overflow:visible;text-overflow:clip;white-space:nowrap", page)
        self.assertIn(".seer-program-block .seer-block-summary{display:inline-block;flex:0 0 auto;min-width:max-content;max-width:none;margin-left:2px;overflow:visible;text-overflow:clip;white-space:nowrap", page)
        self.assertNotIn("max-width:220px;overflow:hidden;text-overflow:ellipsis", page)

    def test_compact_responsive_builder_exposes_all_practical_amr_blocks(self) -> None:
        class Render:
            @staticmethod
            def _flash(_q):
                return ""

            @staticmethod
            def page(_title, body, **_kwargs):
                return body

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "recipes.hcl"
            path.write_text("", encoding="utf-8")
            page = render_builder_page(Render, "csrf", {}, path)

        self.assertIn(".seer-program-block:not(.is-expanded)>.seer-block-fields{display:none}", page)
        self.assertIn("grid-template-columns:minmax(0,1fr);grid-template-rows", page)
        self.assertNotIn("minmax(480px,1fr)", page)
        self.assertIn("data-toggle-config", page)
        self.assertIn("data-block-summary", page)
        for kind in (
            "coordinate",
            "translate",
            "rotate",
            "path_nav",
            "set_do",
            "pulse_do",
            "wait_di",
            "on_di",
            "switch_map",
            "relocate",
            "set_motor",
            "wait",
            "repeat",
            "forever",
            "repeat_until",
            "if",
            "if_else",
            "wait_until",
            "break_loop",
            "continue_loop",
            "stop_program",
            "restart_program",
            "send_do_wait_di",
            "judge",
            "logic",
            "calculate",
            "read_state",
            "set_variable",
            "change_variable",
            "delete_variable",
            "log",
            "define_function",
            "call_function",
        ):
            self.assertIn(f'data-entry-add-block="{kind}"', page)
        self.assertIn('data-entry-category="system"', page)
        self.assertIn('data-entry-category="data"', page)
        self.assertIn('data-entry-category="logic"', page)
        self.assertIn('data-entry-category="math"', page)
        self.assertIn('data-entry-category="function"', page)
        self.assertIn('id="seer-variable-options"', page)
        self.assertIn('id="seer-function-options"', page)
        self.assertIn("field.kind==='choice'||choices.length", page)

    def test_signal_system_and_data_blocks_compile_to_safe_program(self) -> None:
        definition = {
            "name": "practicalAmrProgram",
            "label": "Practical AMR Program",
            "blocks": [
                {
                    "kind": "on_di",
                    "values": {
                        "channel": "2",
                        "mode": "rising",
                        "timeout_sec": "30",
                        "poll_interval_sec": "0.1",
                    },
                    "variables": {},
                    "children": [
                        {
                            "kind": "set_do",
                            "values": {"id": "3", "status": "on"},
                            "variables": {},
                        }
                    ],
                },
                {
                    "kind": "pulse_do",
                    "values": {
                        "id": "4",
                        "seconds": "0.2",
                        "first_status": "on",
                        "final_status": "off",
                    },
                    "variables": {},
                },
                {
                    "kind": "switch_map",
                    "values": {"map_name": "factory_2"},
                    "variables": {},
                },
                {
                    "kind": "relocate",
                    "values": {"mode": "auto", "x": "0", "y": "0", "theta_deg": "0"},
                    "variables": {},
                },
                {
                    "kind": "set_motor",
                    "values": {"target": "all", "motor_name": "", "status": "off"},
                    "variables": {},
                },
                {
                    "kind": "set_variable",
                    "values": {"name": "retry_count", "value": "0"},
                    "variables": {},
                },
                {
                    "kind": "change_variable",
                    "values": {"name": "retry_count", "amount": "1"},
                    "variables": {},
                },
                {
                    "kind": "log",
                    "values": {"message": "signal sequence complete"},
                    "variables": {},
                },
            ],
        }
        hcl, recipe = build_recipe_hcl(json.dumps(definition))
        self.assertTrue(recipe["structured"])
        self.assertEqual(hcl.count('step "seerBlockProgram"'), 1)
        program = tuple(recipe["program"])
        self.assertEqual(
            tuple(node["kind"] for node in program),
            (
                "on_di",
                "pulse_do",
                "switch_map",
                "relocate",
                "set_motor",
                "set_variable",
                "change_variable",
                "log",
            ),
        )
        encoded_match = re.search(r'program_b64 = "([^"]+)"', hcl)
        self.assertIsNotNone(encoded_match)
        self.assertEqual(decode_program(encoded_match.group(1))[0]["kind"], "on_di")

    def test_entry_signal_flow_math_logic_data_and_function_blocks_compile(self) -> None:
        definition = {
            "name": "entryRuntimeAction",
            "label": "Entry Runtime Action",
            "blocks": [
                {
                    "kind": "define_function",
                    "values": {"function_name": "handshake"},
                    "variables": {},
                    "children": [
                        {
                            "kind": "send_do_wait_di",
                            "values": {
                                "do_id": "2",
                                "do_status": "on",
                                "di_channel": "3",
                                "di_mode": "rising",
                                "timeout_sec": "30",
                                "poll_interval_sec": "0.1",
                                "final_do_status": "off",
                            },
                            "variables": {},
                        }
                    ],
                },
                {
                    "kind": "set_variable",
                    "values": {"name": "counter", "value": "0"},
                    "variables": {},
                },
                {
                    "kind": "calculate",
                    "values": {
                        "result_name": "total",
                        "left": "$counter",
                        "operator": "add",
                        "right": "1",
                    },
                    "variables": {},
                },
                {
                    "kind": "judge",
                    "values": {
                        "result_name": "ready",
                        "left": "$total",
                        "operator": ">=",
                        "right": "1",
                    },
                    "variables": {},
                },
                {
                    "kind": "read_state",
                    "values": {"source": "battery", "channel": "0", "result_name": "soc"},
                    "variables": {},
                },
                {
                    "kind": "call_function",
                    "values": {"function_name": "handshake"},
                    "variables": {},
                },
                {
                    "kind": "forever",
                    "values": {},
                    "variables": {},
                    "children": [
                        {"kind": "break_loop", "values": {}, "variables": {}}
                    ],
                },
            ],
        }
        hcl, recipe = build_recipe_hcl(json.dumps(definition))
        kinds = tuple(node["kind"] for node in recipe["program"])

        self.assertEqual(
            kinds,
            (
                "define_function",
                "set_variable",
                "calculate",
                "judge",
                "read_state",
                "call_function",
                "forever",
            ),
        )
        self.assertIn('step "seerBlockProgram"', hcl)
        self.assertEqual(recipe["program"][0]["children"][0]["kind"], "send_do_wait_di")

        broken = json.loads(json.dumps(definition))
        broken["blocks"][5]["values"]["function_name"] = "missingFunction"
        with self.assertRaisesRegex(RecipeBuilderError, "정의되지 않은 함수"):
            build_recipe_hcl(json.dumps(broken))

        broken_signal = json.loads(json.dumps(definition))
        broken_signal["blocks"][0]["children"][0]["values"]["do_id"] = "16"
        with self.assertRaises(RecipeBuilderError):
            build_recipe_hcl(json.dumps(broken_signal))

    def test_adapter_loader_receives_path_nav_delay(self) -> None:
        hcl, _recipe = build_recipe_hcl(_program("delayLoaded"))
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "recipes.hcl"
            path.write_text(hcl, encoding="utf-8")
            loaded = load_recipes(path)
        recipe = next(item for item in loaded if item["action_type"] == "delayLoaded")
        self.assertEqual(recipe["steps"][0]["delay_sec"], 0.25)
        self.assertEqual(recipe["steps"][2]["extension"], "seerPathNav")
        self.assertEqual(recipe["steps"][2]["delay_sec"], 0.1)

    def test_fleet_map_overlays_two_same_map_robots_and_selector(self) -> None:
        raw_map = {
            "header": {"map_name": "shared"},
            "normalPosList": [[0.0, 0.0], [2.0, 2.0]],
            "advancedPointList": [
                {"instanceName": "LM1", "pos": {"x": 0.0, "y": 0.0}, "dir": 0.0},
                {"instanceName": "LM2", "pos": {"x": 1.0, "y": 1.0}, "dir": 0.0},
            ],
            "advancedCurveList": [],
            "advancedLineList": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = Path(temp_dir) / "seer-map.json"
            write_map_cache(cache, raw_map)
            first = SimpleNamespace(
                x=0.0, y=0.0, theta=0.0, battery_soc=80.0,
                map_id="shared", last_node_id="LM1", adapter_online=True,
            )
            second = SimpleNamespace(
                x=1.0, y=1.0, theta=1.0, battery_soc=60.0,
                map_id="shared", last_node_id="LM2", adapter_online=True,
            )
            page = render_fleet_map_card(
                cache,
                first,
                (
                    {"key": "seer:a", "label": "AMR-A", "snapshot": first, "position_unit": "m", "orientation_unit": "rad"},
                    {"key": "seer:b", "label": "AMR-B", "snapshot": second, "position_unit": "m", "orientation_unit": "rad"},
                ),
                position_unit="m",
                orientation_unit="rad",
                adapter_key="seer:a",
                csrf_token="token",
            )
            self.assertIn("SEER Fleet map", page)
            self.assertIn("AMR-A", page)
            self.assertIn("AMR-B", page)
            self.assertIn("/fleet-map?robot=seer%3Ab", page)
            self.assertGreaterEqual(page.count("data-seer-robot-marker"), 2)
            self.assertIn('action="/adapter/seer:a/action"', page)


    def test_jack_blocks_are_available_and_saved_as_vda_actions(self) -> None:
        payload = json.dumps(
            {
                "name": "seerJackCycle",
                "label": "SEER Jack Cycle",
                "blocks": [
                    {"kind": "jack_load", "values": {}, "variables": {}, "delay_sec": 0},
                    {"kind": "jack_unload", "values": {}, "variables": {}, "delay_sec": 0},
                ],
            }
        )
        block, recipe = build_recipe_hcl(payload)
        self.assertIn('step "seerJackLoad"', block)
        self.assertIn('step "seerJackUnload"', block)
        self.assertTrue(recipe["motion"])
        self.assertEqual([item[0].kind for item in recipe["blocks"]], ["jack_load", "jack_unload"])
        self.assertEqual(
            [item[0].action_type for item in recipe["blocks"]],
            ["seerJackLoad", "seerJackUnload"],
        )


if __name__ == "__main__":
    unittest.main()
