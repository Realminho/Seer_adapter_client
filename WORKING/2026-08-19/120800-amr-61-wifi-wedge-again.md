# amr-61-wifi-wedge-again

### 목표
- .61 WiFi 재차 미연결(2026-08-19 11:28~) 원인 확인 및 keeper v2 적용

### 지금
- 유선 10.6.6.6 경유 상태 수집 완료. 8/17·8/18과 동일한 고착 재현 확인

### 완료 — 상태 확인 (12:09 기준)
- 부팅 09:58:56, 현재 uptime 2h10m (재부팅 없음)
- `iw dev wlan0 link` → **Not connected**, wlan0 DOWN
- keeper **v1** (`pci_reset` 없음), PID 631 정상 실행 중
- wpa_supplicant active, RestartUSec=3s (8/15 드롭인 적용됨), bgscan `simple:30:-70:300` 적용됨
- `/var/log/amr-wifi-diag.log` **없음** → v2 미설치 재확인
- journald 여전히 `SystemMaxUse=100M` / `MaxFileSize=20M`, 보존 부팅 3개

### 완료 — 타임라인
```
09:58:56  부팅
09:59:00  keeper link lost → 09:59:20 복구 (부팅 직후 정상 경합)
11:18:05  kernel: wlan0: Connection to AP 20:e1:5d:5c:81:81 lost
11:18:08  재인증·재결합 성공 (status=0, aid=41)   ← 1차 드롭은 자력 복구
11:28:44  kernel: wlan0: Connection to AP ... lost  ← 2차 드롭
11:28:45  keeper link lost on wlan0
11:28~    이후 커널 wlan0 이벤트 **0건**, 스캔 전부 CTRL-EVENT-SCAN-FAILED ret=-22
12:09     40초 주기 restart wpa_supplicant ↔ bouncing wlan0 무한 반복 (약 41분째)
```
- wpa_supplicant PID 14457 → 14801 → 14925 → 15318 계속 교체되나 **새 프로세스도 즉시 -22**
- 8/17(17분), 8/18(1h45m)과 **동일 고착**. v1에는 PCIe 리셋 단계가 없어 자력 복구 불가

### 원인 (기존 분석 그대로 재확인)
1. 유발: AP측 `Connection to AP lost` (10분 새 2회). AP는 hana-stk2-cctv, 2.4GHz ch11 공유
2. 고착: AX210이 스캔 EINVAL 상태로 고착 → wpa 재시작·링크 bounce 무효, PCIe 재프로브만 해소

### 다음
- 설치기 실행 (sudo 비번 필요, 사용자 실행):
  `./scripts/setup-wifi-keeper-over-ssh.sh ucore@10.6.6.6`
  → keeper v2 설치 + 재시작 시 escalation 진행, 약 2~3분 내 PCIe 리셋으로 자동 복구 예상
  → journald 100M → 500M 상향도 함께 적용됨 (다음 먹통의 dmesg 보존)
- 복구 후 검증: `pgrep -af amr-wifi-keeper` PID 변경, `ls /var/log/amr-wifi-diag.log`,
  `iw dev wlan0 link`
- AP측 조사 (미해결 근본원인): 로봇 전용 AP / 5GHz 분리

### 검증
- `journalctl -u wpa_supplicant` 재시작에도 -22 지속 → 고착 위치가 드라이버 계층임 재확인
- `dmesg | grep wlan0` 11:28:44 이후 이벤트 없음

## keeper v2 설치 및 자동 복구 확인 (12:12~12:15)

```
12:12:08  keeper v2 설치·재시작 → diagnostics captured (v2 표시)
12:12:48  offline 40s -> wpa_cli reconnect        (무효)
12:13:28  offline 50s -> restarting wpa_supplicant (무효)
12:14:08  offline 50s -> bouncing wlan0            (무효)
12:14:50  PCIe remove+rescan on 0000:01:00.0
12:14:55  power save off
12:15:15  link recovered after 35s
```
- **46분 고착이 PCIe 리셋 5초 만에 해소**. 8/18 .62 자동복구에 이은 2번째 실측
- .61 최종 상태: keeper v2 / active, journald 500M·50M 드롭인 적용,
  `/var/log/amr-wifi-diag.log` 생성, wpa RestartUSec=3s, 192.168.101.61 WiFi 접속 복귀

## 복구 직후 링크 불안정 급증 — AP측 문제 확정적

