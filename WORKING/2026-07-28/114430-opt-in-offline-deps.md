# opt-in-offline-deps

### 목표
- Python 3.11 오프라인 wheel 호환성 확인 및 SSH 의존성 설치를 명시적 opt-in으로 복원

### 지금
- 수정 및 검증 완료

### 완료
- Python 3.11 ARM64/x86_64 regex wheel과 python-hcl2의 Python >=3.8 지원 확인
- 의존성 설치 기본값을 비활성화하고 `--install-py-deps` 명시 시에만 설치하도록 복원
- hcl2 설치 후 import 검증 로직은 opt-in 설치 경로에 유지

### 다음
-

### 검증
- bash -n, --help 출력 및 git diff --check 통과
- 로컬에는 Python 3.11 실행 파일이 없어 실제 설치 대신 wheel tag와 Requires-Python 메타데이터 확인
