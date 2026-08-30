# AMR 어댑터 사운드 파일 (`sounds/`)

이 폴더는 어댑터가 **호스트 PC 스피커로 재생하는 사운드 파일**을 둡니다.
어댑터는 먼저 `/jrobot_status`의 `system_error_code`에 맞는 `ERRORxxxx.wav`를
찾고, 없으면 로봇의 작업 상태(`workingState` / `workingStateDetail`)에 맞는 mp3를
자동 재생합니다 (로봇 스피커가 아니라 어댑터 호스트의 PulseAudio 출력으로 나갑니다).

## 규칙 (convention)

매 사이클 다음 순서로 파일을 고릅니다:

1. fresh `system_error_code`가 0이 아니고 `ERRORxxxx.wav`가 있으면 그 파일
2. 없으면 비상·위치상실·결함·브레이크 상태 파일
3. 없으면 `slowdown.mp3` / `watch.mp3`가 현재 JIBOT status에 맞을 때 그 파일
4. 없으면 recipe의 현재 step에 맞는 `action-<actionType>.mp3`
5. 없으면 실행 중인 부모 action에 맞는 `action-<actionType>.mp3`
6. 없으면 `workingStateDetail`에 맞는 파일 (예: `loading.mp3`)
7. 없으면 `workingState`에 맞는 파일 (예: `driving.mp3`)
8. 모두 없으면 **무음**

즉 **에러코드 wav가 상태 mp3보다 우선**하고, 장애물 정지/회피 사운드는 일반
주행음보다 우선합니다. 상태 쪽에서는 detail이 state보다 우선합니다. 파일을
두지 않은 상태는 그냥 조용합니다.

`system_error_code=0`은 정상/에러 없음으로 처리하므로 `ERROR0000.wav`가 있어도
자동 재생하지 않습니다.

## Action/recipe 사운드와 상태 표시

extension과 recipe는 구분하지 않고 실제 `actionType` 이름으로 파일을 찾습니다.
예를 들어 `pioElevatorOpen1f` recipe와 그 안의 `pioWriteOut` step에는 다음 파일을
둘 수 있습니다.

```text
action-pioElevatorOpen1f.mp3
action-pioWriteOut.mp3
```

step 파일이 있으면 실행 중인 동안 부모 recipe 파일보다 우선합니다. step 파일이
없으면 부모 action 파일을, 둘 다 없으면 기존 working-state 파일을 사용합니다.
대소문자는 `actionType` 선언과 정확히 맞춰야 합니다.

이름이 어긋나면 에러도 로그도 없이 그냥 무음이라 사람이 못 잡습니다
(`pioElevatorOpen.mp3`는 recipe 이름이 `pioElevatorOpen1f`라 영영 안 울립니다).
`tests/test_sound_track_names.py`가 이 폴더의 파일명을 실제 등록된 `actionType`과
상태 토큰에 대조해 어긋나면 실패시킵니다.

사운드 때문에 `workingState`나 `workingStateDetail`을 바꾸지는 않습니다. 대신
`information[]`의 `AMR_STATE`에 다음 reference를 항상 함께 보냅니다.

- `activeActionType`: 현재 부모 action 하나. 없으면 빈 문자열
- `activeActionTypes`: 동시 실행을 포함한 부모 action 이름의 JSON 배열
- `activeStepActionType`: 현재 recipe step 하나. 없으면 빈 문자열
- `activeStepActionTypes`: 동시 실행 중인 recipe step 이름의 JSON 배열

## JIBOT 에러코드 wav

파일명은 `ERROR%04d.wav` 규칙입니다. 예: `system_error_code=200`이면
`ERROR0200.wav`, `504`이면 `ERROR0504.wav`를 찾습니다.

