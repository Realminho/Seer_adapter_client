# WebUi ↔ adapter 로컬 IPC — MQTT를 ACS 전용으로 분리 — 설계

작성일 2026-06-22. 대상: `adaptor/web/`(WebUi 프로세스) + `adaptor/adapter_jibot.py`(adapter 프로세스).

## 동기

현재 adapter와 WebUi는 **이미 별도 프로세스**(각각 `jibot-adapter.service`,
`amr-webui.service`)지만, 둘 사이 통신을 **MQTT 브로커에 의존**한다. WebUi는
adapter가 publish하는 브로커에 같이 붙어 `{prefix}/state`·`/connection`을 구독하고
(`core/monitor.py:254-255`), 제어는 `instantActions`를 같은 브로커로 publish한다
(`web/server.py:561`). 따라서 **브로커(=ACS 링크)가 끊기면 로컬 WebUi도 같이 먹통**이
된다.

목표는 두 가지다.

1. **MQTT는 ACS/VDA5050 외부 통신 전용**으로만 둔다.
2. **WebUi는 로컬 운영용**으로 MQTT/브로커 가용성과 무관하게 robot 상태를 보고
   제어할 수 있어야 한다.

수단으로는 **같은 robot 온보드 PC에 공존하는 두 프로세스 간 로컬 IPC**를 쓴다.
최적화 기준은 **개발 비용이 아니라 robot 온보드 PC의 런타임 부하**다(아래 §성능 근거).
그 기준에서 상태는 **tmpfs 파일**, 명령은 **Unix domain socket(UDS)** 이 가장 가볍고
안전하다.

## 결정 기록 (2026-06-22 확정)

- **프로세스 분리 유지** — adapter/WebUi 별도 systemd 서비스(현행).
- **MQTT = ACS/VDA5050 외부 통신 전용.**
- **상태 경로 = tmpfs 파일** — `/run/amr-adaptor/<serial>/state.json`·`health.json`,
  on-demand read, `updated_at`로 stale 판정.
- **명령 경로 = Unix domain socket(소켓 허용 결정).** 초기 제안은 "통신을 파일로
  통일(소켓/프로세스 콜 회피)"이었으나, **robot 온보드 PC 런타임 부하**와 **motion
  명령의 fail-closed 안전**을 우선 기준으로 재평가하여 명령 경로는 UDS로 확정한다.
  대안 평가: command-spool 파일은 adapter watcher의 idle 부하 + TTL 수동 안전(= tmpfs
  위 메시지 큐 재발명)으로 탈락; 동기 per-action accept/reject(procedure 리팩터)는
  핵심 명령 경로+MQTT 경로까지 손대는 범위라 이번엔 제외(필요 시 후속).
- **명령 응답 = delivered(접수) 수준.** adapter가 수신·파싱·디스패치했음만 즉시
  반환한다. action별 수락/거부·실행 결과는 state(`instantActionStates`)로 관찰한다
  (현재 `instant_actions_accept_procedure`가 반환값 없이 state만 갱신하므로 동기
  verdict는 불가 — §변경 2 참조).

## 현재 상태 (정확한 지점)

- **프로세스/실행.** adapter = `jibot-adapter.service`(다중 로봇 시
  `jibot-adapter@<id>.service`), WebUi = `amr-webui.service`. 둘 다
  `User=__USER__`로 같은 사용자·같은 robot 호스트에서 실행
  (`scripts/systemd/jibot-adapter.service:11`, `scripts/systemd/amr-webui.service`).
- **상태 발행(adapter).** `publish_state()` 루프(`adapter_jibot.py:373-511`)가 매
  주기 `self._vda3.publish_state(self._build_v3_state_message(), qos=0)`로 VDA5050
  state를 MQTT에 발행(`:501`). 하트비트 간격이며 `_state_publish_event`로 이벤트
  발생 시 즉시 깨어 발행(`:503-511`).
