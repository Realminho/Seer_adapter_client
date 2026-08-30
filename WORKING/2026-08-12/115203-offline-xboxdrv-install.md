# offline-xboxdrv-install

### 목표
- 인터넷이 없는 원격 로봇에 `xboxdrv`를 설치하는 기능 추가

### 지금
- 구현·리뷰 완료. AMR 실기 검증만 남음

### 완료
- 커밋 `1912e4a`: 기존 미커밋 joystick bridge 작업 정리 (계획이 이 위에 얹혀 있었음)
- 커밋 `897bee1`: spec + plan
- 커밋 `3d4e39f`: `install_bundled_offline_debs` 공용 헬퍼 추출 (카메라 동작 무변경)
- 커밋 `f5e83e9`: `scripts/fetch-offline-debs.sh` + `scripts/offline-debs/xboxdrv/` deb 3개 (523 KB)
- 커밋 `d0577a5`: xpad 게이트를 앞으로, 번들 설치, 바이너리 재확인
- 커밋 `c56a12a`: **dpkg 종료 상태로 게이트**. `dpkg -i`는 의존성 실패 시에도 언팩은 하므로
  `command -v xboxdrv`가 참이 되어 crash-loop 유닛이 활성화되던 결함 수정
- 커밋 `3544774`, `20acd0f`: 테스트 고정 + 모순되던 문서 정정

### 다음
- AMR 실기 검증 (아직 안 함):
  1. `scripts/update-jibot-adapter-over-ssh.sh ucore@<host>` → 로봇에서
     `ls ~/adaptor/scripts/offline-debs/xboxdrv/` 로 deb 3개 확인
  2. `scripts/setup-adaptor-service.sh --jibot --dry-run` → 게이트 순서와 `[dry-run]` 줄 확인
  3. `scripts/setup-adaptor-service.sh --jibot` → 그다음 반드시
     `dpkg-query -W -f='${Status}\n' xboxdrv` 가 `install ok installed` 인지 확인.
     `command -v xboxdrv` 만으로는 언팩만 된 상태와 구분되지 않는다
  4. controller 수면/기상 사이클로 bridge 복구 확인
- 미해결(범위 밖, 별도 세션): `install_amr_camera_service`의 설치 후 확인
  `amr_camera_ros_package_available`(`rospack find`)도 언팩만 된 상태를 통과시킬 수 있음.
  카메라 경로에 같은 결함이 있는지 확인 필요. 사전 검사 `dpkg_package_installed`는
  `install ok installed`를 보므로 정상

### 검증
- `./scripts/run-tests.sh --python /ssd2/workspaces/unified-amr-adaptor/adaptor/.venv/bin/python -o addopts="" ../tests`
  → 146 passed, 2 failed. 두 실패는 HEAD에서도 실패하는 기존 건이며 무관:
  `test_run_adapter_dispatches_multi_robot_fleet_to_run_multi`,
  `test_update_script_restarts_after_upload_with_tty`
- deb 3개 SHA256 검증 통과, `dpkg-deb -f`로 arm64/버전 확인
- Task 1 리팩터: setup `--dry-run` 출력 before/after diff 동일
- Task 3 게이트: xpad × 바이너리 × dry-run × `--with-xboxdrv` 16조합 추적
- 수정 검증: 스텁 하네스 6케이스 실행. 수정 전 HEAD에서 재현되던 버그가 수정 후 사라짐
- SDD 기록(리뷰·하네스 결과 원문): `.superpowers/sdd/2026-08-12-offline-xboxdrv-install/`
