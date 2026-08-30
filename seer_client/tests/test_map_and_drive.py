from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
for source in ("amr-client-contract/src", "seer_client/src", "adaptor"):
    path = str(REPO_ROOT / source)
    if path not in sys.path:
        sys.path.insert(0, path)

from seer_client.bridge import SeerSimulatedAdapterClient  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.map_view import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    SEER_MAP_INTERACTION_JS,
    clear_active_route,
    find_reentry_options,
    find_route_options,
    normalize_map,
    read_active_route,
    reconcile_active_route,
    render_map_card,
    write_active_route,
    write_map_cache,
)
from seer_client.io_cache import SeerIOCache  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.status import read_controller_status_cache, write_controller_status_cache  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.protocol import ApiNumber  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.webui import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    _SEER_IO_VIEWS,
    _render_seer_io_page,
    SeerWebUiApplication,
    _SEER_AWARE_JOG_JS,
)


def _config():
    return SimpleNamespace(
        factsheet=SimpleNamespace(
            coordinate_unit_position="mm",
            coordinate_unit_orientation="deg",
        ),
        settings=SimpleNamespace(map_id="webui-map", nearest_node_mode="pathPoint"),
        bms_ros=SimpleNamespace(enabled=True),
        manual_control=SimpleNamespace(
            drive_trans=200.0, drive_rot=30.0, drive_speed=200.0
        ),
    )


class SeerMapAndDriveTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_route_survives_disconnect_and_reconciles_after_reconnect(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "active-route.json"
            write_active_route(marker, ("LM1", "LM2", "LM3"))
            self.assertEqual(
                reconcile_active_route(
                    marker,
                    SimpleNamespace(
                        connection_state="OFFLINE",
                        working_state="IDLE",
                        driving=False,
                        paused=False,
                        last_node_id="LM1",
                    ),
                ),
                ("LM1", "LM2", "LM3"),
            )
            self.assertEqual(
                reconcile_active_route(
                    marker,
                    SimpleNamespace(
                        connection_state="ONLINE",
                        working_state="IDLE",
                        driving=False,
                        paused=False,
                        last_node_id="LM2",
                        errors=[{"errorType": "JIBOT_CONNECTION_LOST"}],
                    ),
                    now=time.time() + 60.0,
                ),
                ("LM1", "LM2", "LM3"),
            )
            self.assertEqual(
                reconcile_active_route(
                    marker,
                    SimpleNamespace(
                        connection_state="ONLINE",
                        working_state="DRIVING",
                        driving=True,
                        paused=False,
                        last_node_id="",
                    ),
                ),
                ("LM1", "LM2", "LM3"),
            )
            self.assertEqual(
                reconcile_active_route(
                    marker,
                    SimpleNamespace(
                        connection_state="ONLINE",
                        working_state="IDLE",
                        driving=False,
                        paused=False,
                        last_node_id="LM3",
                    ),
                ),
                (),
            )
            self.assertFalse(marker.exists())


    async def test_active_route_survives_idle_sample_at_intermediate_node(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "active-route.json"
            write_active_route(
                marker,
                ("LM7", "LM3", "LM4", "LM1"),
                order_id="webui-order-1",
            )
            self.assertEqual(
                reconcile_active_route(
                    marker,
                    SimpleNamespace(
                        connection_state="ONLINE",
                        working_state="IDLE",
                        driving=False,
                        paused=False,
                        last_node_id="LM3",
                        order_id="webui-order-1",
                        node_states=[],
                        edge_states=[],
                        errors=[],
                    ),
                    now=time.time() + 60.0,
                ),
                ("LM7", "LM3", "LM4", "LM1"),
            )
            self.assertTrue(marker.exists())

    async def test_active_route_is_replaced_by_new_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "active-route.json"
            write_active_route(
                marker,
                ("LM7", "LM3", "LM4", "LM1"),
                order_id="webui-order-1",
            )
            self.assertEqual(
                reconcile_active_route(
                    marker,
                    SimpleNamespace(
                        connection_state="ONLINE",
                        working_state="IDLE",
                        driving=False,
                        paused=False,
                        last_node_id="LM3",
                        order_id="webui-order-2",
                        node_states=[],
                        edge_states=[],
                        errors=[],
                    ),
                    now=time.time() + 60.0,
                ),
                (),
            )
            self.assertFalse(marker.exists())

    def test_runtime_webui_patch_renders_seer_speed_units_and_defaults(self):
        from web import render  # pyright: ignore[reportMissingImports]

        SeerWebUiApplication._patch_seer_operator_ui()
        spec = SimpleNamespace(manufacturer="seer", key="seer:test")
        card = render._jog_card(spec, csrf="token")
        self.assertIn('name="linear_speed" value="0.05"', card)
        self.assertIn('name="angular_speed_deg" value="5.0"', card)
        self.assertIn("전후진 속도 (m/s)", card)
        self.assertIn("회전 속도 (deg/s)", card)
        self.assertIn("Path Nav · VDA5050 /order", card)
        self.assertIn('name="id"', card)
        self.assertIn('name="source_id"', card)
        self.assertIn('value="" placeholder="비우면 현재 위치 자동"', card)
        self.assertIn('name="confirm" required', card)
        self.assertIn('name="manual_armed"', card)
        self.assertIn('수동조작 활성화', card)
        self.assertIn('30초 동안 방향키·버튼·속도 입력이 없으면 자동 OFF', card)
        self.assertIn('숫자를 전부 지운 뒤 새 값을 입력할 수 있습니다.', card)

        action = SimpleNamespace(
            action_type="seerEmergencySwitch",
            label="SEER software emergency switch",
            motion=True,
        )
        emergency_spec = SimpleNamespace(
            manufacturer="seer", key="seer:test", instant_actions=(action,)
        )
        emergency = render._emergency_forms(
            emergency_spec,
            "token",
            snapshot=SimpleNamespace(active_emergency_stop="NONE"),
        )
        self.assertIn("seerEmergencySwitch", emergency)
        self.assertIn(">비상정지</button>", emergency)
        self.assertIn('aria-label="비상정지 비활성 상태"', emergency)
        self.assertIn("누르면 즉시 정지하고 진행 중인 작업을 취소합니다.", emergency)
        self.assertIn('class="muted estop-help"', emergency)
        self.assertIn("data-seer-estop-form", emergency)
        self.assertIn('type="submit"', emergency)
        self.assertLess(emergency.index("estop-help"), emergency.index(">비상정지</button>"))
        self.assertIn("#b4232f", emergency)
        self.assertNotIn("SEER SOFT E-STOP", emergency)
        self.assertNotIn("<br><small>", emergency)
        self.assertNotIn('name="confirm"', emergency)
        from web import server  # pyright: ignore[reportMissingImports]

        self.assertNotIn("seerEmergencySwitch", server._CONFIRM_REQUIRED_ACTIONS)
        self.assertIn("seerPathNav", server._CONFIRM_REQUIRED_ACTIONS)
        active_emergency = render._emergency_forms(
            emergency_spec,
            "token",
            snapshot=SimpleNamespace(active_emergency_stop="REMOTE"),
        )
        self.assertIn(">비상정지 해제</button>", active_emergency)
        self.assertIn('aria-label="비상정지 활성 상태"', active_emergency)
        self.assertIn("해제해도 이전 작업은 재개되지 않습니다.", active_emergency)
        self.assertIn('class="muted estop-help"', active_emergency)
        self.assertLess(
            active_emergency.index("estop-help"),
            active_emergency.index(">비상정지 해제</button>"),
        )
        self.assertIn("#13795b", active_emergency)
        self.assertNotIn("<br><small>", active_emergency)

        poll_script = render._poll_script(1)
        self.assertIn(".poll-controls", poll_script)
        self.assertIn(".seer-map-nav-dialog.is-open", poll_script)
        self.assertIn("window.__seerMapDragging", poll_script)
        self.assertIn(".seer-map-dragging", poll_script)
        self.assertIn(
            "if(f&&c&&!window.__seerEmergencyPending&&!window.__seerFormPending",
            poll_script,
        )
        self.assertIn("window.__seerMapZoom", poll_script)
        self.assertIn("window.__seerEmergencyUiInstalled", poll_script)
        self.assertIn("window.__seerEmergencyPending", poll_script)
        self.assertIn("window.__seerFormPending", poll_script)
        self.assertIn("event.preventDefault()", poll_script)
        self.assertIn("new DOMParser()", poll_script)
        self.assertIn("window.scrollTo(x,y)", poll_script)
        self.assertIn("child.tagName==='P'&&child.classList.contains('ok')", poll_script)
        self.assertNotIn("window.scrollBy", poll_script)
        self.assertNotIn("anchorTop", poll_script)
        self.assertEqual(poll_script.count("restoreViewport(scrollX,scrollY);"), 2)
        self.assertIn("data-seer-map-nav-form", poll_script)
        self.assertIn("data-seer-robot-size", poll_script)
        self.assertIn("data-seer-point-label-size", poll_script)
        self.assertIn("data-seer-point-label", poll_script)
        self.assertIn("current.labelScale", poll_script)
        self.assertIn("data-seer-map-fixed-point", poll_script)
        self.assertIn("var inverseScale=1/current.scale", poll_script)
        self.assertIn("localStorage.getItem(k)", poll_script)
        self.assertIn("localStorage.setItem(k,JSON.stringify(current))", poll_script)
        self.assertIn("sessionStorage.getItem(k)", poll_script)
        self.assertIn("sessionStorage.removeItem(k)", poll_script)
        self.assertIn("previewRoute(dialog)", poll_script)
        self.assertIn("input[name=\"navigation_mode\"]:checked", poll_script)
        self.assertIn("choice.dataset.seerRoutePoints", poll_script)
        self.assertIn("fetch(form.action", poll_script)
        self.assertIn("openPathDialog(point)", poll_script)
        self.assertIn("window.__amrSetPoll(1)", poll_script)
        self.assertEqual(render._refresh_secs({}), 1)
        self.assertEqual(render._refresh_secs({"refresh": "4"}), 4)

        any_page = render.page("SEER form test", "<p>body</p>")
        self.assertIn("window.__seerPreserveFormsInstalled", any_page)
        self.assertIn("form.closest('#content')", any_page)
        self.assertIn("event.preventDefault()", any_page)
        self.assertIn("event.submitter", any_page)
        self.assertIn("detachFlash(next)", any_page)
        self.assertIn("position:fixed", any_page)
        self.assertIn("window.scrollTo(x,y)", any_page)
        self.assertIn("data-seer-estop-form],[data-seer-map-nav-form]", any_page)
        self.assertIn(".ok,.error-notice{position:static!important", any_page)
        self.assertIn("['msg','err','act']", any_page)
        self.assertIn("window.history.replaceState", any_page)

    def test_runtime_webui_live_state_is_explicit(self):
        from web import render  # pyright: ignore[reportMissingImports]

        SeerWebUiApplication._patch_seer_operator_ui()
        spec = SimpleNamespace(
            manufacturer="seer", key="seer:live", monitor_kind="vda5050"
        )
        snapshot = SimpleNamespace(
            errors=(), node_states=(), edge_states=(), action_states=(),
            order_id="", order_update_id=0, active_emergency_stop="NONE",
            working_state="IDLE", charging=False, field_violation=False,
            connection_state="", adapter_online=True, operating_mode="AUTOMATIC",
            driving=False, paused=None, battery_soc=70.0, localization_score=1.0,
            last_node_id="SIM_START", map_id="webui-map", x=1000.0, y=2000.0,
            theta=90.0, header_id=1,
        )
        html = render._mqtt_live_table(spec, snapshot)
        self.assertIn("ONLINE", html)
        self.assertIn("RUNNING", html)
        self.assertIn("CLEAR", html)
        self.assertIn("blocked", html)

        snapshot.field_violation = True
        html = render._mqtt_live_table(spec, snapshot)
        self.assertIn("BLOCKED", html)

    def test_seer_io_page_renders_di_and_do_switches(self):
        from web import render  # pyright: ignore[reportMissingImports]

        SeerWebUiApplication._patch_seer_operator_ui()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "seer-io.json"
            SeerIOCache(path).update(
                {
                    "ret_code": 0,
                    "DI": [
                        {"id": 0, "source": "normal", "status": True, "valid": True},
                        {"id": 1, "source": "normal", "status": False, "valid": False},
                    ],
                    "DO": [
                        {"id": 3, "source": "normal", "status": True},
                        {"id": 4, "source": "normal", "status": False},
                    ],
                }
            )
            spec = SimpleNamespace(
                manufacturer="seer",
                key="seer:test",
                display_name="SEER TEST",
                serial="SEER-TEST-001",
            )
            _SEER_IO_VIEWS[spec.key] = path
            html = _render_seer_io_page(render, [spec], "csrf-token", {"refresh": "1"})
            self.assertIn('href="/io" aria-current="page"', html)
            self.assertIn("DI0", html)
            self.assertIn("DO3", html)
            self.assertIn('value="seerSetDO"', html)
            self.assertIn('name="id" value="3"', html)
            self.assertIn('name="status" value="false"', html)
            self.assertIn('role="switch" aria-checked="true"', html)
            self.assertIn(".seer-io-switch .thumb", html)
            self.assertIn("order:-1", html)
            self.assertIn(".seer-io-switch.on .thumb{order:1}", html)
            self.assertIn("API 1013", html)
            self.assertIn("API 6001", html)

    def test_attached_smap_shape_normalizes(self):
        sample = REPO_ROOT / "seer_client" / "sam_sdc_test_1.smap"
        raw = json.loads(sample.read_text(encoding="utf-8"))
        model = normalize_map(raw, max_normal_points=500)
        self.assertEqual(model["schema"], 3)
        self.assertLessEqual(len(model["normal_points"]), 500)
        self.assertEqual(len(model["bounds"]), 4)
        self.assertEqual(model["curves"][0]["start_name"], "LM8")
        self.assertEqual(model["curves"][0]["end_name"], "LM3")
        self.assertGreater(model["curves"][0]["length_m"], 0.0)

        routes = find_route_options(model, "LM1", "LM5")
        self.assertEqual(len(routes), 2)
        self.assertEqual(routes[0]["points"], ["LM1", "LM8", "LM6", "LM5"])
        self.assertEqual(
            routes[1]["points"], ["LM1", "LM8", "LM3", "LM4", "LM5"]
        )
        self.assertLess(routes[0]["distance_m"], routes[1]["distance_m"])

        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "seer-map.json"
            write_map_cache(cache, raw)
            card = render_map_card(
                cache,
                SimpleNamespace(
                    x=0.009,
                    y=0.168,
                    theta=0.0,
                    battery_soc=80.0,
                    map_id="sam_sdc_test_1",
                    last_node_id="LM1",
                ),
                position_unit="m",
                orientation_unit="rad",
                adapter_key="seer:test",
                csrf_token="test-token",
            )
            self.assertIn("지정 경로 1</strong>이 기본 선택입니다", card)
            self.assertIn("지정 경로 1 · 9.09 m", card)
            self.assertIn('data-seer-nav-label="지정 경로" data-seer-route-points=', card)
            self.assertIn('required checked><span><strong>지정 경로 1', card)
            self.assertIn("LM1 → LM8 → LM6 → LM5", card)
            self.assertLess(card.index("지정 경로 1"), card.index("VDA5050 Target Order · 현재 위치 자동"))
            self.assertIn('data-seer-route-count="2"', card)
            self.assertIn('name="route_points" value="" disabled', card)
            self.assertIn('name="waypoint_delay_sec" value="0"', card)
            self.assertIn("node의 actions[]에 blockingType=HARD인 seerWait Action", card)
            self.assertIn("routeField.disabled=!selectedRoute", SEER_MAP_INTERACTION_JS)
            self.assertIn("var selectedRoute=(choice.dataset&&choice.dataset.seerRoutePoints)||''", SEER_MAP_INTERACTION_JS)
            self.assertIn("finalUrl.searchParams.get('err')", SEER_MAP_INTERACTION_JS)
            self.assertIn('name="navigation_mode" value="path"', card)
            self.assertNotIn('name="navigation_mode" value="free"', card)
            self.assertNotIn("Free Nav · 직선거리", card)
            self.assertIn("VDA5050 nodes[] + edges[] order", card)
            self.assertNotIn("API 3051 freeGo", card)
            self.assertIn('data-seer-route-edge="LM1|LM8"', card)
            self.assertIn(" Q ", card)
            self.assertNotIn(" C ", card)
            self.assertIn("data-seer-robot-size", card)
            self.assertIn('min="35" max="150"', card)
            self.assertIn("data-seer-point-size", card)
            self.assertIn('min="35" max="200"', card)
            self.assertIn("data-seer-point-marker", card)
            self.assertIn('data-seer-point-base-radius="16"', card)
            self.assertIn("data-seer-point-label-size", card)
            self.assertIn("data-seer-map-fixed-point", card)
            self.assertIn('data-seer-point-label-base="11"', card)
            self.assertIn('dominant-baseline="central"', card)
            self.assertIn('stroke-linecap="round"', card)
            self.assertIn('stroke-linejoin="round"', card)
            self.assertIn("data-seer-route-base-layer", card)
            self.assertIn("data-seer-route-highlight-layer", card)
            self.assertIn("data-seer-route-layer-order", card)
            self.assertIn('value="above">노란선 위', card)
            self.assertIn('value="below">노란선 아래', card)
            self.assertIn("routeLayerOrder", SEER_MAP_INTERACTION_JS)
            self.assertIn("insertBefore(highlightLayer,baseLayer)", SEER_MAP_INTERACTION_JS)
            self.assertIn("LM 원", card)
            self.assertIn("LM 글자", card)

            active_route_cache = Path(temporary) / "seer-active-route.json"
            route_token = write_active_route(
                active_route_cache, ("LM1", "LM8", "LM6", "LM5")
            )
            active_card = render_map_card(
                cache,
                SimpleNamespace(
                    x=0.009,
                    y=0.168,
                    theta=0.0,
                    battery_soc=80.0,
                    map_id="sam_sdc_test_1",
                    last_node_id="LM1",
                ),
                position_unit="m",
                orientation_unit="rad",
                adapter_key="seer:test",
                csrf_token="test-token",
                active_route_path=active_route_cache,
            )
            self.assertEqual(
                read_active_route(active_route_cache),
                ("LM1", "LM8", "LM6", "LM5"),
            )
            self.assertIn(
                'class="seer-map-route-edge" data-seer-route-edge="LM1|LM8"',
                active_card,
            )
            self.assertIn(
                'class="seer-map-route-edge seer-route-active seer-route-highlight"',
                active_card,
            )
            self.assertNotIn(
                'class="seer-map-route-edge seer-route-active" data-seer-route-edge=',
                active_card,
            )
            self.assertLess(
                active_card.index('stroke="#23b8d1"'),
                active_card.index("data-seer-route-highlight-layer"),
            )
            self.assertIn(
                "노란색은 실행 중인 지정 경로입니다: LM1 → LM8 → LM6 → LM5",
                active_card,
            )
            self.assertTrue(clear_active_route(active_route_cache, token=route_token))
            self.assertEqual(read_active_route(active_route_cache), ())

            off_point_card = render_map_card(
                cache,
                SimpleNamespace(
                    x=99.0,
                    y=99.0,
                    theta=0.0,
                    battery_soc=80.0,
                    map_id="sam_sdc_test_1",
                    last_node_id="LM1",
                ),
                position_unit="m",
                orientation_unit="rad",
                adapter_key="seer:test",
                csrf_token="test-token",
                controller_status={"current_station": "LM1"},
            )
            self.assertIn("RoboShop LM1", off_point_card)
            self.assertIn("PathPoint나 Path 위에 없어도", off_point_card)
            self.assertIn('name="navigation_mode" value="path" data-seer-nav-label="VDA5050 Target Order" required checked', off_point_card)
            self.assertIn("VDA5050 Target Order · 현재 위치 자동", off_point_card)
            self.assertIn("목표 node 하나의 VDA5050 order를 만들고 Adapter의", off_point_card)
            self.assertNotIn('name="navigation_mode" value="reentry"', off_point_card)
            self.assertNotIn('name="navigation_mode" value="free" data-seer-nav-label="Free Nav" required', off_point_card)
            self.assertIn('name="source_id" value=""', off_point_card)

    async def test_reentry_prefers_controller_current_station_and_direct_target_reentry(self):
        sample = REPO_ROOT / "seer_client" / "sam_sdc_test_1.smap"
        raw = json.loads(sample.read_text(encoding="utf-8"))
        model = normalize_map(raw, max_normal_points=500)
        lm1 = next(item for item in model["advanced_points"] if item["name"] == "LM1")
        options = find_reentry_options(
            model,
            float(lm1["x"]) + 0.25,
            float(lm1["y"]),
            "LM5",
            preferred_source="LM1",
        )
        self.assertTrue(options)
        self.assertEqual(options[0]["entry"], "LM1")
        self.assertEqual(options[0]["points"][0], "LM1")
        self.assertEqual(options[0]["points"][-1], "LM5")

        target_options = find_reentry_options(
            model,
            float(lm1["x"]) + 0.25,
            float(lm1["y"]),
            "LM1",
            preferred_source="LM1",
        )
        self.assertEqual(target_options[0]["points"], ["LM1"])

    async def test_controller_status_cache_surfaces_roboshop_current_station(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "seer-controller-status.json"
            client = SimpleNamespace(
                _station="LM7", _target_id="", _task_status=0,
                _x=1000.0, _y=2000.0, _th=90.0,
            )
            write_controller_status_cache(path, client)
            payload = read_controller_status_cache(path)
            self.assertIsNotNone(payload)
            self.assertEqual(payload["current_station"], "LM7")

    async def test_webui_jog_combines_linear_and_turn_keys_without_request_pileup(self):
        self.assertIn("var forward=(pressed.up?1:0)-(pressed.down?1:0)", _SEER_AWARE_JOG_JS)
        self.assertIn("var turn=(pressed.left?1:0)-(pressed.right?1:0)", _SEER_AWARE_JOG_JS)
        self.assertIn("HEARTBEAT_MS=250", _SEER_AWARE_JOG_JS)
        self.assertIn("setInterval(function(){ if(moving) applyMotion(true); },HEARTBEAT_MS)", _SEER_AWARE_JOG_JS)
        self.assertIn("window.__seerBeforeAutonomousCommand=beforeAutonomousCommand", _SEER_AWARE_JOG_JS)
        self.assertIn("return !!(input&&input.checked&&armLatched);", _SEER_AWARE_JOG_JS)
        self.assertIn("ARM_IDLE_MS=30000", _SEER_AWARE_JOG_JS)
        self.assertIn("if(!armLatched||moving||Object.keys(pressed).length) return;", _SEER_AWARE_JOG_JS)
        self.assertIn("clearBeat(); scheduleArmIdle(); return;", _SEER_AWARE_JOG_JS)
        self.assertIn("setTimeout(function(){disarmManual('idle');},ARM_IDLE_MS)", _SEER_AWARE_JOG_JS)
        self.assertIn("window.addEventListener('pagehide',function(){disarmManual('pagehide');});", _SEER_AWARE_JOG_JS)
        self.assertNotIn("visibilitychange", _SEER_AWARE_JOG_JS)
        self.assertNotIn("addEventListener('blur'", _SEER_AWARE_JOG_JS)
        self.assertNotIn("seer-manual-arm:", _SEER_AWARE_JOG_JS)
        self.assertIn("b.set('armed','on');", _SEER_AWARE_JOG_JS)
        self.assertIn("if(inFlight||!queued){resolveDrainWaiters();return;}", _SEER_AWARE_JOG_JS)
        self.assertIn("releaseDir(d)", _SEER_AWARE_JOG_JS)
        self.assertIn("seer-manual-speed:", _SEER_AWARE_JOG_JS)
        self.assertIn("localStorage.setItem", _SEER_AWARE_JOG_JS)
        self.assertIn("localStorage.getItem", _SEER_AWARE_JOG_JS)
        self.assertIn("restoreCurrentSettings();", _SEER_AWARE_JOG_JS)
        self.assertIn("typeof saved.linear==='string'", _SEER_AWARE_JOG_JS)
        self.assertIn("String(linear.value)", _SEER_AWARE_JOG_JS)
        self.assertIn("if(TRANS===null||ROT===null) return null;", _SEER_AWARE_JOG_JS)
        self.assertIn("MutationObserver", _SEER_AWARE_JOG_JS)
        self.assertIn("current!==lastSettingsRoot", _SEER_AWARE_JOG_JS)
        self.assertNotIn("new MutationObserver(function(){ restoreCurrentSettings(); })", _SEER_AWARE_JOG_JS)

    async def test_simulator_downloads_map_and_manual_drive_uses_native_units(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "seer-map.json"
            io_cache = Path(temporary) / "seer-io.json"
            with patch.dict(
                os.environ,
                {
                    "SEER_MAP_CACHE_PATH": str(cache),
                    "SEER_IO_CACHE_PATH": str(io_cache),
                },
                clear=False,
            ):
                config = _config()
                client = SeerSimulatedAdapterClient(
                    config=config,
                    initial_position={"x": 1000, "y": 2000, "theta": 90},
                    initial_battery=70,
                )
                try:
                    await client.connect_socket()
                    deadline = asyncio.get_running_loop().time() + 2.0
                    while not cache.exists() and asyncio.get_running_loop().time() < deadline:
                        await asyncio.sleep(0.02)
                    self.assertTrue(cache.exists())
                    cached = json.loads(cache.read_text(encoding="utf-8"))
                    self.assertEqual(cached["map_name"], "webui-map")
                    self.assertIn("SIM_START", client._map_nodes)
                    self.assertEqual(len(cached["curves"]), 8)
                    self.assertEqual(
                        len(find_route_options(cached, "SIM_START", "SIM_GOAL")),
                        2,
                    )
                    self.assertEqual(config.manual_control.drive_trans, 0.05)
                    self.assertEqual(config.manual_control.drive_rot, 5.0)

                    await client.um_drive(0.05, 5.0, 0.05, 0.0)
                    command = await client._simulator_server.wait_for_command(
                        ApiNumber.CONTROL_MOTION
                    )
                    self.assertAlmostEqual(command["body"]["vx"], 0.05)
                    self.assertAlmostEqual(command["body"]["w"], math.radians(5.0))
                    self.assertEqual(command["body"]["duration"], 1200)
                    with self.assertRaises(ValueError):
                        await client.um_drive(0.51, 0.0, 0.51, 0.0)

                    self.assertTrue(await client.toggle_soft_emergency())
                    emergency_state = await client.get_emergency_state()
                    self.assertTrue(emergency_state["soft_emc"])
                    self.assertTrue(client._emergency)
                    self.assertFalse(await client.toggle_soft_emergency())
                    self.assertFalse(client._emergency)

                    await client.path_navigation(
                        "SIM_START",
                        source_id="SELF_POSITION",
                        task_id="webui-path-1",
                    )
                    path_command = await client._simulator_server.wait_for_command(
                        ApiNumber.TASK_GOTARGET
                    )
                    self.assertEqual(path_command["body"]["id"], "SIM_START")
                    self.assertNotIn("source_id", path_command["body"])
                    self.assertEqual(path_command["body"]["task_id"], "webui-path-1")

                    await client.set_do(3, True)
                    await client.io.query()
                    io_snapshot = SeerIOCache.read(io_cache)
                    self.assertIsNotNone(io_snapshot)
                    self.assertEqual(len(io_snapshot["DI"]), 24)
                    self.assertEqual(len(io_snapshot["DO"]), 16)
                    do3 = next(
                        item for item in io_snapshot["DO"] if item["id"] == 3
                    )
                    self.assertTrue(do3["status"])

                    snapshot = SimpleNamespace(
                        x=1000.0,
                        y=2000.0,
                        theta=90.0,
                        battery_soc=70.0,
                        map_id="webui-map",
                        last_node_id="SIM_START",
                    )
                    card = render_map_card(
                        cache,
                        snapshot,
                        adapter_key="seer:test",
                        csrf_token="test-token",
                        return_to="/adapter/seer:test",
                    )
                    self.assertIn("SEER map", card)
                    self.assertIn("Current AMR pose", card)
                    self.assertIn("current point", card)
                    self.assertIn("SIM_START", card)
                    self.assertIn("rotate(-90.00)", card)
                    self.assertIn('data-seer-map-zoom="in"', card)
                    self.assertIn('data-seer-map-zoom="out"', card)
                    self.assertIn('data-seer-map-zoom="fit"', card)
                    self.assertIn("data-seer-robot-size", card)
                    self.assertIn("data-seer-robot-marker", card)
                    self.assertIn('text-anchor="middle"', card)
                    self.assertIn('data-seer-path-target="SIM_START"', card)
                    self.assertIn('href="#seer-map-nav-', card)
                    self.assertIn('data-seer-dialog-id="seer-map-nav-', card)
                    self.assertNotIn("map_target=", card)
                    self.assertNotIn('class="seer-map-nav-dialog is-open"', card)
                    self.assertIn("목적지:", card)
                    self.assertIn('name="csrf_token" value="test-token"', card)
                    self.assertIn('name="action_type" value="vdaOrderRoute"', card)
                    self.assertIn('name="navigation_mode" value="path" data-seer-nav-label="VDA5050 Target Order" required checked', card)
                    self.assertNotIn('name="navigation_mode" value="free" data-seer-nav-label="Free Nav" required', card)
                    self.assertNotIn('name="free_nav_x"', card)
                    self.assertNotIn('name="free_nav_y"', card)
                    self.assertNotIn('name="free_nav_theta"', card)
                    self.assertIn('name="confirm" value="on"', card)
                    self.assertIn("data-seer-map-nav-form", card)
                    self.assertIn('role="button" href="#content" data-seer-map-nav-cancel>아니오', card)
                    self.assertIn('type="submit" class="btn primary">', card)
                    self.assertIn('role="dialog" aria-modal="true"', card)
                finally:
                    await client.disconnect()


if __name__ == "__main__":
    unittest.main()
