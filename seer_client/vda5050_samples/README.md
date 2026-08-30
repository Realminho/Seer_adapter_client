# VDA5050 JSON samples

실제 FMS 입력과 같은 `/order` 및 `/instantActions` 구조를 테스트하는 샘플이다.
전체 흐름은 `../VDA5050_TEST.md` 참고.

```text
file order seer_client/vda5050_samples/order_LM1_LM4.json
file order seer_client/vda5050_samples/order_with_node_action.json
file order seer_client/vda5050_samples/order_with_edge_action.json
file instantActions seer_client/vda5050_samples/instant_stateRequest.json
file instantActions seer_client/vda5050_samples/instant_startPause.json
file instantActions seer_client/vda5050_samples/instant_stopPause.json
file instantActions seer_client/vda5050_samples/instant_cancelOrder.json
```

이름 있는 Path Nav는 `order_LM1_LM4.json`처럼 `nodes[] + edges[]`를 사용한다.
`order_with_node_action.json`과 `order_with_edge_action.json`은 FMS order 안의
`actions[]` 형식을 검증한다. `instant_*` 파일은 즉시 Action의 VDA5050
`actions[]` 형식이다.

header ID/시간/manufacturer/serialNumber는 `file` 명령에서 실행 대상에 맞게
갱신된다. LM 이름은 실제 SEER 맵에 맞게 수정한다.
