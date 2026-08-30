# deploy-final-fixes

### 목표
- 배포 호환성 최종 검토의 sudoers, Hexplorer preflight, 문서 정확성 문제를 TDD로 수정하고 검증 후 커밋한다.

### 지금
- 지정 커밋을 생성했고 최종 상태와 커밋 범위를 확인한다.

### 완료
- 세 문제의 실패 계약을 먼저 확인하고 정확한 유닛 복사, SSH 전 helper 검증, 문서 표현을 수정했다. 보안/순서/기존 별칭 호환성을 검토하고 8d69e4c로 커밋했다.

### 다음
- 부모 에이전트에 RED/GREEN, 커밋 SHA, 파일, test_fleet.py 경로 문제를 보고한다.

### 검증
- 신규 계약 5개 및 배포 테스트 53개 통과, shell smoke 3종 통과, adaptor 94개 통과(샌드박스 밖 UDP 허용), dry-run/bash -n/diff-check 통과. 지정 test_fleet.py는 없어 test_fleet_registry.py로 검증했다.