- **상태 읽기(WebUi).** `web/main.py:54-65`가 `(host, port, topic_prefix)` 세션마다
  `MqttMonitor`를 만들어 `{prefix}/state`·`{prefix}/connection`을 구독
  (`monitor.py:254-255`). `extract_state()`(`monitor.py:90-156`)가 그 payload를
  `StateSnapshot`(`monitor.py:30-61`)으로 파싱. server는 `_monitor_snapshot()`
  (`server.py:98-115`)로 스냅샷을 받아 렌더하며 `mqtt_connected`/`mqtt_state_fresh`/
  `mqtt_state_age_sec`를 얹는다(`_MQTT_STATE_STALE_SEC=15`, `server.py:34`).
- **명령(WebUi→adapter).**
  - 서비스 verb(start/stop/restart 등)·host reboot·urobot restart는 **이미 MQTT를
    안 거치고** 로컬 `SystemdController`/`HostController`(polkit)로 수행
    (`server.py:417-493`). → **이 경로는 변경 없음.**
  - instant action(Control 뷰)만 MQTT 의존: `_post_action`(`server.py:535-565`)이
    `control.build_instant_actions(...)`(`core/control.py:15`)로 payload를 만들어
    `self._monitors[key].publish_json("instantActions", payload)`(`server.py:561`).
- **명령 소비(adapter).** `subscribe_instant_actions(...)`(`adapter_jibot.py:1597`)
  → `handle_incoming_acs_cmd`(`:1606`)의 instantActions 분기(`:1638-1647`) →
  `instant_actions_accept_procedure(InstantActions)`(`:2771`). 여기서 motion BUSY
  차단·각 action 디스패치를 모두 처리.
- **robot 식별.** `serial = config.vehicle.serial_number`,
  `topic_prefix = {vda_interface}/{vda_version}/{serial}`(`core/registry.py:76-78,
  248-260`). spec.key는 `jibot` 또는 다중 로봇 시 `jibot:<robot_id>`. adapter와
  WebUi가 **같은 config 경로로 동일하게** serial을 도출한다.
- **파일 IPC/`/run` 사용 — 현재 0건.** 신규 도입.

## 결정 / 범위

- **상태 경로 = tmpfs 파일.** adapter가 `/run/amr-adaptor/<serial>/state.json`,
  `health.json`을 **atomic write**(tmp + `os.replace`). WebUi는 렌더 시 읽는다.
  `updated_at` 나이로 stale/offline 판정.
- **명령 경로 = UDS.** adapter가 `/run/amr-adaptor/<serial>/control.sock`에서
  `asyncio.start_unix_server`로 수신. WebUi는 요청/응답으로 보내고 **즉시
  delivered(접수) ack** 를 받는다(동기 verdict 아님). action별 결과는 state의
  `instantActionStates`(§1e)로, 주문 진행은 `actionStates`로 관찰한다.
- **MQTT = ACS 전용.** adapter→ACS state 발행, ACS→adapter order/instantActions 수신은
  그대로 유지. **운영 대상인 JIBOT WebUi 경로는 브로커에 붙지 않는다**(jibot spec은
  `FileMonitor` 사용 → 그 경로에서 paho 미사용). 비-jibot spec(hexplorer/edge-agent)이
  남아 `MqttMonitor`를 쓰는 동안에는 paho 의존이 그대로 남는다(범위 밖). 실제 현장은
  jibot 단독이라 사실상 브로커 비의존이 된다.
- **ACS 연결 상태 표시.** WebUi가 직접 브로커에 붙지 않으므로, adapter가 자신의
  브로커 연결 여부를 `health.json.acs_broker_connected`로 적고 WebUi가 **별도
  항목으로** "ACS broker connected/disconnected"만 표시.
- **스코프 = JIBOT adapter.** 파일/UDS는 JIBOT adapter에 적용한다. 비-jibot spec
  (hexplorer/edge-agent)은 이번 범위 밖(아래 §범위 밖). 모니터 객체를 seam으로 두어
  jibot spec만 `FileMonitor`로 교체하고 나머지는 현행 유지한다.
