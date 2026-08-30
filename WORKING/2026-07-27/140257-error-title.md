# error-title-and-update-path

### 목표
- WebUI의 다른 오류 표시도 타이틀 다음 설명 순서로 통일한다.
- SSH 업데이트 스크립트의 기본 배포 폴더명을 확인하고 바로잡는다.

### 지금
- 오류 렌더링 통일과 기본 원격 경로 수정을 완료했다.

### 완료
- VDA 오류, 전역 오류 알림, EZIO/PIO 오류를 타이틀 다음 설명 순서로 통일했다.
- SSH 업데이트 기본 원격 폴더를 `~/adaptor`로 변경하고 도움말/문서를 맞췄다.

### 다음
- 필요하면 실제 WebUI와 원격 dry-run 환경에서 표시/경로를 확인한다.

### 검증
- `bash -n scripts/update-jibot-adapter-over-ssh.sh` 통과
- 관련 pytest 161개 통과
- 변경 파일 `git diff --check` 통과
