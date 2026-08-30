"""Video helpers for the JIBOT adapter.

Builds web_video_server stream/snapshot URLs and pushes event-triggered JPEG
snapshots to a side MQTT topic. Pixels are never relayed through MQTT as a live
stream; only small, occasional snapshots are published here. Live viewing is done
by the FMS pulling the stream URL directly from web_video_server.

See jibot-client/docs/jibot-video-to-fms-design.md.
"""

from typing import Dict, List, Optional
from urllib.parse import urlencode
from urllib.request import urlopen
import asyncio

from config.config import VideoConfig
from utils.mqtt_client import MQTTClient


class VideoStreamer:
    """Builds web_video_server URLs and publishes event snapshots over MQTT."""

    def __init__(self, config: VideoConfig, mqtt_client: MQTTClient) -> None:
        self._config = config
        self._mqtt = mqtt_client
        # Last time (monotonic) a snapshot was published per event, for debounce.
        self._last_snapshot_ts: Dict[str, float] = {}

    # -------------------------
    # URL builders
    # -------------------------
    def _local_base_url(self) -> str:
        """Base the adapter uses to pull snapshots (same host as the adapter)."""
        return self._config.web_video_server_url.rstrip("/")

    def _public_base_url(self) -> str:
        """Base advertised to the FMS for live streams (FMS-reachable host)."""
        public = (self._config.web_video_server_public_url or "").strip()
        return (public or self._config.web_video_server_url).rstrip("/")

    def stream_url(self, topic: str) -> str:
        """web_video_server live-stream URL advertised to the FMS."""
        query = {"topic": topic}
        stream_type = (self._config.stream_type or "").strip()
        if stream_type and stream_type != "mjpeg":
            query["type"] = stream_type
        return f"{self._public_base_url()}/stream?{urlencode(query, safe='/')}"

    def snapshot_url(self, topic: str) -> str:
        """Local web_video_server single-frame JPEG URL the adapter fetches."""
        query = {"topic": topic, "quality": int(self._config.snapshot_quality)}
        return f"{self._local_base_url()}/snapshot?{urlencode(query, safe='/')}"

    def stream_urls(self, topics: Optional[List[str]] = None) -> Dict[str, str]:
        """Map of {topic: stream_url} for the configured (or given) topics."""
        topics = topics if topics else list(self._config.stream_topics)
        return {topic: self.stream_url(topic) for topic in topics}

    # -------------------------
    # Event snapshots
    # -------------------------
    def _debounced(self, event: str, now: float) -> bool:
        """True if a snapshot for ``event`` was published too recently."""
        window = float(self._config.snapshot_debounce_sec)
        last = self._last_snapshot_ts.get(event)
        return last is not None and (now - last) < window

    async def capture_event_snapshot(self, event: str, topic: Optional[str] = None) -> None:
        """Fetch a JPEG from web_video_server and publish it to the side topic.

        Errors are swallowed (logged) so a video hiccup never disturbs the state
        loop. Runs the blocking HTTP GET in a worker thread.
        """
        if not self._config.enabled:
            return

        now = asyncio.get_running_loop().time()
        if self._debounced(event, now):
            return
        # Reserve the slot before the (awaited) fetch so concurrent transitions
        # for the same event do not both fire.
        self._last_snapshot_ts[event] = now

        source_topic = topic or self._config.snapshot_topic
        if not source_topic:
            return

        url = self.snapshot_url(source_topic)
        timeout = float(self._config.snapshot_http_timeout_sec)
        try:
            jpeg = await asyncio.to_thread(self._http_get, url, timeout)
        except Exception as exc:  # noqa: BLE001 - video must never break the loop
            print(f"[VIDEO SNAPSHOT FAILED] event={event} url={url}: {exc}")
            return

        if not jpeg:
            print(f"[VIDEO SNAPSHOT EMPTY] event={event} url={url}")
            return

        mqtt_topic = f"{self._config.snapshot_mqtt_topic}/{event}"
        # Binary JPEG payload (MQTT is binary-safe); the event is in the topic.
        self._mqtt.publish(mqtt_topic, jpeg, qos=0, retain=False)
        print(
            f"[VIDEO SNAPSHOT PUBLISHED] event={event} topic={mqtt_topic} "
            f"src={source_topic} bytes={len(jpeg)}"
        )

    @staticmethod
    def _http_get(url: str, timeout: float) -> bytes:
        with urlopen(url, timeout=timeout) as response:  # noqa: S310 - local trusted host
            return response.read()
