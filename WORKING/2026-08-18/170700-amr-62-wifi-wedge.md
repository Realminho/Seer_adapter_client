# amr-62-wifi-wedge

### 목표
- 2호기(HN-SH6-TR-002, 192.168.101.62)의 WiFi 고착 확인 및 복구

### 지금
- 유선 10.6.6.6 경유로 먹통 상태 캡처 완료. keeper v2 설치로 자동복구 검증 대기

### 완료
- **신원 확인**: 10.6.6.6 = `HN-SH6-TR-002`, wlan0 MAC `28:0c:50:6b:94:4b` = .62
  (오전 같은 주소는 .61 `28:0c:50:6c:6b:0a` 였음 — 케이블 이동)
- **주의**: 두 로봇 모두 eth01이 `10.6.6.6/24`로 동일 설정. 유선 주소만으로는 구분 불가
- 고착 경위 (부팅 12:17:30):
  ```
  t=565     wlan0 associated → link becomes ready      (12:26:55경)
  t=16813   wlan0: Connection to AP ... lost           (16:57:43경)
            ← 이후 커널 이벤트 0건, wlan0 DOWN/NO-CARRIER
  ```
- .61과 다른 점: 이번 유발은 `Connection to AP lost`(비콘 상실 계열),
  .61은 EAPOL 4-way 타임아웃이었음. **고착 양상은 동일**
- wlan0 존재, PCI 0000:01:00.0 존재, 펌웨어 API 59 폴백 — 카드는 살아 있음
- keeper **v1**이 `restarting wpa_supplicant` ↔ `bouncing wlan0` 무한 반복, 복구 실패
  (.61에서 PCIe 리셋으로 13초 만에 풀린 그 상태)

### 다음
- `./scripts/setup-wifi-keeper-over-ssh.sh ucore@10.6.6.6` 실행
  → v2 설치 + keeper 재시작. 약 160초 뒤 PCIe 리셋이 자동 발동하는지 관찰
- eth01 주소를 로봇별로 분리(10.6.6.61 / 10.6.6.62) 검토 — 오조작 위험

### 검증
- MAC/robots.hcl로 신원 대조, dmesg 타임스탬프로 고착 시점 특정

## 자동 복구 실증 완료 (2026-08-18 18:06, 2호기)

keeper v2 설치 후 사람 개입 없이 복구됨:
```
18:03:20  link lost + /var/log/amr-wifi-diag.log 스냅샷 기록
18:04:01  wpa_cli reconnect        → 무효
18:04:41  wpa_supplicant 재시작     → 무효
18:05:21  링크 bounce              → 무효
18:06:03  PCIe remove+rescan on 0000:01:00.0
18:06:08  power save off → Connected, -41 dBm
```
- 앞 3단계는 전부 무효, **PCIe 리셋만이 고착을 품**. 오전 1호기 수동 검증과 동일 결과
- 이제 이 종류 먹통은 약 160초 안에 자동 복구되며 전원 차단 불필요

### 설치기 결함 2건 (발견·수정·커밋 완료)
1. `ssh -t ... bash -s` + heredoc → stdin이 파이프라 pty 미할당,
   sudo가 "a terminal is required" 로 실패 → 원격 스크립트를 파일로 올린 뒤 tty로 실행 (6db9d6b)
2. `set -e` + `pipefail` 하에서 SystemMaxUse grep이 no-match면 **에러 없이 즉사**.
   .62처럼 상한 미설정인 장비에서만 발동해, keeper 스크립트만 v2로 바뀌고
   서비스 재시작을 포함한 이후 단계가 전부 누락됨 → `|| true` 로 방어 (a55e31f)

### 적용 현황
- **.62(2호기)**: keeper v2, wpa 드롭인 3s, 환경 드롭인(PCI 0000:01:00.0), 진단로그 동작.
  저널 상한은 원래 넉넉해 의도대로 미변경
- **.61(1호기)**: 여전히 keeper v1. 설치기 실행 필요

### 남은 근본 원인 (로봇 밖)
- 유발은 AP 쪽: EAPOL 4-way 타임아웃(.61), `Connection to AP lost`(.62),
  `status=30` 결합 임시거부. 두 대가 CCTV AP 1대(hana-stk2-cctv, 2.4GHz ch11) 공유
- keeper는 증상 자동복구일 뿐 — 로봇 전용 AP 분리(가능하면 5GHz) 검토 필요
