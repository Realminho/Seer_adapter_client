"""연속 주행 구간(coalescing run) 계산.

알고리즘은 inorbit vda5050_connector 의 `_get_drivable_segment()` 를 옮긴 것이다
(vda5050_connector_py/vda5050_controller.py, BSD-3-Clause,
 Copyright (c) InOrbit, Inc. — https://github.com/inorbit-ai/ros_amr_interop).
원본은 미released 엣지/노드와 첫 HARD/SOFT 블로킹 액션에서 구간을 끊는다.
여기서는 무엇이 끊는지를 호출자가 술어로 넘기게 일반화했다 — JIBOT 은 dock 세그먼트
룰, move 룰, dock work 노드, 좌표 미상 노드처럼 벤더 고유의 끊는 조건이 더 있다.
"""

from __future__ import annotations

from typing import Callable, Sequence, TypeVar

T = TypeVar("T")


def drivable_run(
    steps: Sequence[T],
    index: int,
    is_breaker: Callable[[T], bool],
) -> int:
    """index 에서 시작하는 연속 주행 구간의 마지막 인덱스.

    끊는 스텝은 합치지 않고 그 자체로 한 구간이 된다(반환값 == index).
    끊지 않는 스텝은 다음 끊는 스텝 **직전**까지 이어 붙인다.
    """
    if is_breaker(steps[index]):
        return index
    last = index
    for candidate in range(index + 1, len(steps)):
        if is_breaker(steps[candidate]):
            break
        last = candidate
    return last
