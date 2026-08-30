#!/usr/bin/env python3
"""goto 스텝이 라우트로 실행되는지, 그리고 스텝 경계에서 감속하는지를 실기에서 관측한다.

배경: 연속 경로 주행(Continuous Path)의 이득은 "로봇이 감속을 시작하기 전에 다음
목표를 아는가"에 전부 걸려 있다. 2026-06-22 캡처(logs/jibot/f1_60-arrival-complete.jsonl)
에서 UmGoto 1회 주행의 비용은 제자리 회전 13초 + 병진 3.6초였고, 도착존(±40mm) 진입
후 정지까지는 1초 안팎이었다. 즉 `_settle_goto_arrival` 대기를 없애도 감속 자체는
그대로 남는다.

urobot 은 UmGoto 를 매번 임시 라우트 슬롯(routes="ActiveMode [Temp]", key="a")으로
구체화한다 — 즉 UmGoto 는 1스텝 라우트의 설탕문법이다. 다중 스텝을 벤더가 의도한
방식으로 내리는 수단이 UmSchedulerThis 이고, 이 저장소는 이미
move_distance(run_mode="scheduler") 로 그 경로를 실기에서 쓰고 있다.

**주의(PDF 반증)**: PDF p14 는 UmSchedulerThis 가 "디스크에 저장하지 않는다"고
쓰지만, 2026-08-26 amr2 에서 받아 온 /usr/local/urobot/params/routes/routes.temp.json
에는 어댑터가 만든 manual_move 라우트와 ActiveMode/TEMP_DEFAULT 슬롯이 그대로 들어
있다. 즉 영속 파일(routes.json)에는 안 쓰지만 routes.temp.json 에는 남는다. 이
프로브의 cp_probe 라우트도 거기 남을 것으로 봐야 한다(무해하지만 알고 있을 것).

노드 이름은 로봇에서 받아 온 맵(jibot/params/map/)으로 해석한다. PathPoint 면
target="pose"(좌표+방위), Goal/Dock 이면 target="goal"(이름)로 나간다. 어댑터의
`_send_node_goto` 와 같은 규칙이다.

무엇을 하는가:
  P2-1 (--node 1개): goto 스텝 1개짜리 라우트를 UmSchedulerThis 로 실행한다.
    주행 위험은 평소 UmGoto 1회와 같다. 확인 대상은 "goto 스텝이 라우트로 도는가"와
    "UmGetCurTask.routes/key 가 우리가 준 이름으로 바뀌는가" 두 가지다.
  P2-2 (--node 2개): goto 스텝 2개짜리 라우트를 실행한다. P2-1 성공이 전제다.
    확인 대상은 "스텝 경계에서 vel_f 가 0 으로 떨어지는가"와 "key 가 a -> a1 로
    넘어가는가"다.

goto 스텝 스키마와 스텝 전이 규칙은 추측이 아니라 실기에서 받아 온 벤더 라우트
실측이다 — jibot/params/routes/routes.json 의 AB_1000 / EF / AB_Loop:
  {"cmd":"goto","target":"goal","goal":"A","x":0,"y":0,"th":0,
   "time":-1,"note":1,"max_vel":1000}
전이는 키에 "1" 을 덧붙이는 것이다: a -> a1 -> a11 -> a111.

주의: --apply 는 로봇을 실제로 움직인다. 어댑터 서비스를 먼저 내리고(오더 워커가
같은 로봇에 UmGoto 를 쏘면 서로 싸운다), 모터를 켜고, 경로에 사람/장애물이 없는
상태에서 실행할 것. --apply 없이 실행하면 아무것도 보내지 않고 나갈 프레임만 보여 준다.
Ctrl-C 로 중단하면 UmStop 을 보낸다.

사용 (amr2 = 192.168.101.50. --ip 는 SSH 별칭이 아니라 JIBOT TCP 주소다):
  # dry-run — 프레임만 확인
  scripts/probe-jibot-route-goto.py --ip 192.168.101.50 --node p39

  # P2-1 실기 1스텝
  scripts/probe-jibot-route-goto.py --ip 192.168.101.50 --node p39 --apply

  # P2-2 실기 2스텝
  scripts/probe-jibot-route-goto.py --ip 192.168.101.50 \
      --node p39 --node p36 --apply
"""

