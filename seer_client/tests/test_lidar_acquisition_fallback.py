import asyncio

from seer_client.docking_action import (
    _extract_laser_points,
    _extract_netprotocol_laser_step,
    _request_laser_points,
)
from seer_client.protocol import ApiNumber, ApiPort


class _FakeConn:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def request(self, api, body=None, **kwargs):
        self.calls.append((int(api), {} if body is None else dict(body), dict(kwargs)))
        if not self.replies:
            return {}
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class _Vehicle:
    def __init__(self, conn):
        self._ports = {ApiPort.STATE: conn}
        self._x = 0.0
        self._y = 0.0
        self._th = 0.0


def _params(step=5):
    return {"NetProtocol": {"laserStep": {"value": step}}, "ret_code": 0}


def test_extract_legacy_laser_beams():
    points, schema = _extract_laser_points({"laser_beams": [[1, 2], [3.5, -4]]})
    assert points == [[1.0, 2.0], [3.5, -4.0]]
    assert schema == "laser_beams"


def test_extract_newer_multi_laser_wrappers():
    payload = {
        "lasers": [
            {"id": 0, "points": [{"x": 1.0, "y": 2.0}, {"x": 1.1, "y": 2.1}]},
            {"id": 1, "laser_beams": [[3.0, 4.0], [3.1, 4.1]]},
        ]
    }
    points, schema = _extract_laser_points(payload)
    assert len(points) == 4
    assert points[0] == [1.0, 2.0]
    assert points[-1] == [3.1, 4.1]
    assert schema == "lasers"


def test_extract_beams3d_xy_projection():
    points, schema = _extract_laser_points(
        {"beams3D": [[1.0, 2.0, 0.3], [3.0, 4.0, -0.1]]}
    )
    assert points == [[1.0, 2.0], [3.0, 4.0]]
    assert schema == "beams3D"


def test_extract_mid360_nested_points():
    points, schema = _extract_laser_points(
        {"mid360": {"points": [{"x": 1.2, "y": -0.4, "z": 0.2}]}}
    )
    assert points == [[1.2, -0.4]]
    assert schema == "mid360"


def test_extract_netprotocol_laser_step():
    assert _extract_netprotocol_laser_step(_params(5)) == 5
    assert _extract_netprotocol_laser_step({"data": _params(0)}) == 0


def test_request_uses_old_gui_api1009_shape_first():
    conn = _FakeConn([
        _params(5),
        {"ret_code": 0, "laser_beams": [[1.0, 1.0], [1.2, 0.2]]},
    ])
    vehicle = _Vehicle(conn)
    result = asyncio.run(_request_laser_points(vehicle, step=8))
    assert result["ok"] is True
    assert result["source"] == "API1009_GUI_CLASSIC"
    assert result["laser_step_param"] == 5
    assert len(result["points"]) == 2
    assert conn.calls[0][0] == int(ApiNumber.STATUS_PARAMS)
    assert conn.calls[1][0] == int(ApiNumber.STATUS_LASER)
    assert conn.calls[1][1] == {"return_beams3D": False}


def test_request_falls_back_to_step0_then_default_then_requested_then_all2():
    conn = _FakeConn([
        _params(5),
        {"ret_code": 0},
        {"ret_code": 0},
        {"ret_code": 0},
        {"ret_code": 0},
        {"ret_code": 0},
        {"laser_beams": [[2.0, 1.0], [2.1, 1.1]]},
    ])
    vehicle = _Vehicle(conn)
    result = asyncio.run(_request_laser_points(vehicle, step=8))
    assert result["ok"] is True
    assert result["source"] == "API1101_ALL2"
    # Param + old-GUI 1009 + four 1009 variants + all2.
    assert [c[0] for c in conn.calls] == [
        int(ApiNumber.STATUS_PARAMS),
        int(ApiNumber.STATUS_LASER),
        int(ApiNumber.STATUS_LASER),
        int(ApiNumber.STATUS_LASER),
        int(ApiNumber.STATUS_LASER),
        int(ApiNumber.STATUS_LASER),
        int(ApiNumber.STATUS_ALL2),
    ]


