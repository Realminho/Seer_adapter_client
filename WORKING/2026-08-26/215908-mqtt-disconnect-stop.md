# mqtt-disconnect-stop

### 목표
- MQTT(FMS) 연결 끊김 시 AMR 일시정지 옵션 + 알림음(disconnect-with-mw.mp3) 재생

### 지금
- 완료

### 완료
- config: settings.stop_on_mqtt_disconnect(기본 true), mqtt_disconnect_stop_grace_sec(3.0),
  mqtt_disconnect_sound("disconnect-with-mw.mp3"). config.toml 에도 기록
- adapter_jibot: _pause_motion/_resume_motion 헬퍼 추출(startPause/stopPause 와 공유),
  _on_acs_broker_change 에 유예 타이머 + 재연결 자동 재개, _play_mqtt_disconnect_sound
- pause/resume 를 asyncio.Lock 으로 직렬화하고 await 후 링크 상태를 다시 읽어 수렴
  (in-flight 중 링크 뒤집힘 2건: 영구 정지 / 브로커 없이 주행)
- tests/test_mqtt_disconnect_stop.py 8케이스, test_sound_track_names DIRECT_TRACKS 갱신

### 다음
- 실기 배포 시 config.toml 미갱신 로봇은 기본값(true, 3s)으로 동작함에 유의

### 검증
- tests/test_mqtt_disconnect_stop.py 8 passed. 수렴 코드 제거 시 신규 2건 실패 확인
- 기존 실패 13건(airshower/recipes/unknown_config_key/senders)은 HEAD 에서도 동일 — 무관
- 전체 스위트: 15 failed, 2263 passed. 실패 15건은 모두 기존(airshower/recipes/unknown_config_key/senders)