| 파일명 | 간단 설명 |
|---|---|
| `ERROR0000.wav` | 자동 재생 제외: `system_error_code=0`은 정상/에러 없음 |
| `ERROR0001.wav` | tag mode 초기화 시 위치추정 결과 없음 |
| `ERROR0002.wav` | tag mode 초기화 시 위치 오차 과대 |
| `ERROR0003.wav` | tag mode 승인 후 횡방향 오차 과대 |
| `ERROR0004.wav` | tag mode checkpose 후 위치 오차 과대 |
| `ERROR0005.wav` | tag mode checkpose 후 tag id 오류 |
| `ERROR0006.wav` | tag mode 승인 중 위치추정 결과 없음 |
| `ERROR0007.wav` | tag mode checkpose 중 카메라 오프라인 |
| `ERROR0008.wav` | tag mode checkpose 중 코드 인식 없음 |
| `ERROR0009.wav` | tag mode checkpose 중 인식 프로그램 오류 |
| `ERROR0100.wav` | ref mode 승인 중 위치추정 결과 없음 |
| `ERROR0101.wav` | ref mode 초기화 시 위치추정 결과 없음 |
| `ERROR0102.wav` | ref mode 승인 중 레이저 데이터 오류 |
| `ERROR0103.wav` | ref mode 초기화 시 위치 오차 과대 |
| `ERROR0104.wav` | ref mode 최종 확인 중 종방향 오차 과대 |
| `ERROR0105.wav` | ref mode 도킹 타임아웃 |
| `ERROR0106.wav` | ref mode 승인 후 횡방향 오차 과대 |
| `ERROR0107.wav` | ref mode 도킹 3회 실패 |
| `ERROR0200.wav` | 맵에서 목표 이름을 찾지 못함 |
| `ERROR0201.wav` | 충전 모드에서 충전 실패 |
| `ERROR0400.wav` | 장비 복귀 후 로봇 상태 오류 |
| `ERROR0500.wav` | odometry 데이터 timeout |
| `ERROR0501.wav` | 내부 IMU 데이터 timeout |
| `ERROR0502.wav` | 외부 IMU 데이터 timeout |
| `ERROR0503.wav` | 전방 laser 데이터 timeout |
| `ERROR0504.wav` | 후방 laser 데이터 timeout |
| `ERROR0505.wav` | 상단 laser 데이터 timeout |
| `ERROR0506.wav` | 좌측 laser 데이터 timeout |
| `ERROR0507.wav` | 우측 laser 데이터 timeout |
| `ERROR0508.wav` | cam1 데이터 timeout |
| `ERROR0509.wav` | cam2 데이터 timeout |
| `ERROR0510.wav` | depth camera 데이터 timeout |
| `ERROR0600.wav` | map file 오류로 lost |
| `ERROR0601.wav` | 예비/벤더 정의 lost 계열 코드 |
| `ERROR0602.wav` | 예비/벤더 정의 lost 계열 코드 |
| `ERROR0603.wav` | 위치추정 실패로 lost |
| `ERROR0604.wav` | 예비/벤더 정의 lost 계열 코드 |
| `ERROR0605.wav` | 예비/벤더 정의 lost 계열 코드 |
| `ERROR0606.wav` | 예비/벤더 정의 lost 계열 코드 |
| `ERROR0700.wav` | 장시간 메시지 수신 없음 |
| `ERROR0701.wav` | 장시간 velocity 송신 없음 |
| `ERROR0702.wav` | robot main loop stuck |

## 인식하는 파일명

**workingStateDetail 기반** (있으면 우선):

| 파일명 | 상태 |
|---|---|
| `loading.mp3` | 적재 작업 중 |
| `unloading.mp3` | 하역 작업 중 |
| `docking.mp3` | 도킹 중 |
| `brake.mp3` | 장애물 때문에 정지/대기 중. brake가 풀릴 때까지 반복 재생 |
| `emergency.mp3` | 비상정지 |
| `lost.mp3` | 위치 추정 상실 |
| `fault.mp3` | 결함(FATAL) |

**JIBOT status 토큰 기반** (있으면 주행음보다 우선):

| 파일명 | 상태 |
|---|---|
| `slowdown.mp3` | 장애물/조건 때문에 감속 중 |
| `watch.mp3` | 장애물/조건 감시 주행 중 |

**workingState 기반** (detail 파일이 없을 때 폴백):