- 12:15~12:22 **7분간 CTRL-EVENT-DISCONNECTED 10회** (reason=4 ×8, reason=3 ×2)
- reason=4 `locally_generated=1` = 비콘 상실(DISASSOC_DUE_TO_INACTIVITY).
  kernel도 `Connection to AP ... lost` 반복. 매 회 20초 내 자력 재결합
- **신호는 −44~−47 dBm로 양호**, power_save off 적용됨 → 거리·절전 문제 아님
- txrate 58500~243700 kbps로 심하게 요동
- → 2.4GHz ch11(2462MHz) 간섭/AP 과부하. 로봇 단말이 비콘을 못 받는 상황

### 결론
- **고착(먹통)은 해결**: keeper v2가 사람 개입 없이 복구. 1·2호기 모두 적용 완료
- **끊김 자체는 미해결**: AP(hana-stk2-cctv, 2.4GHz ch11) 측 원인. 현재도 분당 1~2회 끊김

### 다음
- AP 조사: ch11 혼잡도·접속 단말 수 확인, 로봇 전용 AP(5GHz) 분리
- .62는 12:19 현재 `192.168.101.62` 무응답(No route to host) — 전원 상태 별도 확인 필요

## 17:00 재확인 — 새 발견: wlan0을 두 주체가 동시에 관리 중

### 경위 (부팅 14:26:42, 이후 재부팅 3회 있었음: 14:18, 14:26)
```
14:26:50  NetworkManager가 프로파일 'TP_Link_AMR_5G'로 wlan0 활성화 → 2시간 27분 정상
16:53:19  CTRL-EVENT-DISCONNECTED reason=4 locally_generated=1, signal=0
          kernel: Connection to AP ... lost          ← 비콘 상실(기존과 동일 유발)
16:54:15~ keeper escalation 시작, PCIe 리셋 4회 (16:56/16:58/17:00/17:04)
17:05:10  link recovered (총 11분 36초, PCIe 리셋 4회 소요)
```

### 왜 12:14처럼 5초 만에 안 풀렸나 — 관리 주체 충돌
- `wpa_supplicant.service`가 `-u -s` (D-Bus 모드)로 뜸 → **NetworkManager의 supplicant 백엔드**
- 동시에 `/etc/wpa_supplicant/wpa_supplicant.conf`에도 `hana-stk2-cctv` network 블록 존재
- 즉 **NM 프로파일과 wpa_supplicant.conf가 같은 SSID를 두고 경합**
- 17:00:41 wpa_supplicant.conf 쪽이 먼저 결합 성공(−41dBm, Key negotiation completed)
  → 17:00:46 `reason=3 DEAUTH_LEAVING locally_generated=1`로 **5초 만에 강제 해제**
  → NM이 자기 프로파일로 다시 붙으려다 실패 반복
- keeper의 `systemctl restart wpa_supplicant`가 NM의 supplicant를 죽임
  → NM 로그 `state change: config -> unavailable (reason 'supplicant-failed')`
  → 매 escalation 사이클마다 NM 상태머신 재구축 필요 → 복구가 5초→2분으로 늘어남

### NM 프로파일 실태
- `TP_Link_AMR_5G` (uuid d0e2612f…, autoconnect=yes, priority=10, interface-name=wlan0)
  → **실제 ssid는 `hana-stk2-cctv`**. 이름과 내용 불일치
- 부팅 시 경고: `/etc/NetworkManager/system-connections/TP_Link_AMR_5G.nmconnection`
  `failed to load connection: invalid connection: connection.type: property is missing`
  → **5GHz 전용 AP용 키파일을 만들다 만 흔적**. 현재 로드되는 프로파일은 별개
- 스캔 결과 `TP_Link`/`AMR` SSID **미검출** → 전용 AP가 없거나 꺼져 있음

### RF 환경 (17:05 스캔)
- 2.4GHz 7개 / 5GHz 25개. **ch11(2462MHz)에 3개 동일채널**
- 주변: Tapo_Cam_B98A(2437), DobotCR16A-4526-1000(2427), HMK-MES(5620) 등

### 결론 정리
1. 먹통 자동복구는 동작함 (사람 개입 0). 단 관리 주체 충돌로 복구 시간이 길어짐
2. 유발 원인은 여전히 AP측 비콘 상실
3. **새 과제**: wlan0 관리 주체를 하나로 정리해야 함

