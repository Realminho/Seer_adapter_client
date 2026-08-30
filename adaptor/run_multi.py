"""Launch several JIBOT adapters at once, one main.py process per robot.

여러 대의 JIBOT adapter를 한 번에 띄운다. robots.hcl에 적힌 로봇마다 main.py
프로세스를 하나씩 띄우고, 각 로봇의 설정(id/ip/ezi 주소 등)을 CLI 옵션으로
넘긴다. EZI IO/모터가 블로킹 하드웨어 클라이언트라 한 프로세스에 여러 어댑터를
넣기보다 프로세스를 분리하는 편이 안전하고 크래시 격리도 된다.

사용법:
    python run_multi.py --robots config/robots.hcl
    python run_multi.py --robots config/robots.hcl --simulator   # 전체 시뮬레이터

각 로봇 키 -> main.py 옵션 매핑은 config/robots.hcl.example 참고.
Ctrl+C 한 번이면 모든 자식 프로세스에 SIGINT를 보내 graceful shutdown 한다.
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config.fleet import DEFAULT_FLEET_PATH, FleetError, load_fleet

ADAPTER_ROOT = Path(__file__).resolve().parent
MAIN_SCRIPT = ADAPTER_ROOT / "main.py"


def build_command(
    python_bin: str, robot: Dict[str, Any], robots_path: str, force_simulator: bool
) -> List[str]:
    """robots.hcl의 robot 한 항목을 main.py 실행 커맨드로 변환한다.

    인스턴스 설정은 ``main.py --robot <id>``가 robots.hcl에서 직접 읽으므로
    여기서는 id와 fleet 파일 경로만 넘긴다. 이렇게 하면 systemd 유닛과 완전히
    같은 실행 경로가 된다.
    """
    cmd = [python_bin, str(MAIN_SCRIPT), "--robots", robots_path, "--robot", robot["id"]]
    if force_simulator:
        cmd.append("--simulator")
    extra = robot.get("extra_args") or []
    cmd += [str(arg) for arg in extra]
    return cmd


def _stream_output(proc: subprocess.Popen, label: str) -> None:
    """자식 stdout을 [label] 접두사 붙여 그대로 흘려보낸다."""
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(f"[{label}] {line}")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Launch multiple JIBOT adapters from a robots.hcl fleet file."
    )
    parser.add_argument(
        "--robots",
        default=str(DEFAULT_FLEET_PATH),
        help="Fleet definition HCL. Default: config/robots.hcl",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter used for each adapter. Default: current interpreter.",
    )
    parser.add_argument(
        "--simulator",
        action="store_true",
        help="Force every robot to start in simulator mode.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the robot ids in the fleet file, one per line, then exit. "
        "Used by scripts/setup-adaptor-service.sh to enable per-robot units.",
    )
    args = parser.parse_args()

    # id 검증(비어있음/중복)은 load_fleet이 한곳에서 처리한다.
    try:
        robots = load_fleet(args.robots)
    except FleetError as exc:
        print(f"[run_multi] {exc}", file=sys.stderr)
        return 2

    if args.list:
        for robot in robots:
            print(robot["id"])
        return 0

    procs: List[subprocess.Popen] = []
    threads: List[threading.Thread] = []

    print(f"[run_multi] starting {len(robots)} adapter(s) from {args.robots}")
    for robot in robots:
        label = robot["id"]
        cmd = build_command(args.python, robot, args.robots, args.simulator)
        print(f"[run_multi] launch {label}: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd,
            cwd=str(ADAPTER_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        procs.append(proc)
        thread = threading.Thread(
            target=_stream_output, args=(proc, label), daemon=True
        )
        thread.start()
        threads.append(thread)

    stopping = threading.Event()

    def _shutdown(signum, _frame) -> None:
        if stopping.is_set():
            return
        stopping.set()
        print(f"\n[run_multi] signal {signum} received; stopping all adapters...")
        for proc in procs:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # systemd supervises this launcher as one service. If any child exits on its
    # own, stop the rest and return non-zero so systemd can restart the group.
    exit_code = 0
    remaining = set(range(len(procs)))
    while remaining:
        for index in list(remaining):
            proc = procs[index]
            rc = proc.poll()
            if rc is None:
                continue
            remaining.remove(index)
            label = robots[index]["id"]
            if stopping.is_set():
                continue
            exit_code = rc if rc not in (0, None) else 1
            stopping.set()
            print(
                f"[run_multi] adapter {label} exited; stopping all adapters "
                f"(returncode={rc})"
            )
            for other in procs:
                if other.poll() is None:
                    other.send_signal(signal.SIGINT)
            break
        else:
            time.sleep(0.2)
            continue

        while remaining:
            for index in list(remaining):
                proc = procs[index]
                rc = proc.poll()
                if rc is not None:
                    remaining.remove(index)
            if remaining:
                time.sleep(0.2)

    print("[run_multi] all adapters exited")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
