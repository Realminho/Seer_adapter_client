# offline-python-hcl2

### 목표
- python-hcl2 의존성을 SSH 업데이트에서 오프라인 설치 가능하도록 구성

### 지금
- 구현 및 검증 완료

### 완료
- SSH 업데이트의 오프라인 Python 의존성 설치를 기본 활성화
- 설치 및 복사 venv 검증에 hcl2/SerializationOptions import 추가
- 수동 오프라인 설치 스크립트도 hcl2 검증 추가
- 번들에 python-hcl2 8.1.2, lark 및 Python/CPU별 regex wheel 존재 확인

### 다음
-

### 검증
- 깨끗한 Python 3.12 venv에서 --no-index 번들 설치 및 hcl2 8.1.2 import 성공
- 두 shell script bash -n 및 git diff --check 통과
