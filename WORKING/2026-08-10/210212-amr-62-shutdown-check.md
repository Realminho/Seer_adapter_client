# amr-62-shutdown-check

### 목표
- 192.168.101.62 AMR이 종종 꺼지는 원인 확인

### 지금
- 조치 적용 완료. 재발 여부 관찰 중 (다음 단절까지 확정 불가)

### 완료 — 확정된 사슬
- 사용자가 보는 "꺼짐"은 전원 고장이 아니라 **네트워크 소실 → 사람이 전원 차단**이었음
- 부팅별 재현(끊긴 뒤 CONNECTED 이벤트 0건):
  | 부팅 | 접속 | 끊김(reason=4) | 사망 | 오프라인 유지 |
  |---|---|---|---|---|
  | -5 | 10:15:19 | 11:37:36 | 11:49:41 | 12분 05초 |
  | -4 | 11:50:22 | 14:53:59 | 15:13:18 | 19분 19초 |
  | -3 | 15:13:52 | 20:31:15 | 20:40:11 | 8분 56초 |
  | -2 | 20:40:40 | 20:54:25 | 20:56:07 | 1분 42초 |
- 교차검증: 부팅 -4는 14:53:59 끊김 → 14:55:21부터 `[MQTT CONNECT FAILED]` 10초마다 사망까지
- 30개 부팅 중 29개가 종료 절차 없이 끊김. 유일한 예외인 21:27 이벤트는 정상 `reboot`로
  전 과정이 로그에 남아, 저널이 OS 종료를 반드시 기록함을 반증으로 확인

### 완료 — 실제 원인 (가설 정정 포함)
- **폐기된 가설**: `cfg80211: failed to load regulatory.db` → `country 00` NO-IR → 스캔 EINVAL.
  `iw reg get` 결과 **phy#0은 self-managed, country US**이고 채널 11(2462)에 NO-IR 없음.
  regulatory.db 실패는 이 카드와 무관했고 `country=KR`도 self-managed phy에는 무효
- **실제 원인**: `bgscan="simple:5:-10:300"` — 신호 임계값 **-10 dBm**이 비현실적(실측 -38 dBm).
  항상 "약전계"로 판정돼 5초 공격 스캔 모드에 영구 고정 → 스캔이 EINVAL로 실패하며
  `CTRL-EVENT-SCAN-FAILED ret=-22`가 초당 1건 (53시간 부팅 190,302건)
  → 임계값 -70으로 수정 후 `CTRL-EVENT-SIGNAL-CHANGE above=1` 발생, 장주기(300초) 진입,
  **SCAN-FAILED 0건**
- 끊김 자체의 방아쇠는 `Power save: on` → `reason=4 DISASSOC_DUE_TO_INACTIVITY`. 현재 off
- 부수 결함: 부팅 시 `wpa_supplicant.service`가 iwlwifi의 wlan0 생성 전에 기동해 5회 연속 실패,
  `Start request repeated too quickly`로 systemd가 포기(22:30:29). 13초 뒤 재기동돼 살아났으나
  아무도 재기동 안 했으면 부팅 후 WiFi 없음. RestartSec 기본 100ms가 원인

### 완료 — 배제된 것
- 하드웨어 전원 고장: 사망 순간 로그 1초 간격 정상, stall 0, iowait 0, D-state 0,
  배터리 53.0~53.25V/SoC 82~99% 평탄, 42.8°C, OOM/panic 0건
- OS에서 전원 끌 경로 없음: adc-keys에 KEY_POWER 없음, gpio-keys probe 실패, PMIC pwrkey 없음
- 과부하: load 6~8이나 idle 36~44%, 하트비트 drift 0.001s → stall 아님
- `iwlwifi-...pnvm` 부재: 드라이버가 요청한 적 없음(dmesg 0건). 결함 아님

### 완료 — 적용된 조치
- `/etc/wpa_supplicant/wpa_supplicant.conf`: bgscan 임계값 -10 → -70, `country=KR` 추가(무효지만 무해)
- `amr-wifi-keeper.service` 설치·활성: 부팅 시 power_save off, 링크 상실 시
  wpa_cli reconnect → wpa_supplicant 재시작 → 인터페이스 bounce 로 자동 복구
- 사고: 4단계 `systemctl restart wpa_supplicant` 실행 시 22:07~22:30 접속 상실.
  2단계 검증 결과를 확인하지 않고 진행하도록 안내한 것이 원인. 현장 재부팅으로 복구

### 다음
- 다음 단절이 발생하는지, 발생 시 자동 복구되는지 관찰 (ping 측정기 + keeper 로그)
- 무위험 개선 제안: `wpa_supplicant.service` drop-in으로 `StartLimitIntervalSec=0`, `RestartSec=3`

### 검증
- `journalctl -b -N` 원문, MQTT 타임라인 대조, `iw reg get`(self-managed 확인),
  조치 전후 SCAN-FAILED 건수 대조(초당 1건 → 0건)

### 되돌리기
- 로봇: `sudo systemctl disable --now amr-wifi-keeper`;
  `sudo rm /etc/systemd/system/amr-wifi-keeper.service /usr/local/sbin/amr-wifi-keeper.sh`;
  `sudo cp /etc/wpa_supplicant/wpa_supplicant.conf.bak.* ...`
- 진단용: `crontab -r`; `pkill -f "python3 /home/ucore/heartbeat"`;
  `rm ~/heartbeat-diag.py ~/heartbeat-diag.log*`
