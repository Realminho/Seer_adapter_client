# amr-61-pc-power-loss

### 목표
- .61 온보드 PC가 실제로 전원이 끊긴 것인지 확인하고 원인 위치 좁히기

### 지금
- 192.168.101.61 경유 부팅 이력·저널 분석 완료

### 완료 — PC 전원 차단 확정
- 최근 3개 부팅 모두 **정상 종료 흔적 0건**
  (`Reached target Shutdown|Stopping ... Service|systemd-shutdown` 매칭 각 0)
- 저널이 평상시 로그 도중에 그대로 끊김 → 종료 절차 없이 전원이 나간 것
- `last -x`에도 `shutdown system down` 기록 없음. 8/19 18:47 항목 하나뿐인데
  그건 세션 중 실행한 `sudo reboot`
```
boot -3  08-19 18:47:42 ~ 08-20 00:32:12   → 이후 9시간 정지 (09:39:37 복귀)
boot -2  08-20 09:39:37 ~ 12:52:07         → 48초 후 복귀
boot -1  08-20 12:52:55 ~ 13:10:47         → 20초 후 복귀
boot  0  08-20 13:11:07 ~ 현재
```

### 완료 — OS 원인 배제
- `shutdown|poweroff|halt` 매칭 3건은 전부 systemd 유닛 이름(무해):
  `Update UTMP about System Boot/Shutdown`, `Unattended Upgrades Shutdown`
- 커널 패닉·OOM 없음. 온도 정상(zone0 44.4°C, zone1 40.6°C, zone2 45.0°C)
- 배터리 정상: 차단 직전 **99%, 53.48V** / boot -2는 **97%**  → 방전 아님

### 완료 — 충전 회로와의 상관관계 (핵심 단서)
- **3회 중 2회가 충전 중 또는 충전 릴레이 조작 직후 발생**
  - boot -3: 차단 직전까지 `mode=ModeCharge status=charging`, 충전소 `1_01CH`
  - boot -2: 차단 시점 `mode=ModeCharge status=charging`, battery=97
  - boot -1: 충전 활동 없음 (사용자 재부팅 구간으로 추정)
- boot -3 차단 직전 22초 사슬:
```
00:31:45.791  VDA5050 cancelOrder 수신 (headerId=1787153346072)
00:31:45.810  VDA5050 cancelOrder 수신 (headerId=4)          ← 발신 2건 연속
00:31:50.806  [CHARGE CIRCUIT] open-relay publish 타임아웃 5초
              rostopic pub -1 /jcmd jarvis_msgs/Cmd '{cmd: 3, ...}'
00:31:50.806  [CHARGE IN PLACE] hold released
00:31:50.808  [CANCEL ORDER] stop_motion sent
00:32:11      마지막 정상 텔레메트리 (99%, 53.48V, -1.2A, mode=Stop)
00:32:12      ★ 전원 차단
```
- 즉 충전 중 관제에서 cancelOrder → 어댑터가 충전 릴레이 open 시도 → ROS publish 타임아웃
  → 22초 뒤 로봇 전체 전원 상실

### 주의 — 과잉 해석 금지
- `battery_voltage=None`은 차단 전조가 아님. boot -2는 **부팅 직후 09:41:05부터** None이었고
  현재 부팅(0)도 None. 일부 부팅에서 BMS 텔레메트리가 안 올라오는 별개 현상
- open-relay publish가 타임아웃했으므로 릴레이가 실제로 동작했는지는 로그로 확정 불가

### 다음
- 다음 차단 때 충전 상태였는지 즉시 확인 (`mode=ModeCharge` 여부)로 상관관계 검증
- 충전 도크 접점/컨택터, 도크→PC 전원 경로 육안 점검 (2호기 8/10 건과 동일 증상 가능성)
- `[CHARGE CIRCUIT] open-relay`가 5초 타임아웃하는 원인 조사
  (ROS master 응답 지연? `rostopic pub -1`이 latch 3초 대기 후 죽는 구조)
- 무관하지만 미해결: keeper는 아직 **v2**, 배포 필요