| 파일명 | 상태 |
|---|---|
| `driving.mp3` | 주행 중 |
| `acting.mp3` | 작업 중(적재/하역/도킹 등, 비주행) |
| `charging.mp3` | 충전 중 |
| `paused.mp3` | 일시정지 |
| `blocked.mp3` | 장애물 대기 |
| `error.mp3` | 에러 |
| `idle.mp3` | 대기 |

> 예) 주행에만 음악을 넣고 싶으면 `driving.mp3` 하나만 두면 됩니다. 적재/하역/도킹을
> 한 곡으로 통일하려면 `acting.mp3` 하나로 충분하고(세 작업의 workingState가 모두
> `ACTING`), 각각 다른 곡을 쓰려면 `loading.mp3`/`unloading.mp3`/`docking.mp3`를
> 따로 두면 됩니다. (도킹은 주행 중일 수 있어 `docking.mp3`가 없으면 `driving.mp3`로
> 폴백될 수 있습니다.)

## 설정 (`adaptor/config/config.toml` `[sound_settings]`)

```toml
[sound_settings]
enabled        = true                                                 # 기능 on/off
sink           = "alsa_output.platform-rt5651-sound.stereo-fallback"  # 출력 sink ("" = 기본 sink)
sound_dir      = "sounds"                                             # 이 폴더 (상대경로면 adaptor 기준)
player         = "mplayer"                                            # 재생 바이너리
startup_volume = 70                                                  # 시작 시 적용할 sink 볼륨 %
state_replay_gap_sec = 0.0                                           # 상태 사운드 반복 사이 쉼(초), 0이면 연속 반복
state_repeat_count = 0                                               # 재생 횟수, 0이면 무한 반복

[sound_settings.state_replay_gap_overrides]
# loading = 2.0
# docking = 1.0
# driving = 0.5

[sound_settings.repeat_count_overrides]
# action-clamp = 1
# action-pioElevatorOpen1f = 2
# driving = 0
```

- 폴더를 바꾸려면 `sound_dir` 수정 (상대경로는 `adaptor/` 기준).
- `state_replay_gap_sec`는 모든 상태 사운드에 공통 적용됩니다.
- `[sound_settings.state_replay_gap_overrides]`에 lower-case
  `workingStateDetail`/`workingState` 이름을 넣으면 그 상태만 공통값을 덮어씁니다.
  detail 파일이 선택되면 detail 키가 우선이고, state 파일로 폴백되면 state 키가 적용됩니다.
  이 키는 상태 파일에만 붙습니다 — `action-*.mp3`에는 적용되지 않습니다.
- `state_repeat_count` / `[sound_settings.repeat_count_overrides]`는 재생 **횟수**입니다.
  간격 override와 달리 키가 상태 토큰이 아니라 **확장자를 뺀 파일명**이라
  `action-*.mp3`도 지정할 수 있습니다. 자세한 내용은 아래 "형식 / 재생 방식" 참고.
- (구 `travel_music`/`work_music` 키는 더 이상 쓰지 않습니다. convention으로 대체됨 —
  설정에 남아 있어도 무시되며 로딩은 됩니다.)

## 형식 / 재생 방식

- **형식:** 에러코드는 wav, 상태 사운드는 mp3 (`mplayer`로 재생).
  다른 플레이어는 `player`로 변경(예: `mpg123`).
- **루프:** 기본은 해당 상태/action이 유지되는 동안 반복. `state_replay_gap_sec`가
  0이면 플레이어의 연속 루프를 쓰고, 0보다 크면 한 번 재생 후 설정한 시간만큼
  쉬었다가 다시 재생합니다. 상태가 바뀌면 즉시 교체/정지.
