"""Run the repository's unmodified adapter with SeerClient injected at runtime.

Usage from the repository root:

    python seer_client/run_adapter.py --id SEER-001 --vehicle-ip 192.168.192.5
    python seer_client/run_adapter.py --simulator --id SEER-SIM-001
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import sys
import time
from pathlib import Path


# The shared Adapter is imported from its unchanged source directory. Do not
# leave Python bytecode beside those original files during a SEER run.
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"


SEER_CLIENT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SEER_CLIENT_ROOT.parent
ADAPTOR_ROOT = REPO_ROOT / "adaptor"
for source in (
    SEER_CLIENT_ROOT / "src",
    REPO_ROOT,
    REPO_ROOT / "amr-client-contract" / "src",
    REPO_ROOT / "jibot-client" / "src",
    REPO_ROOT / "jibot-simulator",
    ADAPTOR_ROOT,
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from seer_client.bridge import install_into_adapter_main  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.dropin_control import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    install_dropin_control_server,
    reserve_local_port,
)
from seer_client.dropin_devices import install_dropin_device_modules  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.hcl_config import ensure_hcl_files  # pyright: ignore[reportMissingImports]  # noqa: E402


def _apply_seer_runtime_options(argv: list[str]) -> list[str]:
    """Consume SEER-only flags before the shared adapter parses its CLI.

    The original adapter must remain usable by JIBOT and therefore does not
    know SEER's five TCP ports.  This small pre-parser converts the SEER flags
    to the environment variables already consumed by ``seer_client.bridge``
    and returns all remaining shared adapter arguments unchanged.
    """

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--seer-state-port", type=int)
    parser.add_argument("--seer-control-port", type=int)
    parser.add_argument("--seer-task-port", type=int)
    parser.add_argument("--seer-config-port", type=int)
    parser.add_argument("--seer-other-port", type=int)
    parser.add_argument("--seer-motor-names")
    parser.add_argument("--seer-protocol-version", type=int)
    parser.add_argument("--seer-command-timeout", type=float)
    parser.add_argument("--seer-min-request-interval", type=float)
    parser.add_argument("--seer-status-poll-interval", type=float)
    parser.add_argument("--seer-navigation-timeout", type=float)
    parser.add_argument("--seer-extensions-path")
    parser.add_argument("--seer-recipes-path")
    options, remaining = parser.parse_known_args(argv)
    default_hcl = ensure_hcl_files(SEER_CLIENT_ROOT / "runtime")
    values = {
        "SEER_STATE_PORT": options.seer_state_port,
        "SEER_CONTROL_PORT": options.seer_control_port,
        "SEER_TASK_PORT": options.seer_task_port,
        "SEER_CONFIG_PORT": options.seer_config_port,
        "SEER_OTHER_PORT": options.seer_other_port,
        "SEER_MOTOR_NAMES": options.seer_motor_names,
        "SEER_PROTOCOL_VERSION": options.seer_protocol_version,
        "SEER_COMMAND_TIMEOUT_SEC": options.seer_command_timeout,
        "SEER_MIN_REQUEST_INTERVAL_SEC": options.seer_min_request_interval,
        "SEER_STATUS_POLL_INTERVAL_SEC": options.seer_status_poll_interval,
        "SEER_NAVIGATION_TIMEOUT_SEC": options.seer_navigation_timeout,
        "SEER_EXTENSIONS_PATH": (
            options.seer_extensions_path
            or os.getenv("SEER_EXTENSIONS_PATH")
            or str(default_hcl.extensions)
        ),
        "SEER_RECIPES_PATH": (
            options.seer_recipes_path
            or os.getenv("SEER_RECIPES_PATH")
            or str(default_hcl.recipes)
        ),
    }
    for name, value in values.items():
        if value is not None:
            os.environ[name] = str(value)
    return remaining


def _load_original_main():
    path = ADAPTOR_ROOT / "main.py"
    spec = importlib.util.spec_from_file_location("original_amr_adapter_main", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load original adapter main: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _ensure_control_ipc_endpoint() -> None:
    """Choose a localhost control port when CLI users did not provide one.

    WebUI-managed launches already inject ``SEER_CONTROL_IPC_PORT``.  Direct
    ``run_adapter.py`` launches used to fail when that environment variable was
    missing, so ask the OS for a free localhost port instead.
    """

    host = os.getenv("SEER_CONTROL_IPC_HOST", "").strip() or "127.0.0.1"
    os.environ["SEER_CONTROL_IPC_HOST"] = host

    raw_port = os.getenv("SEER_CONTROL_IPC_PORT", "").strip()
    if raw_port:
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise RuntimeError("SEER_CONTROL_IPC_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise RuntimeError("SEER_CONTROL_IPC_PORT must be between 1 and 65535")
        return

    port = reserve_local_port()
    os.environ["SEER_CONTROL_IPC_PORT"] = str(port)
    print(f"[SEER CONTROL IPC AUTO] host={host} port={port}")


def _apply_dropin_ipc_root() -> None:
    """Point original Adapter IPC at an optional drop-in runtime directory."""

    runtime_root = os.getenv("SEER_RUNTIME_ROOT", "").strip()
    if not runtime_root:
        runtime_root = str(SEER_CLIENT_ROOT / "runtime" / "ipc")
        os.environ["SEER_RUNTIME_ROOT"] = runtime_root
    from core import ipc_paths  # pyright: ignore[reportMissingImports]

    ipc_paths.RUNTIME_ROOT = Path(runtime_root)


def _install_windows_ipc_write_retry() -> None:
    """Tolerate short WebUI reader/atomic-replace collisions on Windows."""

    if os.name != "nt":
        return
    from core import ipc_paths  # pyright: ignore[reportMissingImports]

    if getattr(ipc_paths, "_seer_windows_retry_installed", False):
        return
    original = ipc_paths.atomic_write_json

    def atomic_write_json(path, obj) -> None:
        for attempt in range(20):
            try:
                original(path, obj)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.01 * (attempt + 1))

    ipc_paths.atomic_write_json = atomic_write_json
    ipc_paths._seer_windows_retry_installed = True


def main() -> None:
    sys.argv[1:] = _apply_seer_runtime_options(sys.argv[1:])
    adapter_main = _load_original_main()
    _ensure_control_ipc_endpoint()
    _apply_dropin_ipc_root()
    _install_windows_ipc_write_retry()
    install_into_adapter_main(adapter_main)
    install_dropin_device_modules()
    install_dropin_control_server()
    asyncio.run(adapter_main.main())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nKeyboard interrupt received.")
