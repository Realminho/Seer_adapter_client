#!/usr/bin/env python3
"""UmLocalize의 poseTh가 절대값인지 상대값인지 실기에서 1회 관측으로 판별한다.

배경: 어댑터/클라이언트는 recipe의 theta를 그대로 poseTh에 실어 보낸다(변환 없음).
현장에서 heading이 "로봇이 보고 있는 방향 기준 상대값"으로 먹는 것이 관측되어,
urobot 쪽 시맨틱을 확정하려고 만든 read-only 성격의 진단 도구다.

무엇을 하는가:
  1) UmGetRobotInfo 로 현재 pose (H = 현재 heading) 를 읽는다
  2) UmLocalize target=pose, poseX/poseY = **현재값 그대로**, poseTh = T 를 보낸다
     (좌표를 현재값으로 두므로 위치는 제자리 재앵커 = 교란 최소)
  3) 다시 pose 를 읽어 결과 heading R 을 구한다
  4) R 이 T / H+T / H-T 중 무엇에 맞는지 판정한다

주의: --apply 는 로봇의 localization 을 실제로 다시 앵커한다. 로봇을 세워 두고,
주행 중인 order 가 없을 때 실행할 것. 상대값이 맞다면 이 실행 자체가 heading 을
T 만큼 틀어 놓으므로, 끝나고 정상 재위치를 한 번 해 주어야 한다.
--apply 없이 실행하면 아무것도 보내지 않고 나갈 프레임만 보여 준다.

사용:
  scripts/probe-jibot-localize-theta.py --ip 10.8.8.8                 # dry-run
  scripts/probe-jibot-localize-theta.py --ip 10.8.8.8 --theta 30 --apply
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO_ROOT / "jibot-client" / "src")]

from jibot_client import JIBOT  # noqa: E402


def wrap180(deg):
    """(-180, 180] 로 접는다. 359도와 1도가 2도 차이로 읽히게."""
    return (float(deg) + 180.0) % 360.0 - 180.0


POLL_GAP_MS = 200


def _drain(robot):
    """큐에 쌓인 이전 프레임을 버린다 — AFTER 를 읽을 때 localize 이전 프레임을
    집으면 관측 자체가 무의미해진다."""
    while not robot._response_queue.empty():
        try:
            robot._response_queue.get_nowait()
        except asyncio.QueueEmpty:
            break


async def read_pose(robot, timeout, attempts=5):
    """UmGetRobotInfo 로 (x, y, th) 를 읽는다. 못 읽으면 None.

    ``#GAP#`` 은 폴링 주기(ms) 메타데이터다. **0 을 주면 로봇이 응답하지 않는다**
    (어댑터도 main.py 에서 interval*1000, 최소 100 을 쓴다).

    캐시(``robot._x/_y/_th``) 로 폴백하지 않는다 — 그 기본값이 (0, 0, 0) 이라서,
    폴백했다가는 poseX/poseY=0 을 보내 로봇을 맵 원점으로 재앵커해 버린다.
    읽기 실패는 조용히 넘어갈 일이 아니라 중단할 일이다.
    """
    for _ in range(attempts):
        _drain(robot)
        response = await robot.send_command_and_wait(
            "UmGetRobotInfo", gap=POLL_GAP_MS, timeout=timeout
        )
        if response is not None and all(k in response for k in ("x", "y", "th")):
            return (
                float(response["x"]),
                float(response["y"]),
                float(response["th"]),
            )
        await asyncio.sleep(0.3)
    return None


def verdict(before_th, sent_theta, after_th):
    """관측된 heading 이 어느 가설에 맞는지 고른다."""
    candidates = {
        "ABSOLUTE      (R = T)": wrap180(after_th - sent_theta),
        "RELATIVE_ADD  (R = H + T)": wrap180(after_th - (before_th + sent_theta)),
        "RELATIVE_SUB  (R = H - T)": wrap180(after_th - (before_th - sent_theta)),
        "NO_CHANGE     (R = H)": wrap180(after_th - before_th),
    }
    ranked = sorted(candidates.items(), key=lambda kv: abs(kv[1]))
    return ranked, candidates


async def main():
    parser = argparse.ArgumentParser(
        description="UmLocalize poseTh 절대/상대 판별 (실기 1회 관측)"
    )
    parser.add_argument("--ip", required=True, help="로봇 IP (robots.hcl vehicle_ip)")
    parser.add_argument("--port", type=int, default=7273)
    parser.add_argument(
        "--theta",
        type=float,
        default=30.0,
        help="보낼 poseTh(도). 기본 30 — 어느 가설이든 결과가 확연히 갈리는 값이면 된다",
    )
    parser.add_argument(
        "--settle-sec",
        type=float,
        default=3.0,
        help="UmLocalize 후 pose 를 다시 읽기까지 대기(초)",
    )
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제로 UmLocalize 를 보낸다. 없으면 나갈 프레임만 출력하고 종료",
    )
    parser.add_argument(
        "--watch-sec",
        type=float,
        default=0.0,
        metavar="SEC",
        help=(
            "--apply 와 함께. 전송 후 SEC 초 동안 1초 간격으로 pose 를 계속 읽는다. "
            "명령한 heading 이 유지되는지, 실제 방향으로 되돌아가는지를 한 프로세스 "
            "안에서 관측한다(사람 개입 구간이 없다)"
        ),
    )
    parser.add_argument(
        "--watch-only",
        type=float,
        default=0.0,
        metavar="SEC",
        help=(
            "아무것도 보내지 않고 pose 를 SEC 초 동안 1초 간격으로 읽기만 한다. "
            "재위치 직후 heading 이 그대로 있는지, 실제 방향으로 다시 수렴하는지 본다"
        ),
    )
    args = parser.parse_args()

    robot = JIBOT(robot_ip=args.ip, robot_port=args.port)
    await robot.connect_socket()
    try:
        await robot.connect()
        await asyncio.sleep(0.5)

        if args.watch_only > 0:
            # read-only. UmLocalize 직후 앵커가 그대로 남는지, scan-match 가 실제
            # 방향으로 다시 끌어당기는지를 시계열로 본다.
            print(f"WATCH ONLY {args.watch_only:.0f}s — 아무것도 보내지 않는다")
            first = None
            deadline = asyncio.get_running_loop().time() + args.watch_only
            while asyncio.get_running_loop().time() < deadline:
                pose = await read_pose(robot, args.timeout, attempts=2)
                if pose is None:
                    print("  (pose 읽기 실패)")
                else:
                    x, y, th = pose
                    if first is None:
                        first = pose
                    dth = wrap180(th - first[2])
                    print(
                        f"  x={x:9.1f} y={y:9.1f} th={th:7.2f}deg   "
                        f"첫 샘플 대비 dth={dth:+6.2f}deg "
                        f"dx={x - first[0]:+7.1f} dy={y - first[1]:+7.1f}"
                    )
                await asyncio.sleep(1.0)
            return 0

        before = await read_pose(robot, args.timeout)
        if before is None:
            print("[FAIL] 현재 pose 를 읽지 못했다 (UmGetRobotInfo 무응답).")
            print("       아무것도 보내지 않고 중단한다 — 현재 좌표를 모르는 채로")
            print("       UmLocalize 를 보내면 로봇을 엉뚱한 곳에 앵커하게 된다.")
            return 2
        bx, by, bth = before
        print(f"BEFORE  x={bx:.1f} y={by:.1f} th={bth:.2f}deg")

        if bx == 0.0 and by == 0.0:
            # 이 사이트 맵의 MinPose 는 (134, -7728) 이라 (0, 0) 은 정상 pose 가
            # 아니다. 읽기가 실패해 기본값을 본 것일 가능성이 높다.
            print("[ABORT] pose 가 (0, 0) 으로 읽혔다. 이 맵에서 정상적인 위치가")
            print("        아니므로 읽기 실패로 보고 중단한다.")
            return 2

        frame = robot.build_command(
            "UmLocalize",
            gap=-1,
            target="pose",
            goal=None,
            poseX=bx,
            poseY=by,
            poseTh=args.theta,
        )
        print("FRAME  ", json.dumps(frame, ensure_ascii=False))

        if not args.apply:
            print()
            print("dry-run 이라 아무것도 보내지 않았다. 실제 관측은 --apply 를 붙일 것.")
            print("주의: --apply 는 로봇의 localization 을 실제로 다시 앵커한다.")
            return 0

        loop = asyncio.get_running_loop()
        sent_at = loop.time()
        await robot.um_localize(
            target="pose", goal=None, poseX=bx, poseY=by, poseTh=args.theta
        )
        print(f"SENT    poseTh={args.theta} (poseX/poseY 는 현재값 그대로)")

        # 전송과 관측을 한 프로세스 안에서 끝낸다. 두 번에 나눠 실행하면 그 사이
        # 사람이 재위치를 해 버릴 수 있고, 그러면 "저절로 되돌아왔는지"와
        # "누가 되돌렸는지"를 구분할 수 없다.
        samples = []
        if args.watch_sec > 0:
            print()
            print(f"WATCH {args.watch_sec:.0f}s — 명령한 heading 이 유지되는지 본다")
            print(f"{'t(s)':>6} {'x':>9} {'y':>9} {'th':>8}   {'T대비':>8} {'H대비':>8}")
            while loop.time() - sent_at < args.watch_sec:
                pose = await read_pose(robot, args.timeout, attempts=2)
                elapsed = loop.time() - sent_at
                if pose is None:
                    print(f"{elapsed:6.1f}  (pose 읽기 실패)")
                else:
                    x, y, th = pose
                    samples.append((elapsed, x, y, th))
                    print(
                        f"{elapsed:6.1f} {x:9.1f} {y:9.1f} {th:8.2f}   "
                        f"{wrap180(th - args.theta):+8.2f} {wrap180(th - bth):+8.2f}"
                    )
                await asyncio.sleep(1.0)
            if not samples:
                print("[FAIL] 관측 구간에서 pose 를 한 번도 못 읽었다")
                return 2
            ax, ay, ath = samples[-1][1:]
            print()
            # 명령이 반영되기까지 1초 남짓 걸린다. 첫 샘플은 아직 이전 heading 일
            # 수 있으므로, 안정성은 "반영된 시점부터 마지막까지"로 따진다 —
            # 반영 전 샘플을 끼워 비교하면 가만히 있는 로봇도 "움직였다"가 된다.
            applied = next(
                (s for s in samples if abs(wrap180(s[3] - bth)) > 2.0), None
            )
            if applied is None:
                print("명령한 heading 이 한 번도 반영되지 않았다 "
                      f"(관측 내내 th≈{bth:.2f} = 재위치 이전 값).")
                print("  => UmLocalize 가 heading 을 바꾸지 못했다는 뜻이다.")
            else:
                drift = wrap180(ath - applied[3])
                print(f"반영 시점 (t={applied[0]:.1f}s) th={applied[3]:.2f}  ->  "
                      f"마지막 (t={samples[-1][0]:.1f}s) th={ath:.2f}  "
                      f"(그 사이 {drift:+.2f}deg)")
                if abs(drift) > 5.0:
                    print("  => 반영 후에도 heading 이 움직였다. 재위치는 확정 적용이")
                    print("     아니라 초기 추정치(seed)이고 이후 다시 수렴한다.")
                else:
                    print("  => 반영된 뒤로는 유지됐다. 확정 적용이다.")
        else:
            await asyncio.sleep(args.settle_sec)
            after = await read_pose(robot, args.timeout)
            if after is None:
                print("[FAIL] 재위치 후 pose 를 읽지 못했다")
                return 2
            ax, ay, ath = after
            print(f"AFTER   x={ax:.1f} y={ay:.1f} th={ath:.2f}deg")
        print()

        ranked, _ = verdict(bth, args.theta, ath)
        print(f"H(before)={bth:.2f}  T(sent)={args.theta:.2f}  R(after)={ath:.2f}")
        for name, residual in ranked:
            print(f"  {name:26s} 잔차 {residual:+8.2f}deg")
        best_name, best_residual = ranked[0]
        runner_up = ranked[1][1]
        print()
        if abs(best_residual) <= 2.0 and abs(runner_up) > 5.0:
            print(f"VERDICT: {best_name.split()[0]}  (잔차 {best_residual:+.2f}deg)")
        else:
            print("VERDICT: 판정 보류 — 후보들이 충분히 안 갈렸다.")
            print("         H 가 0 에 가깝거나 T 가 작으면 가설이 겹친다.")
            print("         로봇을 다른 heading 으로 돌려 두고 --theta 를 바꿔 재실행할 것.")

        print()
        print(f"위치 이동량: dx={ax - bx:+.1f}mm dy={ay - by:+.1f}mm "
              "(poseX/poseY 를 현재값으로 보냈으므로 0 에 가까워야 절대 좌표계다)")
        print()
        print("!! 이 실행으로 heading 이 틀어졌을 수 있다. 정상 재위치를 한 번 해 둘 것.")
        return 0
    finally:
        await robot.disconnect()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
