"""설비와 병렬 I/O 신호를 교환하는 PIO action extension."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from core.action_registry import (
    ActionParameterSpec,
    ActionResult,
    ActionSpec,
    action_params,
)
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus


PIO_ACTION_TYPES = (
    "pioInit",
    "pioReadIn",
    "pioWriteOut",
    "pioDisconnect",
    "pioScenario",
    "pioPing",
)

MODULE_TITLE = "PIO"

_PIO_LABELS = {
    "pioInit": "PIO init (연결)",
    "pioReadIn": "PIO read inputs",
    "pioWriteOut": "PIO write output",
    "pioDisconnect": "PIO disconnect",
    "pioScenario": "PIO scenario 실행",
    "pioPing": "PIO 연결 확인",
}


#: PIOMaster.make_frame가 보내는 형식과 같은 <payload+checksum> 프레임.
#: 잡음 속에 섞여 들어와도 찾을 수 있게 search로 쓴다.
#: 구분자는 방향마다 다르다. 우리가 <payload+cs>를 보내면 보드는 [payload+cs]로
#: 답한다 (실기 확인: TX <BC=...59>, RX [BC=...6B]). 잡음에 섞여 와도 찾도록
#: search가 아니라 finditer로 훑는다.
_PIO_FRAME_RE = re.compile(r"[<\[]([^<>\[\]]*)[>\]]")


def pio_checksum_hex(payload: str) -> str:
    """PIO 프레임 체크섬. utils.pio.PIOMaster.checksum_hex와 같은 식이어야 한다.

    여기서 다시 정의하는 이유는 utils.pio가 import 시점에 pyserial을 요구하기
    때문이다. 두 식이 어긋나지 않게 테스트로 묶어 두었다.
    """
    return f"{sum(payload.encode('ascii', 'ignore')) & 0xFF:02X}"


def find_pio_frame(response: Any) -> Optional[str]:
    """원시 응답에서 체크섬이 맞는 프레임을 찾는다. 없으면 None.

    구분자만 보면 엉뚱한 포트의 잡음도 통과한다. 체크섬까지 맞아야 PIO 보드로
    인정하므로, 이 검사가 포트/보드레이트 오설정을 잡아내는 지점이다.
    """
    text = "" if response is None else str(response)
    for match in _PIO_FRAME_RE.finditer(text):
        body = match.group(1)
        if len(body) < 3:
            continue
        payload, checksum = body[:-2], body[-2:]
        if checksum.upper() == pio_checksum_hex(payload):
            return match.group(0)
    return None


#: udev가 만드는 안정 경로. `/dev/ttyUSBn`은 열거 순서가 정하는 이름이라 로봇마다
#: 다르고(같은 PL2303이 1호기 ttyUSB4, 2호기 ttyUSB0), USB를 옮겨 꽂으면 또 바뀐다.
_SERIAL_BY_ID_DIR = Path("/dev/serial/by-id")


def serial_port_candidates(by_id_dir: Any = None) -> Tuple[str, ...]:
    """이 로봇에서 쓸 수 있는 안정 시리얼 경로 목록.

    pyserial은 심볼릭 링크를 그대로 열기 때문에 pio_serial_port에 이 경로를 적으면
    ttyUSB 번호가 바뀌어도 따라간다. 디렉터리는 udev가 만들므로 없을 수 있고,
    그때는 빈 튜플이다 — 후보를 못 찾는 것이 실패 경로를 막아서는 안 된다.
    """
    base = _SERIAL_BY_ID_DIR if by_id_dir is None else Path(by_id_dir)
    try:
        names = sorted(entry.name for entry in base.iterdir())
    except OSError:
        return ()
    return tuple(f"/dev/serial/by-id/{name}" for name in names)


def describe_serial_port_candidates(by_id_dir: Any = None) -> str:
    """실패 메시지에 덧붙일 후보 안내. 후보가 없으면 빈 문자열.

    빈 목록을 "; candidates: "처럼 보여 주면 안내가 아니라 잡음이 된다.
    """
    candidates = serial_port_candidates(by_id_dir)
    if not candidates:
        return ""
    return "; stable paths on this robot: " + ", ".join(candidates)


def is_pio_action(action_type: str) -> bool:
    """action type이 PIO 기능에 속하는지 확인한다."""
    return action_type in PIO_ACTION_TYPES


def pio_num(adapter: Any, key: str, default: float) -> float:
    """PIO 고급 숫자 설정을 읽되 bool이나 잘못된 값은 기본값으로 처리한다."""
    val = getattr(getattr(adapter.config, "pio_advanced", None), key, default)
    return (
        float(val)
        if isinstance(val, (int, float)) and not isinstance(val, bool)
        else float(default)
    )


def pio_text(adapter: Any, key: str, default: str) -> str:
    """PIO 고급 문자열 설정을 공백 제거한 소문자로 읽는다."""
    value = getattr(getattr(adapter.config, "pio_advanced", None), key, default)
    text = str(value).strip().lower()
    return text or default


def get_pio_client(adapter: Any) -> Any:
    """주입된 factory 또는 기본 PIOMaster로 공용 PIO 클라이언트를 만든다."""
    if adapter._pio_client is None:
        if adapter._pio_client_factory is not None:
            adapter._pio_client = adapter._pio_client_factory()
        else:
            from utils.pio import PIOMaster

            adapter._pio_client = PIOMaster(
                adapter.config.pio_config.pio_serial_port,
                adapter.config.pio_config.pio_baudrate,
                timeout=pio_num(adapter, "socket_timeout_sec", 0.2),
                connect_delay_sec=pio_num(adapter, "connect_delay_sec", 0.5),
                read_frame_poll_sec=pio_num(adapter, "read_frame_poll_sec", 0.05),
                read_frames_wait_sec=pio_num(adapter, "read_frames_wait_sec", 2.0),
                send_wait_sec=pio_num(adapter, "send_wait_sec", 2.0),
            )
    return adapter._pio_client


async def ensure_pio_connected(adapter: Any) -> Any:
    """포트가 닫혀 있으면 열어, 낱개 PIO action도 pioInit 없이 실행되게 한다."""
    client = get_pio_client(adapter)
    serial_port = getattr(client, "ser", None)
    if serial_port is not None and bool(getattr(serial_port, "is_open", False)):
        return client
    connect = getattr(client, "connect", None)
    if callable(connect):
        connect()
    return client


def known_station_channel_pairs(adapter: Any) -> Tuple[Tuple[str, str], ...]:
    """설정이 아는 (station, channel) BC 주소 조합.

    station과 channel은 한 주소의 두 절반이다 — 에어샤워의 station을 엘리베이터의
    channel과 섞으면 어느 설비에도 없는 주소가 된다. pio_link_params가 그 조합을
    검증할 때, 그리고 WebUI가 유효한 조합을 안내할 때 쓴다.

    facility extension을 import하지 않고 config에서 직접 읽는다. 그쪽을 꺼도
    PIO 패널/검증은 그대로 동작해야 한다.
    """
    config = getattr(adapter, "config", None)
    pairs: list = []
    air = getattr(config, "air_shower_config", None)
    air_station = str(getattr(air, "pio_station_id", "") or "").strip()
    air_channel = getattr(air, "channel", None)
    if air_station and air_channel is not None:
        pair = (air_station, str(air_channel).strip())
        if pair not in pairs:
            pairs.append(pair)
    elevator = getattr(config, "elevator_config", None)
    elevator_channel = getattr(elevator, "channel", None)
    if elevator_channel is not None:
        rules = getattr(elevator, "elevator_motion_rules", ()) or ()
        for rule in rules:
            station = str(getattr(rule, "pio_station_id", "") or "").strip()
            if station:
                pair = (station, str(elevator_channel).strip())
                if pair not in pairs:
                    pairs.append(pair)
    return tuple(pairs)


def pio_link_params(adapter: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """pioInit/pioPing이 공유하는 BC 상대와 게이팅 핀을 한자리에서 뽑는다."""
    pio_cfg = adapter.config.pio_config
    ezi_cfg = adapter.config.ezi_config
    select_off_timing = pio_text(adapter, "select_off_timing", "after_bc")
    if select_off_timing not in {"after_go", "after_bc"}:
        raise ValueError(
            "select_off_timing must be 'after_go' or 'after_bc' "
            f"(got {select_off_timing!r})"
        )
    pair_confirmation = pio_text(adapter, "pair_confirmation", "bc_reply")
    if pair_confirmation not in {"bc_reply", "go"}:
        raise ValueError(
            "pair_confirmation must be 'bc_reply' or 'go' "
            f"(got {pair_confirmation!r})"
        )
    station_id = params.get("stationId")
    channel = params.get("channel")
    if station_id is None or str(station_id).strip() == "":
        raise ValueError(
            "PIO pairing requires stationId; extension \"pio\"의 station_id "
            "폴백은 설비 블록으로 옮겨졌다"
        )
    if channel is None or str(channel).strip() == "":
        raise ValueError(
            "PIO pairing requires channel; extension \"pio\"의 channel "
            "폴백은 설비 블록으로 옮겨졌다"
        )
    # 폼에서 들어온 값은 앞뒤 공백을 그대로 담고 있을 수 있다(" 250"). 여기서
    # 벗겨 두지 않으면 이 문자열이 그대로 BC=... 페이로드에 박힌다
    # (utils/pio.py send_bc) — 공백을 포함한 값으로 실제 설비에 나간다.
    station_id = str(station_id).strip()
    channel = str(channel).strip()
    # station과 channel은 한 주소의 두 절반이다. WebUI의 stationId는 모든 설비의
    # station을 모아 놓은 select이고 channel은 자유 숫자 입력이라, 운영자가
    # 엘리베이터의 station에 에어샤워의 channel을 짝지어 보낼 수 있다 — 존재하지
    # 않는 주소로 BC를 쏘는 것이다. 설정이 아는 조합이 있을 때만 검증한다(구성
    # 정보가 없는 옛 테스트 더블/최소 adapter는 그대로 통과시킨다).
    known_pairs = known_station_channel_pairs(adapter)
    if known_pairs and (station_id, channel) not in known_pairs:
        valid = ", ".join(f"{s}/{c}" for s, c in known_pairs)
        raise ValueError(
            f"PIO pairing rejects stationId={station_id} channel={channel}: "
            f"no configured facility uses that pair. Valid station/channel "
            f"pairs: {valid}"
        )
    return {
        "media": params.get("media", pio_cfg.media),
        "station_id": station_id,
        "channel": channel,
        "bc_port": params.get("port", pio_cfg.port),
        "oht_number": params.get("ohtNumber", pio_cfg.vehicle_num),
        "select_pin": int(params.get("selectPin", ezi_cfg.select)),
        "go_pin": int(params.get("goPin", ezi_cfg.go)),
        "serial_port": getattr(pio_cfg, "pio_serial_port", None),
        "settle_sec": pio_num(adapter, "select_settle_sec", 0.2),
        "select_off_timing": select_off_timing,
        "pair_confirmation": pair_confirmation,
        "select_off_delay_sec": max(
            0.0, pio_num(adapter, "select_off_delay_sec", 0.0)
        ),
        "poll_sec": pio_num(adapter, "call_poll_interval_sec", 0.05),
    }


async def pio_pair(
    adapter: Any,
    *,
    media: Any,
    station_id: Any,
    channel: Any,
    bc_port: Any,
    oht_number: Any,
    select_pin: int,
    settle_sec: float,
    select_off_delay_sec: float,
    wait_sec: float,
) -> str:
    """SELECT를 올린 채 BC를 보내 pairing을 건다. 원시 응답을 그대로 돌려준다.

    airshower/elevator의 pairing()과 같은 절차다. SELECT 없이 BC만 보내면
    보드가 받지 않아 응답이 0바이트다 (실기 확인).
    """
    try:
        await adapter._ezi_io.turn_on_output(select_pin)
        await asyncio.sleep(settle_sec)
        response = await call_pio(
            adapter, "send_bc", media, station_id, channel, bc_port, oht_number,
            wait_sec=wait_sec,
        )
    finally:
        # SELECT를 올린 채 빠져나가면 설비가 선택된 상태로 남는다.
        await asyncio.sleep(max(0.0, select_off_delay_sec))
        try:
            await adapter._ezi_io.turn_off_output(select_pin)
        except Exception:
            pass
        await asyncio.sleep(settle_sec)
    return "" if response is None else str(response)


async def pio_wait_go(
    adapter: Any, *, go_pin: int, want: bool, timeout_sec: float, poll_sec: float
) -> bool:
    """GO 입력이 원하는 상태가 될 때까지 기다린다. 도달했으면 True."""
    deadline = time.monotonic() + max(0.0, timeout_sec)
    while True:
        if bool(await adapter._ezi_io.get_input_pin(go_pin)) is want:
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(poll_sec)


async def pio_pair_until_go(
    adapter: Any, *, link: Dict[str, Any], wait_sec: float, pair_timeout_sec: float
) -> Tuple[bool, str, int]:
    """GO가 올라올 때까지 pairing을 되풀이한다. (성립 여부, 마지막 응답, 시도 횟수).

    설비는 첫 BC에 바로 GO를 올려주지 않는다. airshower/elevator의
    handle_pairing도 한 번 보내고 기다리는 게 아니라 GO가 올라올 때까지
    pairing()을 다시 호출한다 (elevator.py:341-347). 한 번만 보내고 수동으로
    기다리면 실기에서 "GO stayed off"로 끝난다.
    """
    deadline = time.monotonic() + max(0.0, pair_timeout_sec)
    reply = ""
    attempts = 0

    # 일부 설비는 SELECT가 ON인 동안에만 GO를 올린다. 이 모드에서는 첫 BC
    # 전부터 GO 확인까지 SELECT를 유지하고, BC만 반복한다. GO를 확인하거나
    # timeout/예외가 나면 finally에서 반드시 해제한다.
    if link["select_off_timing"] == "after_go":
        await adapter._ezi_io.turn_on_output(link["select_pin"])
        await asyncio.sleep(link["settle_sec"])
        try:
            while True:
                attempts += 1
                reply = await call_pio(
                    adapter, "send_bc",
                    link["media"], link["station_id"], link["channel"],
                    link["bc_port"], link["oht_number"],
                    wait_sec=wait_sec,
                )
                if bool(await adapter._ezi_io.get_input_pin(link["go_pin"])):
                    return True, "" if reply is None else str(reply), attempts
                if time.monotonic() >= deadline:
                    return False, "" if reply is None else str(reply), attempts
                await asyncio.sleep(link["poll_sec"])
        finally:
            await asyncio.sleep(link["select_off_delay_sec"])
            try:
                await adapter._ezi_io.turn_off_output(link["select_pin"])
            finally:
                await asyncio.sleep(link["settle_sec"])

    # 기존 장비 호환 모드: BC마다 SELECT를 내린 다음 GO를 한 번 확인한다.
    while True:
        attempts += 1
        reply = await pio_pair(
            adapter,
            media=link["media"], station_id=link["station_id"],
            channel=link["channel"], bc_port=link["bc_port"],
            oht_number=link["oht_number"], select_pin=link["select_pin"],
            settle_sec=link["settle_sec"],
            select_off_delay_sec=link["select_off_delay_sec"],
            wait_sec=wait_sec,
        )
        if bool(await adapter._ezi_io.get_input_pin(link["go_pin"])):
            return True, reply, attempts
        if time.monotonic() >= deadline:
            return False, reply, attempts
        await asyncio.sleep(link["settle_sec"])


async def pio_establish(
    adapter: Any, *, link: Dict[str, Any], wait_sec: float, pair_timeout_sec: float
) -> Tuple[bool, str, int]:
    """설정된 성공 조건으로 pairing을 수행한다.

    bc_reply 장비는 BC 응답 뒤 SELECT OFF가 handshake의 끝이고 GO는 나중에
    올라오는 상태 신호다. go 장비만 GO가 켜질 때까지 기존 재시도를 수행한다.
    """
    if link["pair_confirmation"] == "bc_reply":
        reply = await pio_pair(
            adapter,
            media=link["media"], station_id=link["station_id"],
            channel=link["channel"], bc_port=link["bc_port"],
            oht_number=link["oht_number"], select_pin=link["select_pin"],
            settle_sec=link["settle_sec"],
            select_off_delay_sec=link["select_off_delay_sec"],
            wait_sec=wait_sec,
        )
        return bool(find_pio_frame(reply)), reply, 1
    return await pio_pair_until_go(
        adapter, link=link, wait_sec=wait_sec, pair_timeout_sec=pair_timeout_sec
    )


def require_ezi_io(adapter: Any, action_type: str, select_pin: int) -> None:
    """SELECT를 걸 수 없으면 조용히 실패하지 않고 이유를 말한다."""
    if adapter._ezi_io is None:
        raise ValueError(
            f"EZI IO is not initialized, so the SELECT pin (out{select_pin}) "
            f"cannot gate the PIO board for {action_type}"
        )


async def call_pio(adapter: Any, method_name: str, *args: Any, **kwargs: Any) -> Any:
    """공용 PIO 클라이언트의 지정 메서드를 호출한다."""
    client = get_pio_client(adapter)
    method = getattr(client, method_name)
    return method(*args, **kwargs)


async def execute_pio_action(adapter: Any, action: Any) -> Dict[str, Any]:
    """PIO action을 분기 실행하고 성공·실패 결과 형식을 통일한다."""
    try:
        if action.action_type == "pioInit":
            return await pio_init(adapter, action)
        if action.action_type == "pioReadIn":
            inputs = await pio_read_inputs(adapter, action)
            return {
                "ok": True,
                "action": "pioReadIn",
                "message": f"pioReadIn finished: inputs={format_pio_inputs(inputs)}",
                "inputs": inputs,
                "steps": [],
            }
        if action.action_type == "pioWriteOut":
            params = action_params(action)
            index = resolve_pio_output_index(adapter, params)
            state = parse_pio_state(params.get("state"))
            written = await pio_write_output(adapter, index, state)
            return {
                "ok": True,
                "action": "pioWriteOut",
                "pin": written["pin"],
                "message": (
                    f"pioWriteOut finished: out{index}={state} "
                    f"(EZI IO out{written['pin']}, read back)"
                ),
                "steps": [],
            }
        if action.action_type == "pioDisconnect":
            return await pio_disconnect_action(adapter, action)
        if action.action_type == "pioScenario":
            return await execute_pio_scenario(adapter, action)
        if action.action_type == "pioPing":
            return await pio_ping(adapter, action)
        raise ValueError(f"Unsupported PIO action: {action.action_type}")
    except Exception as exc:
        adapter._note_pio(error=str(exc))
        return {
            "ok": False,
            "action": action.action_type,
            "failedReason": type(exc).__name__,
            "message": f"{action.action_type} failed: {exc}",
            "steps": [],
        }


async def pio_init(adapter: Any, action: Any) -> Dict[str, Any]:
    """설비와 pairing을 걸고 그대로 유지한다. 운영용 연결 action.

    SELECT를 올린 채 BC를 보내고, 설비가 GO를 올리면 성립이다. 끊기는
    pioDisconnect가 담당한다.
    """
    params = action_params(action)
    timeout_sec = float(
        params.get("timeoutSec", pio_num(adapter, "init_default_timeout_sec", 5.0))
    )
    pair_timeout_sec = float(
        params.get("pairTimeoutSec", pio_num(adapter, "pair_timeout_sec", 30.0))
    )
    link = pio_link_params(adapter, params)
    require_ezi_io(adapter, "pioInit", link["select_pin"])
    await ensure_pio_connected(adapter)

    paired, reply, attempts = await pio_establish(
        adapter, link=link, wait_sec=timeout_sec, pair_timeout_sec=pair_timeout_sec,
    )
    framed = find_pio_frame(reply)
    if not reply.strip():
        raise ValueError(
            f"BC response timeout; media={link['media']} "
            f"stationId={link['station_id']} channel={link['channel']} "
            f"port={link['bc_port']} ohtNumber={link['oht_number']}"
        )
    # 체크섬이 맞아야 PIO 보드다. 구분자만 보면 엉뚱한 포트의 잡음도 통과한다
    # (amr2 ttyUSB4 오설정 사례). 보드는 [payload+cs]로 답한다.
    if framed is None:
        raise ValueError(
            f"BC reply has no valid PIO frame: {reply!r}; check that "
            f"pio_serial_port ({link['serial_port']}) is the PIO converter "
            f"(dmesg | grep pl2303) and that pio_baudrate matches the board"
            f"{describe_serial_port_candidates()}"
        )
    if not paired:
        raise ValueError(
            f"BC was answered on {link['serial_port']} but the GO input "
            f"in{link['go_pin']} stayed off after {attempts} BC attempts over "
            f"{pair_timeout_sec:.1f}s (facility did not pair); check stationId="
            f"{link['station_id']} channel={link['channel']} and the GO wiring"
        )
    adapter._note_pio(connected=True, error="")
    confirmation = link["pair_confirmation"]
    confirmed_by = (
        f"valid BC reply={framed}"
        if confirmation == "bc_reply"
        else f"GO input in{link['go_pin']} on; reply={framed}"
    )
    return {
        "ok": True,
        "action": "pioInit",
        "port": link["serial_port"],
        "selectPin": link["select_pin"],
        "goPin": link["go_pin"],
        "bcResponse": reply,
        "bcReplyFramed": framed,
        "bcAttempts": attempts,
        "pairConfirmation": confirmation,
        "message": (
            f"pioInit finished: paired on {link['serial_port']} after "
            f"{attempts} BC attempt(s) ({confirmed_by})"
        ),
        "steps": [],
    }


async def pio_ping(adapter: Any, action: Any) -> Dict[str, Any]:
    """연결 → 출력 1~8 하나씩 껐다 켬 → 해제까지 한 번에 돌리는 점검 action.

    pioInit과 같은 절차로 pairing을 걸고, PIO 출력 8점을 순서대로 on/off 시킨
    뒤, pioDisconnect와 같은 절차로 unpair하고 포트를 닫는다. 끝나면 시작 전
    상태로 돌아가므로 흔적이 남지 않는다.

    출력 전체 정리(handle_initial의 reset_mask)는 하지 않는다. 점검 때문에 문/
    층 요청 같은 다른 출력을 떨어뜨릴 수는 없다.
    """
    params = action_params(action)
    timeout_sec = float(
        params.get("timeoutSec", pio_num(adapter, "ping_default_timeout_sec", 5.0))
    )
    hold_sec = pio_num(adapter, "ping_output_hold_sec", 0.2)
    pair_timeout_sec = float(
        params.get("pairTimeoutSec", pio_num(adapter, "pair_timeout_sec", 30.0))
    )
    link = pio_link_params(adapter, params)
    select_pin, go_pin = link["select_pin"], link["go_pin"]
    serial_port = link["serial_port"]

    steps: list = []
    base: Dict[str, Any] = {
        "action": "pioPing",
        "port": serial_port,
        "baudrate": getattr(adapter.config.pio_config, "pio_baudrate", None),
        "selectPin": select_pin,
        "goPin": go_pin,
        "steps": steps,
    }
    step_no = 0

    def note(kind: str, ok: bool, message: str) -> None:
        nonlocal step_no
        step_no += 1
        steps.append({"step": step_no, "type": kind, "ok": ok, "message": message})

    def failure(reason: str, message: str, **extra: Any) -> Dict[str, Any]:
        adapter._note_pio(connected=False, error=message)
        return {**base, "ok": False, "failedReason": reason, "message": message, **extra}

    if adapter._ezi_io is None:
        note("select", False, "no EZI IO")
        return failure(
            "ValueError",
            f"pioPing failed: EZI IO is not initialized, so the SELECT pin "
            f"(out{select_pin}) cannot gate the PIO board",
            opened=False,
        )

    # 우리가 연 포트만 우리가 닫는다. 이미 열려 있었다면 남의 연결이다.
    client = get_pio_client(adapter)
    was_open = bool(getattr(getattr(client, "ser", None), "is_open", False))
    try:
        await ensure_pio_connected(adapter)
    except Exception as exc:
        note("open", False, str(exc))
        return failure(
            type(exc).__name__,
            f"pioPing failed: cannot open {serial_port}: {exc}",
            opened=False,
        )
    note("open", True, f"{serial_port} open")

    paired, reply, attempts = await pio_establish(
        adapter, link=link, wait_sec=timeout_sec, pair_timeout_sec=pair_timeout_sec,
    )
    framed = find_pio_frame(reply)
    note("bc", True, f"{attempts} BC attempt(s), reply {len(reply)} bytes framed={framed}")
    reply_info: Dict[str, Any] = {
        "opened": True,
        "bcReply": reply,
        "bcReplyBytes": len(reply),
        "bcReplyFramed": framed,
        "bcAttempts": attempts,
    }

    if not paired:
        note("go", False, f"in{go_pin} stayed off")
        await pio_unpair(adapter, select_pin=select_pin, settle_sec=link["settle_sec"])
        if not was_open:
            await pio_disconnect(adapter)
        return failure(
            "not paired",
            f"pioPing failed: {attempts} BC attempt(s) on {serial_port} with SELECT "
            f"out{select_pin}, but the GO input in{go_pin} stayed off over "
            f"{pair_timeout_sec:.1f}s (facility did not pair)",
            **reply_info,
        )
    if link["pair_confirmation"] == "go":
        note("go", True, f"in{go_pin}=on")
    else:
        note("pair", True, "valid BC reply; GO is not required for connection")

    # 출력 점검. 실패해도 반드시 unpair까지 가도록 finally로 감싼다.
    outputs: list = []
    try:
        for index in range(1, 9):
            # 한 점이 실패해도 나머지를 마저 훑는다. 어느 핀이 문제인지가
            # 점검의 목적이라 첫 실패에서 멈추면 쓸모가 없다.
            entry: Dict[str, Any] = {"index": index, "ok": True}
            try:
                entry["pin"] = pio_output_pin(adapter, index)
                for state in ("on", "off"):
                    await pio_write_output(adapter, index, state)
                    await asyncio.sleep(hold_sec)
            except Exception as exc:
                entry["ok"] = False
                entry["error"] = str(exc)
            outputs.append(entry)
        good = sum(1 for o in outputs if o["ok"])
        note("outputs", good == len(outputs), f"out1-8 toggled on/off; {good}/8 ok")
    finally:
        await pio_unpair(adapter, select_pin=select_pin, settle_sec=link["settle_sec"])

    released = await pio_wait_go(
        adapter, go_pin=go_pin, want=False,
        timeout_sec=timeout_sec, poll_sec=link["poll_sec"],
    )
    note("unpair", released, f"in{go_pin}={'off' if released else 'stayed on'}")

    if not was_open:
        await pio_disconnect(adapter)
        note("close", True, f"{serial_port} closed")
    else:
        note("close", True, f"{serial_port} left open (was open before)")

    reply_info["outputs"] = outputs
    if not released:
        return failure(
            "still paired",
            f"pioPing failed: outputs were swept but the GO input in{go_pin} "
            f"stayed on for {timeout_sec:.1f}s after unpair (facility still paired)",
            **reply_info,
        )

    adapter._note_pio(connected=False, error="")
    good = sum(1 for o in outputs if o["ok"])
    return {
        **base,
        **reply_info,
        "ok": True,
        "message": (
            f"pioPing finished: paired on {serial_port}, swept out1-8 via EZI IO "
            f"({good}/8 ok), unpaired (GO in{go_pin} off)"
        ),
    }


async def pio_unpair(adapter: Any, *, select_pin: int, settle_sec: float) -> None:
    """BC 없이 SELECT만 토글해 설비와의 pairing을 푼다.

    airshower/elevator의 handle_unpairing과 같은 절차다. 시리얼 포트를 닫는
    것만으로는 설비가 pairing을 유지해 GO 입력이 계속 올라와 있다.
    """
    await adapter._ezi_io.turn_on_output(select_pin)
    await asyncio.sleep(settle_sec)
    await adapter._ezi_io.turn_off_output(select_pin)
    await asyncio.sleep(settle_sec)


async def pio_disconnect(adapter: Any) -> None:
    """시리얼 포트만 닫는다. 설비 pairing은 건드리지 않는다.

    pioScenario의 뒷정리처럼 포트만 놓으면 되는 자리에서 쓴다. 운영자가 누르는
    끊기는 pio_disconnect_action이 담당한다.
    """
    client = get_pio_client(adapter)
    close = getattr(client, "close", None)
    if callable(close):
        close()
    adapter._note_pio(connected=False)


async def pio_release(adapter: Any, params: Dict[str, Any]) -> None:
    """설비 pairing을 풀고 포트를 닫는다. 어떤 경우에도 raise하지 않는다.

    pioScenario의 자동 뒷정리다. pair가 기본 true라 scenario는 스스로 pairing을
    거는데, 끝낼 때 포트만 닫으면 설비는 계속 물려 있고 GO 입력도 올라와 있다
    (pio_unpair 참고). 자동으로 걸었으면 자동으로 풀어야 대칭이다.

    finally에서 불리므로 여기서 예외가 새어 나가면 본문의 성공/실패가 통째로
    가려진다. 그래서 실패를 밖으로 던지지 않되 조용히 넘기지도 않는다 — 로그와
    _note_pio(error=)로 남기고, unpair가 실패해도 포트는 그래도 닫는다.
    """
    def _report(message: str) -> None:
        print(f"[PIO SCENARIO RELEASE] {message}")
        adapter._note_pio(error=message)

    try:
        if adapter._ezi_io is None:
            # 포트만 닫으면 설비는 계속 물려 있다. 끊은 척하지 않는다.
            _report(
                "EZI IO is not initialized, so the SELECT pin could not unpair "
                "the facility; the serial port is closed but the link may stay up"
            )
        else:
            ezi_cfg = adapter.config.ezi_config
            await pio_unpair(
                adapter,
                select_pin=int(params.get("selectPin", ezi_cfg.select)),
                settle_sec=pio_num(adapter, "select_settle_sec", 0.2),
            )
    except Exception as exc:
        _report(f"could not unpair the facility: {exc}")

    try:
        await pio_disconnect(adapter)
    except Exception as exc:
        _report(f"could not close the serial port: {exc}")


async def pio_disconnect_action(adapter: Any, action: Any) -> Dict[str, Any]:
    """설비 pairing을 풀고 GO가 내려가는지 확인한 뒤 포트를 닫는다."""
    params = action_params(action)
    ezi_cfg = adapter.config.ezi_config
    select_pin = int(params.get("selectPin", ezi_cfg.select))
    go_pin = int(params.get("goPin", ezi_cfg.go))
    timeout_sec = float(
        params.get("timeoutSec", pio_num(adapter, "unpair_default_timeout_sec", 5.0))
    )
    settle_sec = pio_num(adapter, "select_settle_sec", 0.2)
    poll_sec = pio_num(adapter, "call_poll_interval_sec", 0.05)
    clear_outputs = parse_pio_flag(params.get("clearOutputs"), default=False)
    serial_port = getattr(adapter.config.pio_config, "pio_serial_port", None)

    steps: list = []
    base: Dict[str, Any] = {
        "action": "pioDisconnect",
        "port": serial_port,
        "selectPin": select_pin,
        "goPin": go_pin,
        "steps": steps,
    }

    if adapter._ezi_io is None:
        # 포트만 닫으면 설비는 계속 물려 있다. 끊은 척하지 않는다.
        await pio_disconnect(adapter)
        steps.append({"step": 1, "type": "unpair", "ok": False, "message": "no EZI IO"})
        message = (
            f"pioDisconnect failed: {serial_port} closed, but EZI IO is not "
            f"initialized so the SELECT pin (out{select_pin}) could not unpair "
            "the facility"
        )
        adapter._note_pio(error=message)
        return {**base, "ok": False, "unpaired": False, "failedReason": "ValueError",
                "message": message}

    await pio_unpair(adapter, select_pin=select_pin, settle_sec=settle_sec)
    steps.append(
        {"step": 1, "type": "unpair", "ok": True, "message": f"out{select_pin} on→off"}
    )

    deadline = time.monotonic() + max(0.0, timeout_sec)
    still_paired = True
    while True:
        still_paired = bool(await adapter._ezi_io.get_input_pin(go_pin))
        if not still_paired or time.monotonic() >= deadline:
            break
        await asyncio.sleep(poll_sec)

    if clear_outputs:
        # workflow의 뒷정리와 같다. 문·층 요청까지 같이 떨어지므로 opt-in이다.
        await adapter._ezi_io.set_output(reset_mask=0xFFFF << 16)
        await asyncio.sleep(settle_sec)
        steps.append(
            {"step": 2, "type": "clear", "ok": True, "message": "all outputs reset"}
        )

    await pio_disconnect(adapter)
    steps.append(
        {"step": 3, "type": "close", "ok": True, "message": f"{serial_port} closed"}
    )

    if still_paired:
        message = (
            f"pioDisconnect failed: SELECT out{select_pin} was toggled and "
            f"{serial_port} closed, but the GO input in{go_pin} stayed on for "
            f"{timeout_sec:.1f}s (facility still paired)"
        )
        adapter._note_pio(error=message)
        return {**base, "ok": False, "unpaired": False, "failedReason": "still paired",
                "message": message}

    adapter._note_pio(connected=False, error="")
    return {
        **base,
        "ok": True,
        "unpaired": True,
        "message": (
            f"pioDisconnect finished: unpaired (GO input in{go_pin} went off) "
            f"and {serial_port} closed"
        ),
    }


async def pio_read_inputs(adapter: Any, action: Any) -> Dict[str, str]:
    """PIO 입력 8점을 EZI IO에서 읽어 on/off로 정규화한다.

    출력과 같은 이유로 직렬이 아니다. 설비가 보내오는 신호선은 EZI IO digital
    input에 걸린다 — elevator.py가 floor를 `get_input_pin`으로 확인하고,
    GO도 입력 15번이다. 예전에 쓰던 `D={channel}`은 응답이 온 적이 없다.
    """
    from extensions.ezio import read_ezio_input_bits

    bits = await read_ezio_input_bits(adapter)
    inputs = map_pio_inputs(adapter, bits)
    adapter._note_pio(inputs=inputs, error="")
    return inputs


def map_pio_inputs(adapter: Any, bits: Any, *, strict: bool = True) -> Dict[str, str]:
    """EZI IO 입력 비트를 PIO in 1~8의 on/off로 옮긴다.

    strict면 설정된 핀이 읽어 온 비트 범위 밖일 때 실패한다 — pioReadIn은 빠진
    점을 조용히 만들면 안 된다. 표시용 주기 갱신은 strict=False로 부르고 범위를
    벗어난 점만 건너뛴다. 한 점의 설정 실수 때문에 나머지 7점까지 멈추면 안 된다.
    """
    pins = list(getattr(adapter.config.pio_config, "input_pins", None) or range(8))
    inputs: Dict[str, str] = {}
    for index, pin in enumerate(pins, start=1):
        if not 0 <= int(pin) < len(bits):
            if strict:
                raise ValueError(
                    f"PIO input {index} maps to EZI IO in{pin}, "
                    f"but only {len(bits)} input bits were read"
                )
            continue
        inputs[str(index)] = "on" if bits[int(pin)] else "off"
    return inputs


def map_pio_outputs(adapter: Any, bits: Any, *, strict: bool = True) -> Dict[str, str]:
    """EZI IO 출력 레지스터 비트를 PIO out 1~8의 on/off로 옮긴다.

    쓰기가 아니라 되읽기다 — pio_write_output이 쓴 뒤에 하는 확인과 같은 값을
    본다. strict=False면 output_pin_map에 없는 out 번호는 건너뛴다.
    """
    outputs: Dict[str, str] = {}
    for index in range(1, 9):
        try:
            pin = pio_output_pin(adapter, index)
        except ValueError:
            if strict:
                raise
            continue
        if not 0 <= pin < len(bits):
            if strict:
                raise ValueError(
                    f"PIO output {index} maps to EZI IO out{pin}, "
                    f"but only {len(bits)} output bits were read"
                )
            continue
        outputs[str(index)] = "on" if bits[pin] else "off"
    return outputs


def pio_output_pin(adapter: Any, index: int) -> int:
    """PIO out 번호(1~8)를 실제 EZI IO digital output 번호로 바꾼다.

    output_pin_map이 있으면 그 짝을 그대로 쓰고, 없으면 예전 output_pins 리스트를
    위치로 읽는다(i번째 = out i+1). 리스트는 한 칸 밀려도 알 수 없어 맵을 권한다.
    """
    # PioConfig.__post_init__이 진짜 dict로 정규화한다. dict가 아니면 그 config는
    # 맵을 선언한 적이 없는 것이므로(손으로 만든 stub 등) 리스트 쪽으로 간다.
    pin_map = getattr(adapter.config.pio_config, "output_pin_map", None)
    if isinstance(pin_map, dict) and pin_map:
        if index not in pin_map:
            declared = ", ".join(str(key) for key in sorted(pin_map))
            raise ValueError(
                f"PIO output {index} has no EZI IO pin; "
                f'extension "pio"의 output_pin_map에 선언된 out은 {declared}이다'
            )
        return int(pin_map[index])

    pins = list(getattr(adapter.config.pio_config, "output_pins", None) or range(8))
    if not 1 <= index <= len(pins):
        raise ValueError(
            f"PIO output {index} has no EZI IO pin; "
            f"extension \"pio\"의 output_pins는 {len(pins)}개다"
        )
    return int(pins[index - 1])


def pio_signal_index(adapter: Any, name: Any) -> int:
    """설비 신호 이름을 PIO out 번호로 바꾼다."""
    return _pio_named_index(adapter, name, "output_signals")


def pio_input_signal_index(adapter: Any, name: Any) -> int:
    """설비 신호 이름을 PIO in 번호로 바꾼다.

    출력과 맵을 나눠 둔다. 같은 번호가 입력·출력 레지스터에 각각 있고 뜻이 달라
    (out2 = 4L 문 열기 요청, in2 = 4L 문 열림 확인) 한 맵으로 합치면 방향이
    섞인 채로도 그럴듯한 번호가 나온다.
    """
    return _pio_named_index(adapter, name, "input_signals")


def _pio_named_index(adapter: Any, name: Any, attribute: str) -> int:
    signals = getattr(adapter.config.pio_config, attribute, None) or {}
    key = str(name).strip()
    if key not in signals:
        declared = ", ".join(sorted(signals)) or "(없음)"
        raise ValueError(
            f'PIO signal "{key}" is not declared; '
            f'extension "pio"의 {attribute}에 있는 이름은 {declared}이다'
        )
    return int(signals[key])


def resolve_pio_output_index(adapter: Any, params: Dict[str, Any]) -> int:
    """action 파라미터의 signal 또는 index를 PIO out 번호로 정리한다.

    signal은 이름 -> out 번호 -> EZI IO 핀 두 단계를 config에서 풀기 때문에,
    핀이 바뀌어도 recipes.hcl은 그대로 둘 수 있다. index는 예전 호출(WebUI의
    직접 실행 등)을 위해 계속 받는다. 둘 다 오면 어느 쪽을 믿어야 할지 알 수
    없으므로 거절한다 — 조용히 하나를 고르면 다른 점을 치고도 성공으로 보인다.
    """
    return _resolve_pio_index(adapter, params, pio_signal_index, "output")


def resolve_pio_input_index(adapter: Any, params: Dict[str, Any]) -> int:
    """action 파라미터의 signal 또는 index를 PIO in 번호로 정리한다.

    출력의 resolve_pio_output_index와 같은 규칙이다. 입력에도 이름을 두는 이유는
    같다 — 문 열림 확인 핀이 바뀌어도 recipes.hcl은 그대로 둔다.
    """
    return _resolve_pio_index(adapter, params, pio_input_signal_index, "input")


def _resolve_pio_index(
    adapter: Any,
    params: Dict[str, Any],
    lookup: Any,
    direction: str,
) -> int:
    signal = params.get("signal")
    index = params.get("index")
    has_signal = signal is not None and str(signal).strip() != ""
    has_index = index is not None and str(index).strip() != ""

    if has_signal and has_index:
        raise ValueError(
            f"PIO {direction} takes either signal or index, not both"
        )
    if has_signal:
        return lookup(adapter, signal)
    if has_index:
        return parse_pio_index(index)
    raise ValueError(f"PIO {direction} requires signal or index")


async def pio_write_output(
    adapter: Any,
    index: int,
    state: str,
    wait_sec: Optional[float] = None,
) -> Any:
    """출력 한 점을 EZI IO로 내보내고 어댑터가 보관하는 출력 상태를 갱신한다.

    설비로 나가는 신호선은 직렬이 아니라 EZI IO가 구동한다 — elevator.py의
    문/층 핀, airshower.py의 문 핀 모두 ezi_io.turn_on_output()으로 친다.
    직렬은 BC pairing 전용이다 (recipes.hcl 주석도 같은 이야기를 한다).
    예전에 보내던 `OUT=n:v`는 보드 명령이 아니어서 아무 일도 일어나지 않았다
    (최초 구현 utils/cls_pio.py의 명령은 BC=/C=/D= 뿐이다).
    """
    if adapter._ezi_io is None:
        raise ValueError(
            f"EZI IO is not initialized, so PIO output {index} cannot be driven"
        )
    pin = pio_output_pin(adapter, index)
    if state == "on":
        await adapter._ezi_io.turn_on_output(pin)
    else:
        await adapter._ezi_io.turn_off_output(pin)

    merged = dict(adapter._pio_outputs or {})
    merged[str(index)] = state
    adapter._note_pio(outputs=merged)
    # 읽어서 확인한다. 쓰기 성공만 믿으면 비트 맵이 어긋나도 모른다.
    resp = await adapter._ezi_io.get_output()
    if resp and "outputs" in resp:
        outputs = list(resp["outputs"])
        adapter._note_ezio(outputs=outputs)
        if 0 <= pin < len(outputs):
            actual = "on" if int(outputs[pin]) else "off"
            if actual != state:
                raise ValueError(
                    f"PIO output {index} (EZI IO out{pin}) read back {actual} "
                    f"after writing {state}"
                )
    return {"index": index, "pin": pin, "state": state}


async def pio_blink_output(
    adapter: Any,
    action: Any,
    *,
    output_index: int,
    on_sec: float,
    off_sec: float,
    until: Optional[Tuple[int, str]],
    count: Optional[int],
    timeout_sec: float,
    min_cycles: int = 1,
) -> Tuple[bool, Dict[str, Any]]:
    """출력 한 점을 껐다 켰다 하며 종료 조건이 설 때까지 유지한다.

    설비가 "열림 요청"을 레벨이 아니라 반복 신호로 읽는 경우(에어샤워 문을 지나는
    동안 계속 눌러 줘야 하는 배선)를 위한 단계다. recipe 문법에는 반복이 없고
    step은 직렬이라, 주행과 겹쳐 돌리려면 이 반복이 액션 하나 안에 들어가야 한다
    — recipe를 blockingType NONE으로 걸어 두면 주행이 도는 동안 이 액션이 배경에서
    돈다 (adapter_jibot.py의 _execute_order_action_sequence).

    종료 조건은 둘 다 선택이지만 최소 하나는 있어야 한다. 없으면 끝나지 않는
    액션이 되고, 뒤따르는 HARD 액션이 이 배경 task를 기다리다 같이 멈춘다.
      until  (입력 번호, 기대 상태) 이 상태가 되면 그 자리에서 멈춘다.
      count  이 횟수만큼 깜박이면 멈춘다. until의 상한으로도 쓴다.
    timeout_sec를 넘기면 실패다 — 조건이 안 서는 것은 설비가 응답하지 않는다는
    뜻이므로 조용히 성공으로 끝내지 않는다.

    min_cycles는 조건을 보기 전에 반드시 채워야 할 사이클 수다(기본 1). 종료
    조건이 처음부터 참이면 예전에는 한 번도 안 켜고 성공으로 끝났다 — 설비는
    반복 신호를 요청으로 읽으므로 그것은 문을 전혀 잡지 않은 것인데 결과만
    FINISHED다. 첫 사이클은 조건과 무관하게 온전히 돌린다. 조건 핀을 믿을 수
    없는 동안 문을 더 오래 잡아 둘 손잡이이기도 하다 — 다만 실제 값은 통과에
    걸리는 시간을 재고 정해야 한다.

    "처음부터 참이었다"는 conditionTrueAtStart로 따로 남긴다. 깜박이다 조건이
    선 것과 같은 문자열로 적으면 until 핀이 엉뚱하다는 사실이 로그에서 지워진다.

    끝날 때는 항상 출력을 off로 둔다. 취소되어도 마찬가지다(호출한 recipe의
    cleanup이 한 번 더 내리지만, 여기서 먼저 내려야 취소 경로에서 켜진 채로
    남지 않는다).
    """
    if until is None and count is None:
        raise ValueError("blink requires until (untilIndex/untilState) or count")
    if min_cycles < 0:
        raise ValueError("blink minCycles must be zero or greater")
    poll_sec = pio_num(adapter, "call_poll_interval_sec", 0.05)
    deadline = time.monotonic() + max(0.0, timeout_sec)
    latest_inputs: Dict[str, str] = {}
    cycles = 0

    async def _reached() -> bool:
        """종료 입력이 기대 상태인지 확인한다. until이 없으면 언제나 거짓."""
        nonlocal latest_inputs
        if until is None:
            return False
        latest_inputs = await pio_read_inputs(adapter, action)
        return latest_inputs.get(str(until[0])) == until[1]

    async def _hold(state: str, duration: float, *, interruptible: bool) -> bool:
        """출력을 state로 두고 유지하되 종료 조건이 서면 즉시 참을 돌려준다.

        interruptible이 거짓이면 조건을 보지 않고 duration을 채운다 — 채워야 할
        사이클을 조건이 중간에 자르면 pulse 폭이 0에 가까워져 설비가 요청으로
        읽지 못한다. deadline은 그래도 지킨다.
        """
        await pio_write_output(adapter, output_index, state)
        end = time.monotonic() + max(0.0, duration)
        if not interruptible:
            remaining = min(end, deadline) - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(remaining)
            return False
        while True:
            if await _reached():
                return True
            now = time.monotonic()
            if now >= end or now >= deadline:
                return False
            await asyncio.sleep(min(poll_sec, end - now, deadline - now))

    # 기록용으로만 한 번 본다. 여기서 돌려보내면 한 번도 안 켜고 끝나 버린다.
    condition_true_at_start = await _reached()

    def _done(stopped_by: str, done_cycles: int) -> Tuple[bool, Dict[str, Any]]:
        return True, {
            "cycles": done_cycles,
            "stoppedBy": stopped_by,
            "conditionTrueAtStart": condition_true_at_start,
            "inputs": latest_inputs,
        }

    try:
        while True:
            if cycles >= min_cycles:
                if await _reached():
                    return _done("condition", cycles)
                if count is not None and cycles >= count:
                    return _done("count", cycles)
            if time.monotonic() >= deadline:
                return False, {
                    "cycles": cycles,
                    "stoppedBy": "timeout",
                    "conditionTrueAtStart": condition_true_at_start,
                    "inputs": latest_inputs,
                }
            # 채워야 할 사이클 안에서는 조건이 서도 자르지 않는다.
            interruptible = cycles >= min_cycles
            if await _hold("on", on_sec, interruptible=interruptible) or await _hold(
                "off", off_sec, interruptible=interruptible
            ):
                return _done("condition", cycles + 1)
            cycles += 1
    finally:
        await pio_write_output(adapter, output_index, "off")


def parse_blink_until(step: Dict[str, Any]) -> Optional[Tuple[int, str]]:
    """blink 단계의 종료 입력 조건을 (입력 번호, 기대 상태)로 정리한다."""
    until_index = step.get("untilIndex")
    if until_index is None or str(until_index).strip() == "":
        return None
    return (
        parse_pio_index(until_index),
        parse_pio_state(step.get("untilState", "on")),
    )


def parse_blink_min_cycles(step: Dict[str, Any]) -> int:
    """조건을 보기 전에 반드시 채울 사이클 수. 없으면 1."""
    value = step.get("minCycles")
    if value is None or str(value).strip() == "":
        return 1
    cycles = int(value)
    if cycles < 0:
        raise ValueError("blink minCycles must be zero or greater")
    return cycles


def parse_blink_count(step: Dict[str, Any]) -> Optional[int]:
    """blink 단계의 반복 횟수 상한을 정리한다. 없으면 None."""
    count = step.get("count")
    if count is None or str(count).strip() == "":
        return None
    value = int(count)
    if value <= 0:
        raise ValueError("blink count must be greater than zero")
    return value


async def choose_pio_branch(
    adapter: Any,
    action: Any,
    step: Dict[str, Any],
    idx: int,
) -> Tuple[list, str, int, Dict[str, str]]:
    """if 단계의 조건 입력을 **한 번** 읽고 실행할 갈래를 고른다.

    갈래마다 따로 읽으면 그 사이 설비 상태가 바뀌었을 때 어느 쪽도 실행되지
    않는다(엘리베이터 카가 내려오는 중이면 "위에 있음"도 "여기 있음"도 거짓이
    되어, 문을 전혀 열지 않은 채 열림 대기로 넘어간다). 그래서 한 번 읽은 값으로
    then/else를 가른다.

    읽기가 실패하면 여기서 예외가 나가 recipe가 FAILED로 끝난다. 못 읽은 것을
    "off"로 해석하면 카가 상층에 있는데 1층 문 열기 갈래를 타서 빈 승강로 문을
    연다 — 이 갈래의 안전은 전적으로 이 지점에 달려 있다.

    갈래 이름이 else가 아니라 otherwise인 이유: else는 HCL2 문법의 예약 토큰이라
    따옴표 없이 키로 쓰면 파싱이 그 자리에서 깨진다.

    중첩은 한 단만 허용한다. 갈래 안의 if까지 받으면 HCL에서 눈으로 따라갈 수
    없어지므로 부팅이 아니라 실행 시점에라도 그 자리에서 막는다.
    """
    input_index = resolve_pio_input_index(adapter, step)
    expected_state = parse_pio_state(step.get("state", "on"))
    inputs = await pio_read_inputs(adapter, action)
    actual = inputs.get(str(input_index))
    if actual is None:
        raise ValueError(
            f"step {idx}: if condition input in{input_index} was not reported "
            f"by the facility (inputs={format_pio_inputs(inputs)})"
        )
    taken = "then" if actual == expected_state else "otherwise"
    branch = step.get(taken) or []
    if not isinstance(branch, list):
        raise ValueError(f"step {idx}: if '{taken}' must be a list")
    for item in branch:
        if not isinstance(item, dict):
            raise ValueError(f"step {idx}: if '{taken}' items must be objects")
        if item.get("type") == "if":
            raise ValueError(f"step {idx}: nested if is not allowed")
    return list(branch), taken, input_index, inputs


async def execute_pio_scenario(adapter: Any, action: Any) -> Dict[str, Any]:
    """PIO 연결 후 출력·입력 대기 단계로 구성된 scenario를 순서대로 실행한다."""
    params = action_params(action)
    scenario = params.get("scenario")
    if not isinstance(scenario, list):
        raise ValueError("pioScenario requires scenario list")

    # 연결과 해제는 둘 다 기본으로 켜져 있고 서로 대칭이다: pair가 pairing을 걸면
    # disconnect가 pio_release로 그것을 푼다(SELECT 토글 unpair + 포트 close).
    # 포트만 닫는 것은 해제가 아니다 — 설비는 계속 물려 있고 GO도 올라와 있다.
    #
    # 통과 절차를 recipe 여러 개로 쪼갤 때만 둘 다 끈다. 두 번째부터는 이미 붙어
    # 있으므로 pair = false로 건너뛰고(다시 걸면 SELECT를 올렸다 내리고 BC를 새로
    # 보내는 데 0.7~2.7초가 들어 그동안 blink 주기가 끊긴다), 중간 recipe는
    # disconnect = false로 링크를 남겨 다음 recipe에 넘긴다. 중간에 풀면 다음
    # recipe가 허공에 출력한다. out/in/blink/delay는 EZI IO만 쓰므로 직렬 연결
    # 없이도 돌기 때문에, 그 고장은 실패로 보이지 않는다.
    pair = parse_pio_flag(params.get("pair"), default=True)
    init_result = await pio_init(adapter, action) if pair else None
    default_timeout = float(
        params.get("timeoutSec", pio_num(adapter, "scenario_default_timeout_sec", 2.0))
    )
    steps = []
    latest_inputs: Dict[str, str] = {}
    disconnect = parse_pio_flag(params.get("disconnect"), default=True)
    # 남은 단계를 앞에서부터 꺼내 쓴다. if 단계가 고른 갈래를 이 목록 맨 앞에
    # 끼워 넣는 방식으로 분기를 처리하므로 enumerate로 고정할 수 없다.
    pending = list(scenario)
    idx = 0
    try:
        while pending:
            step = pending.pop(0)
            idx += 1
            if not isinstance(step, dict):
                raise ValueError(f"step {idx} must be an object")
            step_type = step.get("type")
            if step_type == "if":
                branch, taken, input_index, latest_inputs = await choose_pio_branch(
                    adapter, action, step, idx
                )
                # 어느 갈래를 탔는지 반드시 남긴다. 갈래를 잘못 타도 결과는 성공이라
                # 이 표시가 없으면 조건 핀이 엉뚱하다는 사실을 사람이 잡을 수 없다.
                steps.append(
                    {
                        "step": idx,
                        "type": "if",
                        "ok": True,
                        "branch": taken,
                        "message": (
                            f"in{input_index}={latest_inputs.get(str(input_index))} "
                            f"-> {taken} ({len(branch)} step(s))"
                        ),
                    }
                )
                pending[:0] = branch
                continue
            if step_type == "out":
                output_index = resolve_pio_output_index(adapter, step)
                state = parse_pio_state(step.get("state"))
                await pio_write_output(adapter, output_index, state)
                steps.append(
                    {
                        "step": idx,
                        "type": "out",
                        "ok": True,
                        "message": f"out{output_index}={state}",
                    }
                )
                continue
            if step_type == "in":
                input_index = resolve_pio_input_index(adapter, step)
                expected_state = parse_pio_state(step.get("state"))
                timeout = float(step.get("timeoutSec", default_timeout))
                ok, latest_inputs = await wait_pio_input(
                    adapter,
                    action,
                    input_index,
                    expected_state,
                    timeout,
                )
                if not ok:
                    message = (
                        f"expected in{input_index}={expected_state} "
                        f"within {timeout:.1f}s"
                    )
                    steps.append(
                        {
                            "step": idx,
                            "type": "in",
                            "ok": False,
                            "reason": "timeout",
                            "message": message,
                        }
                    )
                    return {
                        "ok": False,
                        "action": "pioScenario",
                        "failedStep": idx,
                        "failedReason": "timeout waiting input",
                        "message": message,
                        "inputs": latest_inputs,
                        "steps": steps,
                        "init": init_result,
                    }
                steps.append(
                    {
                        "step": idx,
                        "type": "in",
                        "ok": True,
                        "message": f"in{input_index}={expected_state}",
                    }
                )
                continue
            if step_type == "blink":
                output_index = resolve_pio_output_index(adapter, step)
                until = parse_blink_until(step)
                count = parse_blink_count(step)
                timeout = float(step.get("timeoutSec", default_timeout))
                ok, detail = await pio_blink_output(
                    adapter,
                    action,
                    output_index=output_index,
                    on_sec=float(step.get("onSec", 0.5)),
                    off_sec=float(step.get("offSec", 0.5)),
                    until=until,
                    count=count,
                    timeout_sec=timeout,
                    min_cycles=parse_blink_min_cycles(step),
                )
                if detail["inputs"]:
                    latest_inputs = detail["inputs"]
                stop = (
                    f"in{until[0]}={until[1]}" if until is not None
                    else f"{count} cycles"
                )
                if not ok:
                    message = (
                        f"out{output_index} blinked {detail['cycles']} time(s) but "
                        f"{stop} did not come within {timeout:.1f}s"
                    )
                    steps.append(
                        {
                            "step": idx,
                            "type": "blink",
                            "ok": False,
                            "reason": "timeout",
                            "message": message,
                        }
                    )
                    return {
                        "ok": False,
                        "action": "pioScenario",
                        "failedStep": idx,
                        "failedReason": "timeout while blinking",
                        "message": message,
                        "inputs": latest_inputs,
                        "steps": steps,
                        "init": init_result,
                    }
                steps.append(
                    {
                        "step": idx,
                        "type": "blink",
                        "ok": True,
                        # 어느 종료 조건이 걸렸는지 남긴다. until이 엉뚱한 입력을
                        # 가리키면 count 상한으로 끝나는데, 결과만 보면 둘 다
                        # 성공이라 이 표시가 없으면 구분할 수 없다.
                        # 조건이 처음부터 참이었으면 그 사실도 남긴다. 같은
                        # "stopped by condition"으로만 적으면 until 핀이 엉뚱한
                        # 곳을 가리킨다는 사실이 로그에서 지워진다.
                        "message": (
                            f"out{output_index} blinked {detail['cycles']} time(s), "
                            f"stopped by {detail['stoppedBy']} ({stop})"
                            + (
                                " [condition already true at start]"
                                if detail.get("conditionTrueAtStart")
                                else ""
                            )
                        ),
                    }
                )
                continue
            if step_type == "delay":
                seconds = float(step.get("sec", 0))
                if seconds < 0:
                    raise ValueError("delay sec must be >= 0")
                await asyncio.sleep(seconds)
                steps.append(
                    {
                        "step": idx,
                        "type": "delay",
                        "ok": True,
                        "message": f"delay={seconds}",
                    }
                )
                continue
            raise ValueError(f"Unsupported pioScenario step type at step {idx}: {step_type}")

        return {
            "ok": True,
            "action": "pioScenario",
            # 실행한 단계 수만 적는다. if가 고른 갈래가 붙었다 빠졌다 하므로
            # 선언된 scenario 길이와는 애초에 같을 수 없다.
            "message": (
                f"pioScenario finished: {len(steps)} steps ok; "
                f"inputs={format_pio_inputs(latest_inputs)}"
            ),
            "inputs": latest_inputs,
            "steps": steps,
            "init": init_result,
        }
    finally:
        if disconnect:
            await pio_release(adapter, params)


async def wait_pio_input(
    adapter: Any,
    action: Any,
    index: int,
    expected_state: str,
    timeout_sec: float,
) -> Tuple[bool, Dict[str, str]]:
    """제한 시간 동안 입력이 기대 상태가 될 때까지 반복 확인한다."""
    deadline = time.monotonic() + max(0.0, timeout_sec)
    latest_inputs: Dict[str, str] = {}
    while True:
        latest_inputs = await pio_read_inputs(adapter, action)
        if latest_inputs.get(str(index)) == expected_state:
            return True, latest_inputs
        if time.monotonic() >= deadline:
            return False, latest_inputs
        await asyncio.sleep(pio_num(adapter, "call_poll_interval_sec", 0.05))


def parse_pio_index(value: Any) -> int:
    """PIO 점 번호를 정수로 변환하고 허용 범위를 검증한다."""
    try:
        index = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("PIO index must be an integer from 1 to 8") from exc
    if index < 1 or index > 8:
        raise ValueError("PIO index must be from 1 to 8")
    return index


def parse_pio_state(value: Any) -> str:
    """여러 형태의 입출력 상태 값을 표준 on/off 문자열로 바꾼다."""
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "on"}:
        return "on"
    if normalized in {"0", "false", "off"}:
        return "off"
    raise ValueError("PIO state must be on or off")


def parse_pio_flag(value: Any, *, default: bool) -> bool:
    """비어 있으면 기본값, 아니면 on/off 표기를 bool로 바꾼다."""
    if value is None or str(value).strip() == "":
        return default
    return parse_pio_state(value) == "on"


def format_pio_inputs(inputs: Dict[str, str]) -> str:
    """입력 상태를 로그에 적합한 안정적인 순서의 문자열로 만든다."""
    if not inputs:
        return "{}"
    return "{" + ",".join(
        f"{index}:{inputs[str(index)]}"
        for index in range(1, 9)
        if str(index) in inputs
    ) + "}"


def summarize_pio_result(result: Dict[str, Any]) -> str:
    """PIO 결과에서 사용자에게 보여 줄 대표 메시지를 선택한다."""
    return str(result.get("message") or result)


async def handle_pio_action(ctx: Any) -> ActionResult:
    """PIO 실행 결과를 표준 ActionResult로 변환한다."""
    result = await execute_pio_action(ctx.adapter, ctx.action)
    status = ActionStatus.FINISHED if result["ok"] else ActionStatus.FAILED
    description = summarize_pio_result(result)
    print(f"[PIO ACTION RESULT] {json.dumps(result, ensure_ascii=False)}")
    return ActionResult(status, description)


def known_output_signals(config: Any) -> Tuple[str, ...]:
    """extension "pio"의 output_signals에 선언된 이름 목록.

    자유 입력이면 오타가 실행 시점에야 드러난다. 선언된 것만 고르게 한다.
    """
    signals = getattr(getattr(config, "pio_config", None), "output_signals", None)
    return tuple(sorted(signals)) if signals else ()


def known_input_signals(config: Any) -> Tuple[str, ...]:
    """extension "pio"의 input_signals에 선언된 이름 목록."""
    signals = getattr(getattr(config, "pio_config", None), "input_signals", None)
    return tuple(sorted(signals)) if signals else ()


def known_station_ids(config: Any) -> Tuple[str, ...]:
    """설정이 아는 설비 station 목록.

    station은 설비마다 다르다 — extension "airshower"의 pio_station_id와
    elevator motion rule의 pio_station_id다. 자유 입력이면 어긋난 값을 넣고도
    pairing 실패만 보게 되므로 목록으로 고르게 한다.

    facility extension을 import하지 않고 config에서 직접 읽는다. 그쪽을 꺼도
    PIO 패널은 그대로 떠야 한다.
    """
    ids: list = []
    air = str(
        getattr(getattr(config, "air_shower_config", None), "pio_station_id", "") or ""
    ).strip()
    if air:
        ids.append(air)
    rules = getattr(
        getattr(config, "elevator_config", None), "elevator_motion_rules", ()
    ) or ()
    for rule in rules:
        station = str(getattr(rule, "pio_station_id", "") or "").strip()
        if station and station not in ids:
            ids.append(station)
    return tuple(ids)


def action_specs(config: Any = None) -> Iterable[ActionSpec]:
    """PIO action별 handler와 UI 표시 정보를 등록한다.

    pioPing은 config를 받으면 station을 목록으로 노출한다. 다만 그 목록은
    힌트일 뿐이라 editable이다 — 아직 설정에 없는 설비를 시험할 때 직접 칠 수
    있어야 하고, 설정이 비어 목록이 없을 때 required select가 아무것도 고를 수
    없는 칸이 되는 것도 막는다. pioInit의 stationId는 실행 대상 숫자를 직접
    입력할 수 있게 number input으로 둔다.

    label은 WebUI가 칸 이름 옆에 붙이는 설명이다. 좁은 패널 카드에서 media나
    port가 무엇인지 이름만으로는 알 수 없어서, 칸마다 하나씩 달아 둔다.
    """
    stations = known_station_ids(config) if config is not None else ()
    parameters = {
        # BC 상대는 실행마다 달라 운영자가 넣는다. ohtNumber만 로봇 고유값이라
        # extensions.hcl의 vehicle_num에서 오고, 여기 칸을 두지 않는다.
        "pioInit": (
            ActionParameterSpec(
                "media", required=True, input_type="number", placeholder="예: 2",
                label="BC 매체 번호",
            ),
            ActionParameterSpec(
                "stationId", required=True, input_type="number",
                placeholder="예: 000030",
                label="설비 station",
            ),
            ActionParameterSpec(
                "channel", required=True, input_type="number", placeholder="예: 250",
                label="설비 channel",
            ),
            ActionParameterSpec(
                "port", required=True, input_type="number",
                placeholder="BC 포트 번호 (시리얼 포트 아님)",
                label="BC 포트",
            ),
            ActionParameterSpec(
                "timeoutSec", input_type="number", placeholder="비우면 2.0",
                label="제한시간(초)",
            ),
        ),
        # 입력은 EZI IO에서 바로 읽는다. 직렬 채널을 물을 이유가 없다.
        "pioReadIn": (),
        # signal 또는 index 중 하나. signal은 이름 -> out 번호 -> EZI IO 핀을
        # config가 풀어 주므로 핀이 바뀌어도 부르는 쪽은 그대로다.
        "pioWriteOut": (
            ActionParameterSpec(
                "signal", placeholder="설비 신호 이름 (index 대신)",
                choices=known_output_signals(config) if config is not None else (),
                label="설비 신호 이름",
            ),
            ActionParameterSpec(
                "index", input_type="number",
                placeholder="PIO out 1-8 (signal을 쓰면 비움)",
                label="out 핀 1-8",
            ),
            ActionParameterSpec(
                "state", required=True, placeholder="on 또는 off",
                choices=("on", "off"),
                label="on/off",
            ),
        ),
        "pioPing": (
            ActionParameterSpec(
                "media", input_type="number", placeholder="비우면 config 값",
                label="BC 매체 번호",
            ),
            ActionParameterSpec(
                "stationId", required=True,
                choices=stations,
                editable=True,
                placeholder="예: 000030 (목록에 없으면 직접 입력)",
                label="설비 station",
            ),
            ActionParameterSpec(
                "channel", required=True, input_type="number", placeholder="예: 250",
                label="설비 channel",
            ),
            ActionParameterSpec(
                "port", input_type="number",
                placeholder="BC 포트 번호 (시리얼 포트 아님)",
                label="BC 포트",
            ),
            ActionParameterSpec(
                "timeoutSec", input_type="number", placeholder="비우면 5.0",
                label="제한시간(초)",
            ),
        ),
        # 출력 전체 정리는 문·층 요청까지 떨어뜨리므로 기본은 끈다.
        "pioDisconnect": (
            ActionParameterSpec(
                "clearOutputs", placeholder="비우면 off", choices=("off", "on"),
                label="출력 초기화",
            ),
            ActionParameterSpec(
                "timeoutSec", input_type="number", placeholder="비우면 5.0",
                label="제한시간(초)",
            ),
        ),
        "pioScenario": (
            ActionParameterSpec(
                "stationId", required=True,
                choices=stations,
                editable=True,
                placeholder="예: 000030 (목록에 없으면 직접 입력)",
                label="설비 station",
            ),
            ActionParameterSpec(
                "channel", required=True, input_type="number", placeholder="예: 250",
                label="설비 channel",
            ),
            ActionParameterSpec(
                "scenario",
                required=True,
                placeholder='[{"type":"out","index":1,"state":"on"}]',
                value_type="json",
                label="스텝 JSON",
            ),
            # 통과 절차를 recipe 여러 개로 쪼갠 경우 첫 recipe만 pairing을 걸고
            # 나머지는 pair=off / disconnect=off로 그 연결을 이어 쓴다.
            ActionParameterSpec(
                "pair", placeholder="비우면 on(매번 pairing)",
                choices=("on", "off"),
                label="시작 시 pairing",
            ),
            ActionParameterSpec(
                "disconnect", placeholder="비우면 on(unpair 후 포트 닫기)",
                choices=("on", "off"),
                label="끝나고 해제 (SELECT 토글 unpair + 포트 close)",
            ),
        ),
    }
    return tuple(
        ActionSpec(
            action_type=action_type,
            handler=handle_pio_action,
            label=_PIO_LABELS.get(action_type, action_type),
            parameters=parameters.get(action_type, ()),
        )
        for action_type in PIO_ACTION_TYPES
    )
