# robot2-no-sound-and-latency

### 목표
- 배포 후 로그로 #2 상태 사운드 지연 재측정
- 1호기 소리 나는데 2호기 안 나는 원인 확인

### 지금
- 조사 완료. 프로덕션 변경(볼륨/재시작)은 사용자 승인 대기

### 완료
- 접속 경로: Mac이 en0=10.8.8.213으로 2호기 카메라 LAN에 물려 있음
  ssh ucore@10.8.8.8 (wlan0 192.168.2.10). 실제 배포 dir = /home/ucore/adaptor
  (~/jibotadpt 는 Jun 8 짜리 죽은 디렉토리, serial TR-001로 남아있어 혼동 주의)
- 이 보드는 2호기: `main.py --robot HN-SH6-TR-002`
- 1호기는 접근 불가 (192.168.2.10~31 스캔에서 .10=본인, .30만 응답). 1호기 관련
  주장은 전부 미확인으로 남길 것
- 2호기 무음 원인 (증거 확보)
  - pactl list sinks: alsa_output.platform-rt5651-sound.stereo-fallback
    Volume: 0 / 0% / -inf dB, Mute: no
  - config/config.toml [sound_settings] startup_volume = 0
  - 어댑터가 기동 시 1회 set_volume(startup_volume) 적용 -> 0%
  - 즉 파일/코드와 무관하게 무조건 무음
- 2호기 부차 원인: sounds/ 가 rename 전 이름 (clamp.mp3, pioElevatorOpen.mp3 ...)
  action-*.mp3 없음 -> 볼륨 올려도 action 사운드는 여전히 무음
- #2 재측정 불가 — 2호기는 수정 전 빌드
  - utils/sound.py 7655B (HEAD 9560B), _dispatch_sound/repeat_count 0건
  - adapter_jibot.py 368915B (HEAD 381931B)
  - sounds/ 도 rename 전
  - debug_log = false 라 [SOUND] / STATE PUBLISH REQUESTED 로그 자체가 없음
- state_publish_delay = 5 (2호기 실제 값)
- 내 작업분은 3914abe 로 커밋되어 있음 (다른 세션이 커밋)

### 다음
- 사용자 승인 후: startup_volume 0 -> 70 수정, 서비스 재시작
- 인과 증명 원하면: pactl로 100% 올림 -> 재시작 -> 다시 0% 되는지 확인
- 재배포 시 debug_log = true 해야 #2 측정 가능
- config.toml/robots.hcl/extensions.hcl/recipes.hcl 은 배포 exclude —
  state_repeat_count / repeat_count_overrides 는 로봇마다 수동 입력 필요

### 검증
- ssh ucore@10.8.8.8 'pactl list sinks | grep -E "Name:|Volume:|Mute:"'
  -> rt5651 sink 0% / -inf dB, Mute: no
- grep -c "_dispatch_sound|repeat_count" utils/sound.py -> 0
