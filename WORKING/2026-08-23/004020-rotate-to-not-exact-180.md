# rotate-to-not-exact-180

### 목표
- rotateTo 가 딱 180도로 서지 않는 원인 규명

### 지금
- 수정 구현 완료. 전체 스위트 확인 중

### 완료
- 62(HN-SH6-TR-002) 00:39:37 실기 로그로 재현 확인. 목표 270deg(=-90), 시작 89deg → 정확히 180도 회전 요구
- 회전 중 표본 -91deg 한 개를 보고 오차 1.0deg 로 판정, 즉시 종료 후 UmStop
- 정지 후 실제 안착값은 **-83deg** (연속 9표본 동일). 즉 172도만 돌아 8도 모자람
- heading 텔레메트리는 정수 도 단위이고 이상표본이 섞인다: 00:40:31 정지 상태에서
  -83 연속 중 -90 표본 1개(같은 시각 x 도 16303 -> 16330, 27mm 튐)
- 즉 루프가 **이상표본 1개에 걸려 조기 종료**했고, 정지 후 재확인 경로가 없어 아무도 못 잡음
- _rotate_to_with_jog 의 finally 는 UmStop 만 보내고 heading 재확인 없음

### 수정 (TDD)
- 실패 테스트 2건 선작성 → 둘 다 실기 증상대로 실패 확인 후 구현
  - test_rotate_to_jog_does_not_finish_on_a_single_stray_sample
  - test_rotate_to_jog_rechecks_the_heading_after_it_stops
- _rotate_to_with_jog 를 (한 차례 조그) + (안착 확인/재보정) 으로 분리
  - _rotate_to_jog_pass: 허용치 안 표본을 confirm_samples 번 연속으로 봐야 도달.
    확인 표본 받는 동안에는 밀지 않음(밀면 확인 사이에 지나침)
  - _rotate_to_settled_error: UmStop 뒤 settle_sec 대기 → pose 재조회 → 오차 반환
  - 벗어나 있으면 settle_retries 번까지 재보정. 전체 timeout 안에서만
- config: rotate_to_confirm_samples=2, rotate_to_settle_sec=0.6, rotate_to_settle_retries=2

### 다음
- 실기 재검증 필요(62에서 89deg -> 270deg 재현). 미배포 상태
- 더 조이려면 rotate_to_tolerance_deg 를 1.0 으로. 단 정수 텔레메트리 + 지터 때문에
  현실적 하한은 +-1~2deg 이고 0deg 는 불가

### 검증
- journalctl -u amr-adaptor(62) 00:39:20~00:40:30 heading 궤적: 89 -> -58 -> -91(판정) -> -83(안착, 9표본)