- **다른 로컬 제어는 그대로.** 서비스 verb·host reboot·urobot restart·config·log·
  camera는 기존 로컬 수행 유지.

## 아키텍처 개요

```
adapter process (jibot-adapter.service)
  ├─ MQTT ──▶ ACS broker      (state 발행 / order·instantActions 수신)  [변경 없음]
  ├─ write ─▶ /run/amr-adaptor/<serial>/state.json   (publish_state 루프, atomic)
  ├─ write ─▶ /run/amr-adaptor/<serial>/health.json  (브로커 연결 변화·기동 시)
  └─ UDS  ◀─ /run/amr-adaptor/<serial>/control.sock  (명령 수신 → 기존 procedure)

webui process (amr-webui.service)        [브로커에 붙지 않음]
  ├─ read  ◀─ state.json / health.json  (렌더 시 on-demand)
  └─ UDS  ─▶ control.sock               (명령 요청/응답, fail-closed)
```

`/run`은 systemd가 tmpfs로 마운트하므로 state.json은 **RAM 백업**(디스크 IO·플래시
마모 0). `/run/amr-adaptor` root는 `tmpfiles.d`로, `<serial>/` 하위는 각 adapter가
런타임에 만든다(§변경 3).

## 변경 1 — 상태 읽기 경로 (MQTT 구독 → 파일 읽기)

### 1a. adapter: state.json atomic write

`publish_state()` 루프(`adapter_jibot.py:501`)에서 **이미 만든 같은 dict** 를 파일에도
쓰되, **로컬 파일 write를 MQTT publish보다 먼저** 한다. MQTT publish는 별도로 예외
격리한다 — 그래야 브로커 장애로 publish가 던져도 로컬 state는 항상 갱신된다(설계 목표:
WebUi는 broker 무관).

```python
msg = self._build_v3_state_message()
self._write_state_file(msg)                  # 신규: 로컬 WebUi 우선 — broker 무관 보장
try:
    self._vda3.publish_state(msg, qos=0)     # 기존 (ACS) — 예외가 로컬 경로/루프를 막지 않게 격리
except Exception as exc:                      # noqa: BLE001
    log(...)  # ACS publish 실패 로그만; 루프 계속
```

(현재 `MQTTClient.publish()`(`utils/mqtt_client.py:130-144`)와 `publish_state()` 루프는
publish 예외를 격리하지 않으므로, 위 격리는 신규.)

`_write_state_file`은 `{ ...msg, "updated_at": <epoch float> }`를 `state.json.tmp`에
쓰고 `os.replace(tmp, "state.json")`로 교체(같은 tmpfs 내 rename = atomic). 자체 오류는
삼키고 로그만 — 파일 쓰기 오류가 ACS 발행/제어 루프를 막지 않는다.

**핵심 재사용:** state.json의 내용은 MQTT로 발행하는 payload와 **동일**하므로 WebUi의
기존 `extract_state()`(`monitor.py:90-156`)가 **그대로** 파싱한다. 새 스키마 없음.

### 1b. adapter: health.json

ACS 브로커 연결 상태 전이 시 + 기동 시 `health.json`을 atomic write(내용은 §스키마).
고빈도 아님(연결 변화 시만)이라 비용 무시 가능. 단, 현재 `MQTTClient`는
`on_connect`만 등록하고 **`on_disconnect`가 없으며**(`utils/mqtt_client.py:45-46`)
외부 리스너 훅도 없다. 따라서 구체 인터페이스를 신규로 둔다:

- `MQTTClient.__init__(..., on_connection_change: Optional[Callable[[bool], None]] = None)`
  를 추가하고 저장.
- `self._client.on_disconnect = self._on_disconnect`를 **신규 등록**. 이 클라이언트는
  paho v1 콜백 API(`mqtt.Client(client_id=...)`, `:44`)이므로 시그니처는
  `_on_disconnect(self, client, userdata, rc)`.
