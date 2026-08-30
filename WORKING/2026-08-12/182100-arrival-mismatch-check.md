# arrival-mismatch-check

(이전 작업: pose goto 오탐 제거 — 수정·테스트 완료, 커밋/62 배포 미완)

### 목표
- `unclamp: motion never started within 1.0s ... (target=-20000 actual=-137 limitsActive=FFLAG_HWNEGALMT)`
  에러의 발생 지점과 해결 방안 확인

### 지금
- 발생 지점·원인 규명 완료. `target=-20000` 출처만 62 오프라인으로 미확인

### 완료 — 발생 지점 (에러 사슬)
1. `adaptor/extensions/clamp/__init__.py:399` `_wait_motion_done()`
   → 1.0s(`clamp_motion_start_timeout_sec`) 안에 `FFLAG_MOTIONING`이 안 서고
   `_confirm_arrival()`도 실패 → RuntimeError
2. 괄호 안 `target=… actual=… limitsActive=…` 는 `_describe_axis()` (같은 파일 291행)
3. `adapter_jibot.py:4179` `ORDER_ACTION_FAILED`(FATAL) 로 감싸 state.errors 에 발행
   → UI 문구 `Order action failed: unclamp failed: …`

### 완료 — 원인
- 축이 **마이너스 하드웨어 리미트에 물려 있음**(`limitsActive=FFLAG_HWNEGALMT`, `actual=-137`)
- 거기서 **-20000(더 마이너스)** 으로 이동 명령 → 드라이브는 패킷만 ACK(communication_status=0)
  하고 이동은 시작 안 함 → MOTIONING 안 섬 → 1초 후 실패
- 즉 어댑터 동작은 정상(실제 거부를 정확히 보고). 잘못된 건 **unclamp 목표값의 부호/크기**
- 저장소 config 에는 -20000 없음 → 62 배포본 `~/adaptor/config/extensions.hcl` 의
  `unclamp_position`/`unclamp_offset` 또는 action 파라미터 `position` 에서 온 값
- 부호 규약이 저장소 안에서 서로 어긋나 있음:
  `origin_encoder_offset` 주석은 open=+/close=- 인데, 실제 설정은 `clamp_position = 35000`(닫힘=+)

### 다음
- 62 복귀 후: `grep -n 'clamp' ~/adaptor/config/extensions.hcl` 로 -20000 출처 확정
- 사용자 확인 필요: 열림/닫힘 실측 엔코더 값 (닫힘 35000 이면 열림은 리미트 근처 0 부근 추정)
- 값 확정되면 `unclamp_position` 명시 → 리미트 스위치에서 살짝 떨어진 값 권장

### 검증
- `ssh ucore@192.168.101.62` → `No route to host` (62 오프라인, 배포본 확인 불가)
- 저장소 전체 grep 에 `-20000` 없음
