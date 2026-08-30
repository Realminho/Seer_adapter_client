# sound-not-playing-and-latency

### 목표
- action(recipes/extensions) 사운드 무음 원인 규명 + 후속 수정
- 사운드 지연 원인 분석
- 재생 횟수 설정 추가

### 지금
- 전체 테스트 스위트 재실행 중

### 완료
- 원인: resolver는 `action-<actionType>.mp3`를 찾는데 파일은 접두사 없는 이름.
  fallback도 IDLE/NONE -> idle.mp3 없음 -> 무음. 사용자가 mp3 10개 rename 완료
- #3 이벤트 루프 블로킹 제거 (adapter_jibot.py)
  - SoundPlayer 블로킹 호출을 단일 워커 스레드로 이전
  - _sound_executor / _submit_sound / _dispatch_sound / _drain_sound_worker / _shutdown_sound_worker
  - 어댑터 루프 미가동 시(기동/종료/테스트)는 인라인 유지
  - _dispatch_sound dedupe로 publish 사이클마다 큐 적체 방지
  - stopSound는 force=True로 dedupe 우회
  - testSound / setSoundVolume / startup volume 도 워커 경유
  - atexit LIFO: 워커 드레인 -> SoundPlayer.stop() 순서 보장
- #4 tests/test_sound_track_names.py 신규 (3 tests)
  - action-*.mp3 <-> 등록 actionType 대조
  - 나머지 파일 <-> 상태 토큰 대조
  - UNREACHABLE 목록 stale 방지
- 재생 횟수 기능 추가
  - utils/sound.py: play(repeat_count=0), _normalize_repeat, 관리 루프에 plays 카운터,
    dedupe 키에 _current_repeat 포함
  - config/config.py: state_repeat_count, repeat_count_overrides
  - config/config.toml: [sound_settings] + [sound_settings.repeat_count_overrides]
  - adapter_jibot.py: _resolve_sound_repeat_count (키 = 확장자 뺀 파일명)
  - sounds/README.md 문서화
- #2 상태 사운드 5초 지연: 사용자 지시로 보류 (#3 배포 후 재측정)
- 죽은 파일 3개(moving.mp3 / excuseme.mp3 / xuanzhuan.wav) 유지, UNREACHABLE 등재

### 다음
- 사용자 현장 재측정 결과 대기 -> 필요 시 #2 (state_publish_delay vs 워처 루프)
- repeat_count_overrides 실제 값은 현장에서 사용자가 채움

### 검증
- pytest tests/test_sound_player.py => 22 passed
- pytest tests/test_adapter_jibot_v3_order.py -k "sound or repeat" => 62 passed
- pytest tests/test_sound_track_names.py => 3 passed
- pytest sound/config 관련 5개 파일 => 109 passed
- 신규 테스트가 실제 버그 검출하는지 확인: 가짜 폴더로 clamp.mp3 /
  action-pioElevatorOpen.mp3 둘 다 검출됨
- 전체 스위트: 직전 실행 2101 passed / 1 failed
  (test_recipes_config.py air_shower doorSensor — 다른 세션의 recipes.hcl 작업,
   사운드와 무관)