- `_on_connect`(rc==0 → True, 아니면 False)와 `_on_disconnect`(False)에서
  `self._connected` 갱신 후 `on_connection_change(self._connected)` 호출.
- adapter는 `on_connection_change=self._on_acs_broker_change`를 주입; 콜백이
  `acs_broker_connected`를 갱신하고 `health.json`을 write.
- **초기값:** 기동 직후, 첫 연결 성공 전까지 `acs_broker_connected=false`로
  health.json을 1회 write(`connect()`가 내부 재시도 루프이고 `_on_connect`가 rc!=0이면
  False이므로, 연결 전/실패 상태가 정확히 false로 드러남).

### 1c. WebUi: FileMonitor (MqttMonitor drop-in)

`core/monitor.py`에 `FileMonitor`를 추가한다. server.py가 의존하는 모니터 인터페이스만
맞추면 된다:

- `get_snapshot() -> StateSnapshot`: state.json을 읽어 `extract_state()`로 파싱,
  `state_ts = updated_at`(파일이 준 시각, 읽은 시각 아님 — 진짜 신선도)로 채운다.
  파일 없음/JSON 깨짐 → 빈 `StateSnapshot`(필드 None).
- `broker_connected` 대체: 이 모니터는 브로커가 아니라 **adapter 생존**을 본다.
  server.py가 쓰는 의미("라이브 상태 있음")에 맞춰 `adapter_alive`(state.json 신선도)로
  노출. ACS 브로커 상태는 health.json에서 별도로 읽어 표시(§1d).
- `last_error`: 마지막 읽기/파싱 오류 문자열.

`web/main.py`의 monitor 와이어링(`:54-65`)에서 jibot spec은 `FileMonitor(serial)`로
생성(`MqttMonitor` 대신). server.py `_monitor_snapshot()`(`:98-115`)는 모니터
인터페이스만 쓰므로 **거의 그대로**: `mqtt_*` 명칭을 freshness 의미로 매핑하거나
중립 명칭(`state_fresh`/`state_age_sec`/`adapter_alive`)으로 정리. `_manual_test_block_reason`
(`server.py:50-61`)의 `mqtt_connected`/`mqtt_state_fresh` 체크도 같은 freshness 기준으로
치환.

### 1d. WebUi: ACS 상태 항목

`_monitor_snapshot`(또는 인접)에서 health.json을 읽어 `acs_broker_connected`를 스냅샷에
얹는다. 상세/목록 렌더에 **"ACS: connected/disconnected"** 한 항목 추가(기존 연결 행을
이 의미로 대체). adapter 생존(파일 신선도)과 ACS 연결을 **분리 표시**한다:
"adapter offline/stale"(파일 stale)와 "ACS disconnected"(health 플래그)는 다른 상태다.

### 1e. 명령 결과 가시성 — `instantActionStates` 파싱 추가

명령 응답이 delivered 수준이므로(§변경 2), action별 수락/거부·진행은 state로 봐야
한다. instant action 결과는 `_build_v3_state_message()`에서 **`instantActionStates`**
로 직렬화되지만(`messages.py:573`), 현재 `extract_state()`는 `actionStates`만 파싱하고
`instantActionStates`는 읽지 않는다(`monitor.py:125-133`). 따라서:

- `extract_state()`에 `instantActionStates` 파싱을 추가(`actionStates`와 동일 패턴),
  `StateSnapshot`에 `instant_action_states: List[Dict]` 필드 추가(`monitor.py:30-61`).
- 상세 페이지에 최근 instant action 상태(예: `clamp FAILED: busy …`)를 표시. 운영자가
  "delivered" 직후 ~1초 내 결과를 확인할 수 있다.

이 보강은 transport(UDS/spool)와 무관하며, MQTT 경로의 instant action 결과도 함께
보이게 만드는 부수 이득이 있다.

## 변경 2 — 명령 경로 (MQTT publish → UDS 요청/응답)

### 2a. adapter: UDS 서버

