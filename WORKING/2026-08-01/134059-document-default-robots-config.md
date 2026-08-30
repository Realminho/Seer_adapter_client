# document-default-robots-config

### 목표
- 기본 실행에서도 사용할 robots HCL 설정 파일을 명시하는 방법을 매뉴얼에 추가한다.

### 지금
- 문서 갱신 및 검증 완료.

### 완료
- 기본 `run-main.sh` 실행에서 `--robots`와 `--robot`을 명시하는 예시를 추가했다.
- HCL의 `simulator = true` 적용 규칙과 명시적 `--simulator` 예시를 기록했다.

### 다음
- 없음.

### 검증
- 문서의 두 명령 모두 `resolve_instance()`에서 `simulator=True`로 확인했다.
- `git diff --check` 통과.
