# hexplorer-restart-tty

### 목표
- Hexplorer restart의 sudo 프롬프트 전달 공백을 최소 변경으로 보완한다.

### 지금
- feature branch를 develop에 로컬 fast-forward 병합하고 삭제했다.

### 완료
- Hexplorer 기본/custom restart가 공통 명령과 TTY/non-TTY 분기를 쓰는 설계를 작성했다.
- 실패 테스트, 최소 구현, 회귀 검증, 커밋 단계가 있는 Task 6 계획을 작성했다.
- Hexplorer 기본/custom restart에 openable TTY `ssh -tt`와 headless plain SSH fallback을 구현하고 두 단계 리뷰를 통과했다.
- 설계·계획을 실제 fd openability 구현과 맞추고 커밋했다.
- 병합된 develop에서 회귀를 재검증했다.

### 다음
- 필요하면 develop을 origin에 push한다.

### 검증
- 병합된 develop: root 90 passed, adapter 94 passed, installer·dispatcher·tmpfiles smoke, 관련 `bash -n`과 `git diff --check` 통과.
