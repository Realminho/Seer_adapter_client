"""어댑터 소유 클라이언트로 에어샤워·엘리베이터 절차를 실행한다."""

from __future__ import annotations

from typing import Any

from core.action_registry import (
    ActionParameterSpec,
    ActionResult,
    ActionSpec,
    InlineActionContext,
)
from extensions.pio import get_pio_client
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus
from utils.airshower import ASWorkflow
from utils.elevator import EVWorkflow
from utils.ezi_io import EZIIOClient

MODULE_TITLE = "Facility workflows"

_AIR = {
    "airShowerEnter": "ENTER",
    "airShowerInside": "INSIDE",
    "airShowerPassed": "PASSED",
}
_ELEVATOR = {
    "elevatorEnter": "ENTER",
    "elevatorInside": "INSIDE",
    "elevatorPassed": "PASSED",
}


def _ezi_client(adapter: Any) -> Any:
    """공용 EZIO 클라이언트를 필요할 때 한 번만 생성한다."""
    if adapter._ezi_io is None:
        adapter._ezi_io = EZIIOClient(adapter.config.ezi_config.ezi_io)
    return adapter._ezi_io


def _required_int(params, *names):
    """여러 별칭 중 존재하는 필수 정수 파라미터를 반환한다."""
    for name in names:
        if name in params:
            try:
                return int(params[name])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be an integer") from exc
    raise ValueError(f"missing parameter: {names[0]}")


def _required_text(params, *names):
    """여러 별칭 중 비어 있지 않은 필수 문자열 파라미터를 반환한다."""
    for name in names:
        value = str(params.get(name, "")).strip()
        if value:
            return value
    raise ValueError(f"missing parameter: {names[0]}")


async def _run_air(ctx: InlineActionContext, action: str) -> ActionResult:
    """에어샤워 상태 머신을 실행해 VDA action 결과로 변환한다."""
    pin = _required_int(ctx.params, "doorPin", "door_pin")
    workflow = ASWorkflow(
        pin,
        action,
        config_data=ctx.adapter.config,
        pio=get_pio_client(ctx.adapter),
        ezi_io=_ezi_client(ctx.adapter),
    )
    result, error_state, message = await workflow.workflow_sequence()
    if result:
        return ActionResult(ActionStatus.FINISHED, f"{ctx.action.action_type} finished")
    detail = getattr(error_state, "value", str(error_state))
    return ActionResult(ActionStatus.FAILED, f"{detail}: {message}")


async def _run_elevator(ctx: InlineActionContext, action: str) -> ActionResult:
    """엘리베이터 상태 머신을 실행해 VDA action 결과로 변환한다."""
    # station 하나로 고르면 짝이 어긋날 수 없다. floorPin/pioStationId를 직접
    # 주는 기존 호출(현장 ACS, 예전 recipe)도 그대로 받는다.
    chosen = ctx.params.get("station") or ctx.params.get("stationId")
    if chosen:
        floor_pin, station = resolve_station(ctx.adapter.config, chosen)
    else:
        floor_pin = _required_int(ctx.params, "floorPin", "floor_pin")
        station = _required_text(ctx.params, "pioStationId", "pio_station_id")
    workflow = EVWorkflow(
        floor_pin,
        station,
        action,
        # 채널은 설비 하나에 하나다. station은 그 채널 위의 어느 층인지를 고른다.
        channel=ctx.adapter.config.elevator_config.channel,
        config_data=ctx.adapter.config,
        pio=get_pio_client(ctx.adapter),
        ezi_io=_ezi_client(ctx.adapter),
    )
    result, error_state, message = await workflow.workflow_sequence()
    if result:
        return ActionResult(ActionStatus.FINISHED, f"{ctx.action.action_type} finished")
    detail = getattr(error_state, "value", str(error_state))
    return ActionResult(ActionStatus.FAILED, f"{detail}: {message}")


def elevator_stations(config) -> tuple:
    """설정의 motion rule에서 (station id, floor pin) 짝을 뽑는다.

    floor_pin과 pio_station_id는 따로 고르면 안 되는 값이다. 짝이 어긋나면
    엘리베이터는 페어링된 것과 다른 층 버튼을 누른다. 짝은 이미
    elevator_motion_rules에 있으므로 운영자가 손으로 맞출 이유가 없다.
    """
    rules = getattr(
        getattr(config, "elevator_config", None), "elevator_motion_rules", ()
    ) or ()
    pins: dict = {}
    for rule in rules:
        station = str(getattr(rule, "pio_station_id", "")).strip()
        pin = getattr(rule, "floor_pin", None)
        if not station or pin is None:
            continue
        if station in pins and pins[station] != pin:
            raise ValueError(
                f"elevator station '{station}' maps to floor pins "
                f"{pins[station]} and {pin}; motion rules must agree"
            )
        pins[station] = pin
    return tuple(sorted(pins.items()))


def resolve_station(config, station: str) -> tuple:
    """station id 하나로 (floor_pin, station id)를 되돌린다."""
    wanted = str(station).strip()
    for known, pin in elevator_stations(config):
        if known == wanted:
            return int(pin), known
    known_ids = ", ".join(sid for sid, _ in elevator_stations(config)) or "(none)"
    raise ValueError(f"unknown elevator station: {wanted} (known: {known_ids})")


def _door_pin_choices(config) -> tuple:
    pins = getattr(getattr(config, "air_shower_config", None), "door_pin", ()) or ()
    return tuple(str(pin) for pin in pins)


def action_specs(config=None) -> tuple[ActionSpec, ...]:
    """시설 workflow별 action type과 실행 handler를 등록한다.

    config를 받으면 핀과 station을 자유 입력 대신 목록으로 노출한다. 값이
    설정에 이미 있는데 운영자가 외워서 타이핑할 이유가 없다.
    """
    door_choices = _door_pin_choices(config) if config is not None else ()
    try:
        station_choices = tuple(
            sid for sid, _ in elevator_stations(config)
        ) if config is not None else ()
    except ValueError as exc:
        # 설정이 모순이면 목록을 만들 수 없다. 자유 입력으로 두되 이유를 남긴다.
        print(f"[FACILITY] elevator station list unavailable: {exc}")
        station_choices = ()

    specs = [
        ActionSpec(
            name,
            handler=lambda ctx, action=action: _run_air(ctx, action),
            parameters=(
                ActionParameterSpec(
                    "doorPin", required=True,
                    input_type="text" if door_choices else "number",
                    choices=door_choices,
                    label="에어샤워 문 핀",
                ),
            ),
        )
        for name, action in _AIR.items()
    ]
    specs.extend(
        ActionSpec(
            name,
            handler=lambda ctx, action=action: _run_elevator(ctx, action),
            parameters=(
                ActionParameterSpec(
                    "station", required=True,
                    choices=station_choices,
                    placeholder="PIO station id",
                    # 목록 밖 값은 resolve_station이 어차피 거절한다. 설정을
                    # 되짚어 푸는 칸이라 select 그대로 둔다.
                    label="엘리베이터 station",
                ),
            ) if station_choices else (
                ActionParameterSpec(
                    "floorPin", required=True, input_type="number",
                    label="층 핀 번호",
                ),
                ActionParameterSpec(
                    "pioStationId", required=True, label="PIO station id",
                ),
            ),
        )
        for name, action in _ELEVATOR.items()
    )
    return tuple(specs)