- **재생 횟수:** `state_repeat_count`가 공통값이고 0이면 위처럼 무한 반복입니다.
  1이면 한 번만 울리고 조용해집니다. 같은 상태/action이 유지되는 동안에는 다시
  울리지 않고, 그 사이에 다른 트랙(또는 무음)이 한 번이라도 선택되어야 다시
  울립니다. 즉 같은 action이 연달아 두 번 실행되는데 그 경계가 state publish
  주기(`state_publish_delay`) 안에 다 들어가 버리면 두 번째는 소리가 나지
  않습니다 — 어댑터가 그 틈을 못 보기 때문입니다. 파일별로 다르게 주려면
  `[sound_settings.repeat_count_overrides]`에 **확장자를 뺀 파일명**을 키로 적습니다.
  간격 override와 달리 action 파일도 여기서 지정할 수 있고, `actionType` 대소문자를
  그대로 씁니다.

  ```toml
  [sound_settings]
  state_repeat_count = 0

  [sound_settings.repeat_count_overrides]
  action-clamp = 1              # clamp 할 때 한 번만
  action-pioElevatorOpen1f = 2  # 두 번
  driving = 0                   # 주행 중에는 계속
  ```
- **볼륨:** 시작 시 `startup_volume`. 런타임은 `setSoundVolume` instant action 또는
  호스트에서 `pactl set-sink-volume <sink> N%`.
- **출력 sink:** 기본은 온보드 아날로그(`alsa_output.platform-rt5651-sound.stereo-fallback`).
  PulseAudio가 HDMI(무신호)로 기본 잡히면 소리가 안 나므로 이 sink로 고정.

## 출력 장치 찾기

보드마다 실제 스피커가 붙은 PulseAudio sink가 다를 수 있습니다. 현장에서 아래
스크립트를 실행하면 모든 sink를 순서대로 짧게 재생하고, 들리는 장치 이름을
`config/config.toml`에 복사할 수 있습니다.

```bash
cd ~/adapter
scripts/test-sound-devices.sh --seconds 3
```

들리는 줄의 sink 이름을 설정합니다.

```toml
[sound_settings]
sink = "alsa_output.platform-rt5651-sound.stereo-fallback"
```

설정 위치는 보드 기준 `~/adapter/config/config.toml`입니다. WebUI가 떠 있으면
`http://<board-ip>:<webui-port>/config`에서 `[sound_settings]` 섹션의 `sink`
행을 같은 값으로 저장해도 됩니다. WebUI는 scalar config 값을 자동으로 노출하며,
저장 후에는 어댑터 서비스를 재시작해야 런타임에 반영됩니다.

설정 후 서비스를 재시작합니다.

```bash
sudo systemctl restart amr-adaptor.service
```

그래도 무음이면 rt5651 mixer route가 일부 off일 수 있습니다. 이번 JIBOT 보드에서는
`mplayer`와 PulseAudio sink-input은 정상이었지만 codec 내부의 `OUT MIX*`,
`LOUT MIX*`, `HPO MIX HPVOL` route가 꺼져 있으면 실제 출력으로 이어지지
않았습니다.

상태 확인:

```bash
cd ~/adapter
scripts/test-sound-devices.sh --show-mixer --list-only
```

known-good rt5651 route를 켜고 다시 PulseAudio sink를 순회:

```bash
cd ~/adapter
scripts/test-sound-devices.sh --prepare-rt5651 --seconds 3
```

PulseAudio를 우회해서 ALSA card 0에 직접 테스트 톤을 보내려면:

```bash
cd ~/adapter
scripts/test-sound-devices.sh --prepare-rt5651 --direct-alsa --seconds 5
```

`--direct-alsa`에서도 소리가 안 나면 WebUI/adapter/PulseAudio 문제는 아닙니다.
rt5651 codec route가 보드 이미지와 맞지 않거나, 스피커/앰프 전원/배선 쪽을
확인해야 합니다. 다른 ALSA card 번호를 쓰는 보드는 `ALSA_CARD=1`처럼 지정합니다.

## 런타임 업로드 (선택)

ACS/eq에서 `uploadSound` instant action으로 mp3를 이 폴더에 푸시할 수 있습니다
(`fileName`=`*.mp3`, `data`=base64). 위 규칙에 맞는 파일명으로 올리면 됩니다.

## 동작 메모

- 현재 상태에 맞는 파일이 **없으면** 무음(크래시·경고 스팸 없음).
- `enabled = false`면 사운드 기능 전체 off (스피커 없는 로봇용).
- 소리가 안 나오면 보통 sink 문제: 호스트에서 `pactl list short sinks`로 확인.