### 다음
- 관리 주체 일원화 (택1)
  - NM 유지: `wpa_supplicant.conf`의 network 블록 제거 +
    keeper의 wpa 재시작 단계를 `nmcli con up`/`nmcli device reapply`로 교체
  - NM 배제: wlan0을 NM unmanaged로 두고 wpa_supplicant.conf 단독 운용
- `TP_Link_AMR_5G.nmconnection` 손상 키파일 정리 및 5GHz 전용 AP 실제 구축

## 17:1x 관리 주체 조사 결과 — NM은 뺄 수 없음, 대신 결함 4개

### 실제 구성은 3중 관리였음
| 계층 | 담당 | 근거 |
|---|---|---|
| systemd-networkd (netplan) | wlan0 **정적 IP 192.168.101.61** + 정적 기본경로 | `networkctl status wlan0` → `/run/systemd/network/10-netplan-wlan0.network`, `State: routable (configured)` |
| NetworkManager | wlan0 **무선 결합** (프로파일 `TP_Link_AMR_5G`) | `nmcli device status` → wlan0 connected |
| wpa_supplicant.conf | 같은 SSID network 블록 (psk 포함) | `wpa_supplicant.service`가 `-c ... -i wlan0`로 직접 로드 |

- 이 3중 구조는 **의도된 설계**임. 저장소의 `scripts/change-jibot-network-over-ssh.sh`(7/27)
  헤더에 "editing the three places involved when a JIBOT's address moves"로 명시:
  netplan / wpa_supplicant.conf / NM 프로파일 동기화 → `netplan apply` →
  `restart NetworkManager` → `restart urobot.service`
- 로봇에도 벤더 스크립트 `/home/ucore/setcoreip.sh`(중문)가 같은 netplan을 씀
- → **NM 제거는 불가.** IP 변경 툴링과 urobot 재기동 흐름이 NM에 의존

### 결함 1 — wpa_supplicant가 NM 백엔드 + 독립 연결을 겸함
```
ExecStart=/sbin/wpa_supplicant -u -s -c /etc/wpa_supplicant/wpa_supplicant.conf -i wlan0
```
- `-u -s` = NM의 D-Bus 백엔드. 여기에 `-c/-i`가 붙어 **자기 network 블록으로도 독립 연결**
- 결과: 17:00:41 conf 쪽이 결합 성공 → 17:00:46 NM이 `reason=3 DEAUTH_LEAVING`로 해제
- 파일은 `/etc/systemd/system/wpa_supplicant.service` (7/21 10:16 작성, 패키지 유닛 덮어씀)

### 결함 2 — NM DHCP가 IP·기본경로를 하나 더 만듦
```
wlan0  192.168.101.114/24  192.168.101.61/24     ← IP 2개
default via 192.168.101.1 dev wlan0 proto static           ← netplan
default via 192.168.101.1 dev wlan0 proto dhcp metric 20600 ← NM DHCP
```
- NM 프로파일 `ipv4.method: auto` 때문. 지금은 정적 경로가 이기지만 불안정하고,
  .114는 임대라 언제든 바뀜 → FMS가 .61로 못 찾는 상황 유발 가능

### 결함 3 — NM이 매 활성화마다 power save를 다시 켬  ★유발 원인 후보★
```
/etc/NetworkManager/conf.d/default-wifi-powersave-on.conf
[connection]
wifi.powersave = 3        # 3 = enable
```
- 프로파일은 `802-11-wireless.powersave: 0 (default)` → 위 전역값 3을 상속
- keeper가 `iw ... power_save off`로 끄지만 **NM이 재활성화할 때마다 다시 켜짐**
- power save는 이 AX210에서 `reason=4 DISASSOC_DUE_TO_INACTIVITY`의 알려진 원인이고,
  오늘 16:53:19 최초 끊김도 정확히 `reason=4 locally_generated=1`

### 결함 4 — keeper의 `systemctl restart wpa_supplicant`가 NM 백엔드를 죽임
- NM 로그: `state change: config -> unavailable (reason 'supplicant-failed')`
- escalation 사이클마다 NM 상태머신 재구축 → 복구가 5초에서 11분으로 늘어남

