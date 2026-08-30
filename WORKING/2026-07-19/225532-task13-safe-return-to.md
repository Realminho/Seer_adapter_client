# task13-safe-return-to

### 목표
- `/action` return redirect의 non-ASCII/header 안전성과 flash query 교체를 보완한다.

### 지금
- review 보완 구현과 focused 검증을 마쳤다.

### 완료
- non-ASCII return target을 거부하고 stale msg/err를 새 flash로 교체한다.

### 다음
- loopback 권한 환경에서 새 HTTP fixture 테스트를 실행한다.

### 검증
- RED 2건 확인; helper 9 passed, direct `_post_action` passed, py_compile/diff-check passed.
