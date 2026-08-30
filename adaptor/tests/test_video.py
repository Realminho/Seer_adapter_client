import asyncio
import unittest

from config.config import VideoConfig
from utils.video import VideoStreamer


def _video_config(**overrides) -> VideoConfig:
    base = dict(
        enabled=True,
        web_video_server_url="http://127.0.0.1:8080",
        stream_type="mjpeg",
        stream_topics=["/camera/cam2/image_raw", "/camera/cam3/image_raw"],
        snapshot_events=["brake", "slowdown"],
        snapshot_topic="/bundle_detection_image",
        snapshot_quality=40,
        snapshot_mqtt_topic="event_snapshot",
        snapshot_debounce_sec=5.0,
        snapshot_http_timeout_sec=3.0,
    )
    base.update(overrides)
    return VideoConfig(**base)


class _FakeMQTT:
    def __init__(self) -> None:
        self.calls = []

    def publish(self, topic, payload, qos=0, retain=False) -> None:
        self.calls.append((topic, payload, qos, retain))


class VideoStreamerUrlTest(unittest.TestCase):
    def test_stream_url_keeps_slashes_and_omits_default_type(self) -> None:
        vs = VideoStreamer(_video_config(), _FakeMQTT())
        self.assertEqual(
            vs.stream_url("/camera/cam2/image_raw"),
            "http://127.0.0.1:8080/stream?topic=/camera/cam2/image_raw",
        )

    def test_stream_url_includes_non_default_type(self) -> None:
        vs = VideoStreamer(_video_config(stream_type="h264"), _FakeMQTT())
        self.assertEqual(
            vs.stream_url("/camera/cam2/image_raw"),
            "http://127.0.0.1:8080/stream?topic=/camera/cam2/image_raw&type=h264",
        )

    def test_snapshot_url_carries_quality(self) -> None:
        vs = VideoStreamer(_video_config(snapshot_quality=25), _FakeMQTT())
        self.assertEqual(
            vs.snapshot_url("/bundle_detection_image"),
            "http://127.0.0.1:8080/snapshot?topic=/bundle_detection_image&quality=25",
        )

    def test_stream_url_uses_public_base_when_set(self) -> None:
        vs = VideoStreamer(
            _video_config(web_video_server_public_url="http://192.168.3.222:8080"),
            _FakeMQTT(),
        )
        # Stream is advertised on the FMS-reachable host...
        self.assertEqual(
            vs.stream_url("/camera/cam2/image_raw"),
            "http://192.168.3.222:8080/stream?topic=/camera/cam2/image_raw",
        )
        # ...while snapshots are still fetched locally.
        self.assertEqual(
            vs.snapshot_url("/bundle_detection_image"),
            "http://127.0.0.1:8080/snapshot?topic=/bundle_detection_image&quality=40",
        )

    def test_stream_urls_defaults_to_configured_topics(self) -> None:
        vs = VideoStreamer(_video_config(), _FakeMQTT())
        self.assertEqual(
            vs.stream_urls(),
            {
                "/camera/cam2/image_raw": "http://127.0.0.1:8080/stream?topic=/camera/cam2/image_raw",
                "/camera/cam3/image_raw": "http://127.0.0.1:8080/stream?topic=/camera/cam3/image_raw",
            },
        )


class VideoStreamerSnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_http_get = VideoStreamer._http_get
        VideoStreamer._http_get = staticmethod(lambda url, timeout: b"\xff\xd8JPEG\xff\xd9")

    def tearDown(self) -> None:
        VideoStreamer._http_get = self._orig_http_get

    def test_snapshot_publishes_binary_to_per_event_topic(self) -> None:
        mqtt = _FakeMQTT()
        vs = VideoStreamer(_video_config(), mqtt)
        asyncio.run(vs.capture_event_snapshot("brake"))
        self.assertEqual(len(mqtt.calls), 1)
        topic, payload, qos, retain = mqtt.calls[0]
        self.assertEqual(topic, "event_snapshot/brake")
        self.assertIsInstance(payload, (bytes, bytearray))
        self.assertEqual(qos, 0)
        self.assertFalse(retain)

    def test_debounce_suppresses_repeat_within_window(self) -> None:
        mqtt = _FakeMQTT()
        vs = VideoStreamer(_video_config(snapshot_debounce_sec=100.0), mqtt)

        async def run() -> None:
            await vs.capture_event_snapshot("brake")
            await vs.capture_event_snapshot("brake")  # debounced
            await vs.capture_event_snapshot("slowdown")  # different event -> allowed

        asyncio.run(run())
        topics = [call[0] for call in mqtt.calls]
        self.assertEqual(topics, ["event_snapshot/brake", "event_snapshot/slowdown"])

    def test_disabled_streamer_publishes_nothing(self) -> None:
        mqtt = _FakeMQTT()
        vs = VideoStreamer(_video_config(enabled=False), mqtt)
        asyncio.run(vs.capture_event_snapshot("brake"))
        self.assertEqual(mqtt.calls, [])


if __name__ == "__main__":
    unittest.main()
