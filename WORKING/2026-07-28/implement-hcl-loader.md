# implement-hcl-loader

### 목표
- Task 1: HCL 로더 (`config/hcl.py`) 구현
- TDD 방식으로 테스트 먼저 작성, 그 다음 구현
- 정확히 브리프의 코드를 사용 (개선이나 재구성 없음)

### 지금
- 작업 완료

### 완료
- Step 1: python-hcl2>=8,<9 의존성 추가 및 uv sync
- Step 2: 테스트 파일 (9개 테스트 케이스) 작성
- Step 3: 테스트 실패 확인 (ModuleNotFoundError 예상대로 발생)
- Step 4: HCL 로더 구현 (load_hcl, blocks, HclError, PARAM_PATTERN)
- Step 5: 테스트 통과 확인 (9/9 passing)
- Step 6: 커밋 (4f11e81 feat(config): add HCL loader with label-block normalization)

### 다음
- 작업 완료

### 검증
- 9개 테스트 전부 통과 (빠진 요구사항 없음)
- 모듈 docstring 한국어, `from __future__ import annotations` 사용
- SerializationOptions 세 개 옵션 정확히 사용 (with_comments=False, explicit_blocks=False, strip_string_quotes=True)
- PARAM_PATTERN 정규표현식 검증됨: fullmatch 전체 문자열만 매칭
- 커밋은 명시적 파일 4개만 포함 (다른 미커밋 파일 제외)