def test_request_enables_3d_and_mid360_all1_fallback():
    conn = _FakeConn([
        _params(0),
        {}, {}, {}, {}, {}, {},
        {"beams3D": [[4.0, 5.0, 0.1], [4.1, 5.1, 0.2]]},
    ])
    vehicle = _Vehicle(conn)
    result = asyncio.run(_request_laser_points(vehicle, step=1))
    assert result["ok"] is True
    assert result["source"] == "API1100_SENSOR_FLAGS"
    assert result["schema"] == "beams3D"
    sensor_call = conn.calls[-1]
    assert sensor_call[0] == int(ApiNumber.STATUS_ALL1)
    assert sensor_call[1]["return_laser"] is True
    assert sensor_call[1]["return_beams3D"] is True
    assert sensor_call[1]["return_mid360"] is True
    assert "keys" not in sensor_call[1]


def test_remembered_source_is_tried_first_after_param_diagnostic():
    conn = _FakeConn([
        _params(5),
        {"laser_beams": [[9.0, 8.0]]},
    ])
    vehicle = _Vehicle(conn)
    vehicle._docking_laser_source_hint = "API1101_ALL2"
    result = asyncio.run(_request_laser_points(vehicle, step=8))
    assert result["ok"] is True
    assert result["source"] == "API1101_ALL2"
    assert len(conn.calls) == 2
    assert conn.calls[1][0] == int(ApiNumber.STATUS_ALL2)


def test_failure_reports_netprotocol_empty_and_diagnostics():
    # param + 9 acquisition probes
    conn = _FakeConn([_params(5)] + [{"ret_code": 0}] * 9)
    vehicle = _Vehicle(conn)
    result = asyncio.run(_request_laser_points(vehicle, step=1))
    assert result["ok"] is False
    assert result["source"] == "NETPROTOCOL_EMPTY"
    assert result["retry_after_s"] >= 2.0
    assert len(result["diagnostics"]) >= 9
    assert "laserStep=5" in result["diagnostics"][0]


def test_extract_old_gui_polar_beams_applies_install_and_robot_pose():
    payload = {
        "lasers": [
            {
                "install_info": {"x": 0.30, "y": 0.10, "yaw": 90.0},
                "beams": [
                    {"valid": True, "angle": 0.0, "dist": 1.0},
                    {"valid": False, "angle": 90.0, "dist": 2.0},
                ],
            }
        ]
    }
    # Local beam after mount = (0.30, 1.10). Robot is at (10,20), yaw 90deg.
    # World = (10 - 1.10, 20 + 0.30) = (8.90, 20.30).
    points, schema = _extract_laser_points(
        payload, robot_map_pose=(10.0, 20.0, 3.141592653589793 / 2.0)
    )
    assert schema == "lasers.polar_beams_world"
    assert len(points) == 1
    assert abs(points[0][0] - 8.9) < 1e-9
    assert abs(points[0][1] - 20.3) < 1e-9


def test_extract_old_gui_polar_requires_map_pose_for_world_normalization():
    payload = {
        "lasers": [{"install_info": {}, "beams": [{"valid": True, "angle": 0.0, "dist": 1.0}]}]
    }
    points, schema = _extract_laser_points(payload)
    assert points == []
    assert schema == "missing"


def test_request_old_gui_polar_response_returns_world_points():
    conn = _FakeConn([
        _params(1),
        {
            "ret_code": 0,
            "lasers": [{
                "install_info": {"x": 0.2, "y": 0.0, "yaw": 0.0},
                "beams": [{"valid": True, "angle": 0.0, "dist": 1.0}],
            }],
        },
    ])
    vehicle = _Vehicle(conn)
    vehicle._x = 2.0
    vehicle._y = 3.0
    vehicle._th = 0.0
    result = asyncio.run(_request_laser_points(vehicle, step=1))
    assert result["ok"] is True
    assert result["source"] == "API1009_GUI_CLASSIC"
    assert result["schema"] == "lasers.polar_beams_world"
    assert result["points"] == [[3.2, 3.0]]
