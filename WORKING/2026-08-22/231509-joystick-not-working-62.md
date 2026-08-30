# joystick-not-working-62

### 목표
- 192.168.101.62(HN-SH6-TR-002) 조이스틱 입력이 안 먹는 원인 규명
- 조이스틱 연결 시 연결음(sound) 재생 가능 여부 확인

### 지금
- 코드 수정 + 테스트 완료(로컬). 로봇 배포는 사용자 승인 대기

### 완료
- 수정 1: `_order_motion_in_flight()` 신설, `_manual_blocked_reason` 이 order 객체 존재가 아니라
  `_is_v3_order_active()` + 미완료 order 배경 액션 유무로 판정 (adapter_jibot.py)
- 수정 2: 거부 사유를 시도당 1줄 로그 `[JOYSTICK *] refused: <reason>` (joystick_runtime.py, Micro/Ultimate2)
- 수정 3: 연결 시 `play_joystick_connect_sound()` -> joystick-connected.mp3 1회 재생.
  `_sound_request = None` 으로 상태음 루프가 다음 주기에 복구되게 함
- 테스트: test_action_bridge 4건, test_joystick_runtime 4건, test_sound_track_names 1건 추가
- HW/드라이버 정상 확인: 2dc8:310b 연결, amr-xboxdrv active, /dev/input/event6 생성,
  어댑터가 `connected via xboxdrv evdev: /dev/input/event6` 로 바인딩, enabled/ultimate2_enabled = true
- 근본 원인: `_manual_blocked_reason()` (adapter_jibot.py:6252) 이 `self.order is not None` 이면
  "order in progress; cancel order first" 반환 -> joystick_runtime.py:362 에서 `_stop(reason, latch=True)`
- `self.order` 는 `_clear_cancelled_order_state()` (adapter_jibot.py:8050) 에서만 None 이 되고,
  이건 cancelOrder 핸들러(8014)에서만 호출됨. `[ORDER COMPLETE]` (4200) 은 order 를 안 지움
  -> 오더 1건 끝나면 cancelOrder 오기 전까지 조이스틱 영구 차단
- 무로그 이유: `_stop()` 은 `was_moving` 이 True 일 때만 print (joystick_runtime.py:346-348).
  정지 상태에서 막히면 로그가 아예 안 남음. 추가로 `_blocked_until_neutral` 래치가 걸림
- 로그 상관관계(22:00~23:22): 조이스틱이 실제 주행한 구간(22:26~22:34, 22:51:31~34)은 전부
  order 미설정 구간. order 설정 구간(23:12:52~23:19:57 등)은 주행 로그 0건
- 현재 상태: 23:22:04 order 20260822-127-1 수신, driving=true -> 지금은 설계상 정상 차단
- 사운드: SoundPlayer(adaptor/utils/sound.py) + adapter `self._sound`/`_submit_sound` 이미 존재.
  62에 mplayer/pulse sink 설정 완비, sounds/ 에 mp3 다수. 연결음 추가 가능(작업량 소)

### 다음
- 사용자 승인 후 .62 배포(scripts/update-jibot-adapter-over-ssh.sh). 지금 오더 주행 중이라 재시작 주의
- 조이스틱 재연결이 1~2분마다 발생(23:15/23:16/23:21) -> 연결음이 잦을 수 있음. 절전/링크 별건 조사

### 검증
- MQTT amr/v3/HN-SH6-TR-002/state 실측: orderId=20260822-127-1, driving=true (차단 조건 성립)
- 손댄 4개 파일 768 passed, 4 subtests (test_adapter_jibot_v3_order/action_bridge/joystick_runtime/sound_track_names)
- 전체 스위트 2209 passed / 14 failed -> 그중 13건은 HEAD 클린 워크트리에서도 동일 실패(shipped
  recipes/extensions 설정 드리프트, 본 작업과 무관). 나머지 1건은 본 작업 회귀였고 수정 완료