adapter 기동 시 `/run/amr-adaptor/<serial>/control.sock`에서
`asyncio.start_unix_server(handler, path=sock)`를 띄운다(기존 asyncio 루프와 동일
루프 → 콜백에서 `instant_actions_accept_procedure`를 직접 호출해도 스레드 안전).
소켓은 기동 시 stale 소켓 파일 제거 후 bind, 권한은 dir 권한으로 제어(§3).

handler 처리:

1. 요청 1건(JSON, §스키마) 읽기. 실패 → `{"delivered": false, "error": ...}`.
2. `meta.confirmed`/`source_user`/`created_at`을 audit 로그로 남김(누가 로컬에서
   명령했는지 — 기존 MQTT instantActions에는 없던 출처 추적).
3. `InstantActions.from_dict(req["instantActions"])`로 객체화(실패 → `delivered=false`
   응답).
4. `self.instant_actions_accept_procedure(obj)` 호출 — **기존 MQTT instantActions
   경로와 동일 procedure 재사용**(motion BUSY 차단·미지원 거부 등 모든 검증 그대로).
   이 procedure는 **반환값이 없고** 결과를 state의 `instantActionStates`에 기록한다
   (`adapter_jibot.py:2771-2879`) — 그래서 동기 per-action verdict는 불가.
5. `{"delivered": true, "action_ids": [<id>...]}` 응답 후 연결 종료.

응답은 **delivered(접수) 수준**이다 — adapter가 요청을 수신·파싱·디스패치했음을
뜻한다. action별 수락/거부·실행 결과는 dispatch 후 state(`instantActionStates`,
§1e)로 ~1초 내 관찰한다. 현행보다 엄밀하다 — 지금 WebUi는 "broker publish 성공"만
알지만, 앞으로는 "adapter 접수"까지 확인하고 결과도 state로 따라간다.

### 2b. WebUi: UDS 클라이언트

`_post_action`(`server.py:535-565`)에서 `self._monitors[key].publish_json("instantActions", payload)`
(`:561`)를 UDS 호출로 교체:

```python
payload = control.build_instant_actions(...)   # 기존 그대로
req = {"instantActions": payload,
       "meta": {"confirmed": _confirmed(form),
                "source_user": self._auth_user(...),
                "created_at": <epoch>}}
delivered, msg = uds_client.send(sock_path_for(key), req, timeout=2.0)
```

`uds_client.send`는 `socket.AF_UNIX`로 connect→요청 쓰기→응답 읽기. **fail-closed**:
adapter가 죽어 있으면 connect 실패 → `delivered=False, msg="adapter offline — not delivered"`.
**옛 명령이 큐에 쌓였다가 재시작 후 발사되는 위험이 구조적으로 없다.** flash 메시지는
기존과 동일하게 처리(성공 시 "delivered", 실패 시 사유).

## 변경 3 — systemd / 배포

- **공유 root는 systemd `RuntimeDirectory`로 만들지 않는다.** 다중 로봇이면 여러
  `jibot-adapter@<id>.service`가 모두 `RuntimeDirectory=amr-adaptor`를 선언하게 되어,
  한 인스턴스의 stop/restart가 공유 `/run/amr-adaptor`를 정리(clobber)하는 운영
  리스크가 있다. 대신:
  - `/etc/tmpfiles.d/amr-adaptor.conf`로 root를 선언:
    `d /run/amr-adaptor 0750 <user> <group> -`. 부팅 시 systemd-tmpfiles가 tmpfs에
    한 번 생성하며 인스턴스 생명주기와 무관(설치 스크립트가 `<user>`/`<group>` 치환).
  - **각 adapter는 자신의 `<serial>/` 하위 디렉터리만** 런타임에 생성·소유·관리한다
    (다른 인스턴스 것을 건드리지 않음, 0750). 인스턴스 stop/restart가 다른 로봇의
    경로를 건드리지 않는다.
  - 종료 시 자기 `<serial>/`를 지울 필요는 없다 — tmpfs는 재부팅 시 비워지고, 살아
    있는 파일은 `updated_at`로 stale 판정된다. `systemctl restart` 갭 동안 last-known
    state가 남아 WebUi가 stale 표기와 함께 보여줄 수 있다.
