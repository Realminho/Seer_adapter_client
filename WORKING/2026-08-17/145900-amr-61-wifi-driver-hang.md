# amr-61-wifi-driver-hang

### 목표
- .61에서 keeper 조치 후에도 WiFi가 끊기고 자동 복구가 실패하는 원인 확인

### 지금
- 14:30~14:47 먹통 구간의 커널/iwlwifi 로그 수집

### 완료
- 8/15 조치(드롭인+keeper+bgscan+power_save)로 **부팅 경합은 해결됨**
  (wpa_supplicant 재시작 0회, `Scheduled restart|SCAN-FAILED` 집계 0)
- 그러나 8/17 14:30~14:47 약 17분간 링크 상실, keeper가 40초 주기로
  `bouncing wlan0` ↔ `restarting wpa_supplicant`를 반복했으나 **한 번도 복구 못 함**
- 14:48:02 재부팅 후에야 정상 결합 → wpa_supplicant 계층이 아니라 드라이버/펌웨어 의심
- 참고: 저널이 08-17 14:30:49부터만 남아 있음(이전엔 07-29부터 보존) — 별도 확인 필요

### 완료 — 원인 위치 특정
- 먹통 구간 wpa_supplicant는 PID 24635 → 30738로 **재시작됐는데도 새 프로세스가 즉시
  `SCAN-FAILED ret=-22`** 재개. 인터페이스 bounce도 무효 → 막힌 곳은 wpa_supplicant 아래,
  즉 드라이버/펌웨어 계층
- 먹통 구간 커널 메시지 **0건** → 진단 불가. 원인은 저널 상한:
  `SystemMaxUse=100M`인데 실사용 124M, **보존 부팅 2개뿐**. 디스크는 12G 여유.
  초당 1건 `SCAN-FAILED` 스팸이 커널 로그를 밀어냄
- `iwlwifi`는 커널 빌트인(lsmod 없음) → `modprobe -r` 불가.
  단 `/sys/bus/pci/devices/0000:01:00.0/remove` + `/sys/bus/pci/rescan` 존재
  → **재부팅 없이 카드 재초기화 가능**. 기존 keeper에 빠져 있던 복구 수단

### 완료 — keeper v2 작성·업로드 (`~/wifi-fix/`)
- 링크 상실 시 1회 진단 스냅샷을 `/var/log/amr-wifi-diag.log`에 기록
  (dmesg tail 60, iw link/info/reg, ip link, wpa 유닛 상태·최근 로그)
- escalation에 **PCIe remove+rescan** 단계 추가:
  reconnect → wpa 재시작 → 링크 bounce → PCIe 리셋 → 순환
- `journald-keep-history.conf`: SystemMaxUse 100M → 500M, MaxFileSize 20M → 50M

### 완료 — repo 편입 (재사용 가능하게)
- `scripts/amr-wifi-keeper.sh` (v2)
- `scripts/systemd/amr-wifi-keeper.service`
- `scripts/systemd/wpa-supplicant-wait-for-device.conf`
- `scripts/systemd/journald-keep-history.conf`
- `scripts/setup-wifi-keeper-over-ssh.sh` — 대상 호스트만 바꿔 실행하는 설치기.
  PCI 슬롯 자동 감지, bgscan은 나쁜 값일 때만 치환(재실행 안전),
  **wpa_supplicant를 재시작하지 않음**(8/10 .62 접속 상실 재발 방지)

### 적용 현황 (2026-08-17 16:10 확인)
- **.61**: 8/15분 전부 적용됨(드롭인·bgscan·power_save·keeper v1).
  keeper **v2**와 journald 상한은 **미적용** → 설치기 실행 필요
- **.62**: `No route to host`. 이동 후 다른 망이라 접속 불가. 복귀 시 설치기 실행

### 다음
- `scripts/setup-wifi-keeper-over-ssh.sh ucore@192.168.101.61` 실행해 v2로 올리기
- 다음 먹통 때 `/var/log/amr-wifi-diag.log`의 dmesg로 펌웨어 크래시 여부 확정
- 확정되면 AX210 ucode 갱신 검토 (드라이버는 API 65~60 요청, 현재 59 폴백)

