import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock


CLIENT_SRC = Path(__file__).resolve().parents[2] / "jibot-client" / "src"
if str(CLIENT_SRC) not in sys.path:
    sys.path.insert(0, str(CLIENT_SRC))

from jibot_client import JIBOT


class JibotClientLocalizeTest(unittest.TestCase):
    def test_localize_delegates_to_um_localize(self):
        vehicle = JIBOT(robot_ip="127.0.0.1", robot_port=7273)
        vehicle.um_localize = AsyncMock()

        asyncio.run(vehicle.localize("goal", "p2", None, None, None))

        vehicle.um_localize.assert_awaited_once_with(
            target="goal",
            goal="p2",
            poseX=None,
            poseY=None,
            poseTh=None,
        )


if __name__ == "__main__":
    unittest.main()
