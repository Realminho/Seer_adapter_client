# Default Service Ports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** WebUI 기본 포트를 9000으로, AMR camera 기본 포트를 9001로 일관되게 변경하고 camera 실행 상태를 표시한다.

**Architecture:** 기존 설정과 셸 스크립트의 기본값만 직접 변경한다. 사용자 지정 포트 override 동작은 건드리지 않는다.

**Tech Stack:** Python dataclass/TOML, Bash, systemd, pytest

---

### Task 1: 기본 포트 계약 테스트

**Files:**
- Modify: `adaptor/tests/test_web_ui_config.py`
- Modify: `tests/test_adaptor_service_scripts.py`
- Modify: `tests/test_setup_web_video_server_script.py`

- [x] 설정 기본값이 WebUI `9000`, camera `9001`인지 assertion을 추가한다.
- [x] 설치 스크립트와 systemd 템플릿의 camera 기본 포트가 `9001`인지 assertion을 추가한다.
- [x] `pytest`로 새 테스트가 기존 `8090`/`8080` 때문에 실패하는지 확인한다.

### Task 2: 최소 기본값 변경

**Files:**
- Modify: `adaptor/config/config.py`
- Modify: `adaptor/config/config.toml`
- Modify: `adaptor/run-web.sh`
- Modify: `adaptor/web/server.py`
- Modify: `scripts/setup-adaptor-service.sh`
- Modify: `scripts/setup-web-video-server-on-onboard.sh`
- Modify: `scripts/systemd/amr-camera.service`
- Modify: `README.md`
- Modify: `docs/guide/web-ui.md`

- [x] WebUI 설정과 fallback의 `8090`을 `9000`으로 바꾼다.
- [x] camera 설정 URL, 파생 링크 fallback, 설치 기본값과 템플릿의 `8080`을 `9001`로 바꾼다.
- [x] 사용자용 현재 기본 포트 문서를 새 값으로 바꾼다.
- [x] 새 테스트와 관련 테스트 전체를 실행해 통과를 확인한다.
- [x] 실행 경로와 현재 사용자 문서에 남은 구 기본값이 없는지 `rg`로 확인한다.

### Task 3: Camera 실행 상태 배지

**Files:**
- Modify: `adaptor/tests/test_web_render.py`
- Modify: `adaptor/web/render.py`

- [x] 메인 대시보드와 `/camera` 화면이 `active`일 때 `실행 중`, 그 외에는 `중지됨` 배지를 표시하는 테스트를 추가한다.
- [x] 새 렌더링 테스트가 배지 부재로 실패하는지 확인한다.
- [x] 기존 `active_state`와 `_pill`을 재사용해 두 화면에 배지를 렌더링한다.
- [x] 렌더링 테스트 전체가 통과하는지 확인한다.
