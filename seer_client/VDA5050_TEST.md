# SEER VDA5050 FMS 동일 입력 검증 가이드

## 1. 목표 구조

```text
실제 FMS / manual_test.py --vda5050
  │
  ├─ MQTT amr/v3/<AMR_ID>/order
  │    header + orderId + orderUpdateId + nodes[] + edges[]
  │    node/edge actions[] 포함 가능
  │
  └─ MQTT amr/v3/<AMR_ID>/instantActions
       header + actions[]
       actionId + actionType + blockingType + actionParameters[]
                    │
WebUI ── Local VDA5050(default) 또는 FMS MQTT ──┘
  │       같은 order/instantActions JSON
  │
  ▼
              기존 Adapter
           VDA5050 v3 parser
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
       실물 SEER          seer_simulator
          └────── 동일 SEER southbound 처리 ──────┘
```

`adaptor/`는 수정하지 않는다. WebUI가 만드는 JSON은 실제 FMS와 같은 VDA5050
`order` / `instantActions` 형식이다. 기본 전송은 `Local VDA5050`이며 완성된 VDA5050
JSON을 localhost TCP로 Adapter에 전달해 MQTT 불안정과 분리한다. VDA5050 화면에서
`FMS MQTT` 모드로 바꾸면 동일 JSON을 실제 broker/topic으로 보내 end-to-end 검증할 수 있다.

`manual_test.py --simulator`와 `manual_test.py --ip`는 SEER TCP 드라이버 자체를
진단하는 직접 모드다. FMS 입력 경로 검증은 반드시 `--vda5050`을 사용한다.

## 2. 주행과 Action

이름 있는 경로 주행은 `/order`를 사용한다.

```text
vda5050> goto LM4 LM1
vda5050> goto_route LM1 LM5 LM4
vda5050> order test-1 LM1 LM5 LM4
```

생성되는 핵심 구조는 다음과 같다.

```json
{
  "orderId": "test-1",
  "orderUpdateId": 0,
  "nodes": [
    {"nodeId":"LM1","sequenceId":0,"released":true,"actions":[]},
    {"nodeId":"LM5","sequenceId":2,"released":true,"actions":[]},
    {"nodeId":"LM4","sequenceId":4,"released":true,"actions":[]}
  ],
  "edges": [
    {"edgeId":"...","sequenceId":1,"released":true,"startNodeId":"LM1","endNodeId":"LM5","actions":[]},
    {"edgeId":"...","sequenceId":3,"released":true,"startNodeId":"LM5","endNodeId":"LM4","actions":[]}
  ]
}
```

Order 내부 Action도 VDA5050 `actions[]` 그대로 보낸다.

```text
vda5050> order_action wait-order LM4 seerWait seconds=1.5
vda5050> edge_action edge-order LM1 LM4 seerWait seconds=0.5
```

즉시 Action은 `/instantActions`를 사용한다.

```text
vda5050> pause
vda5050> resume
vda5050> cancel
vda5050> emc
vda5050> emc_release
vda5050> motor on
vda5050> do 3 on
vda5050> goto_xyz 1000 500 90
vda5050> translate 0.5 0.1
vda5050> turn 90 10
```

`goto_xyz`는 `seerCoordinateNav`라는 vendor Action이지만 wire 형식은 표준
VDA5050 `instantActions.actions[]`다. 기존 실물 Adapter가 임의 nodeId의
`nodePosition`을 직접 좌표주행으로 사용하지 않으므로 이 방식이 실물과 Simulator를
동일하게 유지한다.

## 3. WebUI

WebUI의 지도 Path Nav는 선택 경로를 VDA5050 `/order`의 `nodes[] + edges[]`로
생성한다. 기본 `Local VDA5050`에서는 localhost TCP로 Adapter에 전달하고, `FMS MQTT`
모드에서는 동일 payload를 MQTT `/order`로 발행한다. 중간 node 대기시간을 0보다 크게 넣으면 중간 node의
`actions[]`에 다음 형태의 Action이 포함된다.

```json
{
  "actionId": "webui-wait-...",
  "actionType": "seerWait",
  "blockingType": "HARD",
  "actionParameters": [{"key":"seconds","value":1.0}]
}
```

Pause/Resume/E-stop/DO/Motor/수동 이동 등 일반 WebUI Action도 VDA5050
`instantActions.actions[]` JSON을 그대로 사용한다. `Local VDA5050` 모드에서는 MQTT broker가
OFFLINE이어도 WebUI → Adapter 명령은 동작할 수 있다. `FMS MQTT` 모드는 broker 연결이
정상일 때만 동작한다.

상단 `VDA5050` 화면에서 WebUI 명령 전송 모드를 `Local VDA5050` / `FMS MQTT`로
전환할 수 있고, `order`, `instantActions`, `state`, `connection` 원문과 MQTT 연결 상태도
확인할 수 있다. 페이지의 **MQTT 테스트 발행** 폼은 선택 모드와 관계없이 항상 broker로 보낸다.

## 4. Simulator 실행 예

```powershell
python .\seer_client\run_webui.py `
  --simulator `
  --id SEER-SIM-001 `
  --mqtt-host 127.0.0.1 `
  --mqtt-port 1883
```

기본값은 `--webui-vda-transport local`이다. 실제 FMS broker 경유까지 시험하려면
`--webui-vda-transport mqtt`를 추가하거나 WebUI의 **VDA5050** 화면에서 전환한다.

다른 PowerShell:

```powershell
python .\seer_client\manual_test.py `
  --vda5050 `
  --id SEER-SIM-001 `
  --mqtt-host 127.0.0.1 `
  --mqtt-port 1883 `
  --allow-write
```

## 5. 실물 실행 예

```powershell
python .\seer_client\run_webui.py `
  --id SEER-REAL-001 `
  --vehicle-ip 192.168.43.103 `
  --mqtt-host <FMS_BROKER_IP> `
  --mqtt-port <FMS_BROKER_PORT>
```

실물과 Simulator의 차이는 Adapter 아래쪽 대상뿐이다. FMS, WebUI, manual_test가 만드는
`/order`와 `/instantActions` JSON 구조는 동일하다. WebUI 기본값만 전송 경로가 localhost이고,
`--webui-vda-transport mqtt` 또는 VDA5050 화면에서 FMS MQTT로 전환하면 broker까지 동일하게 검증한다.

## 6. JSON 파일 샘플

```text
vda5050> file order seer_client/vda5050_samples/order_LM1_LM4.json
vda5050> file order seer_client/vda5050_samples/order_with_node_action.json
vda5050> file order seer_client/vda5050_samples/order_with_edge_action.json
vda5050> file instantActions seer_client/vda5050_samples/instant_startPause.json
```

`file` 명령은 실행 대상에 맞게 header ID/time/manufacturer/serialNumber를 갱신한 뒤
같은 VDA5050 구조로 MQTT 발행한다.

## 7. 자동 검증

```powershell
python seer_client\tests\test_vda5050_manual.py
python seer_simulator\tests\test_seer_simulator.py
```

검증 항목은 `/order` node/edge sequence, node/edge `actions[]`, `/instantActions`,
WebUI Local VDA5050 무-wrapper 전달, 선택형 FMS MQTT 발행, Simulator의 order
`nodePosition` southbound 이동이다.