import argparse
import asyncio
import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO_ROOT / "jibot-client" / "src")]

from jibot_client import JIBOT  # noqa: E402

# 스텝 전이 규칙: 키 뒤에 "1" 을 덧붙이면 다음 스텝이다. 추측이 아니라 실기에서
# 받아 온 벤더 라우트 실측이다 — jibot/params/routes/routes.json 의 AB_Loop 이
# a -> a1 -> a11 -> a111(jump) 이고, routes.template.json 의 48스텝짜리들도 같다.
# ("0" 으로 끝나는 키(a110, a1110)는 조건 분기의 다른 가지로 보인다.)
def step_key(index):
    return "a" + "1" * index


def load_map(map_path=None):
    """로봇에서 받아 온 맵에서 PathPoint / Goal / Dock 이름을 읽는다.

    `target` 을 무엇으로 줄지는 이름이 어느 목록에 있느냐로 갈린다. 어댑터도
    같은 규칙이다 — `_send_node_goto` 는 `get_path_point_pose(node_id)` 가 좌표를
    돌려주면 `goto_xyz`(=target "pose")를, 아니면 `goto_point`(=target "goal")를
    쓴다. PathPoint 는 urobot 의 goal 네임스페이스에 없어서 이름으로 못 부른다.
    """
    root = REPO_ROOT / "jibot" / "params" / "map"
    if map_path is None:
        pointer = root / "map.json"
        if not pointer.exists():
            return None
        map_path = root / json.loads(pointer.read_text(encoding="utf-8-sig"))["name"]
    map_path = Path(map_path)
    if not map_path.exists():
        raise SystemExit(
            f"맵 파일이 없다: {map_path}\n"
            "scripts/fetch-jibot-params-over-ssh.sh <user@host> 로 먼저 받을 것."
        )
    objs = json.loads(map_path.read_text(encoding="utf-8-sig"))["Objs"]

    def poses(key):
        out = {}
        for item in objs.get(key) or []:
            parts = str(item.get("pose", "")).split()
            if len(parts) >= 3:
                out[item["name"]] = tuple(float(v) for v in parts[:3])
        return out

    return {
        "path": str(map_path),
        "pathpoints": poses("PathPoint"),
        "goals": poses("Goal"),
        "docks": poses("Dock"),
    }


def bearing_deg(src, dst):
    return math.degrees(math.atan2(dst[1] - src[1], dst[0] - src[0]))