### 검증
- `journalctl -b -N | grep -c "Reached target Shutdown|..."` = 0 (3개 부팅 전부)
- `last -x`에 `crash` 표기 확인 (8/19 18:48 세션이 crash로 종료)

## 1·2·3순위 작업 착수 (사용자 지시)

### 1순위 — 전원 차단
**코드 결함 수정 (`adaptor/utils/charge_circuit.py`)**
- 로그 `publishing and latching message for 3.0 seconds`로 **릴레이 open은 실제 발행됨**이 확인됨.
  `bash -lc` + `source setup.bash` + 노드 등록 위에 latch 3초가 얹혀 5초 예산을 1.5초 초과한 것
- `off_timeout_sec` 기본값 **5.0 → 15.0** (`config.py`)
- 타임아웃 시 프로세스를 **kill** 하도록 수정. 기존에는 `wait(timeout)`이 예외를 던진 뒤
  프로세스를 방치해 `/jcmd`에 계속 관여하는 고아가 남았음
- 타임아웃을 일반 실패와 구분해 로그: `relay state unknown` (실제로 열렸을 수 있으므로)
- 테스트 3건 추가 → `python3 -m unittest tests.test_charge_circuit` **8건 전부 통과**
  (`test_charge_in_place`, `test_charge_circuit_config`는 `paho`/`hcl2` 미설치로 임포트 실패.
   이번 변경과 무관한 기존 환경 문제)

**증거 보존 (`scripts/systemd/journald-sync-fast.conf`)**
- journald 기본 `SyncIntervalSec`은 **5분**. 급전원차단 시 마지막 수 분이 통째로 날아갈 수 있음.
  8/20 건은 마지막 초까지 남아 있었으나 그건 운이었음 → `SyncIntervalSec=1s`로 보장
- 설치기가 **항상** 적용하도록 편입 (저널 상한과 달리 조건부 아님)
- 조사 결과 PC에 전압 레일 sysfs가 없음(`power_supply`는 USB-C PD 노드뿐,
  hwmon은 cpu/gpu 온도와 iwlwifi뿐) → 별도 1초 샘플러는 어댑터 로그와 중복이라 만들지 않음

### 2순위 — wifi
**`scripts/unify-wlan0-networkmanager.sh` 신규**
- 8/19에 .61에 손으로 넣은 NM 일원화를 재현 가능한 스크립트로 정리
- 적용 항목: wpa_supplicant 유닛에서 `-c/-i` 제거(NM 전용 백엔드화),
  `wifi.powersave 3 → 2`, 파싱 실패 키파일 격리, `--hidden-ap`로 숨김 SSID 능동탐색
- **기본은 미적용(write-only)**. `--apply`는 wpa/NM을 재시작해 링크를 끊으므로
  유선/콘솔 경로에서만 쓰도록 옵션으로 분리 (keeper 설치기와 같은 원칙)
- IP 주소 체계는 **보고만 하고 바꾸지 않음**. netplan 정적 + NM DHCP 이중화는
  플릿 주소 정책 결정 사항이라 임의로 정하지 않음. 기본경로 2개면 경고 출력
- .61에서 멱등성 실측: ExecStart·powersave 모두 `left alone` 판정 확인

### 3순위 — 정리
- 깨진 `*.nmconnection` 격리는 위 스크립트에 포함(루트 전용 디렉터리라 `sudo find`로 열거)

### 다음 — 사용자 실행 필요
```bash
./scripts/setup-wifi-keeper-over-ssh.sh ucore@192.168.101.61   # keeper v3 + 저널 sync
```
- 하드웨어: 충전 도크 접점, **배터리 메인 컨택터/커넥터** 점검
- 관제: 8/20 00:31:45 cancelOrder 2건 발신 주체 확인 (하나는 `headerId=4`)
- HMK enterprise 망 **IP 대역** 회신 → netplan 포함 5GHz 이전 설계
- netplan `ethernets:` 중복 키와 `/etc/netplan` 백업 5개 정리 (미착수)
