# mqtt-reconnect-check

### 목표
- MQTT 끊김 후 재접속 로직 점검, ONLINE 재publish 구멍 수정, 현재 접속 상태 확인

### 지금
- 남은 일 2건 모두 완료

### 완료
- 전송 재접속은 정상: connect_background() = connect_async +
  reconnect_delay_set(1,10) + loop_start, _on_connect마다 _resubscribe_all().
- 수정(adapter_jibot.py):
  - `_connection_offline_intent` 플래그(__init__) — OFFLINE에만 latch
  - `_publish_connection_state()` 동기 헬퍼 분리, `publish_connection()`은 위임
  - `_on_acs_broker_change(True)` -> `_republish_connection_online()`
    (`_call_on_adapter_loop`; _loop 미설정이면 inline)
  - try/except 1회 로그 — paho는 콜백 예외를 안 삼켜서 여기서 새면
    네트워크 스레드가 죽고 자동 재접속 자체가 멈춤
- 테스트 2건 추가(test_adapter_jibot_v3_order.py)

### 현장 실측 (2026-08-20 23:50 KST, ssh ucore@10.8.8.8)
- 로봇 = 10.8.8.8. 브로커 = **10.8.8.213:11883** (= 이 맥, Docker 리스너).
  robot config.toml [mqtt_broker].host = 10.8.8.213. robots.hcl에 mqtt_host 없음.
- amr-adaptor.service active, pid 176094, started 23:33:35
- ss: ESTAB 10.8.8.8:42241 -> 10.8.8.213:11883
- health.json acs_broker_connected=true (last_change == started_at → 무중단)
- 브로커 retained `amr/v3/HN-SH6-TR-001/connection` = ONLINE (headerId 2, 23:33:35)
- state 12초에 3건 수신 (state_publish_delay=5와 일치)
- => 지금은 정상 접속 중. 미접속은 23:33 재시작 이전 상태로 보임.

### 남은 일 (2026-08-21)
- hexplorer 동일 구멍 수정 완료: adapter_hexplorer.py
  - MQTTClient(on_connection_change=self._on_broker_change) 배선
  - LWT가 retain=True CONNECTION_BROKEN이라 jibot과 같은 구멍이 실재했음
  - _connection_offline_intent latch + paho 스레드 예외 가드
  - 테스트 3건(test_adapter_hexplorer_state.py) + 배선 assert를 jibot에도 추가
- config: 코드가 읽는 브로커 설정은 adaptor/config/config.toml **하나뿐**
  (config.py:853이 __file__ 기준 절대경로). "3중화"는 부정확했음 —
  루트 config.toml / robots.hcl / extensions.hcl은 어떤 코드도 안 읽고
  배포도 adaptor/만 올라감(update-jibot-adapter-over-ssh.sh LOCAL_ADAPTER_DIR).
  그런데 최근까지 커밋되고 있어(3bae3e4 등) 편집해도 무효과인 함정.
  루트 robots.hcl의 10.8.8.8/10.8.8.87은 실배포본과 정확히 일치 -> 현장 기록으로 보임.
  조치: 세 파일 상단에 "코드가 읽지 않음 / 반영하려면 adaptor/config/를 고칠 것"
  주석만 추가. IP 변경 없음, 삭제 없음, 런타임 동작 변화 없음.

### 다음
- 로봇 배포 필요 시 scripts/update-jibot-adapter-over-ssh.sh ucore@10.8.8.8 --restart
- 레포 config 브로커 주소 정리 여부 결정
  (config.toml=192.168.101.50 / adaptor/config/config.toml=192.168.3.108 /
   adaptor/config/robots.hcl mqtt_host=192.168.2.61) — 실제 배포본은 10.8.8.213

### 검증
- fix 제거 시 신규 테스트 fail, 적용 시 pass (역검증)
- scripts/run-tests.sh tests/test_adapter_jibot_v3_order.py tests/test_mqtt.py
  tests/test_vda5050_transport.py -> 689 passed
