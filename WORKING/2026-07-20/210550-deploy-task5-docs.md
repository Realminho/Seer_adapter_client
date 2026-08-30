# deploy-task5-docs

### 목표
- JIBOT SSH/서비스 운영 문서를 현재 CLI 계약에 맞추고 전체 회귀 검증 후 커밋한다.

### 지금
- 상위 agent에 최종 결과를 보고한다.

### 완료
- 문서 계약 테스트 추가, stale positional remote path로 RED 확인.
- 세 운영 문서의 remote-dir/restart/full-stack 설치 계약 수정, 단일 계약 테스트 GREEN.
- 전체 회귀 검증과 문서 명령 self-review 완료.
- 요청된 네 파일만 commit `4107f97`로 생성.

### 다음
- 없음.

### 검증
- RED: docs contract 1 failed (stale `ucore@192.168.3.222 /home/ucore/adapter`).
- GREEN: docs contract 1 passed.
- SSH deploy pytest 49 passed; adaptor pytest 78 passed; shell suites/dry-runs/bash -n/diff check all exit 0.