### 검증
- `journalctl -b -1 -u wpa_supplicant` PID 변화 및 재시작 후 즉시 -22 재개 확인
- `journalctl --disk-usage`, `journalctl --list-boots | wc -l` = 2

## 2026-08-18 먹통 상태 실시간 캡처 (유선 10.6.6.6 경유)

### 발단 (부팅 09:46:26 직후)
```
09:46:37  wlan0: authenticate → authenticated       (802.11 인증 성공)
09:46:38  wlan0: associated                          (결합 성공)
09:46:48  wpa_supplicant: Authentication with 20:e1:5d:5c:81:81 timed out.
          → WPA 4-way(EAPOL) 핸드셰이크 타임아웃. 802.11 인증이 아니라 키 교환 실패
09:46:48  kernel: deauthenticating ... by local choice (Reason: 3=DEAUTH_LEAVING)
```
- 이후 1시간 45분간 스캔이 전부 `ret=-22`, 커널 wlan0 이벤트 0건
- 직전 부팅 dmesg에 `RX AssocResp ... status=30` +
  `rejected association temporarily; comeback duration 1024 TU`
  → AP가 "지금 바쁘니 나중에" 응답. AP(hana-stk2-cctv, CCTV용 2.4GHz ch11) 과부하 정황

### 상태 스냅샷
- wlan0 **존재**(DOWN/NO-CARRIER), PCI 0000:01:00.0 **존재**, 펌웨어 API 59 폴백
- wpa_supplicant **실행 중**(재시작 반복, PID 계속 바뀜), keeper **실행 중**
- keeper 로그: `restarting wpa_supplicant` ↔ `bouncing wlan0` 40초 간격 무한 반복
  → **설치된 것이 v1이라 PCIe 리셋 단계 없음**. v2 미설치가 확인됨

### 원인 두 층으로 분리됨
1. **유발**: AP 측 결합 임시거부(status=30) / EAPOL 핸드셰이크 타임아웃
2. **고착**: 한 번 실패하면 AX210이 스캔 EINVAL 상태로 고착, wpa 재시작·링크 bounce로 못 풀림
   → keeper v2의 PCIe remove/rescan이 겨냥하는 지점

### 다음
- 유선 접속이 살아 있는 지금이 PCIe 리셋 유효성 검증 적기 (실패해도 접속 유지)

### PCIe 리셋 유효성 검증 — 성공 (2026-08-18 11:4x, 유선 경유 수동 실행)
```
t=6575  echo 1 > .../0000:01:00.0/remove ; echo 1 > /sys/bus/pci/rescan
        → 펌웨어 재로드, AX210 재검출, base HW address 재설정
t=6588  authenticate → authenticated → associate → associated
        IPv6: ADDRCONF(NETDEV_CHANGE): wlan0: link becomes ready
```
- **1시간 45분 고착이 약 13초 만에 해소**. wpa 재시작·링크 bounce로는 못 풀던 것이 풀림
- keeper v2의 PCIe remove/rescan 단계가 이 종류 먹통을 자동 복구할 수 있음이 실측 확인됨
- 192.168.101.61 WiFi 복구 확인 (-38dBm)
- 펌웨어 크래시(Microcode SW error) 아님 — 카드는 살아 있고 재초기화만으로 해소됨.
  따라서 AX210 ucode 갱신은 후순위

### 남은 작업
- .61 keeper를 v1 → **v2로 교체** (현재도 v1, journald 상한도 미설정)
  `./scripts/setup-wifi-keeper-over-ssh.sh ucore@10.6.6.6`  (유선 주소 권장)
- AP측 조사: EAPOL 4-way 타임아웃 + `status=30` 임시거부 반복 →
  CCTV AP(hana-stk2-cctv, 2.4GHz ch11) 부하/채널 점검, 로봇 전용 AP 검토
