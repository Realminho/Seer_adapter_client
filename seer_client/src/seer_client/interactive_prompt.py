"""Terminal-style interactive input with contextual Tab completion."""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Optional, Sequence, Set, Tuple

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import (
    CompleteEvent,
    Completer,
    Completion,
    PathCompleter,
    get_common_complete_suffix,
)
from prompt_toolkit.document import Document
from prompt_toolkit.enums import DEFAULT_BUFFER
from prompt_toolkit.filters import has_focus
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.shortcuts import CompleteStyle


@dataclass(frozen=True)
class CompletionCatalog:
    """Commands and argument choices available at one manual-test prompt."""

    commands: Mapping[str, str]
    argument_choices: Mapping[str, Mapping[int, Sequence[str]]] = field(
        default_factory=dict
    )
    path_arguments: Set[Tuple[str, int]] = field(default_factory=set)


def _tokens_before_cursor(text: str) -> Tuple[Sequence[str], bool]:
    trailing_space = bool(text) and text[-1].isspace()
    stripped = text.strip()
    if not stripped:
        return (), trailing_space
    return tuple(token for token in re.split(r"\s+", stripped) if token), trailing_space


class ContextualCommandCompleter(Completer):
    """Complete command names, fixed argument choices, and file paths."""

    def __init__(self, catalog: CompletionCatalog) -> None:
        self.catalog = catalog
        self._path_completer = PathCompleter(expanduser=True)

    @staticmethod
    def _completion(
        value: str, current: str, *, meta: str = ""
    ) -> Completion:
        return Completion(
            value,
            start_position=-len(current),
            display=value,
            display_meta=meta,
        )

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterator[Completion]:
        del complete_event
        tokens, trailing_space = _tokens_before_cursor(document.text_before_cursor)
        if not tokens:
            for command, description in self.catalog.commands.items():
                yield self._completion(command, "", meta=description)
            return

        if len(tokens) == 1 and not trailing_space:
            current = tokens[0]
            normalized = current.lower().replace("-", "_")
            for command, description in self.catalog.commands.items():
                if command.lower().startswith(normalized):
                    yield self._completion(command, current, meta=description)
            return

        command = tokens[0].lower().replace("-", "_")
        current = "" if trailing_space else tokens[-1]
        argument_index = len(tokens) - 1 if trailing_space else len(tokens) - 2
        choices = self.catalog.argument_choices.get(command, {}).get(argument_index, ())
        for choice in choices:
            if choice.lower().startswith(current.lower()):
                yield self._completion(choice, current)

        if (command, argument_index) in self.catalog.path_arguments:
            path_document = Document(current, cursor_position=len(current))
            yield from self._path_completer.get_completions(
                path_document, CompleteEvent(completion_requested=True)
            )


@dataclass
class TerminalTabState:
    """State needed to distinguish the first and second consecutive Tab."""

    last_marker: Optional[Tuple[str, int]] = None


def apply_terminal_tab(
    buffer: Any,
    completer: Completer,
    state: TerminalTabState,
) -> None:
    """Apply Bash-like Tab behavior to a prompt_toolkit buffer.

    The first Tab inserts the common completion prefix when possible.  If the
    buffer does not change, the second Tab opens the candidate menu.  Further
    Tabs cycle through the visible candidates.
    """

    if buffer.complete_state is not None:
        buffer.complete_next()
        return

    document = buffer.document
    completions = list(
        completer.get_completions(
            document, CompleteEvent(completion_requested=True)
        )
    )
    if not completions:
        state.last_marker = None
        return

    common_suffix = get_common_complete_suffix(document, completions)
    if common_suffix:
        buffer.insert_text(common_suffix)
        state.last_marker = (buffer.text, buffer.cursor_position)
        return

    marker = (buffer.text, buffer.cursor_position)
    if state.last_marker == marker:
        buffer.start_completion(select_first=False)
    else:
        state.last_marker = marker


def _terminal_tab_bindings(completer: Completer) -> KeyBindings:
    bindings = KeyBindings()
    state = TerminalTabState()

    @bindings.add("tab", filter=has_focus(DEFAULT_BUFFER))
    def _tab(event: Any) -> None:
        apply_terminal_tab(event.current_buffer, completer, state)

    @bindings.add(Keys.BackTab, filter=has_focus(DEFAULT_BUFFER))
    def _back_tab(event: Any) -> None:
        buffer = event.current_buffer
        if buffer.complete_state is None:
            buffer.start_completion(select_first=False)
        else:
            buffer.complete_previous()

    return bindings


class InteractiveCommandPrompt:
    """One prompt session with in-memory history and terminal-like completion."""

    def __init__(
        self,
        catalog: CompletionCatalog,
        *,
        input_stream: Any = None,
        output_stream: Any = None,
    ) -> None:
        self.completer = ContextualCommandCompleter(catalog)
        session_options: dict[str, Any] = {}
        if input_stream is not None:
            session_options["input"] = input_stream
        if output_stream is not None:
            session_options["output"] = output_stream
        self.unavailable_reason = ""
        try:
            self.session: Optional[PromptSession[str]] = PromptSession(
                completer=self.completer,
                complete_while_typing=False,
                complete_in_thread=False,
                complete_style=CompleteStyle.MULTI_COLUMN,
                reserve_space_for_menu=8,
                history=InMemoryHistory(),
                key_bindings=_terminal_tab_bindings(self.completer),
                enable_history_search=True,
                **session_options,
            )
        except Exception as exc:
            self.session = None
            self.unavailable_reason = f"{type(exc).__name__}: {exc}"

    @property
    def completion_enabled(self) -> bool:
        return self.session is not None

    async def read(self, prompt_text: str) -> str:
        if self.session is None:
            return await asyncio.to_thread(input, prompt_text)
        return await self.session.prompt_async(prompt_text)


def terminal_completion_available() -> bool:
    """Return whether the current standard input appears interactive."""

    return os.isatty(0)
