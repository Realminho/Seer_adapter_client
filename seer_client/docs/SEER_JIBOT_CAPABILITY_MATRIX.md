# SEER / JIBOT 기능 판단표

이 문서는 실제 공용 Adapter의 ActionRegistry를 로드한 뒤 `capabilities.py` 정책으로 생성한 결과다.
실제 VDA5050 Factsheet에는 현재 런타임에서 실행 가능한 액션만 표시된다.

## 상태 의미

- `supported`: 현재 SEER 런타임에서 실행 가능한 기능
- `conditional`: 호스트 장치나 설정이 있을 때만 가능한 기능
- `simulator_only`: Simulator에서만 안전하게 제공하는 기능
- `blocked`: SEER API 또는 장치 계약이 없어 광고하면 안 되는 기능

## 집계

- `supported`: 22개
- `conditional`: 5개
- `simulator_only`: 2개
- `blocked`: 37개

| Action | 상태 | 조건 | 판단 이유 |
|---|---|---|---|
| `airShowerEnter` | `blocked` | - | no SEER-compatible handler is registered |
| `airShowerInside` | `blocked` | - | no SEER-compatible handler is registered |
| `airShowerPassed` | `blocked` | - | no SEER-compatible handler is registered |
| `cancelOrder` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `chargeInPlace` | `blocked` | - | external charge-circuit contract is not configured |
| `clamp` | `blocked` | - | no SEER-compatible handler is registered |
| `clampHome` | `blocked` | - | no SEER-compatible handler is registered |
| `clampMax` | `blocked` | - | no SEER-compatible handler is registered |
| `clampMin` | `blocked` | - | no SEER-compatible handler is registered |
| `clampMoveTo` | `blocked` | - | no SEER-compatible handler is registered |
| `clampOff` | `blocked` | - | no SEER-compatible handler is registered |
| `clampOn` | `blocked` | - | no SEER-compatible handler is registered |
| `clampStop` | `blocked` | - | no SEER-compatible handler is registered |
| `clampTeach` | `blocked` | - | no SEER-compatible handler is registered |
| `clearErrors` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `clearInstantActions` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `clearZoneActions` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `disableMotor` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `elevatorEnter` | `blocked` | - | no SEER-compatible handler is registered |
| `elevatorInside` | `blocked` | - | no SEER-compatible handler is registered |
| `elevatorPassed` | `blocked` | - | no SEER-compatible handler is registered |
| `enableMotor` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `ezioReadIn` | `blocked` | - | no SEER-compatible handler is registered |
| `ezioWaitIn` | `blocked` | - | no SEER-compatible handler is registered |
| `ezioWriteOut` | `blocked` | - | no SEER-compatible handler is registered |
| `factsheetRequest` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `getMap` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `gotoNearestNode` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `initPosition` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `jibotCommand` | `blocked` | - | raw JIBOT commands must never reach a SEER controller |
| `jibotMotionRule` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `loading` | `blocked` | - | JIBOT-specific device/workflow is not configured for SEER |
| `localize` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `logReport` | `blocked` | - | JIBOT-specific device/workflow is not configured for SEER |
| `manualDrive` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `manualMove` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `manualStop` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `photoSensorRead` | `blocked` | - | no SEER-compatible handler is registered |
| `pioDisconnect` | `blocked` | - | no SEER-compatible handler is registered |
| `pioInit` | `blocked` | - | no SEER-compatible handler is registered |
| `pioReadIn` | `blocked` | - | no SEER-compatible handler is registered |
| `pioScenario` | `blocked` | - | no SEER-compatible handler is registered |
| `pioWriteOut` | `blocked` | - | no SEER-compatible handler is registered |
| `requestLaser` | `blocked` | - | SEER laser streaming API is not confirmed |
| `requestVideo` | `conditional` | video.enabled=true | adapter-host video function |
| `seerEmergencySwitch` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `seerPathNav` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `seerSetDO` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `setMap` | `simulator_only` | simulator=true | safe in-memory map operation in the SEER simulator |
| `setMapSnapshot` | `simulator_only` | simulator=true | safe in-memory map operation in the SEER simulator |
| `setSoundVolume` | `conditional` | sound_settings.enabled=true | adapter-host sound function |
| `startCharging` | `blocked` | - | SEER docking/charging API is not confirmed |
| `startPause` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `stateRequest` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `stopCharging` | `blocked` | - | SEER charging-stop API is not confirmed |
| `stopLaser` | `blocked` | - | SEER laser streaming API is not confirmed |
| `stopLoading` | `blocked` | - | JIBOT-specific device/workflow is not configured for SEER |
| `stopPause` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `stopSound` | `conditional` | sound_settings.enabled=true | adapter-host sound function |
| `stopUnloading` | `blocked` | - | JIBOT-specific device/workflow is not configured for SEER |
| `switchMap` | `supported` | - | SEER-compatible built-in or executable ActionRegistry handler |
| `syncJibotParams` | `blocked` | - | JIBOT parameter synchronization is vendor-specific |
| `testSound` | `conditional` | sound_settings.enabled=true | adapter-host sound function |
| `unclamp` | `blocked` | - | no SEER-compatible handler is registered |
| `unloading` | `blocked` | - | JIBOT-specific device/workflow is not configured for SEER |
| `uploadSound` | `conditional` | sound_settings.enabled=true | adapter-host sound function |

## 현재 외부 자료가 필요한 주요 차단 기능

- `startCharging` / `stopCharging`: SEER 도킹·충전 API, charger ID, 진행·완료 상태, 취소 및 오류코드 필요
- `requestLaser` / `stopLaser`: 공식 laser stream API와 최대 요청주기·데이터 크기 필요
- Clamp/PIO/EZI/시설 기능: 현장 장치 존재 여부와 IP·채널·handshake 설정 필요

실제 런타임 Registry를 포함해 다시 확인하려면:

```powershell
python .\seer_client\print_capabilities.py --runtime-adapter
python .\seer_client\print_capabilities.py --runtime-adapter --simulator --json
```
