import unittest
from config.config import VideoConfig, WebUiConfig, get_config


class TestWebUiConfig(unittest.TestCase):
    def test_defaults_match_current_literals(self):
        w = WebUiConfig()
        self.assertEqual(w.port, 9000)
        self.assertEqual(w.camera_default_port, 9001)
        self.assertEqual(w.http_request_timeout_sec, 10.0)
        self.assertEqual(w.max_post_body_bytes, 65536)
        self.assertEqual(w.password_min_length, 12)
        self.assertIn("changeme", w.password_forbidden_tokens)

    def test_video_defaults_to_camera_service_port(self):
        self.assertEqual(VideoConfig().web_video_server_url, "http://127.0.0.1:9001")

    def test_get_config_exposes_web_ui(self):
        self.assertIsInstance(get_config().web_ui, WebUiConfig)
