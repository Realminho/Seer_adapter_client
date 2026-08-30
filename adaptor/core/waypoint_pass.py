"""연속 주행 중 노드 통과 판정.

점 판정("지금 pose 가 반경 안인가")은 쓸 수 없다. 2026-08-26 192.168.101.50:7274
실측에서 직선 통과 속도가 1002 mm/s 였고 node_position_poll_interval_sec 이 0.2 라
표본 간 이동이 약 200mm 다. 도착존 실효값 ±40mm 는 두 표본 사이로 통째로 지나간다.
그래서 직전 표본과 현재 표본을 잇는 **선분**이 반경 안을 지났는지로 판정한다.

libVDA5050++ 가 같은 문제를 InterpolationType {NONE, LINEAR} 로 노출한다.
NONE 이 점 판정, LINEAR 가 이 선분 판정에 해당한다.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

Point = Tuple[float, float]


def point_to_segment_distance(point: Point, start: Point, end: Point) -> float:
    """point 에서 선분 start-end 까지의 최단 거리."""
    px, py = point
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    # 선분 위 최근접점의 매개변수 t 를 [0, 1] 로 자른다.
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def segment_passes_within(
    node_xy: Point,
    prev_xy: Optional[Point],
    cur_xy: Point,
    radius: float,
) -> bool:
    """직전 표본에서 현재 표본까지 오는 동안 노드 반경 안을 지났는가.

    prev_xy 가 None 이면(첫 표본) 점 판정으로 떨어진다.
    """
    if prev_xy is None:
        return math.hypot(cur_xy[0] - node_xy[0], cur_xy[1] - node_xy[1]) <= radius
    return point_to_segment_distance(node_xy, prev_xy, cur_xy) <= radius