def goto_step(name, mapdata, *, prev_xy=None, max_vel=None, reach_dist=None,
              time=-1, note=1):
    """노드 하나로 가는 goto 라우트 스텝을 만든다.

    필드 구성은 벤더가 이 로봇에서 실제로 쓰고 있는 형태를 따른다
    (jibot/params/routes/routes.json 의 AB_1000 / EF / AB_Loop).

    PathPoint 는 target="pose" 로 좌표를 실어야 하고, 이때 th 는 뺄 수 없다 —
    urobot 은 poseTh 없는 target=pose goto 를 에러 없이 조용히 버린다
    (adapter_jibot.py `_node_goto_theta_deg` 의 2026-08-22 HN-SH6-TR-002 실측).
    이 맵의 PathPoint 는 theta 가 전부 0.00 이라 맵 값을 그대로 쓰면 도착할 때마다
    제자리 회전을 한다. 그래서 어댑터와 같은 기준(직전 지점 -> 이 지점의 방위)을
    쓴다: 굴러온 방향 그대로라 도착 시 돌지 않는다.
    """
    step = {"cmd": "goto", "time": time, "note": note}
    pathpoints = mapdata["pathpoints"] if mapdata else {}
    named = (mapdata["goals"] | mapdata["docks"]) if mapdata else {}

    if name in pathpoints:
        x, y, map_th = pathpoints[name]
        th = bearing_deg(prev_xy, (x, y)) if prev_xy else map_th
        # 벤더의 pose 형 goto 는 goal 필드를 아예 넣지 않는다
        # (routes.samples.json / samples_goto / a1). 런타임 에코의 goal:"none" 은
        # urobot 이 출력할 때 채우는 값이지 입력 형식이 아니다.
        step.update(target="pose", x=int(x), y=int(y), th=round(th, 2))
    elif name in named or mapdata is None:
        step.update(target="goal", goal=str(name), x=0, y=0, th=0)
    else:
        raise SystemExit(
            f"'{name}' 을 맵에서 못 찾았다 ({mapdata['path']}).\n"
            f"  PathPoint: {', '.join(sorted(pathpoints))}\n"
            f"  Goal/Dock: {', '.join(sorted(named))}"
        )

    if max_vel is not None:
        step["max_vel"] = int(max_vel)
    # start_reach_max_dis 는 routes.json 의 A-CAM2 에서 300(mm)으로 한 번 쓰인다.
    # 도착 판정 반경으로 보이며 블렌딩 반경 후보다. 의미 미확정이라 기본은 미지정.
    if reach_dist is not None:
        step["start_reach_max_dis"] = int(reach_dist)
    return step


def build_route(names, mapdata, *, start_xy=None, max_vel=None, reach_dist=None):
    """goto 스텝 N개 + 종료용 idle 스텝 1개짜리 라우트를 만든다.

    종료 스텝은 반드시 필요하다. 벤더의 AB_1000/EF 는 마지막이 cmd:"jump" 로
    첫 스텝에 되돌아가는 **무한 루프**라 그대로 흉내내면 로봇이 안 멈춘다.
    em_route 가 마지막을 cmd:"idle" 로 닫는 형태라 그쪽을 따른다.
    """
    route = {}
    prev = start_xy
    for index, name in enumerate(names):
        step = goto_step(
            name, mapdata, prev_xy=prev,
            max_vel=max_vel, reach_dist=reach_dist,
        )
        route[step_key(index)] = step
        if step["target"] == "pose":
            prev = (step["x"], step["y"])
    route[step_key(len(names))] = {"cmd": "idle", "note": 1}
    return route


def _dist(a, b):
    if None in a or None in b:
        return None
    return math.hypot(a[0] - b[0], a[1] - b[1])


async def _preflight(vehicle, timeout):
    """모터/로컬라이제이션 상태를 읽어 그대로 보여 준다.

    모터가 꺼져 있으면 urobot 은 명령을 **받아들이고** 라우트를 현재 태스크로
    올린 뒤 그대로 서 있는다 — 에러 프레임도 없다. adapter_jibot.py
    `_ensure_motor_enabled_before_order_motion` 의 2026-08-17 192.168.101.61
    실측이 같은 증상이다(UmDock 을 motor=False 로 보내고 3분 뒤에도 Stopped).
    어댑터를 내리고 프로브를 돌리면 그 자동 인가가 없으므로 여기서 직접 읽는다.
    """
    out = {}
    for command in ("UmGetMotorState", "UmGetLocState", "UmGetTaskInfo"):
        out[command] = await vehicle.send_command_and_wait(
            command, gap=-1, timeout=timeout, accept_errors=True
        )
    return out