### 참고 — 오늘 14:27에 누군가 netplan을 건드림
- `/etc/netplan/01-network-manager-all.yaml_back` mtime **Aug 19 14:27**
- 같은 시각 재부팅 2회(14:18, 14:26). 오늘 사태의 발단일 수 있음 → 사용자 확인 필요
- 또한 netplan 본문에 `ethernets:` 키가 2번 등장(파서가 병합해 현재는 정상 동작하나 위험)

### 다음 — 권고 조치 (유선 10.6.6.6에서 실행, 무선 끊김 동반)
```bash
sudo sed -i 's/^wifi.powersave = 3/wifi.powersave = 2/' \
  /etc/NetworkManager/conf.d/default-wifi-powersave-on.conf          # 결함 3
sudo nmcli con mod TP_Link_AMR_5G ipv4.method disabled               # 결함 2
sudo sed -i 's|-u -s -c /etc/wpa_supplicant/wpa_supplicant.conf -i wlan0|-u -s -O /run/wpa_supplicant|' \
  /etc/systemd/system/wpa_supplicant.service                          # 결함 1
sudo systemctl daemon-reload && sudo systemctl restart NetworkManager
```
- 결함 4는 저장소 수정: keeper의 wpa 재시작 단계를 `nmcli con up` / `nmcli device reapply`로 교체

## 18:2x~18:4x 조치 적용 후 경과 — 미해결

### 적용된 것 (사용자 실행)
- `wifi.powersave = 3` → `2` (NM이 power save 재활성화하던 것 차단)
- `nmcli con mod TP_Link_AMR_5G ipv4.method disabled` → 이후 `auto`로 되돌림
- `wpa_supplicant.service` ExecStart: `-u -s -c ... -i wlan0` → `-u -s -O /run/wpa_supplicant`
  (NM 전용 백엔드로 일원화. 독립 연결 제거)
- `nmcli con mod TP_Link_AMR_5G 802-11-wireless.hidden yes`
- keeper는 stop 상태로 둠

### 문제 — 링크 복구 실패
- 18:29:14 다시 링크 상실(오늘 3번째: 11:28 / 16:53 / 18:29)
- keeper가 멈춰 있어 PCIe 리셋이 안 돌아감 → 수동 리셋 실행
- 리셋 직후 **스캔 1회 성공**(AP 목록 정상 수신) 후 **수 분 내 재고착**
  → `CTRL-EVENT-SCAN-FAILED ret=-22` 재개
- NM 활성화는 `reason 'ssid-not-found'`로 실패하나, 이는 hidden SSID 때문이 아니라
  **스캔 자체가 불가능해서** 발생한 것

### 정정 — "AP가 SSID를 숨기기 시작했다"는 관측은 오독
- 사용자 확인: **AP는 원래 숨김 SSID**
- 17:05 스캔에 `hana-stk2-cctv`가 보였던 것은 당시 결합 상태여서 SSID가 채워진 것.
  18:29 이후 `--`로 보인 것은 결합한 적이 없어서일 뿐. AP 설정 변경 아님
- 기존 `wpa_supplicant.conf`와 NM 모두 `scan_ssid=1`을 쓰고 있었으므로 숨김은 원래 처리되던 사항
- `hidden=yes`는 숨김 AP에 더 정확한 설정이므로 유지

### 현재 상태
- wlan0 DOWN, 스캔 EINVAL 고착. keeper 정지 상태
- 유선 10.6.6.6만 접속 가능

### 다음
- 재부팅으로 카드 초기화 + 오늘 변경분(netplan 정적 IP, hidden, powersave off) 부팅 경로 검증
- 붙으면 keeper v3(NM용) 작성해 재적용
- 안 붙으면 카드/펌웨어 레벨 조사 (리셋 후 수 분 내 재고착이 새 증상)

## 18:47 재부팅 후 — 정상 복귀, 조치 전부 유효

| 항목 | 결과 |
|---|---|
| 연결 | `wlan0 connected TP_Link_AMR_5G`, −40 dBm, ch11 |
| IP | **192.168.101.61 하나** (이전엔 NM DHCP의 .114가 추가로 붙었음) |
| 기본경로 | **`proto static` 하나** (이전엔 DHCP 경로 중복) |
| power_save | **off** — NM 전역값이 2라 재활성화되지 않음 |
| wpa_supplicant | `-u -s -O /run/wpa_supplicant` (NM 전용 백엔드) |

→ 결함 1·2·3 해소 확인. 남은 것은 결함 4(keeper)뿐이었음

