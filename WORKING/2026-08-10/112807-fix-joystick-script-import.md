# fix-joystick-script-import

### 목표
- 조이스틱 테스트 스크립트가 저장소와 AMR 배포 디렉터리 구조 모두에서 utils를 찾게 한다.

### 지금
- 수정과 검증을 완료했다.

### 완료
- source checkout과 평탄화 AMR 배포 구조를 모두 지원하고 임시 우회 명령을 문서화했다.

### 다음
- AMR에서 업데이트 후 `--list`를 다시 실행한다.

### 검증
- py_compile/help/diff-check 및 source/flattened 경로 탐지 테스트 통과
