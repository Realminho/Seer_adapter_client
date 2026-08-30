# amr-61-wifi-drop

### 목표
- 192.168.101.61(amr1)에서 WiFi가 끊기는 문제 확인

### 지금
- 8/15 14:37 .61에 조치 적용·검증 완료. 다음 재부팅에서 경합 해소 여부 판정 대기

### 적용 결과 (2026-08-15 14:37, 원격 검증됨)
- `RestartUSec` 100ms → **3s**, `StartLimitIntervalUSec` 10s → **0**, ExecStartPre wlan0 대기 루프 적용
- `amr-wifi-keeper.service` enabled+active, 로그에 `power save off on wlan0`
- `Power save` on → **off**
- `bgscan` `simple:5:-10:300` → **`simple:30:-70:300`** (다음 wpa 기동부터 반영)
- 백업 `/etc/wpa_supplicant/wpa_supplicant.conf.bak.20260815-143744`
- 적용 중 링크 무중단 유지 (-42dBm, uptime 1h25m)

### 판정 결과 — 해결 확인 (2026-08-15 16:01 부팅)
- 조치 후 첫 부팅(16:01:38)에서 **wpa_supplicant 재시작 0회** (이전엔 매 부팅 예외 없이 5회)
- `Could not read interface wlan0 flags: No such device` **0건**
- 기동 순서: 16:01:42 Starting → (ExecStartPre가 wlan0 대기) → 16:01:44 초기화 성공
  → 16:01:48 Associated/CONNECTED. SCAN-FAILED 0건
- keeper 동작 확인: 16:01:45 기동(결합 전이라 link lost 보고) → 16:02:05 link recovered,
  결합 후 power_save 재적용. 결합 시 절전이 되살아나는 것을 막음
- `CTRL-EVENT-SIGNAL-CHANGE above=1` (-42~-43dBm) → 새 bgscan 임계값 -70 정상 동작, 장주기 진입
- 주의: `journalctl -b 0 | grep -c "Scheduled restart"`(유닛 필터 없음)는 8이 나오지만
  전부 `jfservice.service`이며 wpa_supplicant와 무관. 반드시 `-u wpa_supplicant`로 세야 함

### 다음
- 며칠 관찰 후 `scripts/`에 keeper + 드롭인 편입 검토 (62번 복귀 시에도 동일 조치 필요)

### 완료
- .61 상태: 8/13 15:52:55 부팅, 신호 -47dBm, 현재 정상 결합
- .62와 **동일 설정**: `bgscan="simple:5:-10:300"`, `Power save: on`
- **부팅 경합이 실제 원인**: 매 부팅마다 iwlwifi가 wlan0을 만들기 전에
  wpa_supplicant가 기동해 `Could not read interface wlan0 flags: No such device`로 실패.
  `RestartSec` 기본 100ms라 1초 만에 재시작 5회를 소진
  | 부팅 | wpa재시작 | 결합성공 | SCAN-FAILED |
  |---|---|---|---|
  | 0 | 5 | 1 | 0 |
  | **-1** | 5 | **0** | **191** |
  | -2 | 5 | 1 | 0 |
  | -3 | 5 | 1 | 0 |
  | -4 | 5 | 1 | 0 |
- 부팅 -1(14:46:46~15:52:43)은 경합에서 회복 못 해 **1시간 6분간 WiFi 전무**.
  커널은 14:46:52에 authenticate 시도했으나 이후 `SCAN-FAILED ret=-22`만 반복, CONNECTED 0건
- 모든 부팅이 정상 종료 기록 없음(전원 차단) — .62와 동일
- 부팅 -4(30초), -3(35초)처럼 극단적으로 짧은 부팅 존재

### 완료 — 8/10 .62 결론 정정
- `bgscan` 임계값 -10dBm은 스캔 **빈도**(영구 5초 단주기)를 설명하지만
  `SCAN-FAILED ret=-22` **실패 자체**의 원인은 아님
- .61은 같은 bgscan인데 정상 부팅에선 SCAN-FAILED 0건 → bgscan 단독 원인 아님
- 공통 결함: 양쪽 다 AX210이 API 59 폴백(`api flags index 2 larger than supported by driver`),
  그리고 **한 번 스캔이 EINVAL 상태에 빠지면 스스로 절대 못 벗어남**.
  거기 빠뜨리는 계기가 .62는 절단 후 재접속, .61은 부팅 경합으로 다를 뿐

### 다음
- .61에 keeper + wpa 드롭인 설치 (sudo 필요). `~/wifi-fix/`에 준비 완료, **8/15 기준 여전히 미적용**

### 8/15 재현 확인 (조치 미적용 상태)
- 적용 상태 점검: `RestartUSec=100ms`, `StartLimitIntervalUSec=10s`, keeper `inactive`,
  `Power save: on`, `bgscan="simple:5:-10:300"` → 아무것도 반영 안 됨
- 부팅별 실태 (정상종료=0, 즉 전부 전원 차단):
  | 부팅 | wpa재시작 | 결합 | 절단 | SCAN-FAILED |
  |---|---|---|---|---|
  | 0 (13:12~) | 5 | 1 | 0 | 0 |
  | **-1 (13:06:22~13:11:54)** | 5 | **0** | 1 | **124** |
  | -2 (11:02~13:05) | 5 | 1 | 0 | 0 |
- 부팅 -1이 8/13 부팅 -1과 동일한 실패: 경합에서 회복 못 해 결합 0건, 5분 30초 만에 전원 차단
- 준비 파일은 `~/wifi-fix/`에 그대로 잔존(8/13 20:31), `/tmp`가 아니라 재부팅에도 안전
- 확정 시 keeper/드롭인을 repo `scripts/`에 넣어 전 로봇 배포 대상화 검토

### 범위
- **.62는 다른 곳으로 이동해 이번 조사 대상에서 제외** (8/13)

### 수정 이력
- 드롭인 `ExecStartPre` 대기 루프에 `$i`/`$((i+1))` 사용 → systemd가 Exec 줄의 `$`를
  변수 확장으로 먹어 동작 불가. `$` 없는 `for i in 1..30` 형태로 교체 후 재업로드

### 검증
- `journalctl -b -N -u wpa_supplicant` 원문, 부팅별 재시작/결합/SCAN-FAILED 교차 집계