- **권한.** adapter와 WebUi가 같은 `__USER__`이면 owner rw로 충분(현 설정). 만약
  사용자가 갈리면 공용 그룹 + dir mode 0770(tmpfiles 라인) + state.json 0640으로 처리.
  설계 기준은 **동일 사용자**(현행).
- **paho 의존.** WebUi(jibot 경로)는 더 이상 MQTT를 쓰지 않으므로 `web/main.py`의
  `mqtt_broker` 읽기·`MqttMonitor` 생성 제거(비-jibot spec이 남아 있으면 그쪽만 유지).
- 배포 스크립트(`scripts/update-jibot-adapter-over-ssh.sh`,
  `scripts/setup-adaptor-service.sh`)는 유닛 변경분 반영.

## 파일/프로토콜 스키마

### state.json (adapter write, 매 발행 주기)

VDA5050 v3 state message(`_build_v3_state_message().to_dict()` 출력) **그대로** + 한
필드. 키는 실제 `State.to_dict()` 출력(`protocol/vda5050_3_0/messages.py:549-584`)을
따른다 — `safetyState.activeEmergencyStop`(:505), `mobileRobotPosition`(:560),
`powerSupply`(:576). (`extract_state`는 이들을 **1순위 키**로 파싱한다 —
`activeEmergencyStop`/`mobileRobotPosition`/`powerSupply`; `eStop`/`agvPosition`/
`batteryState`는 fallback일 뿐이므로 예시·writer는 1순위 키로 쓴다.)

```jsonc
{
  "headerId": 12345, "timestamp": "...", "version": "...",
  "manufacturer": "jibot", "serialNumber": "...",
  "operatingMode": "AUTOMATIC",
  "safetyState": {"activeEmergencyStop": "NONE", "fieldViolation": false},
  "mobileRobotPosition": {"x": 1.2, "y": 3.4, "theta": 0.1, "mapId": "...",
                          "localizationScore": 0.98},
  "powerSupply": {"stateOfCharge": 87.0, "charging": false, "batteryVoltage": 48.1},
  "nodeStates": [], "edgeStates": [], "driving": false,
  "actionStates": [], "instantActionStates": [], "errors": [], "loads": [],
  "lastNodeId": "...", "lastNodeSequenceId": 0, "orderId": "...", "orderUpdateId": 0,
  "updated_at": 1750000000.123        // 신규: epoch seconds (float)
}
```

writer는 `to_dict()` 결과 dict에 `updated_at`만 더해 그대로 쓴다(필드 누락/이름
변형 금지). `extract_state()`가 이미 이 모양을 파싱하므로 WebUi 쪽 파싱 코드는 추가
없음.

### health.json (adapter write, 연결 변화·기동 시)

```jsonc
{
  "pid": 4321,
  "started_at": 1749990000.0,
  "acs_broker_connected": true,    // adapter↔ACS 브로커 연결 여부
  "acs_broker_last_change": 1750000000.0,
  "version": "<adapter version>",
  "updated_at": 1750000000.0
}
```

### UDS 요청/응답 (WebUi ↔ adapter, control.sock)

요청(한 줄 JSON):

```jsonc
{
  "instantActions": { /* control.build_instant_actions() 출력 그대로 */ },
  "meta": {"confirmed": true, "source_user": "operator", "created_at": 1750000000.0}
}
```

응답(delivered 수준 — 수신/파싱/디스패치 확인만):

```jsonc
{"delivered": true, "action_ids": ["uuid..."]}
// 또는 (수신/파싱/검증 실패 — dispatch 전)
{"delivered": false, "error": "invalid instantActions payload"}
```

action별 수락/거부(예: "busy with loading work; motion blocked")는 이 응답이 아니라
state의 `instantActionStates`(§1e)로 관찰한다.