## keeper v3 작성 — NetworkManager 대응

- `nm_owns_iface()` 추가: `wpa_supplicant` 유닛의 ExecStart에 `-i $IFACE`가 있는지로 판정.
  런타임 장치 상태가 아니라 **유닛 정의**로 판정하는 이유는, 먹통 중 NM이 장치를
  `unavailable`로 보고하므로 런타임 탐지는 정작 필요한 순간에 잘못된 경로를 타기 때문
- escalation 이원화
  | 단계 | NM 경로 (신규) | 레거시 경로 (유지) |
  |---|---|---|
  | 0 | `nmcli device connect` | `wpa_cli reconnect` |
  | 1 | `nmcli device disconnect` + `connect` | `systemctl restart wpa_supplicant` |
  | 2 | `nmcli radio wifi off/on` | `ip link down/up` |
  | 3 | PCIe remove+rescan | PCIe remove+rescan |
- `ip link down/up`을 NM 경로에서 뺀 이유: NM의 스캔 결과를 날려버려
  이후 활성화가 `ssid-not-found`로 실패함 (18:43 실측)
- `pci_reset()` 말미에 NM이면 `nmcli device connect`로 명시적 재활성화 추가
  (NM은 인터페이스 재등록은 하지만 프로파일 자동 활성화는 신뢰할 수 없음)
- 진단 스냅샷에 NM 상태/로그 추가, 시작 시 어느 경로인지 로그로 남김
- `.62`는 아직 레거시 구성이므로 같은 스크립트가 양쪽 모두 커버함
- 검증: `bash -n` 통과, .61에서 판정 로직 실측 → `NetworkManager-managed` 정상

### 정정 — AP는 원래 숨김 SSID였음
- 17:05에 `hana-stk2-cctv`가 스캔에 보인 건 당시 결합 중이라 SSID가 채워진 것.
  AP 설정 변경 없음. `hidden=yes`는 숨김 AP에 더 정확하므로 유지

### 다음
- `./scripts/setup-wifi-keeper-over-ssh.sh ucore@192.168.101.61` 실행해 v3 배포
- `.62` 접속 복구되면 동일 실행 (레거시 경로로 동작)
- NM 일원화 4단계는 `.61`에만 수동 적용됨 → 재현 가능한 스크립트로 정리 필요
- AP측: ch11 동일채널 3개. 같은 AP의 5GHz(ch48, BSSID …:82) 활용 검토

## 22:44 야간 확인

### 10.6.6.6은 현재 로봇이 아님
```
ip route get 10.6.6.6 → via 192.168.1.1 dev enx00e04c681ec9 src 192.168.1.100
ping 84~155ms, port 22 refused
```
- 개발서버에 10.6.6.x 인터페이스 없음. USB 이더넷(192.168.1.100) 쪽으로 빠져나가는
  전혀 다른 호스트임. 유선 직결 경로가 지금은 없음
- `.61`은 무선 192.168.101.61로 정상 접속됨 (ARP MAC 28:0c:50:6c:6b:0a = wlan0)

### .61 — 현재 정상, 단 야간에 3회 끊김
- 22:43:07 자력 복구, 현재 −42 dBm 연결 중
- 이번 부팅(18:47) 이후 `link lost` **3회**
```
21:44:36 lost → 21:47:43 recovered   (PCIe 리셋 1회, 3분 07초)
22:35:46 lost → 22:43:07 recovered   (PCIe 리셋 3회, 7분 21초)
```
- **끊김 사유 전부 `reason=4`** (비콘 상실, kernel `Connection to AP ... lost`)
- **power save는 off 상태에서 발생** → 결함 3 수정은 옳았으나 유발 원인은 아니었음.
  유발은 순수하게 AP/RF 쪽
- keeper는 아직 **v2**(미배포). NM 구성에서 헛도는 단계 때문에 복구가 7분까지 늘어남.
  v3 배포하면 단축될 것

### .62 — 종일 무응답
- ping 100% 손실, ARP FAILED. 오늘 12:19부터 계속 접속 불가

### 다음
- `./scripts/setup-wifi-keeper-over-ssh.sh ucore@192.168.101.61` 로 v3 배포
- .62 전원/위치 확인
- AP: reason=4 반복이 확정적. ch11 → 같은 AP의 5GHz(ch48) 또는 HMK 계열(ch100) 이전 검토
