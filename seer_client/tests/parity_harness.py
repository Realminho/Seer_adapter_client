"""Small reusable harness for JIBOT/SEER behavior comparisons."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence


@dataclass(frozen=True)
class ParityResult:
    terminal_status: str
    calls: tuple[tuple[str, Mapping[str, Any]], ...]
    state: Mapping[str, Any]
    error: str = ""


def normalize_status(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw).upper()


async def execute_scenario(
    operation: Callable[[], Any],
    *,
    calls: Sequence[tuple[str, Mapping[str, Any]]],
    state: Mapping[str, Any],
    terminal_status: str = "FINISHED",
) -> ParityResult:
    """Execute an operation and normalize terminal/error information."""

    try:
        result = operation()
        if inspect.isawaitable(result):
            await result
        return ParityResult(
            terminal_status=normalize_status(terminal_status),
            calls=tuple((name, dict(body)) for name, body in calls),
            state=dict(state),
        )
    except Exception as exc:
        return ParityResult(
            terminal_status="FAILED",
            calls=tuple((name, dict(body)) for name, body in calls),
            state=dict(state),
            error=f"{type(exc).__name__}: {exc}",
        )


def assert_same_operational_result(testcase: Any, expected: ParityResult, actual: ParityResult) -> None:
    """Compare normalized FMS-visible behavior, not vendor wire details."""

    testcase.assertEqual(actual.terminal_status, expected.terminal_status)
    testcase.assertEqual(actual.state, expected.state)
    testcase.assertEqual(bool(actual.error), bool(expected.error))
