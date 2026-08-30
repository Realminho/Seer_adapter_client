"""Runtime entrypoint for the Dobot Hexplorer VDA5050 adapter."""

from __future__ import annotations

import argparse
import asyncio

from adapter_hexplorer import HexplorerAdapter
from protocol.vda5050_3_0.messages import ConnectionState


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run the Dobot Hexplorer VDA5050 adapter.")
    parser.add_argument(
        "--config",
        default=None,
        help="Instance config TOML path. Default: config/config.toml.",
    )
    return parser.parse_args(argv)


async def main(config_path: str | None = None) -> None:
    adapter = HexplorerAdapter(config_path=config_path)
    try:
        await adapter.run()
    finally:
        adapter.publish_connection(ConnectionState.OFFLINE)
        adapter.disconnect_mqtt()
        adapter.client.stop()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(main(config_path=args.config))
