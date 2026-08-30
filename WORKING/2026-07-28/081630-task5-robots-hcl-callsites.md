# task5-robots-hcl-callsites

### 목표
- Task 5: 파이썬 호출부 문구 갱신(robots.toml -> robots.hcl) + config 경로를 resolve_robot_path로 교정

### 지금
- 완료. 커밋함 (9c8878d)

### 완료
- Step1 sed 치환, Step2 [[robot]] 잔재 제거(main.py 2곳), Step3 audit 라벨 확인
- Step4 TDD: resolve_robot_path로 main.py 2분기 + web/server.py _validate_robots_on_disk 수정
- Step5 테스트 실행, Step6 커밋(7개 파일만), 리포트 작성

### 다음
- (완료, 후속 작업 없음. Task 6이 TOML 픽스처를 HCL로 교체할 것)

### 검증
- tests/test_adaptor_cli.py::test_relative_config_path_resolves_against_fleet_file PASSED
- test_web_main.py/test_web_server.py: 105 passed, 1 failed(예상, TOML 픽스처)
- test_adaptor_cli.py: 6 passed, 3 failed(예상, TOML 픽스처)
