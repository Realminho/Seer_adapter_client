"""amr_map_publish 코어 단위 테스트(fake collaborator)."""
import asyncio
import unittest

from amr_map_publish import (
    MAP_TOPIC,
    build_map_message,
    extract_request_id,
    get_map_and_publish,
)

# 최소 JIBOT UmGetMap raw fixture(노드 2개)
RAW_MAP = {
    "Header": "umcl-map",
    "MapName": "lab2m",
    "MapRes": 20,
    "MinPose": "0 0",
    "MaxPose": "20000 10000",
    "Objs": {
        "Goal": [{"name": "F1_40", "pose": "5953 4854 0.00", "allowPassingThrough": False}],
        "PathPoint": [{"name": "p1", "pose": "1000 1000 0.00", "vertex": ""}],
    },
}


class FakeParam:
    def __init__(self, key, value):
        self.key = key
        self.value = value


class FakeVehicle:
    """async get_map + _map_raw 보유 fake.

    fresh=True(기본): get_map() 호출 시 _map_raw에 새 dict 객체를 할당해 fresh parse를 시뮬레이션.
    fresh=False: timeout처럼 _map_raw를 갱신하지 않아 identity 불변을 시뮬레이션.
    is_simulator=True: SimulatedJIBOT처럼 소켓 없이 정적 _map_raw를 유지(항상 최신).
    """
    def __init__(self, raw, fresh=True, is_simulator=False):
        self._map_raw = raw
        self._fresh = fresh
        self.is_simulator = is_simulator
        self.get_map_called = 0

    async def get_map(self):
        self.get_map_called += 1
        if self._fresh and self._map_raw is not None:
            # 새 객체를 할당해 identity 변경(fresh parse 시뮬레이션)
            self._map_raw = dict(self._map_raw)
        # fresh=False 이면 _map_raw 그대로 유지(timeout/stale 시뮬레이션)


class FakeMqtt:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False, use_prefix=True):
        self.published.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})


class ExtractRequestIdTest(unittest.TestCase):
    def test_param_우선(self):
        params = [FakeParam("mapId", "lab2m"), FakeParam("requestId", "req-9")]
        self.assertEqual(extract_request_id(params, "act-1"), "req-9")

    def test_param_없으면_action_id_fallback(self):
        params = [FakeParam("mapId", "lab2m")]
        self.assertEqual(extract_request_id(params, "act-1"), "act-1")


class BuildMapMessageTest(unittest.TestCase):
    def test_uamap_변환_및_envelope(self):
        msg = build_map_message(RAW_MAP, map_id="lab2m", request_id="req-9", generated_at="2026-06-19T00:00:00Z")
        self.assertEqual(msg["schemaVersion"], "uamap.core.v1")
        self.assertEqual(msg["map"]["mapId"], "lab2m")
        self.assertEqual(msg["requestId"], "req-9")
        self.assertEqual(msg["generatedAt"], "2026-06-19T00:00:00Z")
        self.assertTrue(any(n["id"] == "F1_40" for n in msg["graph"]["nodes"]))


class GetMapAndPublishTest(unittest.TestCase):
    def test_fetch_후_map_토픽_publish(self):
        vehicle = FakeVehicle(RAW_MAP, fresh=True)
        mqtt = FakeMqtt()
        ok = asyncio.run(get_map_and_publish(vehicle, mqtt, map_id="lab2m", request_id="req-9", generated_at="2026-06-19T00:00:00Z"))
        self.assertTrue(ok)
        self.assertEqual(vehicle.get_map_called, 1)
        self.assertEqual(len(mqtt.published), 1)
        pub = mqtt.published[0]
        self.assertEqual(pub["topic"], MAP_TOPIC)
        self.assertEqual(pub["qos"], 1)
        self.assertFalse(pub["retain"])
        self.assertEqual(pub["payload"]["requestId"], "req-9")

    def test_raw_없으면_publish_안함(self):
        vehicle = FakeVehicle(None)
        mqtt = FakeMqtt()
        ok = asyncio.run(get_map_and_publish(vehicle, mqtt, map_id="lab2m", request_id="req-9", generated_at="t"))
        self.assertFalse(ok)
        self.assertEqual(len(mqtt.published), 0)

    def test_stale_timeout_publish_안함(self):
        """timeout 시 _map_raw identity 불변 → publish 하지 않고 False 반환."""
        vehicle = FakeVehicle(RAW_MAP, fresh=False)
        mqtt = FakeMqtt()
        ok = asyncio.run(get_map_and_publish(vehicle, mqtt, map_id="lab2m", request_id="req-9", generated_at="t"))
        self.assertFalse(ok)
        self.assertEqual(vehicle.get_map_called, 1)
        self.assertEqual(len(mqtt.published), 0)

    def test_simulator_정적맵도_publish(self):
        """시뮬레이터는 소켓이 없어 _map_raw identity가 정적이어도 항상 최신 → 발행해야 함."""
        vehicle = FakeVehicle(RAW_MAP, fresh=False, is_simulator=True)
        mqtt = FakeMqtt()
        ok = asyncio.run(get_map_and_publish(vehicle, mqtt, map_id="lab2m", request_id="req-9", generated_at="t"))
        self.assertTrue(ok)
        self.assertEqual(vehicle.get_map_called, 1)
        self.assertEqual(len(mqtt.published), 1)
        self.assertEqual(mqtt.published[0]["payload"]["requestId"], "req-9")

    def test_simulator라도_raw_없으면_publish_안함(self):
        """시뮬레이터라도 로드된 맵(_map_raw)이 없으면 발행할 게 없으므로 skip."""
        vehicle = FakeVehicle(None, is_simulator=True)
        mqtt = FakeMqtt()
        ok = asyncio.run(get_map_and_publish(vehicle, mqtt, map_id="lab2m", request_id="req-9", generated_at="t"))
        self.assertFalse(ok)
        self.assertEqual(len(mqtt.published), 0)


if __name__ == "__main__":
    unittest.main()