## 안전 경계

- **fail-closed 명령.** adapter가 없으면 UDS connect 실패 → 미전달 표시. 큐잉으로
  인한 지연 발사 없음. motion/clamp 등 안전 관련 명령에 중요.
- **확인·audit 유지.** motion action은 기존대로 `confirm` 체크 필수
  (`server.py:547`). 모든 명령은 `source_user` 포함 audit. adapter도 수신 시 audit.
- **권한.** UDS·파일은 service user 소유 디렉터리(0750) 안에만 존재 → 로컬 동일
  사용자만 접근. 네트워크 노출 없음(포트 없음).
- **WebUi 게이트 유지.** HTTP Basic + CSRF 그대로. **네트워크 노출은 `[web_ui].host`/
  systemd 설정을 따른다**(`web/main.py:30` 기본 `127.0.0.1`이나 config로 override
  가능 — 운영상 타 PC에서 브라우징될 수 있음, `web/main.py:43` 주석). 즉 localhost
  강제는 보장되지 않으므로 노출 범위는 배포 설정으로 관리한다. 소프트웨어 게이트일 뿐
  안전 PLC/비상정지를 대체하지 않는다.
- **ACS 경로 불변.** MQTT를 통한 ACS state/order/instantActions 처리는 손대지 않으므로
  fleet 운영 동작은 회귀 없음.

## robot PC 성능 근거 (왜 broker가 아닌 file+UDS인가)

- **/run = tmpfs(RAM).** state.json을 매 주기 써도 디스크 IO·eMMC 마모 0. adapter는
  ACS용 직렬화 payload를 **재사용**하므로 추가 비용은 tmpfs write 1회뿐.
- **on-demand read.** WebUi는 페이지를 그릴 때만 읽는다. 아무도 안 보면 robot PC
  추가 부하 0. (브로커 push는 시청자 없어도 매 주기 직렬화·전달·파싱을 계속하고 상주
  프로세스가 필요.)
- **내부 홉에서 TCP loopback·브로커 라우팅 제거.** 명령은 UDS(커널 로컬), idle 시
  `accept` 블록으로 CPU 0(파일 spool처럼 디렉터리를 watch할 필요도 없음).
- 비교 결과 robot PC 부하: `state=파일 + 명령=UDS` < 순수 파일(명령 watcher 상주) <
  로컬 브로커(상주 프로세스 + 주기적 loopback push).

## 테스트

- `extract_state` — 기존 단위 테스트 그대로 적용(state.json 내용이 동일 payload).
- `FileMonitor` — 임시 state.json에 대해 fresh / stale(updated_at 과거) / 파일 없음 /
  JSON 깨짐 각각에서 `get_snapshot()`·freshness·`last_error` 검증.
- adapter `_write_state_file` — atomic(rename) 동작; **MQTT publish가 예외를 던져도
  로컬 state는 기록**됨(write-first + 격리) 검증; 파일 write 실패 시 루프 비차단.
- health.json — 기동 시 `acs_broker_connected=false`, `on_connect`(rc=0)→true,
  `on_disconnect`→false 전이마다 갱신(`on_connection_change` 훅 stub).
- UDS 라운드트립 — 정상 dispatch 시 `delivered=true`, 파싱/검증 실패 시
  `delivered=false`, adapter-down 시 connect 실패 → fail-closed 메시지. **motion BUSY는
  `delivered=true`이되 `instantActionStates`에 FAILED가 기록**됨을 state로 검증(응답이
  아니라 state로 관찰). server `_post_action`은 UDS 클라이언트 stub로 검증(현 MQTT
  publish 테스트 패턴 치환).
- `extract_state` `instantActionStates` 파싱 + 렌더 — instant action 결과 행/요약.
- server `_manual_test_block_reason` / `_monitor_snapshot` — freshness 기반 분기로
  치환 후 회귀.
