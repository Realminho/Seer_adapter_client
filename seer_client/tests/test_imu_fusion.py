import asyncio
import math

from seer_client.docking_action import _request_imu_attitude
from seer_client.protocol import ApiNumber, ApiPort
from seer_docking.imu_fusion import ImuMapYawFusion, parse_seer_imu_attitude


class _FakeConn:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def request(self, api, body=None, **kwargs):
        self.calls.append((int(api), body, dict(kwargs)))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class _Vehicle:
    def __init__(self, conn):
        self._ports = {ApiPort.STATE: conn}


def test_parse_documented_api1014_attitude_radians():
    parsed = parse_seer_imu_attitude({"yaw": 0.25, "roll": -0.01, "pitch": 0.02, "ret_code": 0})
    assert parsed is not None
    assert parsed.yaw_rad == 0.25
    assert parsed.roll_rad == -0.01
    assert parsed.pitch_rad == 0.02


def test_parse_wrapped_imu_response():
    parsed = parse_seer_imu_attitude({"data": {"yaw": -0.4, "roll": 0.0, "pitch": 0.0}})
    assert parsed is not None
    assert math.isclose(parsed.yaw_rad, -0.4, abs_tol=1e-12)
    assert parsed.schema == "data.yaw/roll/pitch"


def test_request_imu_uses_api1014_without_json_body():
    conn = _FakeConn({"yaw": 0.1, "roll": 0.0, "pitch": 0.0, "ret_code": 0})
    result = asyncio.run(_request_imu_attitude(_Vehicle(conn)))
    assert result["ok"] is True
    assert math.isclose(result["attitude"].yaw_rad, 0.1, abs_tol=1e-12)
    assert conn.calls == [(int(ApiNumber.STATUS_IMU), None, {"timeout": 1.2})]


def test_relative_imu_yaw_advances_fused_map_yaw_without_common_zero():
    fusion = ImuMapYawFusion(map_correction_gain=0.0, yaw_rate_alpha=1.0)
    # First sample only establishes relative alignment: map yaw 30deg, IMU yaw 100deg.
    first = fusion.update(
        (1.0, 2.0, math.radians(30.0)),
        imu_yaw_rad=math.radians(100.0),
        imu_sample_mono=1.0,
        imu_fresh=True,
    )
    assert abs(math.degrees(first.fused_yaw_rad) - 30.0) < 1e-9

    # IMU rotates +5deg. Map cache has not changed yet, so fused yaw becomes 35deg.
    second = fusion.update(
        (1.0, 2.0, math.radians(30.0)),
        imu_yaw_rad=math.radians(105.0),
        imu_sample_mono=1.5,
        imu_fresh=True,
    )
    assert abs(math.degrees(second.fused_yaw_rad) - 35.0) < 1e-9
    assert second.used_imu_delta is True
    assert abs(second.yaw_rate_rps - math.radians(10.0)) < 1e-9


def test_stale_imu_reanchors_instead_of_double_integrating_gap_motion():
    fusion = ImuMapYawFusion(map_correction_gain=0.0, yaw_rate_alpha=1.0)
    fusion.update((0.0, 0.0, 0.0), imu_yaw_rad=1.0, imu_sample_mono=1.0, imu_fresh=True)
    fusion.update((0.0, 0.0, 0.0), imu_yaw_rad=None, imu_sample_mono=None, imu_fresh=False)
    # During the gap SEER map has already moved to 0.2rad.
    stale = fusion.update((0.0, 0.0, 0.2), imu_yaw_rad=None, imu_sample_mono=None, imu_fresh=False)
    assert abs(stale.fused_yaw_rad - 0.2) < 1e-9
    # Returning IMU yaw differs from the old sample, but this first fresh sample is re-anchored.
    returned = fusion.update((0.0, 0.0, 0.2), imu_yaw_rad=1.2, imu_sample_mono=3.0, imu_fresh=True)
    assert abs(returned.fused_yaw_rad - 0.2) < 1e-9
    assert returned.used_imu_delta is False