def _motor_is_off(payload):
    """명시적으로 '꺼짐'일 때만 True. 못 읽었으면 unknown(None)이지 off 가 아니다."""
    if not isinstance(payload, dict):
        return None
    for key in ("flag", "state", "motor", "status", "data"):
        if key not in payload:
            continue
        flag = payload[key]
        if isinstance(flag, dict):
            continue
        if isinstance(flag, str):
            low = flag.strip().lower()
            if low in {"0", "false", "off"}:
                return True
            if low in {"1", "true", "on"}:
                return False
            continue
        return not bool(flag)
    return None


async def _sample(vehicle, timeout, *, with_path=False):
    """UmGetRobotInfo + UmGetCurTask 를 한 번씩 읽어 관심 필드만 뽑는다.

    with_path 면 UmGetPath 도 읽는다. 이게 로봇이 **계획한** 전역 경로다.
    2026-08-26 관측에서 로봇이 목표 반대 방향으로 가는 것을 보고도 이유를 못 댔는데,
    그건 계획 경로를 안 찍고 있었기 때문이다. 매 샘플 찍기엔 무거워서 주기적으로만 본다.
    """
    info = await vehicle.send_command_and_wait(
        "UmGetRobotInfo", gap=-1, timeout=timeout, accept_errors=True
    ) or {}
    task = await vehicle.send_command_and_wait(
        "UmGetCurTask", gap=-1, timeout=timeout, accept_errors=True
    ) or {}
    path = None
    if with_path:
        raw = await vehicle.send_command_and_wait(
            "UmGetPath", gap=-1, timeout=timeout, accept_errors=True
        ) or {}
        pts = raw.get("path") or []
        if isinstance(pts, list) and pts:
            def xy(pt):
                if isinstance(pt, dict):
                    return (pt.get("x"), pt.get("y"))
                return tuple(pt[:2]) if isinstance(pt, (list, tuple)) else None
            path = {"num": raw.get("num", len(pts)), "first": xy(pts[0]), "last": xy(pts[-1])}
    data = task.get("data") or {}
    value = data.get("value") or {}
    return {
        "path": path,
        "x": info.get("x"),
        "y": info.get("y"),
        "th": info.get("th"),
        "vel_f": info.get("vel_f"),
        "vel_r": info.get("vel_r"),
        "station": info.get("station"),
        "mode": info.get("mode"),
        "status": info.get("status"),
        "obs": info.get("obs"),
        "task_routes": data.get("routes"),
        "task_key": data.get("key"),
        "task_cmd": value.get("cmd") if isinstance(value, dict) else value,
        "task_goal": value.get("goal") if isinstance(value, dict) else None,
    }


def _print_sample(t, s):
    print(
        f"{t:7.2f}  "
        f"x={s['x']!s:>7} y={s['y']!s:>7} th={s['th']!s:>5} "
        f"vf={s['vel_f']!s:>9} vr={s['vel_r']!s:>9} "
        f"st={s['station']!r:<12} mode={s['mode']!r:<10} "
        f"routes={s['task_routes']!r:<22} key={s['task_key']!r:<6} "
        f"cmd={s['task_cmd']!r:<7} goal={s['task_goal']!r} "
        f"status={s['status']!r}"
        + (f"  PATH n={s['path']['num']} {s['path']['first']}->{s['path']['last']}"
           if s.get("path") else ""),
        flush=True,
    )


