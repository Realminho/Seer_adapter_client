# pio-station-channel-task3

### 목표
- PioConfig에서 station_id/channel 필드 삭제, HCL 3벌에서 해당 줄 삭제, 이관 가드 추가

### 지금
- 완료. 커밋 2b47f77.

### 완료
- config.py: PioConfig.station_id/channel 삭제, _MOVED_PIO_KEYS 가드 추가
- 3개 HCL 파일에서 station_id/channel 줄 삭제, adaptor/config/extensions.hcl 블록 코멘트 재작성
- test_config.py: TestMovedPioStationAndChannelKeys 테스트 추가/통과
- test_pio_output_mapping.py: station_id=/channel= kwargs 제거
- test_pio_select_timing.py: PioConfig(...)를 쓰지 않고 별개의 link dict라 수정 불필요 확인(브리핑 기술과 실제 코드 불일치 확인됨)
- RED 재현(가드 임시 제거 → TypeError, "airshower" 없음) 후 복원해 GREEN 확인
- 대상 4개 테스트 파일 92 passed, 전체 스위트 1951 passed / 7 failed(기존 실패와 동일)
- 커밋 2b47f77

### 다음
- (완료, 후속 작업 없음)

### 검증
- tests/test_config.py -k moved_pio: RED(TypeError, airshower 없음) → GREEN(가드 복원 후 통과)
- tests/test_config.py tests/test_pio_output_mapping.py tests/test_pio_select_timing.py tests/test_extensions_config.py: 92 passed
- 전체 스위트: 1951 passed, 7 failed(기존 pio_init/pio_ping ×6 + goto_nearest_node timeout, task 무관)
