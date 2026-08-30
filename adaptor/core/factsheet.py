"""Pure VDA5050 factsheet builder shared by the adapter and the WebUi.
Runtime-only values (headerId/timestamp/simulation/loadPositions/videoStreams)
are injected by the caller; everything else comes from Config. The instant-
action catalog lives here as the single source so the WebUi /factsheet preview
matches what the adapter publishes."""
from typing import Any, Dict, List, Optional

# Single source for the factsheet agvActions catalog. Moved verbatim from
# adapter_jibot.py SUPPORTED_INSTANT_ACTIONS; the adapter aliases
# Adapter.SUPPORTED_INSTANT_ACTIONS = INSTANT_ACTION_TYPES.
# stopCharging/logReport are intentionally absent: the JIBOT API has no
# matching command, so they are rejected as FAILED and must not be advertised.
INSTANT_ACTION_TYPES = (
    "cancelOrder",
    "stateRequest",
    "factsheetRequest",
    "startPause",
    "stopPause",
    "startCharging",
    "chargeInPlace",
    "initPosition",
    "localize",
    "jibotCommand",
    "syncJibotParams",
    "requestVideo",
    "requestLaser",
    "stopLaser",
    "setMapSnapshot",
    "getMap",
    "getParameters",
    "setParameters",
    "setMap",
    "switchMap",
    "clamp",
    "unclamp",
    "clampTeach",
    "clampOn",
    "clampOff",
    "clampStop",
    "pioInit",
    "pioReadIn",
    "pioWriteOut",
    "pioDisconnect",
    "pioScenario",
    "ezioReadIn",
    "photoSensorRead",
    "loading",
    "unloading",
    "stopLoading",
    "stopUnloading",
    "clearInstantActions",
    "clearZoneActions",
    "clearErrors",
    "setSoundVolume",
    "testSound",
    "stopSound",
    "uploadSound",
    "manualDrive",
    "manualMove",
    "manualStop",
    "enableMotor",
    "disableMotor",
    "gotoNearestNode",
)

ACTION_SCOPES = {
    "switchMap": ["INSTANT", "NODE"],
    "jibotCommand": ["INSTANT", "NODE", "EDGE"],
}

EXTENSION_MANAGED_INSTANT_ACTION_TYPES = frozenset(
    {
        "clamp",
        "unclamp",
        "clampTeach",
        "clampOn",
        "clampOff",
        "clampStop",
        "clampMin",
        "clampMax",
        "clampHome",
        "clampMoveTo",
        "pioInit",
        "pioReadIn",
        "pioWriteOut",
        "pioDisconnect",
        "pioScenario",
        "ezioReadIn",
        "photoSensorRead",
        "ezioWriteOut",
        "ezioWaitIn",
    }
)

BASE_INSTANT_ACTION_TYPES = tuple(
    action_type
    for action_type in INSTANT_ACTION_TYPES
    if action_type not in EXTENSION_MANAGED_INSTANT_ACTION_TYPES
)


def merge_instant_action_types(extra_action_types=()) -> tuple[str, ...]:
    merged = []
    seen = set()
    for action_type in (*BASE_INSTANT_ACTION_TYPES, *tuple(extra_action_types or ())):
        if action_type in seen:
            continue
        seen.add(action_type)
        merged.append(action_type)
    return tuple(merged)


def build_factsheet(config, *, header_id, timestamp, simulation,
                    load_positions, video_streams=None,
                    instant_action_types=None) -> Dict[str, Any]:
    f = config.factsheet
    action_types = (
        INSTANT_ACTION_TYPES
        if instant_action_types is None
        else tuple(instant_action_types)
    )
    agv_actions = [
        {"actionType": action_type,
         "actionScopes": ACTION_SCOPES.get(action_type, ["INSTANT"])}
        for action_type in action_types
    ]
    factsheet: Dict[str, Any] = {
        "headerId": header_id,
        "timestamp": timestamp,
        "version": config.vehicle.vda_full_version,
        "manufacturer": config.vehicle.manufacturer,
        "serialNumber": config.vehicle.serial_number,
        "coordinateUnits": {"position": f.coordinate_unit_position,
                            "orientation": f.coordinate_unit_orientation},
        "simulation": simulation,
        "typeSpecification": {
            "seriesName": f.series_name, "agvKinematic": f.agv_kinematic,
            "agvClass": f.agv_class, "localizationTypes": list(f.localization_types),
            "navigationTypes": list(f.navigation_types),
        },
        "physicalParameters": {"speedMin": f.speed_min,
                               "speedMax": float(config.settings.speed)},
        "protocolLimits": {"maxStringLens": {}, "maxArrayLens": {},
                           "timing": {"minOrderInterval": f.min_order_interval_sec,
                                      "minStateInterval": float(config.settings.state_publish_delay)}},
        "protocolFeatures": {"optionalParameters": [], "agvActions": agv_actions},
        "agvGeometry": {},
        "loadSpecification": {"loadPositions": list(load_positions)},
    }
    if video_streams:
        factsheet["videoStreams"] = video_streams
    return factsheet