- 배포/유닛 — `tests/test_update_jibot_adapter_over_ssh.py`에 tmpfiles.d 렌더·유닛
  변경 회귀.

## 변경 파일 요약

- `adaptor/adapter_jibot.py` — `_write_state_file`(state.json), health.json writer,
  UDS 서버(`control.sock` → `instant_actions_accept_procedure` 재사용), 경로 헬퍼.
- `adaptor/utils/mqtt_client.py` — `on_connection_change` 콜백 파라미터 추가 +
  `on_disconnect` 핸들러 신규 등록(paho v1 시그니처). adapter가 ACS 연결 전이를 받아
  health.json에 반영(§1b).
- `adaptor/core/monitor.py` — `FileMonitor`(state.json + `extract_state` 재사용,
  freshness). `extract_state`에 `instantActionStates` 파싱 추가 + `StateSnapshot`에
  `instant_action_states` 필드 추가(§1e).
- `adaptor/core/control.py` — (변경 없음) `build_instant_actions` 그대로 재사용.
- `adaptor/web/server.py` — `_post_action`을 UDS 호출로, `_monitor_snapshot`·
  `_manual_test_block_reason`을 freshness/ACS 분리 기준으로 정리, UDS 클라이언트 헬퍼.
- `adaptor/web/main.py` — jibot spec을 `FileMonitor`로 와이어링, `mqtt_broker`/
  `MqttMonitor` 의존 제거(비-jibot 경로만 잔존 시 유지).
- `adaptor/web/render.py` — "ACS connected/disconnected" 항목, adapter offline/stale
  표기(연결 행 의미 재정의), 최근 instant action 결과(`instantActionStates`) 표시.
- `scripts/systemd/tmpfiles.d/amr-adaptor.conf`(신규) + 배포/설치 스크립트
  (`setup-adaptor-service.sh`, `update-jibot-adapter-over-ssh.sh`) — `/run/amr-adaptor`
  root를 tmpfiles로 생성(`<user>`/`<group>` 치환). adapter는 런타임에 `<serial>/`
  하위만 생성. systemd `RuntimeDirectory`는 다중 인스턴스 clobber 위험으로 미사용.
- `docs/guide/web-ui.md` — 통신 모델(파일/UDS), ACS 상태 항목, 안전 경계 갱신.
- 테스트 — `adaptor/tests/`(monitor/server) + `tests/test_update_jibot_adapter_over_ssh.py`.

## 단계 (구현 계획에서 분리 권장)

- **Phase 1 — 상태 읽기.** state.json/health.json write(adapter) + FileMonitor(WebUi)
  + ACS 상태 항목 + freshness/stale + **`instantActionStates` 파싱·표시(§1e)**. →
  "브로커 없이 상태가 보인다" 단독 검증. (instantActionStates는 read 경로의 일부이고
  MQTT·로컬 양쪽 명령 결과 가시성을 동시에 개선하므로 FileMonitor와 함께 Phase 1에
  둔다.)
- **Phase 2 — 명령.** UDS 서버(adapter, delivered-ack) + UDS 클라이언트(WebUi) +
  `_post_action` 치환. → "브로커 없이 제어된다" 단독 검증(결과는 Phase 1의
  instantActionStates 표시로 관찰).

각 Phase는 독립적으로 동작·검증 가능하다(Phase 1만으로도 즉시 가치).

## 범위 밖 (YAGNI)

- 비-jibot adapter(hexplorer/edge-agent)의 파일/UDS 이관 — 모니터 seam은 일반화
  가능하게 두되 이번엔 jibot만.
- 영속 명령 큐·재시도·순서 보장 — UDS는 fail-closed라 큐 불필요.
- action 완료(FINISHED)까지 UDS 동기 대기 — 결과는 state.json `actionStates`로 관찰.
- state.json을 VDA5050와 다른 축약 스키마로 분리 — 동일 payload 재사용이 최소·정확.
- 로컬 브로커(mosquitto) 도입 — robot PC 부하 기준에서 탈락(§성능 근거).
