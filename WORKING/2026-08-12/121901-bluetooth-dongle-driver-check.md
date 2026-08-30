# bluetooth-dongle-driver-check

### 목표
- AMR(192.168.101.61) Ubuntu에 꽂은 USB Bluetooth 동글 인식 실패 원인 파악

### 지금
- USB 허브 경유 재삽입도 실패. 허브 자체가 열거되지 않음 → 포트/케이블 문제로 좁혀짐

### 완료
- 커널 BT 지원 확인: `CONFIG_BT=y`, `BT_HCIBTUSB=y`, `BT_RTL/INTEL/BCM=y`, `BT_HIDP=y` (전부 내장)
- 19:49 허브 삽입 시에도 동일 포트에서 실패: `usb 6-1 ... error -62`, 신규로 `usb usb4-port1: Cannot enable. Maybe the USB cable is bad?`
- 서로 다른 장치 2종(동글/허브)이 같은 포트(fe3c0000 EHCI=bus4 / fe3e0000 OHCI=bus6)에서 실패 → 포트 불량 유력
- `/lib/firmware`에 BT 펌웨어 없음(`rtl_bt`/`intel`/`brcm` 부재), `linux-firmware` 미설치, AMR 인터넷 없음
- 포트 여유 파악: SSH는 wlan0(iwlwifi) 경유라 USB 이더넷 탈거해도 세션 안전. `eth01`(허브 포트 `1-1.4`)은 DOWN 상태로 미사용

### 다음
- 1순위: 미사용 USB-C 포트 활용. `fe800000.usb`(dwc3, `dr_mode=otg`)에 Type-C DRP 포트 존재
  - `/sys/class/typec/port0`: `port_type=[dual] source sink`, `data_role=host [device]`, `orientation=unknown`(미연결)
  - USB-C(수)→USB-A(암) OTG 어댑터 연결 시 CC Rd 감지로 host/source 자동 전환, xhci 경유라 고장난 EHCI/OHCI 우회
  - 미전환 시 `data_role`/`preferred_role`/`power_role` 모두 root 쓰기 가능
- 2순위(부품 불필요): `old_scheme_first=Y`, `initial_descriptor_timeout` 증가 후 `ehci-platform`/`ohci-platform` unbind·bind로 물리 재삽입 없이 재열거
  - 바인딩 확인: ehci=`fe380000.usb`,`fe3c0000.usb` / ohci=`fe3a0000.usb`,`fe3e0000.usb` (bus3~6에 다른 장치 없음)
- 참고: WiFi는 Intel AX210(8086:2725)로 BT 내장이지만 `8087:*`가 USB에 전혀 안 뜸 → M.2 USB2 라인 미배선/미활성으로 판단, 내장 BT 사용 불가
- 열거 성공 후 칩셋 확인 → Realtek/Intel이면 로컬 `linux-firmware`에서 압축 해제 후 scp (커널 5.10은 .zst 미지원)

### 검증
- 원격 실행 명령은 모두 조회 전용(lsusb/dmesg/ip/ls/cat), 시스템 변경 없음
