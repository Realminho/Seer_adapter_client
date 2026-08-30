# amr61-goto-nearest-not-moving

### 목표
- 61(HN-SH6-TR-001)에 gotoNearestNode 를 보냈는데 움직이지 않는 원인 규명

### 지금
- 전체 커밋 완료. 남은 일은 amr 4.3.76 배포뿐

### 완료
- state(MQTT retained) 실측: operatingMode=STARTUP, position 0,0, lastNodeId="", nearestNodeId="" (비어 있음)
- state errors 3건: INVALID_INSTANT_ACTION(WARNING), LAST_NODE_ID_MISSING(CRITICAL), **VEHICLE_LINK_LOST(FATAL, 10.8.8.8:7273 disconnected)**
- 20:16:49 gotoNearestNode 수신했으나 blockingType=HARD 로 입구에서 거부 (FMS 는 아직 수정 전 빌드 4.3.75)
- 10.8.8.8 은 이 PC 자신의 eth0. 7273 = Connection refused → mg_main(JSrvTcp) 미기동
- urobot.service active 이나 mg_main 프로세스 없음. urobot-watchdog 은 3회 재시작 후 19:45 포기(/run/urobot-watchdog.gaveup)
- urobot 로그: m04b left/right laser 가 `serialOpen(): cannot open serial port` 무한 반복
- **현재 부팅(19:31:42~)에 USB 장치 0개**. lsusb 는 root hub 만, usb-serial 모듈 미로드, /dev/ttyUSB* 없음
- 부팅별 비교: boot -5/-4/-3/-2 는 부팅 시각에 FTDI 4포트 attach + JIBOT 연결 성공.
  boot -1(14:30:22)부터 부팅 시 attach 실패 → JIBOT 연결 0회. 19:06:49 `usb 1-1: hub failed to enable device, error -62`
  직후 19:07 재열거로 ttyUSB0~4 복귀했으나 watchdog 이 이미 포기해 urobot 재시작 없이 7273 계속 down
- 결론: VIA Labs 2109:2822 USB 허브 + FTDI 4포트 컨버터가 부팅 시 간헐적으로 열거되지 않음(전원/케이블/신호 계열)

### 다음
- unified-amr-adaptor 전체 커밋 (adaptor 테스트 통과 확인 후)
- 별개로 FMS 측 blockingType NONE 수정(amr 4.3.76, 62ab89c) 배포 필요 — 배포 전엔 61이 계속 HARD 로 보냄

### 검증
- `journalctl -b -k | grep -cE 'usb [0-9]-'` → 0 (현재 부팅 USB 열거 0건)
- `ls /sys/bus/usb/devices/` → root hub(usb1~6)만 존재
- `pgrep -a mg_main` → 없음, `/dev/tcp/10.8.8.8/7273` → Connection refused

### 커밋 (22:15~)
- fabris-equipments 75acdd4: hana-p2 MES inbound OUT_SHELF_LOT (jest 45 suites / 1899 통과)
- amr blockingType NONE 수정은 이미 다른 세션이 62ab89c 로 커밋함
- unified-amr-adaptor fe8e867: urobot 7273 워치독 스크립트(bash -n 통과)
- unified-amr-adaptor 49d7b29: 도착 heading + localize node 앵커 + rotateTo
- unified-amr-adaptor 1e480d5: WORKING 로그
- 검증: run-tests.sh -q -> 2171 passed / 14 failed. 13건은 HEAD worktree 에서도
  동일 재현되는 기존 실패, 1건(test_action_modules)은 다른 세션이 22:17 에 해소
- 두 저장소 모두 push 하지 않음