def _summarize(samples, route_name, step_keys, goals):
    """관측이 실제로 무엇을 확정했는지만 적는다. 확정 못 한 건 '미확정'으로 남긴다."""
    print("\n" + "=" * 100)
    print("판정")
    print("=" * 100)

    seen_routes = [s["task_routes"] for s in samples if s["task_routes"]]
    owned = [r for r in seen_routes if route_name in str(r)]
    if owned:
        print(f"  [확정] 우리 라우트가 실행됨 — UmGetCurTask.routes 에 {sorted(set(owned))} 관측")

        # 우리 라우트가 돌다가 다른 것으로 바뀌면 누군가 이 로봇을 같이 몰고 있다는 뜻이다.
        # 이 프로브는 UmStop 을 보내지 않으므로(중단 시에만 보낸다) 우리 소행이 아니다.
        # 2026-08-26 실측에서 이 전환을 놓쳐 "라우트가 한 홉만 간다"로 오독했다.
        first_owned = next(
            i for i, s in enumerate(samples) if route_name in str(s["task_routes"] or "")
        )
        after = samples[first_owned:]
        preempt = next(
            (
                s for s in after
                if s["task_routes"] and route_name not in str(s["task_routes"])
            ),
            None,
        )
        if preempt is not None:
            still_driving = any(
                s["vel_f"] is not None and abs(float(s["vel_f"])) > 1.0
                for s in after[: after.index(preempt)][-3:]
            )
            print(
                f"  [경고] **우리 라우트가 도중에 교체됨** -> routes={preempt['task_routes']!r} "
                f"cmd={preempt['task_cmd']!r}"
                + (" (교체 직전까지 주행 중이었다)" if still_driving else "")
            )
            print(
                "         이 프로브는 UmStop 을 보내지 않는다. 다른 클라이언트(어댑터/FMS)가\n"
                "         같은 로봇에 명령하고 있다는 뜻이다. 그 상태의 관측은 신뢰할 수 없다 —\n"
                "         어댑터 서비스를 내리고 다시 측정할 것."
            )
    elif seen_routes:
        print(
            f"  [반증] 우리 라우트가 안 보임 — 관측된 routes={sorted(set(map(str, seen_routes)))}. "
            f"'ActiveMode [Temp]' 뿐이면 UmSchedulerThis 가 임시 슬롯으로 흡수됐다는 뜻이고, "
            f"라우트 이름을 못 잡는다는 것 자체는 다중 스텝 불가의 근거가 아니다."
        )
    else:
        print("  [미확정] UmGetCurTask 응답에 routes 가 비어 있음")

    keys = [s["task_key"] for s in samples if s["task_cmd"] == "goto" and s["task_key"]]
    ordered = []
    for k in keys:
        if not ordered or ordered[-1] != k:
            ordered.append(k)
    if len(goals) < 2:
        print(f"  [P2-1] goto 스텝 키 전이: {ordered or '없음'} (1스텝이라 전이 대상 아님)")
    elif len(ordered) >= 2:
        print(f"  [확정] 스텝 전이 관측됨 — key {' -> '.join(map(str, ordered))}")
    else:
        print(
            f"  [반증/미확정] 스텝 전이 없음 — key={ordered or '없음'}. "
            f"두 번째 스텝({step_keys[1] if len(step_keys) > 1 else '?'})이 실행되지 않았거나, "
            f"전이 규칙이 이 키 네이밍과 다르다. P3(벤더 라우트 원본)로 규칙 확인 필요."
        )

    driving = [
        (i, s) for i, s in enumerate(samples)
        if s["task_cmd"] == "goto" and s["vel_f"] is not None
    ]
    if driving:
        speeds = [abs(float(s["vel_f"])) for _, s in driving]
        print(f"  vel_f 절대값: max={max(speeds):.1f} min={min(speeds):.1f} (goto 진행 중 샘플만)")
        if len(goals) >= 2 and len(ordered) >= 2:
            first_key = ordered[0]
            boundary = [
                s for _, s in driving
                if s["task_key"] == first_key and abs(float(s["vel_f"])) < 1.0
            ]
            if boundary:
                print(
                    f"  [스텝 경계 감속 있음] 첫 스텝 진행 중 vel_f≈0 인 샘플 {len(boundary)}건 — "
                    "라우트로 묶어도 스텝마다 정지한다는 뜻이면 CP 이득이 작다."
                )
            else:
                print(
                    "  [스텝 경계 감속 없음으로 보임] 첫 스텝 구간에 vel_f≈0 샘플이 없음 — "
                    "다만 샘플 주기보다 짧은 정지는 못 잡는다. --interval 을 줄여 재확인할 것."
                )
    else:
        print("  [미확정] goto 진행 중 샘플이 없음 — 로봇이 아예 움직이지 않았을 수 있다")

    print(
        "\n  주의: 이 프로브는 홉 1~2개짜리 단발 관측이다. 어느 판정도 "
        "여러 회 반복 없이 일반화하지 말 것."
    )


