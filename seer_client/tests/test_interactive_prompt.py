"""Contextual command completion and Bash-like double-Tab tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "seer_client" / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from prompt_toolkit.completion import CompleteEvent  # noqa: E402
from prompt_toolkit.document import Document  # noqa: E402
from prompt_toolkit.input import create_pipe_input  # noqa: E402
from prompt_toolkit.output import DummyOutput  # noqa: E402
from seer_client.interactive_prompt import (  # noqa: E402
    CompletionCatalog,
    ContextualCommandCompleter,
    InteractiveCommandPrompt,
    TerminalTabState,
    apply_terminal_tab,
)


class _FakeBuffer:
    def __init__(self, text: str) -> None:
        self.text = text
        self.cursor_position = len(text)
        self.complete_state = None
        self.started = 0
        self.next_count = 0

    @property
    def document(self) -> Document:
        return Document(self.text, self.cursor_position)

    def insert_text(self, value: str) -> None:
        self.text = (
            self.text[: self.cursor_position]
            + value
            + self.text[self.cursor_position :]
        )
        self.cursor_position += len(value)

    def start_completion(self, *, select_first: bool) -> None:
        self.started += 1
        self.complete_state = SimpleNamespace(select_first=select_first)

    def complete_next(self) -> None:
        self.next_count += 1


class InteractivePromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = CompletionCatalog(
            commands={
                "goto": "go to point",
                "goto_xyz": "go to coordinates",
                "status": "show state",
                "stop": "stop motion",
                "emc": "set emergency",
                "emc_release": "release emergency",
                "last": "show cached topic",
                "do": "set output",
            },
            argument_choices={
                "last": {0: ("state", "connection", "factsheet")},
                "do": {1: ("on", "off")},
            },
        )
        self.completer = ContextualCommandCompleter(self.catalog)

    def completions(self, text: str) -> list[str]:
        return [
            completion.text
            for completion in self.completer.get_completions(
                Document(text, len(text)),
                CompleteEvent(completion_requested=True),
            )
        ]

    def test_command_and_contextual_argument_candidates(self) -> None:
        self.assertEqual(self.completions("em"), ["emc", "emc_release"])
        self.assertEqual(self.completions("last s"), ["state"])
        self.assertEqual(self.completions("do 3 o"), ["on", "off"])

    def test_first_tab_completes_common_prefix_second_opens_candidates(self) -> None:
        buffer = _FakeBuffer("g")
        state = TerminalTabState()
        apply_terminal_tab(buffer, self.completer, state)
        self.assertEqual(buffer.text, "goto")
        self.assertEqual(buffer.started, 0)
        apply_terminal_tab(buffer, self.completer, state)
        self.assertEqual(buffer.started, 1)

    def test_two_tabs_without_common_suffix_open_candidates(self) -> None:
        buffer = _FakeBuffer("st")
        state = TerminalTabState()
        apply_terminal_tab(buffer, self.completer, state)
        self.assertEqual(buffer.started, 0)
        apply_terminal_tab(buffer, self.completer, state)
        self.assertEqual(buffer.started, 1)
        apply_terminal_tab(buffer, self.completer, state)
        self.assertEqual(buffer.next_count, 1)

    def test_prompt_session_can_use_terminal_compatible_input_output(self) -> None:
        with create_pipe_input() as pipe_input:
            prompt = InteractiveCommandPrompt(
                self.catalog,
                input_stream=pipe_input,
                output_stream=DummyOutput(),
            )
            self.assertTrue(prompt.completion_enabled)
            self.assertEqual(prompt.unavailable_reason, "")


if __name__ == "__main__":
    unittest.main()
