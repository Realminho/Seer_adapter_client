from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "seer_client"


class CameraDockingIntegrationTests(unittest.TestCase):
    def test_live_docking_is_registered_as_motion_action(self):
        bridge = (SRC / "bridge.py").read_text(encoding="utf-8")
        self.assertIn('action_type="seerCameraDock"', bridge)
        self.assertIn('label="SEER Camera Docking Start"', bridge)
        self.assertIn('motion=True', bridge[bridge.index('action_type="seerCameraDock"'):][:350])

    def test_camera_page_routes_start_through_action_dispatch(self):
        webui = (SRC / "webui.py").read_text(encoding="utf-8")
        self.assertIn('action_type" value="seerCameraDock"', webui)
        self.assertIn('action="/seer/docking/action"', webui)
        self.assertIn('web._post_action(handler, key, forwarded)', webui)
        self.assertIn('path == "/camera"', webui)

    def test_block_builder_can_use_camera_dock_action(self):
        builder = (SRC / "recipe_builder.py").read_text(encoding="utf-8")
        program = (SRC / "block_program.py").read_text(encoding="utf-8")
        self.assertIn('"camera_dock"', builder)
        self.assertIn('"seerCameraDock"', builder)
        self.assertIn('"seerCameraDock"', program)

    def test_docking_config_uses_measured_camera_forward_offset(self):
        cfg = json.loads((ROOT / "config" / "docking.json").read_text(encoding="utf-8"))
        self.assertAlmostEqual(cfg["camera_mount"]["xyz_m"][0], 0.375, places=6)
        self.assertAlmostEqual(cfg["camera_mount"]["xyz_m"][1], 0.0, places=6)
        self.assertAlmostEqual(cfg["dock_params"]["lateral_tolerance_m"], 0.002, places=9)
        self.assertAlmostEqual(cfg["visual_servo"]["du_tolerance_px"], 10.0, places=9)

    def test_docking_transport_reuses_vehicle_control_instead_of_opening_socket(self):
        source = (SRC / "docking_action.py").read_text(encoding="utf-8")
        self.assertNotIn("socket.create_connection", source)
        self.assertNotIn("SeerProtocolClient", source)
        self.assertIn("vehicle.control.drive_native", source)
        self.assertIn("vehicle.control.stop", source)

    def test_camera_page_uses_continuous_mjpeg_stream(self):
        webui = (SRC / "webui.py").read_text(encoding="utf-8")
        self.assertIn('path == "/seer/docking/stream.mjpg"', webui)
        self.assertIn('multipart/x-mixed-replace; boundary=seerframe', webui)
        self.assertIn('id="seer-docking-live-preview"', webui)
        self.assertIn('/seer/docking/status.json?robot=', webui)

    def test_camera_page_does_not_replace_content_every_second(self):
        webui = (SRC / "webui.py").read_text(encoding="utf-8")
        camera_start = webui.index('def _render_seer_camera_docking_page')
        camera_end = webui.index('def dispatch_get', camera_start)
        camera_source = webui[camera_start:camera_end]
        self.assertNotIn('render._with_poll(body', camera_source)
        self.assertIn('setInterval(poll,100)', camera_source)

    def test_web_camera_preview_is_low_latency_twenty_fps(self):
        cfg = json.loads((ROOT / "config" / "docking.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["camera"]["web_preview_fps"], 20)
        self.assertLessEqual(cfg["camera"]["web_preview_jpeg_quality"], 74)
        self.assertLessEqual(cfg["camera"]["preview_rgb_width_px"], 800)
        source = (SRC / "docking_action.py").read_text(encoding="utf-8")
        self.assertIn('preview_interval_s = 1.0 / preview_fps', source)
        self.assertIn('preview_task = asyncio.create_task', source)

    def test_web_preview_matches_legacy_rgb_depth_camera_view(self):
        source = (SRC / "docking_action.py").read_text(encoding="utf-8")
        self.assertIn("def _draw_legacy_camera_preview", source)
        self.assertIn("depth_colormap", source)
        self.assertIn("np.hstack([color, depth])", source)
        self.assertIn("DU now/target/error=", source)
        self.assertIn("PITCH filtered/raw/target/error=", source)
        self.assertIn("MARKER effective side=", source)
        self.assertIn("AXIS distance=", source)

    def test_preview_uses_filtered_raw_tracker_while_control_keeps_validation_gate(self):
        source = (SRC / "docking_action.py").read_text(encoding="utf-8")
        self.assertIn("detection=raw_detection", source)
        self.assertIn('"tag_visible": raw_detection is not None', source)
        self.assertIn('"tag_control_valid": detection is not None', source)
        self.assertIn("reprojection_error_px", source)
        self.assertIn("_validate_depth", source)

    def test_preview_encoding_does_not_block_detector_loop(self):
        source = (SRC / "docking_action.py").read_text(encoding="utf-8")
        self.assertIn("preview_task = asyncio.create_task", source)
        self.assertIn("if preview_task is None", source)
        self.assertNotIn("await asyncio.to_thread(\n                        _write_preview", source)


    def test_webui_does_not_fake_status_before_adapter_accepts_action(self):
        webui = (SRC / "webui.py").read_text(encoding="utf-8")
        camera_post = webui[webui.index('if path == "/seer/docking/action"'):webui.index('if path == "/recipe-builder/action"')]
        self.assertNotIn('"phase": "REQUESTED"', camera_post)
        self.assertNotIn('requested_mode = "PREVIEW"', camera_post)
        self.assertIn('web._post_action(handler, key, forwarded)', camera_post)

    def test_runtime_mailbox_supports_concurrent_reader_writer(self):
        from seer_client.runtime_mailbox import atomic_write_json, read_json_locked

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "status.json"
            atomic_write_json(path, {"seq": 0})
            errors = []
            stop = threading.Event()

            def writer():
                try:
                    for seq in range(1, 80):
                        atomic_write_json(path, {"seq": seq}, timeout_s=0.2)
                        time.sleep(0.001)
                except Exception as exc:
                    errors.append(exc)
                finally:
                    stop.set()

            def reader():
                while not stop.is_set():
                    try:
                        payload = read_json_locked(path, timeout_s=0.2)
                        self.assertIsInstance(payload.get("seq"), int)
                    except Exception as exc:
                        errors.append(exc)
                        stop.set()

            tw = threading.Thread(target=writer)
            tr = threading.Thread(target=reader)
            tw.start(); tr.start(); tw.join(); tr.join()
            self.assertEqual(errors, [])
            self.assertEqual(read_json_locked(path)["seq"], 79)

    def test_runtime_mailbox_can_remove_preview_under_same_lock(self):
        from seer_client.runtime_mailbox import read_bytes_locked, remove_file_locked

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "preview.jpg"
            path.write_bytes(b"old-jpeg")
            self.assertEqual(read_bytes_locked(path), b"old-jpeg")
            remove_file_locked(path)
            self.assertFalse(path.exists())

    def test_preview_status_marks_motion_as_not_transmitted(self):
        source = (SRC / "docking_action.py").read_text(encoding="utf-8")
        webui = (SRC / "webui.py").read_text(encoding="utf-8")
        self.assertIn('"motion_transmitted": bool(live)', source)
        self.assertIn('Preview applied v / w', webui)

    def test_stream_has_stale_connection_recovery(self):
        webui = (SRC / "webui.py").read_text(encoding="utf-8")
        self.assertIn("time.monotonic() - last_frame_seen > 3.0", webui)
        self.assertIn("Date.now()-lastPreviewAdvance>2500", webui)
        self.assertIn("read_bytes_snapshot(preview_path", webui)

    def test_live_action_performs_preview_to_live_handoff(self):
        bridge = (SRC / "bridge.py").read_text(encoding="utf-8")
        self.assertIn('action_type == "seerCameraDockPreview"', bridge)
        self.assertIn('task.cancel()', bridge)
        self.assertIn('await asyncio.wait_for(asyncio.shield(task), timeout=4.0)', bridge)
        self.assertIn('current_task.cancelling()', bridge)
        self.assertIn('PREVIEW -> LIVE handoff', bridge)

    def test_camera_runtime_uses_cross_process_lease_and_clears_old_jpeg(self):
        source = (SRC / "docking_action.py").read_text(encoding="utf-8")
        self.assertIn("class CameraLease", source)
        self.assertIn("os.O_EXCL", source)
        self.assertIn("_clear_stale_preview(preview_path)", source)
        self.assertIn("await asyncio.to_thread(camera_lease.acquire", source)
        self.assertIn("camera_lease.release()", source)
        self.assertIn('"CAMERA_STREAMING"', source)
        self.assertIn("remove_file_locked", source)

    def test_camera_lease_blocks_second_owner_until_release(self):
        from seer_client.docking_action import CameraBusyError, CameraLease
        with tempfile.TemporaryDirectory() as td:
            original = tempfile.gettempdir
            tempfile.gettempdir = lambda: td
            try:
                one = CameraLease.for_camera({"type": "realsense", "serial": "unit-test"}, robot_ip="a", action_id="1", run_mode="PREVIEW")
                two = CameraLease.for_camera({"type": "realsense", "serial": "unit-test"}, robot_ip="b", action_id="2", run_mode="LIVE")
                one.acquire()
                with self.assertRaises(CameraBusyError):
                    two.acquire()
                one.release()
                two.acquire()
                two.release()
            finally:
                tempfile.gettempdir = original


    def test_camera_lease_can_wait_for_preview_handoff_release(self):
        from seer_client.docking_action import CameraLease

        with tempfile.TemporaryDirectory() as td:
            original = tempfile.gettempdir
            tempfile.gettempdir = lambda: td
            try:
                preview = CameraLease.for_camera(
                    {"type": "realsense", "serial": "handoff-test"},
                    robot_ip="a",
                    action_id="preview",
                    run_mode="PREVIEW",
                )
                live = CameraLease.for_camera(
                    {"type": "realsense", "serial": "handoff-test"},
                    robot_ip="a",
                    action_id="live",
                    run_mode="LIVE",
                )
                preview.acquire()
                releaser = threading.Thread(
                    target=lambda: (time.sleep(0.08), preview.release()),
                    daemon=True,
                )
                releaser.start()
                started = time.monotonic()
                live.acquire(wait_timeout_s=0.5, poll_interval_s=0.02)
                self.assertGreaterEqual(time.monotonic() - started, 0.05)
                live.release()
                releaser.join(timeout=1.0)
            finally:
                tempfile.gettempdir = original

    def test_live_preflight_refreshes_state_before_safety_decision(self):
        from seer_client.docking_action import _live_control_preflight

        class FakeControl:
            def __init__(self):
                self.calls = 0

            async def drive_native(self, **_kwargs):
                self.calls += 1
                return {"ret_code": 0}

        class FakeVehicle:
            _robot_model = "SBA-400EU"
            _mode = "manual"
            _emergency = False
            _physical_emergency = False
            _driver_emergency = False
            _blocked = False
            _motor_flag = True

            def __init__(self):
                self.control = FakeControl()
                self.stale = True
                self.refresh_calls = 0

            def is_connected(self):
                return True

            def is_rx_stale(self, _after_s):
                return self.stale

            async def _poll_once(self):
                self.refresh_calls += 1
                self.stale = False

            def connection_health(self):
                return {"ports": {"CONTROL": {"connected": True}}}

        vehicle = FakeVehicle()
        events = []
        response, _health, _warning = asyncio.run(
            _live_control_preflight(
                vehicle,
                {"seer": {"preflight_retries": 1, "preflight_retry_delay_s": 0.0}},
                lambda status, phase, message, **fields: events.append((status, phase, message, fields)),
            )
        )
        self.assertEqual(response["ret_code"], 0)
        self.assertEqual(vehicle.refresh_calls, 1)
        self.assertEqual(vehicle.control.calls, 1)
        self.assertEqual(events[0][3]["state_refresh"], "STATE refreshed")

    def test_live_recording_captures_startup_failure_before_first_camera_frame(self):
        from seer_client.docking_action import DockingSessionRecorder

        with tempfile.TemporaryDirectory() as td:
            recorder = DockingSessionRecorder(
                Path(td),
                session_id="startup-fail-test",
                started_at=time.time(),
                robot_ip="192.0.2.1",
                action_id="live-test",
                run_mode="LIVE",
                config={"enabled": True, "live_only": True, "video_fps": 5},
            )
            recorder.log_telemetry(
                {
                    "status": "failed",
                    "phase": "LIVE_PREFLIGHT",
                    "note": "API 2010 zero probe timeout",
                    "blocked": False,
                    "emergency": False,
                }
            )
            recorder.finish(status="failed", phase="FAILED", message="preflight failed")
            rows = recorder.csv_path.read_text(encoding="utf-8-sig")
            self.assertIn("LIVE_PREFLIGHT", rows)
            self.assertIn("API 2010 zero probe timeout", rows)

    def test_live_preflight_runs_before_camera_ownership(self):
        source = (SRC / "docking_action.py").read_text(encoding="utf-8")
        preflight = source.index("await _live_control_preflight(")
        lease = source.index("CameraLease.for_camera(", preflight)
        self.assertLess(preflight, lease)
        self.assertIn('"CAMERA_HANDOFF"', source)
        cfg = json.loads((ROOT / "config" / "docking.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(float(cfg["camera"]["live_handoff_wait_s"]), 4.0)

    def test_live_preflight_lets_control_request_reconnect_initially_disconnected_port(self):
        from seer_client.docking_action import _live_control_preflight

        class FakeControl:
            def __init__(self, owner):
                self.owner = owner
                self.calls = 0

            async def drive_native(self, **kwargs):
                self.calls += 1
                self.owner.control_connected = True
                return {"ret_code": 0, "echo": kwargs}

        class FakeVehicle:
            _robot_model = "SBA-400EU"
            _mode = "manual"
            _emergency = False
            _physical_emergency = False
            _driver_emergency = False
            _blocked = False
            _motor_flag = True

            def __init__(self):
                self.control_connected = False
                self.control = FakeControl(self)

            def is_connected(self):
                return True

            def is_rx_stale(self, _after_s):
                return False

            def connection_health(self):
                return {
                    "ports": {
                        "CONTROL": {
                            "connected": self.control_connected,
                            "reconnect_count": 1 if self.control_connected else 0,
                            "last_error": "" if self.control_connected else "socket closed",
                        }
                    }
                }

        vehicle = FakeVehicle()
        events = []
        response, health, warning = asyncio.run(
            _live_control_preflight(
                vehicle,
                {
                    "seer": {
                        "preflight_retries": 3,
                        "preflight_retry_delay_s": 0.0,
                        "status_timeout_s": 1.5,
                        "require_manual_mode": True,
                        "strict_model_match": False,
                    }
                },
                lambda status, phase, message, **fields: events.append((status, phase, message, fields)),
            )
        )
        self.assertEqual(response["ret_code"], 0)
        self.assertTrue(health["connected"])
        self.assertEqual(warning, "")
        self.assertEqual(vehicle.control.calls, 1)
        self.assertIn("LIVE_READY", [event[1] for event in events])

    def test_live_preflight_keeps_hard_safety_interlocks(self):
        from seer_client.docking_action import _live_control_preflight

        class FakeControl:
            def __init__(self):
                self.calls = 0

            async def drive_native(self, **_kwargs):
                self.calls += 1
                return {"ret_code": 0}

        class FakeVehicle:
            _robot_model = "SBA-400EU"
            _mode = "manual"
            _emergency = True
            _physical_emergency = False
            _driver_emergency = False
            _blocked = False
            _motor_flag = True

            def __init__(self):
                self.control = FakeControl()

            def is_connected(self):
                return True

            def is_rx_stale(self, _after_s):
                return False

            def connection_health(self):
                return {"ports": {"CONTROL": {"connected": True}}}

        vehicle = FakeVehicle()
        with self.assertRaisesRegex(RuntimeError, "SEER emergency"):
            asyncio.run(
                _live_control_preflight(
                    vehicle,
                    {"seer": {"preflight_retries": 3, "preflight_retry_delay_s": 0.0}},
                    lambda *_args, **_kwargs: None,
                )
            )
        self.assertEqual(vehicle.control.calls, 0)


    def test_live_preflight_treats_combined_electric_false_as_advisory(self):
        from seer_client.docking_action import _live_control_preflight

        class FakeControl:
            def __init__(self):
                self.calls = 0

            async def drive_native(self, **_kwargs):
                self.calls += 1
                return {"ret_code": 0}

        class FakeVehicle:
            _robot_model = "SBA-400EU"
            _mode = "auto"
            _emergency = False
            _physical_emergency = False
            _driver_emergency = False
            _blocked = False
            _motor_flag = False
            _motor_flag_source = "electric"
            _electric_state = False

            def __init__(self):
                self.control = FakeControl()

            def is_connected(self):
                return True

            def is_rx_stale(self, _after_s):
                return False

            def connection_health(self):
                return {"ports": {"CONTROL": {"connected": True}}}

        vehicle = FakeVehicle()
        events = []
        response, _health, _warning = asyncio.run(
            _live_control_preflight(
                vehicle,
                {"seer": {"preflight_retries": 1, "preflight_retry_delay_s": 0.0}},
                lambda status, phase, message, **fields: events.append((status, phase, message, fields)),
            )
        )
        self.assertEqual(response["ret_code"], 0)
        self.assertEqual(vehicle.control.calls, 1)
        self.assertIn("LIVE_READY", [event[1] for event in events])
        preflight_fields = next(event[3] for event in events if event[1] == "LIVE_PREFLIGHT")
        self.assertEqual(preflight_fields["motor_flag_source"], "electric")
        self.assertIn("advisory", preflight_fields["motor_warning"])

    def test_live_preflight_still_blocks_explicit_motor_disabled(self):
        from seer_client.docking_action import _live_control_preflight

        class FakeControl:
            def __init__(self):
                self.calls = 0

            async def drive_native(self, **_kwargs):
                self.calls += 1
                return {"ret_code": 0}

        class FakeVehicle:
            _robot_model = "SBA-400EU"
            _mode = "auto"
            _emergency = False
            _physical_emergency = False
            _driver_emergency = False
            _blocked = False
            _motor_flag = False
            _motor_flag_source = "motor_enabled"
            _electric_state = False

            def __init__(self):
                self.control = FakeControl()

            def is_connected(self):
                return True

            def is_rx_stale(self, _after_s):
                return False

            def connection_health(self):
                return {"ports": {"CONTROL": {"connected": True}}}

        vehicle = FakeVehicle()
        with self.assertRaisesRegex(RuntimeError, "SEER motor disabled"):
            asyncio.run(
                _live_control_preflight(
                    vehicle,
                    {"seer": {"preflight_retries": 1, "preflight_retry_delay_s": 0.0}},
                    lambda *_args, **_kwargs: None,
                )
            )
        self.assertEqual(vehicle.control.calls, 0)

    def test_lidar_wall_pose_can_rescue_depth_only_tag_rejection(self):
        source = (SRC / "docking_action.py").read_text(encoding="utf-8")
        self.assertIn("depth_only_rejected = True", source)
        self.assertIn("lidar_pose is not None and depth_only_rejected", source)
        self.assertIn("detection = raw_detection", source)
        self.assertIn("lidar_pose is not None or rgbd_pose is not None or detection.angle_reliable", source)

    def test_camera_action_starts_without_page_reload_and_keeps_last_pose(self):
        webui = (SRC / "webui.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(webui.count('data-dock-async="1"'), 2)
        self.assertIn("function submitDockAction(form)", webui)
        self.assertIn("ev.preventDefault();submitDockAction(form)", webui)
        self.assertIn("function burstPoll()", webui)
        self.assertIn("if(d&&hasPose(d))lastPose=d", webui)
        self.assertIn("if(lastPose)drawPose(lastPose)", webui)
        self.assertIn("actionRequestAtMs=Date.now()", webui)
        self.assertIn('document.querySelectorAll("form[data-dock-async]")', webui)
        self.assertNotIn('document.querySelectorAll("form[data-dock-async=\\"1\\"]")', webui)

    def test_terminal_docking_status_keeps_last_pose_fields(self):
        source = (SRC / "docking_action.py").read_text(encoding="utf-8")
        self.assertIn("last_runtime_fields = dict(status_fields)", source)
        self.assertIn('publish("cancelled", final_phase, final_message, **last_runtime_fields)', source)
        self.assertIn('"terminal_message": final_message', source)
        self.assertIn('"failed", final_phase, final_message,', source)

    def test_live_status_exposes_control_probe_and_motion_errors(self):
        source = (SRC / "docking_action.py").read_text(encoding="utf-8")
        webui = (SRC / "webui.py").read_text(encoding="utf-8")
        self.assertIn("control_probe_response", source)
        self.assertIn("last_motion_error", source)
        self.assertIn('id="dock-control-port"', webui)
        self.assertIn('id="dock-control-probe"', webui)
        self.assertIn('id="dock-control-error"', webui)
        self.assertIn('id="dock-motion-error"', webui)
        self.assertIn("_control_port_health", source)


    def test_preview_frame_telemetry_uses_session_sidecar(self):
        from seer_client.docking_action import preview_telemetry_path
        with tempfile.TemporaryDirectory() as td:
            preview = Path(td) / "seer-docking-preview.jpg"
            self.assertEqual(
                preview_telemetry_path(preview).name,
                "seer-docking-preview-telemetry.json",
            )
        source = (SRC / "docking_action.py").read_text(encoding="utf-8")
        self.assertIn('"session_id": session_id', source)
        self.assertIn('telemetry_path=telemetry_path', source)
        self.assertIn('"preview_seq": int(preview_seq + 1)', source)

    def test_webui_merges_current_frame_telemetry_into_status_table(self):
        webui = (SRC / "webui.py").read_text(encoding="utf-8")
        self.assertIn('frame_session == expected_session', webui)
        self.assertIn('payload["telemetry_source"] = "preview-frame"', webui)
        self.assertIn('id="dock-telemetry-source"', webui)
        self.assertIn('id="dock-status-errors"', webui)

    def test_stream_validates_current_run_without_hammering_status_mailboxes(self):
        webui = (SRC / "webui.py").read_text(encoding="utf-8")
        stream_start = webui.index('if path == "/seer/docking/stream.mjpg"')
        stream_end = webui.index('if path == "/seer/docking/preview.jpg"', stream_start)
        stream = webui[stream_start:stream_end]
        # Capture action start once, then gate JPEGs by mtime/freshness. The
        # tight MJPEG loop must never lock status/telemetry on every frame.
        self.assertIn('stream_started_at', stream)
        self.assertIn('frame_mtime + 0.05 < stream_started_at', stream)
        self.assertIn('time.time() - frame_mtime > 3.0', stream)
        while_start = stream.index('while True:')
        hot_loop = stream[while_start:]
        self.assertNotIn('read_json_locked(status_path', hot_loop)
        self.assertNotIn('_docking_preview_telemetry_path(preview_path)', hot_loop)

    def test_status_endpoint_reads_frame_telemetry_without_writer_starvation(self):
        webui = (SRC / "webui.py").read_text(encoding="utf-8")
        reader_start = webui.index('def _read_camera_docking_status')
        reader_end = webui.index('def _docking_value', reader_start)
        reader = webui[reader_start:reader_end]
        self.assertIn('read_json_snapshot(telemetry_path', reader)
        self.assertIn('read_json_snapshot(status_path', reader)
        self.assertNotIn('read_json_locked(telemetry_path', reader)
        self.assertNotIn('read_json_locked(status_path', reader)
        self.assertIn('payload["telemetry_source"] = "preview-frame"', reader)
        self.assertIn('payload["telemetry_read_error"]', reader)

    def test_realsense_open_probe_uses_disposable_process_timeout(self):
        camera_source = (SRC.parent / "seer_docking" / "camera_source.py").read_text(encoding="utf-8")
        docking = (SRC / "docking_action.py").read_text(encoding="utf-8")
        cfg = json.loads((ROOT / "config" / "docking.json").read_text(encoding="utf-8"))
        self.assertIn("def probe_realsense_camera", camera_source)
        self.assertIn("subprocess.run", camera_source)
        self.assertIn("except subprocess.TimeoutExpired", camera_source)
        self.assertIn('"OPEN_TIMEOUT"', camera_source)
        self.assertIn('"CAMERA_PROBE"', docking)
        self.assertIn('"CAMERA_OPEN_FAILED"', docking)
        self.assertGreaterEqual(float(cfg["camera"]["open_timeout_s"]), 1.0)

    def test_quick_realsense_probe_does_not_resolve_or_open_pipeline(self):
        camera_source = (SRC.parent / "seer_docking" / "camera_source.py").read_text(encoding="utf-8")
        quick_start = camera_source.index("def probe_realsense_camera_quick")
        quick_end = camera_source.index("def create_camera_source", quick_start)
        quick = camera_source[quick_start:quick_end]
        self.assertIn("query_devices", quick)
        self.assertIn("get_stream_profiles", quick)
        self.assertNotIn("pipeline.start", quick)
        self.assertNotIn("config.resolve(wrapper)", quick)

    def test_close_range_display_keeps_raw_values_and_short_hold(self):
        source = (SRC / "docking_action.py").read_text(encoding="utf-8")
        cfg = json.loads((ROOT / "config" / "docking.json").read_text(encoding="utf-8"))
        self.assertIn("display_hold_s", source)
        self.assertIn('"tag_display_held": bool(display_held)', source)
        self.assertIn("last_display_metrics = dict(display_metrics)", source)
        self.assertLessEqual(float(cfg["marker_tracking"]["display_hold_s"]), 1.0)

    def test_missing_startup_telemetry_is_waiting_not_file_error(self):
        webui = (SRC / "webui.py").read_text(encoding="utf-8")
        self.assertIn('payload["telemetry_read_error"] = "Waiting for first camera frame"', webui)
        self.assertIn('"No camera frame produced; see CAMERA_OPEN_FAILED message"', webui)
        self.assertIn('id="dock-camera-profile"', webui)
        self.assertIn('id="dock-camera-probe"', webui)

    def test_status_file_marks_stale_starting_heartbeat(self):
        from seer_client.docking_action import read_docking_status
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "status.json"
            path.write_text(json.dumps({"status": "starting", "phase": "CAMERA_OPEN", "updated_at": time.time() - 10}), encoding="utf-8")
            status = read_docking_status(path, max_age_sec=1.0)
            self.assertEqual(status["status"], "stale")
            self.assertEqual(status["phase"], "STALE")

    def test_status_file_marks_stale_running_heartbeat(self):
        from seer_client.docking_action import read_docking_status
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "status.json"
            path.write_text(json.dumps({"status": "running", "phase": "CENTERLINE_TURN", "updated_at": time.time() - 10}), encoding="utf-8")
            status = read_docking_status(path, max_age_sec=1.0)
            self.assertEqual(status["status"], "stale")
            self.assertEqual(status["phase"], "STALE")

    def test_camera_page_has_tag_relative_amr_pose_top_view(self):
        webui = (SRC / "webui.py").read_text(encoding="utf-8")
        self.assertIn('id="dock-pose-canvas"', webui)
        self.assertIn('id="dock-pose-x"', webui)
        self.assertIn('id="dock-pose-y"', webui)
        self.assertIn('id="dock-pose-yaw"', webui)
        self.assertIn('camera_mount_x_m', webui)
        self.assertIn('function drawPose(d)', webui)

    def test_top_view_draws_camera_from_rotation_center_mount_offset(self):
        webui = (SRC / "webui.py").read_text(encoding="utf-8")
        self.assertIn('camera_mount_y_m', webui)
        self.assertIn('cam=P(camForward,camLeft)', webui)
        self.assertIn('c.moveTo(sx,sy);c.lineTo(cam[0],cam[1])', webui)
        self.assertIn('camera +X ', webui)
        self.assertIn('머리 +X', webui)

    def test_display_pose_filter_holds_static_yaw_jitter_but_follows_motion(self):
        from seer_client.docking_action import _StableDisplayPoseFilter

        filt = _StableDisplayPoseFilter()
        stable = []
        jitter = [0.8, -0.7, 0.5, -0.4, 0.2, -0.2, 0.1, -0.1, 0.0]
        for index in range(90):
            _, _, yaw = filt.update(
                0.62,
                -0.0126,
                1.0 + jitter[index % len(jitter)],
                moving=False,
            )
            if index >= 45:
                stable.append(yaw)
        self.assertLess(max(stable) - min(stable), 0.25)

        for yaw_measurement in (2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0):
            _, _, yaw = filt.update(0.62, -0.0126, yaw_measurement, moving=True)
        self.assertGreater(yaw, 6.0)

    def test_live_docking_recording_is_enabled_and_exposed_in_webui(self):
        cfg = json.loads((ROOT / "config" / "docking.json").read_text(encoding="utf-8"))
        self.assertTrue(cfg["recording"]["enabled"])
        self.assertTrue(cfg["recording"]["live_only"])
        self.assertGreaterEqual(float(cfg["recording"]["video_fps"]), 5.0)
        source = (SRC / "docking_action.py").read_text(encoding="utf-8")
        webui = (SRC / "webui.py").read_text(encoding="utf-8")
        self.assertIn("class DockingSessionRecorder", source)
        self.assertIn("recorder.log_telemetry", source)
        self.assertIn("recorder.submit_frame", source)
        self.assertIn('path == "/seer/docking/recordings.json"', webui)
        self.assertIn('path == "/seer/docking/replay"', webui)
        self.assertIn('path == "/seer/docking/recording/stream.mjpg"', webui)
        self.assertIn('name=camera_commands.avi', webui)
        self.assertIn('name=telemetry.csv', webui)

    def test_docking_session_recorder_writes_video_csv_and_metadata(self):
        import numpy as np
        from seer_client.docking_action import DockingSessionRecorder

        with tempfile.TemporaryDirectory() as td:
            started = time.time()
            recorder = DockingSessionRecorder(
                Path(td),
                session_id="0123456789abcdef",
                started_at=started,
                robot_ip="192.168.43.103",
                action_id="test-action",
                run_mode="LIVE",
                config={"enabled": True, "live_only": True, "video_fps": 20, "retain_sessions": 3},
            )
            base = {
                "status": "running", "phase": "CENTERLINE_TURN",
                "tag_visible": True, "tag_control_valid": True,
                "axis_distance_m": 1.1, "axis_error_m": 0.004,
                "yaw_error_deg": 1.25, "du_error_px": 3.0, "tag_depth_m": 0.9,
                "target_v_mps": 0.04, "target_w_rps": -0.03,
                "sent_v_mps": 0.03, "sent_w_rps": -0.02,
                "motor_left_cmd_rpm": 110.0, "motor_right_cmd_rpm": 95.0,
                "seer_vx_mps": 0.028, "seer_w_rps": -0.018,
                "blocked": False, "emergency": False, "note": "unit-test",
            }
            frame = np.zeros((120, 320, 3), dtype=np.uint8)
            for _ in range(4):
                recorder.log_telemetry({"timestamp_epoch_s": time.time(), **base})
                recorder.submit_frame(frame, {"frame_updated_at": time.time(), **base})
                time.sleep(0.06)
            recorder.finish(status="finished", phase="DOCKED", message="done")
            self.assertIsNotNone(recorder.session_dir)
            session_dir = recorder.session_dir
            self.assertTrue((session_dir / "telemetry.csv").is_file())
            self.assertGreater((session_dir / "telemetry.csv").stat().st_size, 100)
            self.assertTrue((session_dir / "camera_commands.avi").is_file())
            self.assertGreater((session_dir / "camera_commands.avi").stat().st_size, 100)
            meta = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["status"], "finished")
            self.assertEqual(meta["phase"], "DOCKED")
            self.assertGreaterEqual(int(meta["telemetry_rows"]), 4)
            self.assertGreaterEqual(int(meta["video_frames"]), 2)


class CameraDockingStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_safe_stop_uses_control_api2000_without_task_cancel_path(self):
        from seer_client.docking_action import _safe_stop

        class FakeVehicle:
            command_timeout = 0.05
            def __init__(self):
                self.send_command = AsyncMock(return_value={"ret_code": 0})
                self.control = type("Control", (), {})()
                self.control.stop = AsyncMock(side_effect=AssertionError("공용 stop은 TASK cancel까지 호출하므로 도킹에서 사용하면 안 됨"))
                self.control.drive_native = AsyncMock(return_value={"ret_code": 0})
                self._vx = self._vy = self._w = 1.0

        vehicle = FakeVehicle()
        result = await _safe_stop(vehicle)
        self.assertIn("API 2000", result)
        vehicle.send_command.assert_awaited_once()
        self.assertEqual(vehicle.send_command.await_args.args[0], "stop")
        vehicle.control.stop.assert_not_awaited()
        vehicle.control.drive_native.assert_not_awaited()
        self.assertEqual((vehicle._vx, vehicle._vy, vehicle._w), (0.0, 0.0, 0.0))

    async def test_safe_stop_uses_zero_fallback_after_stop_error(self):
        from seer_client.docking_action import _safe_stop

        class FakeVehicle:
            command_timeout = 0.05
            def __init__(self):
                self.send_command = AsyncMock(side_effect=TimeoutError("stop timeout"))
                self.control = type("Control", (), {})()
                self.control.drive_native = AsyncMock(return_value={"ret_code": 0})
                self._vx = self._vy = self._w = 1.0

        vehicle = FakeVehicle()
        result = await _safe_stop(vehicle)
        self.assertIn("zero fallback confirmed", result)
        vehicle.send_command.assert_awaited_once()
        vehicle.control.drive_native.assert_awaited_once()
        self.assertEqual((vehicle._vx, vehicle._vy, vehicle._w), (0.0, 0.0, 0.0))

    async def test_zero_control_probe_reports_transient_failure_without_nonzero_retry(self):
        from seer_client.docking_action import _zero_control_probe

        class FakeVehicle:
            def __init__(self):
                self.control = type("Control", (), {})()
                self.control.drive_native = AsyncMock(side_effect=TimeoutError("probe timeout"))

        vehicle = FakeVehicle()
        ok, detail, response = await _zero_control_probe(vehicle, timeout_s=0.05)
        self.assertFalse(ok)
        self.assertIsNone(response)
        self.assertIn("TimeoutError", detail)
        kwargs = vehicle.control.drive_native.await_args.kwargs
        self.assertEqual(kwargs["vx_mps"], 0.0)
        self.assertEqual(kwargs["w_rad_s"], 0.0)

    def test_runtime_blocked_recovery_keeps_hard_stop_but_requires_zero_probe_before_resume(self):
        source = (SRC / "docking_action.py").read_text(encoding="utf-8")
        self.assertIn("HARD STOP {stop_reason}", source)
        self.assertIn("entering_hard_stop = not hard_stop_latched", source)
        self.assertIn("if entering_hard_stop:", source)
        self.assertIn("_zero_control_probe", source)
        self.assertIn("safety clear, {probe_detail}", source)
        self.assertIn("CONTROL ACK timeout tolerated", source)
        self.assertIn("max_consecutive_motion_ack_timeouts", source)
        self.assertIn("motion_command_hz", source)
        self.assertIn("CONTROL ACK timeout x{consecutive_motion_ack_timeouts}", source)
        self.assertIn('"block_reason": str(getattr(vehicle, "_block_reason", "") or "")', source)
        self.assertIn('"terminal_message": final_message', source)

    def test_manual_stage_keeps_10hz_and_tolerates_poor_wifi_control_ack_gaps(self):
        source = (SRC / "docking_action.py").read_text(encoding="utf-8")
        cfg = json.loads((ROOT / "config" / "docking.json").read_text(encoding="utf-8"))
        stage = cfg["manual_stage"]
        self.assertAlmostEqual(stage["control_motion_hz"], 10.0, places=9)
        self.assertEqual(stage["control_duration_ms"], 450)
        self.assertAlmostEqual(stage["control_ack_timeout_s"], 0.28, places=9)
        self.assertAlmostEqual(stage["control_loss_grace_s"], 4.0, places=9)
        self.assertIn("_motion_command_observed_in_state", source)
        self.assertIn("manual stage CONTROL transport unavailable", source)
        self.assertIn("STATE confirm={'yes' if state_confirmed else 'no'}", source)
        self.assertNotIn("manual stage CONTROL ACK timeout x{consecutive_motion_ack_timeouts};", source)



if __name__ == "__main__":
    unittest.main()