async def run(args):
    goals = list(args.node)
    mapdata = load_map(args.map)
    step_keys = [step_key(i) for i in range(len(goals) + 1)]
    entry_key = step_keys[0]

    def make_route(start_xy):
        return build_route(
            goals, mapdata, start_xy=start_xy,
            max_vel=args.max_vel, reach_dist=args.reach_dist,
        )

    # 첫 스텝의 th 는 "로봇이 지금 있는 곳 -> 첫 노드"의 방위여야 한다. 실기에서는
    # 접속 후 실제 pose 를 읽어 다시 만든다. dry-run 은 현재 pose 를 모르므로
    # 맵 theta(이 사이트는 전부 0.00)로 두고 그 사실을 아래에서 밝힌다.
    route = make_route(None)

    frame = {
        "#CMD#": "UmSchedulerThis",
        "#GAP#": -1,
        "name": args.route_name,
        "key": entry_key,
        "content": route,
    }
    mode = "P2-1 (1스텝)" if len(goals) == 1 else f"P2-2 ({len(goals)}스텝)"
    print(f"# {mode}  host={args.ip}:{args.port}  route={args.route_name} entry={entry_key}")
    if mapdata:
        kinds = ", ".join(
            f"{n}={'PathPoint(pose)' if n in mapdata['pathpoints'] else 'Goal/Dock(goal)'}"
            for n in goals
        )
        print(f"# 맵: {mapdata['path']}")
        print(f"# 노드 해석: {kinds}")
    else:
        print("# 맵 없음 — 모든 노드를 target=goal 로 보낸다 (PathPoint 면 로봇이 무시할 수 있다)")
    print("# 보낼 프레임:")
    print(json.dumps(frame, ensure_ascii=False, indent=2))

    if not args.apply and not args.status_only:
        print(
            "\n# dry-run 이라 아무것도 보내지 않았다. 실기 발사는 --apply.\n"
            "# 첫 스텝의 th 는 실기에서 로봇 현재 pose 기준 방위로 다시 계산된다.\n"
            "# 발사 전 확인: 어댑터 서비스 정지, 모터 ON, 경로에 사람/장애물 없음."
        )
        return

    if args.record:
        os.environ.setdefault("JIBOT_RECORD", "1")
        if not os.getenv("JIBOT_RECORD_FILE"):
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            record_dir = Path(os.getenv("JIBOT_RECORD_DIR", "logs/jibot"))
            record_dir.mkdir(parents=True, exist_ok=True)
            os.environ["JIBOT_RECORD_FILE"] = str(
                record_dir / f"{stamp}-route-goto-probe.jsonl"
            )

    vehicle = JIBOT(args.ip, args.port)
    started = time.monotonic()
    samples = []
    sent = False
    try:
        await vehicle.connect_socket()
        await vehicle.connect()

        print("\n# 사전 상태")
        pre = await _preflight(vehicle, args.timeout)
        for command, payload in pre.items():
            print(f"  {command:<16} {json.dumps(payload, ensure_ascii=False)[:200]}")
        motor_off = _motor_is_off(pre.get("UmGetMotorState"))
        print(f"  모터 판정: {'OFF' if motor_off else ('ON' if motor_off is False else '미확인')}")

        if args.status_only:
            # 조회만 하고 끝낸다. 로봇에 모션 명령을 하나도 보내지 않는다.
            s = await _sample(vehicle, args.timeout)
            _print_sample(time.monotonic() - started, s)
            print("\n# --status-only 라 모션 명령은 보내지 않았다.")
            return

        if motor_off and not args.enable_motor:
            # 모터가 꺼져 있으면 urobot 은 라우트를 현재 태스크로 올린 뒤 그대로
            # 서 있는다. 그 상태로 관측하면 "라우트가 실패했다"로 오독하게 된다.
            print(
                "\n# 중단: 모터가 꺼져 있다. 이 상태에서는 명령이 받아들여져도 로봇은\n"
                "#       움직이지 않고, 관측이 라우트 실패처럼 보인다.\n"
                "#       --enable-motor 를 주거나 로봇에서 직접 켜고 다시 실행할 것."
            )
            return
        if motor_off and args.enable_motor:
            print("\n# 모터 인가 (UmSetMotor True)")
            await vehicle.um_set_motor(True)
            for _ in range(20):
                await asyncio.sleep(0.3)
                again = _motor_is_off(
                    await vehicle.send_command_and_wait(
                        "UmGetMotorState", gap=-1,
                        timeout=args.timeout, accept_errors=True,
                    )
                )
                if again is False:
                    print("  모터 ON 확인")
                    break
            else:
                print("  모터가 계속 OFF — 안전정지 래치일 수 있다. 중단.")
                return

        print("\n# pre-sample (정지 상태 기준선)")
        for _ in range(args.pre_samples):
            s = await _sample(vehicle, args.timeout)
            samples.append(s)
            _print_sample(time.monotonic() - started, s)
            await asyncio.sleep(args.interval)

        # 첫 스텝의 th 를 로봇의 실제 현재 위치 기준 방위로 다시 계산한다.
        here = samples[-1] if samples else {}
        if here.get("x") is not None and here.get("y") is not None:
            here_xy = (float(here["x"]), float(here["y"]))
            route = make_route(here_xy)
            first = route[entry_key]
            if first.get("target") == "pose":
                gap_mm = math.dist(here_xy, (first["x"], first["y"]))
                print(f"\n# 현재 pose {here_xy} -> 첫 노드 거리 {gap_mm:.0f} mm")
                if gap_mm > args.max_gap:
                    # 맵의 PathPoint 가 서로 멀리 떨어진 여러 구역으로 나뉘어 있어,
                    # 로봇이 있는 구역이 아닌 노드를 고르기 쉽다. 실수로 수십 미터를
                    # 주행시키지 않도록 막는다.
                    print(
                        f"# 중단: 첫 노드가 {gap_mm:.0f} mm 떨어져 있다 "
                        f"(--max-gap {args.max_gap:.0f}).\n"
                        f"#       로봇 근처 노드를 고르거나 --max-gap 을 올릴 것."
                    )
                    return
            print("# 첫 스텝 th 재계산 결과:")
            print(json.dumps(route, ensure_ascii=False, indent=2))
        else:
            print("\n# 현재 pose 를 못 읽어 맵 theta 를 그대로 쓴다")

        if args.plain_goto:
            # 대조군: 라우트가 아니라 평범한 UmGoto 를 같은 목표로 쏜다.
            # "로봇이 지금 움직일 수 있는가"와 "라우트 경로가 도는가"를 분리한다.
            first = route[entry_key]
            print(f"\n# [대조군] UmGoto 발사 (t={time.monotonic() - started:.2f})")
            if first.get("target") == "pose":
                await vehicle.send_command(
                    "UmGoto", gap=-1, target="pose",
                    poseX=first["x"], poseY=first["y"], poseTh=first["th"],
                )
            else:
                await vehicle.send_command(
                    "UmGoto", gap=-1, target="goal", goal=first["goal"]
                )
        else:
            print(f"\n# UmSchedulerThis 발사 (t={time.monotonic() - started:.2f})")
            await vehicle.send_command(
                "UmSchedulerThis",
                gap=-1,
                name=args.route_name,
                key=entry_key,
                content=route,
            )
        sent = True

        print("# 관측  (PATH = 로봇이 계획한 전역 경로)")
        deadline = time.monotonic() + args.duration
        tick = 0
        while time.monotonic() < deadline:
            tick += 1
            s = await _sample(vehicle, args.timeout, with_path=(tick % 4 == 1))
            samples.append(s)
            _print_sample(time.monotonic() - started, s)
            await asyncio.sleep(args.interval)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n# 중단됨 — UmStop 전송")
        if sent:
            try:
                await vehicle.um_stop()
            except Exception as exc:  # noqa: BLE001
                print(f"# UmStop 실패: {exc}")
        raise
    finally:
        try:
            await vehicle.disconnect()
        except Exception:  # noqa: BLE001
            pass

    _summarize(samples, args.route_name, step_keys, goals)
    if args.record:
        print(f"\n  원시 프레임: {os.getenv('JIBOT_RECORD_FILE')}")


