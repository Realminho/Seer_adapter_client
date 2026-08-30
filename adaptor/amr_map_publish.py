"""getMap instant-action 처리: JIBOT 맵을 공통 맵으로 변환해 MQTT publish.

발행 토픽은 mqtt_client가 prefix(amr/{vda_version}/{serial})를 붙여
amr/v3/{serial}/map 이 된다(non-retained). 페이로드는 uamap.core.v1 +
top-level requestId/generatedAt(요청-응답 correlation, spec D3).
"""
from typing import Any, Iterable, Optional

from common_amr_map import from_jibot_snapshot

# mqtt_client가 prefix를 붙이므로 subtopic만 지정 → amr/v3/{serial}/map
MAP_TOPIC = "map"


def extract_request_id(action_parameters: Iterable[Any], action_id: str) -> str:
    """getMap action에서 requestId를 추출한다.

    action_parameters에 key 'requestId'가 있으면 그 value(str), 없으면 action_id.
    """
    for param in action_parameters or []:
        if getattr(param, "key", None) == "requestId" and getattr(param, "value", None) is not None:
            return str(param.value)
    return str(action_id)


def build_map_message(raw_map: dict, map_id: str, request_id: str, generated_at: str) -> dict:
    """JIBOT raw UmGetMap을 uamap.core.v1로 변환하고 correlation 필드를 덧붙인다.

    Args:
        raw_map: JIBOT UmGetMap 응답 dict(vehicle._map_raw).
        map_id: 맵 id.
        request_id: 요청 correlation id(echo).
        generated_at: 생성 시각(ISO8601 UTC, ...Z).

    Returns: uamap.core.v1 dict + top-level requestId/generatedAt.
    """
    common = from_jibot_snapshot({"raw": raw_map, "mapId": map_id}, raw_ref="runtime/jibot-map.json")
    common["requestId"] = request_id
    common["generatedAt"] = generated_at
    return common


async def get_map_and_publish(vehicle: Any, mqtt: Any, *, map_id: str, request_id: str, generated_at: str) -> bool:
    """JIBOT 맵을 fetch해 map 토픽에 publish 한다.

    실로봇은 get_map()이 timeout 시 캐시 노드를 반환하며 _map_raw를 갱신하지 않으므로,
    이번 호출에서 _map_raw가 새로 파싱되었을 때(객체 identity 변경)만 발행한다(stale 오발행 방지).

    시뮬레이터(is_simulator)는 조회할 소켓이 없어 _map_raw가 startup 이후 정적이지만
    항상 최신/유효한 맵이다. identity 검사를 적용하면 매번 stale로 오판되어 발행이 막히므로,
    시뮬레이터는 identity 검사를 면제하고 _map_raw가 있으면 발행한다.

    맵이 없거나(_map_raw None) stale/timeout/예외 시 발행하지 않고 False 반환.
    """
    is_simulator = bool(getattr(vehicle, "is_simulator", False))
    before = getattr(vehicle, "_map_raw", None)
    before_id = id(before) if before is not None else None
    try:
        await vehicle.get_map()
    except Exception as exc:  # noqa: BLE001
        print(f"[getMap] get_map failed for request {request_id}: {exc}")
        return False
    raw = getattr(vehicle, "_map_raw", None)
    if raw is None:
        print(f"[getMap] no map loaded for request {request_id}; skip publish")
        return False
    # 실로봇만 identity 기반 freshness 검사(timeout 시 stale 캐시 오발행 방지).
    if not is_simulator and before_id is not None and id(raw) == before_id:
        print(f"[getMap] no fresh map for request {request_id}; skip publish (stale/timeout)")
        return False
    try:
        message = build_map_message(raw, map_id, request_id, generated_at)
        mqtt.publish(MAP_TOPIC, message, qos=1, retain=False)
    except Exception as exc:  # noqa: BLE001
        print(f"[getMap] publish failed for request {request_id}: {exc}")
        return False
    return True
