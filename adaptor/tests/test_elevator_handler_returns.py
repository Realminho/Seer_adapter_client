"""엘리베이터 상태 handler가 성공 경로에서 True를 돌려주는지 고정한다.

run()은 `ok = await self.handle_state(state); if not ok: -> FAILED`로 판정한다.
handler가 성공하고도 아무것도 반환하지 않으면 None이 falsy라 workflow가 실패로
끝난다. 실제로 두 곳이 그랬다:
  - handle_goto_floor: 성공 경로에 return이 없어 INSIDE(층 이동)가 항상 실패
  - handle_requesting_floor: 다른 층을 호출하는 else 분기에 return이 없어
    엘리베이터가 그 층에 없을 때 ENTER가 항상 실패
"""
import ast
import asyncio
import copy
import pathlib
import unittest

from config.config import get_config
from utils.elevator import EVWorkflow


class _Ezi:
    """층 입력을 지정한 값으로 고정하는 EZI IO 가짜."""

    def __init__(self, floor_input=0):
        self.floor_input = floor_input
        self.outputs = {}

    async def turn_on_output(self, n):
        self.outputs[n] = 1

    async def turn_off_output(self, n):
        self.outputs[n] = 0

    async def get_input_pin(self, pin):
        return self.floor_input

    async def get_output_pin(self, pin):
        return self.outputs.get(pin, 0)

    async def set_output(self, set_mask=0, reset_mask=0):
        return 0


def _workflow(ezi):
    config = copy.deepcopy(get_config())
    config.elevator_config.elevating_timing_second = 0
    return EVWorkflow(
        floor_pin=0,
        pio_station_id="000010",
        action="ENTER",
        channel=config.elevator_config.channel,
        config_data=config,
        ezi_io=ezi,
    )


class ElevatorHandlerReturnTest(unittest.TestCase):
    def test_goto_floor_reports_success(self):
        ezi = _Ezi()
        wf = _workflow(ezi)

        self.assertIs(asyncio.run(wf.handle_goto_floor()), True)
        self.assertEqual(ezi.outputs[wf.floor_pin], 1)   # 층 버튼은 실제로 눌렀다

    def test_requesting_floor_reports_success_when_already_on_the_floor(self):
        wf = _workflow(_Ezi(floor_input=1))

        self.assertIs(asyncio.run(wf.handle_requesting_floor()), True)

    def test_requesting_floor_reports_success_after_calling_another_floor(self):
        """엘리베이터가 다른 층에 있을 때가 원래 실패하던 경로다."""
        wf = _workflow(_Ezi(floor_input=0))

        self.assertIs(asyncio.run(wf.handle_requesting_floor()), True)

    def test_handlers_only_touch_attributes_the_constructor_sets(self):
        """`self.floor`처럼 없는 속성을 쓰면 bare except가 실패로 삼켜 버린다.

        생성자는 floor_pin을 만드는데 handler들이 self.floor를 봤다. 그래서
        ENTER/INSIDE가 AttributeError로 항상 실패했고, except가 워크플로 실패로
        바꿔 놓아 원인이 드러나지 않았다.
        """
        for module_name, cls_name in (("utils.elevator", "EVWorkflow"),
                                      ("utils.airshower", "ASWorkflow")):
            module = __import__(module_name, fromlist=[cls_name])
            source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
            tree = ast.parse(source)
            cls = next(
                node for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef) and node.name == cls_name
            )
            assigned = {
                target.attr
                for node in ast.walk(cls)
                if isinstance(node, ast.Assign)
                for target in node.targets
                if isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            }
            methods = {
                node.name for node in ast.walk(cls)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            used = {
                node.attr
                for handler in ast.walk(cls)
                if isinstance(handler, ast.AsyncFunctionDef)
                and handler.name.startswith("handle_")
                for node in ast.walk(handler)
                if isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
            }
            unknown = sorted(used - assigned - methods)
            self.assertEqual(unknown, [], f"{cls_name}: undefined self.<attr>")

    def test_every_handler_returns_explicitly(self):
        """새 handler가 return을 빠뜨린 채 들어오는 것을 막는다."""

        def always_returns(stmts):
            if not stmts:
                return False
            last = stmts[-1]
            if isinstance(last, ast.Return):
                return True
            if isinstance(last, ast.If):
                return always_returns(last.body) and always_returns(last.orelse)
            if isinstance(last, ast.Try):
                return always_returns(last.body) and all(
                    always_returns(handler.body) for handler in last.handlers
                )
            return False

        root = pathlib.Path(__file__).resolve().parents[1] / "utils"
        for name in ("elevator.py", "airshower.py"):
            tree = ast.parse((root / name).read_text(encoding="utf-8"))
            missing = [
                node.name
                for node in ast.walk(tree)
                if isinstance(node, ast.AsyncFunctionDef)
                and node.name.startswith("handle_")
                and not always_returns(node.body)
            ]
            self.assertEqual(missing, [], f"{name}: handler without explicit return")


if __name__ == "__main__":
    unittest.main()