def main():
    parser = argparse.ArgumentParser(
        description="goto 라우트 스텝 실행과 스텝 경계 감속을 실기에서 관측한다."
    )
    parser.add_argument("--ip", required=True, help="로봇 IP")
    parser.add_argument("--port", type=int, default=7273)
    parser.add_argument(
        "--node",
        "--goal",
        dest="node",
        action="append",
        required=True,
        metavar="NAME",
        help="목표 노드 이름(PathPoint 또는 Goal/Dock). 1개면 P2-1, 2개 이상이면 P2-2. "
             "순서대로 스텝이 된다.",
    )
    parser.add_argument("--route-name", default="cp_probe", help="임시 라우트 이름")
    parser.add_argument(
        "--map",
        default=None,
        help="맵 파일 경로. 기본은 jibot/params/map/map.json 이 가리키는 파일.",
    )
    parser.add_argument(
        "--max-vel",
        type=int,
        default=None,
        help="goto 스텝의 max_vel(mm/s). 미지정이면 로봇 config 값(task-goto.max_vel=350). "
             "벤더 라우트 실측 범위는 200~1500.",
    )
    parser.add_argument(
        "--reach-dist",
        type=int,
        default=None,
        help="goto 스텝의 start_reach_max_dis(mm). 도착 판정 반경으로 보이는 필드이며 "
             "벤더 라우트에 300 으로 한 번 쓰인다. 의미 미확정이라 기본은 미지정.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.3,
        help="샘플 주기(초). 기존 캡처는 1.78초라 감속 구간을 못 잡았다.",
    )
    parser.add_argument("--pre-samples", type=int, default=3)
    parser.add_argument("--duration", type=float, default=90.0, help="발사 후 관측 시간(초)")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--record", action="store_true", default=True)
    parser.add_argument("--no-record", dest="record", action="store_false")
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="접속해서 모터/로컬라이제이션/태스크 상태만 읽고 끝낸다. "
             "모션 명령을 하나도 보내지 않는다.",
    )
    parser.add_argument(
        "--enable-motor",
        action="store_true",
        help="모터가 꺼져 있으면 UmSetMotor 로 켜고 확인될 때까지 기다린다. "
             "없으면 모터 OFF 일 때 아무것도 보내지 않고 중단한다.",
    )
    parser.add_argument(
        "--plain-goto",
        action="store_true",
        help="대조군: 라우트 대신 평범한 UmGoto 를 첫 노드로 쏜다. "
             "'로봇이 지금 움직일 수 있는가'를 라우트와 분리해서 본다.",
    )
    parser.add_argument(
        "--max-gap",
        type=float,
        default=20000.0,
        help="첫 노드까지 허용 거리(mm). 맵의 PathPoint 가 멀리 떨어진 여러 구역으로 "
             "나뉘어 있어 엉뚱한 구역 노드를 고르기 쉽다. 기본 20 m.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제로 로봇에 보낸다. 없으면 프레임만 출력하고 끝난다.",
    )
    args = parser.parse_args()

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
