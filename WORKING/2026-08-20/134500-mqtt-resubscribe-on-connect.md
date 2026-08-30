# mqtt-resubscribe-on-connect

### 목표
- 브로커가 adaptor보다 늦게 뜨면 downlink(order/instantActions)가 영구 사망하는 결함 수정

### 지금
- 소스 수정 + 테스트 완료. 로봇 배포 여부 대기

### 완료
- `adaptor/utils/mqtt_client.py`
  - `_subscription_qos: Dict[str, int]` 추가 — subscribe()에 들어온 topic/qos 전량 기록
  - `_on_connect` rc==0에서 `_resubscribe_all()` 호출 (재연결 포함 매 연결)
  - `_resubscribe_all()`이 `[MQTT RESUBSCRIBE] topic=... qos=...`를 남겨 다음엔 로그로 판별 가능
- `adaptor/tests/test_mqtt.py`에 회귀 테스트 2건 추가

### 다음
- 순회/order 재시작해 orderCompareStatus=MATCH 실증 (milk run은 13:16 ORDER_FAILED로 ABORTED라 자동 재발행 없음)

### 검증
- `.venv/bin/python -m unittest tests.test_mqtt` → Ran 8 tests, OK
- `_resubscribe_all()` 호출을 빼고 재실행 → FAILED (failures=1). 테스트가 실제로 결함을 잡음
- 로봇 배포본 md5 c5c148… = 수정 전 소스와 byte-identical (diff로 확인). 1파일 교체로 충분
- 로봇 배포 완료(13:58): /home/ucore/adaptor/utils/mqtt_client.py 교체(백업 .bak-20260820-1355), systemctl restart amr-adaptor
- 재시작 journal에서 결함 시나리오 그대로 재현되고 새 코드가 막아냄:
  13:59:01 subscribe(미연결) → 13:59:04 CONNECT FAILED → 13:59:07 CONNECTED rc=0 → [MQTT RESUBSCRIBE] order/instantActions 2건
