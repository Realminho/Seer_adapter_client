# Default Service Ports Design

## 목표

- WebUI 기본 포트를 `8090`에서 `9000`으로 변경한다.
- AMR camera 기본 포트를 `8080`에서 `9001`로 변경한다.
- WebUI 메인 대시보드와 `/camera` 화면에 camera 실행 여부를 눈에 띄는 배지로 표시한다.

## 설계

기존 설정 구조를 유지하고 새 설정이나 추상화는 추가하지 않는다. WebUI는 `WebUiConfig`, 기본 TOML, 실행 스크립트 fallback을 함께 바꾸고, camera는 video URL, WebUI 파생 링크, 설치 스크립트 환경변수 fallback, systemd 참조 템플릿을 함께 바꿔 기본값 불일치를 막는다. 명시적으로 포트를 덮어쓰는 기능은 그대로 유지한다.

Camera 실행 표시는 이미 조회 중인 systemd `active_state`를 재사용한다. `active`이면 초록색 `실행 중`, 그 외에는 빨간색 `중지됨` 배지를 렌더링하며 별도 상태 조회나 API는 추가하지 않는다.

## 검증

기존 설정 및 스크립트 테스트에 기본 포트 계약을 추가하고 렌더링 테스트에 camera 실행/중지 배지를 검증해 변경 전 실패와 변경 후 성공을 확인한다. 관련 Python 테스트를 실행하고 남은 구 기본값이 실행 경로에 없는지 검색한다.
