# fleet-hcl-conversion

### 목표
- Task 2: fleet.py를 HCL 파서로 전환
- 공개 API 시그니처 유지 (load_fleet, find_robot, robot_overrides, robot_ids)
- TDD 접근: 테스트 작성 -> 실패 확인 -> 구현 -> 통과 확인 -> 커밋

### 지금
- 브리프 읽음
- 현재 파일 상태 점검 중

### 완료
- Step 1: 테스트 코드 작성 (FLEET_TOML → FLEET_HCL, 픽스처 교체, 새 테스트 클래스 2개 추가)
- Step 2: 테스트 실행 (실패 확인) - 11개 실패, 8개 통과 (예상대로)
- Step 3: fleet.py 구현 (HCL 파서로 전환, resolve_robot_path 추가)
- Step 4: 테스트 실행 (통과 확인) - 19개 전부 통과
- Step 5: 커밋 (commit 9f8ca54)

### 다음
- 완료

### 검증
- 모든 17개 테스트 통과 (중복 제거 후)
- 코드 리뷰 finding 반영 완료
- 공개 API 시그니처 유지 확인
- 파일 명시적 스테이징 및 커밋 완료
