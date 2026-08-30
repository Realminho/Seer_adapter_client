# 액션 모듈 패널 설계

## 1. 목적

PIO나 clamp처럼 서로 관련된 여러 VDA5050 액션과 전용 조작 화면을 하나의
액션 모듈로 묶는다. first-party 모듈과 신뢰할 수 있는 커스텀 액션 모듈이 같은
계약을 사용하며, 모듈에 `panel.html`이 있으면 WebUi 상세 대시보드가 이를
불러온다.

핵심 단위는 개별 action이 아니라 다음 두 요소를 가진 Python 패키지다.

```text
custom_actions/
└── air_shower/
    ├── __init__.py   # action_specs()가 ActionSpec 여러 개를 반환
    └── panel.html    # 선택 사항: 상세 대시보드 전용 패널
```

기존 PIO와 clamp도 같은 패키지 구조와 계약을 사용한다.

## 2. 목표

- 한 모듈이 여러 `ActionSpec`을 등록한다.
- 액션 코드와 선택적 WebUi 패널을 같은 패키지에서 설치·삭제·비활성화한다.
- `panel.html`이 있으면 상세 대시보드에 모듈 패널을 한 번만 표시한다.
- `panel.html`이 없거나 사용할 수 없으면 활성 액션을 기존 일반 버튼으로 표시한다.
- 패널의 form도 기존 `/adapter/{key}/action` 경로, CSRF 검증, action whitelist와
  action별 확인 정책을 그대로 사용한다.
- 기존 `[[actions]]` 액션별 설정과 실행 동작을 유지한다.

## 3. 비목표

- Jinja 같은 템플릿 엔진이나 새 프런트엔드 프레임워크 도입
- 모듈별 HTTP endpoint 또는 WebUi 플러그인 API 추가
- 외부 JavaScript/CSS asset 번들링
- 임의 경로의 HTML 로드
- 현재 액션 실행기와 VDA5050 상태 처리 재설계
- 격리 목적의 subprocess 코드를 WebUi 프로세스에서 import

subprocess 방식의 기존 `[[actions]]`는 현재 계약을 그대로 사용한다. 이번 모듈
패키지 계약은 WebUi와 adapter가 import해도 되는 first-party 또는 신뢰 코드만
대상으로 한다.

## 4. 모듈 계약

모듈 패키지의 `__init__.py`는 `action_specs()`를 제공한다.

```python
def action_specs() -> tuple[ActionSpec, ...]:
    return (
        ActionSpec(action_type="airShowerStart", handler=start, label="Air shower start"),
        ActionSpec(action_type="airShowerStop", handler=stop, label="Air shower stop"),
    )
```

규칙은 다음과 같다.

- 반환값은 기존 `ActionSpec`의 iterable이다.
- `action_type`은 전체 registry에서 유일해야 한다. first-party·built-in
  action type을 잠식할 수 없으며, 이는 기존 `reserved_action_types` 보호를
  `[[action_modules]]`까지 확장해 강제한다.
- handler와 `motion`/`timeout_sec` 설정은 기존 `ActionSpec` 의미를 유지한다.
- **fallback 라벨 출처(결정 D1)**: `ActionSpec`에 선택 필드 `label: str = ""`을
  추가한다. panel.html이 없거나 부분 비활성 상태로 일반 card를 그릴 때 WebUi는
  `spec.label or spec.action_type`을 버튼 라벨로 쓴다. 기존 `ActionSpec` 사용처는
  기본값으로 무영향이다. panel.html이 있는 정상 경로의 라벨은 panel.html이 소유한다.
- **import-light 계약(결정 D2)**: 패키지 `__init__.py`는 import 시점에 ROS, serial,
  `ezi_motor` 등 하드웨어·무거운 의존성을 top-level import하지 않는다. `action_specs()`
  호출도 하드웨어 연결이나 background task를 시작하지 않는다. 실제 하드웨어 코드는
  별도 submodule에 두고 handler가 호출될 때 lazy import한다. adapter와 WebUi가 같은
  패키지를 import해 발견하므로(6절), 이 계약이 무거운/실패 가능한 import를 막는다.
- UI가 필요하면 패키지 루트에 정확히 `panel.html`을 둔다.
- `panel.html`은 없어도 된다.

커스텀 모듈은 설정에서 패키지 하나를 등록한다.

```toml
[[action_modules]]
module = "custom_actions.air_shower"
enabled = true
```

first-party PIO/clamp는 기본 모듈 목록에 포함하므로 사용자 설정에 반복해서 쓰지
않는다. 기존 `[[actions]]`는 단일 액션과 subprocess 액션을 위해 계속 지원한다.

## 5. 패널 계약

`panel.html`은 완전한 HTML 문서가 아니라 상세 대시보드의 command 영역에 들어갈
fragment다. 모듈이 화면 전체 구조, `<html>`, `<head>`, 공통 CSS를 소유하지 않는다.

WebUi는 표준 라이브러리 `string.Template`로 다음 값만 치환한다.

| 변수 | 의미 |
|---|---|
| `$adapter_key` | URL에 사용할 adapter 식별자 |
| `$csrf_token` | 기존 POST CSRF token |
| `$return_to` | 처리 뒤 돌아올 상세 대시보드 경로 |

예시는 다음과 같다.

```html
<section class="command-group">
  <div class="command-head"><h2>Air shower</h2></div>
  <div class="command-body">
    <form method="post" action="/adapter/$adapter_key/action">
      <input type="hidden" name="csrf_token" value="$csrf_token">
      <input type="hidden" name="return_to" value="$return_to">
      <input type="hidden" name="action_type" value="airShowerStart">
      <button>Start</button>
    </form>
  </div>
</section>
```

치환값은 HTML escape 후 전달한다. literal `$`가 필요하면 `$$`를 사용한다.
WebUi는 strict `Template.substitute`를 쓴다(`safe_substitute` 아님). 매핑에 없는
`$var`나 잘못된 `$`는 예외를 내며, 이 예외는 7절 fallback으로 잡아 패널 대신
유효 액션 card를 그린다. 신뢰 first-party 코드의 오타를 조용히 통과시키지 않기 위함이다.
패널은 기존 WebUi CSS class를 재사용한다. module-specific JavaScript나 CSS는 이번
범위에 포함하지 않는다.

## 6. 발견과 데이터 흐름

**공유 발견 함수(결정 D3)**: adapter와 WebUi가 `core`에 있는 하나의 순수 함수
(`core/action_modules.py`의 `discover_action_modules(config)` 가칭)를 사용한다. 이
함수는 config만 입력받아 모듈별 `(specs, panel_html | None, error | None)`를 결정적으로
반환한다. adapter(`build_registry_from_config` 경로)와 WebUi(`AdaptorSpec` 빌더)는
서로 다른 코드로 목록을 재계산하지 않고 이 함수 결과를 공유한다.

발견 순서는 다음과 같다.

1. 기본 first-party 모듈과 활성 `[[action_modules]]` 패키지를 import한다.
2. 각 패키지의 `action_specs()`를 호출한다.
3. 중복 action type, 예약(first-party·built-in) type 잠식, 잘못된 반환값을 검증한다.
   하나라도 잘못되면 그 모듈 전체를 등록하지 않아 부분 등록을 피한다. 서로 다른 두
   모듈이 같은 type을 내면 먼저 발견된 모듈이 이기고 나중 모듈 전체를 거부한다.
4. adapter는 반환된 spec을 기존 `ActionRegistry`에 등록한다.
5. WebUi는 같은 spec의 `action_type`/`motion`/`label`을 `AdaptorSpec`의
   `InstantAction`으로 반영한다(`InstantAction(action_type, spec.label or action_type,
   motion=spec.motion)`).
6. WebUi는 `importlib.resources`로 해당 패키지의 `panel.html` 존재 여부와 내용을
   확인한다. 설정에서 filesystem 경로를 받지 않는다.
7. 상세 대시보드는 패널이 있으면 모듈당 한 번 렌더하고, 그 모듈 액션을 일반
   Vehicle action 목록에서 제외한다.
8. 패널의 POST는 기존 action sender와 adapter registry로 전달된다.

```text
action module package
 ├─ action_specs() ──> ActionRegistry ──> 기존 VDA5050 실행/결과 처리
 └─ panel.html ──────> 상세 대시보드 ──> 기존 /action POST
```

### 6.1 별도 프로세스 import 현실과 비대칭 실패

WebUi는 adapter와 **별도 프로세스/systemd 유닛**이며, 상태는 파일(`FileMonitor`)로
읽고 명령은 UDS(`control.sock`)로만 보낸다. 지금까지 WebUi는 `extensions/pio.py`·
`clamp.py`를 import하지 않았다. 이 설계는 WebUi가 `panel.html`과 spec 메타를 얻기
위해 모듈 패키지를 import하게 만든다. 이를 안전하게 하려고:

- 모듈 `__init__.py`는 4절의 import-light 계약을 지킨다(하드웨어 lib는 handler
  lazy import). 두 프로세스가 같은 저장소/venv에서 같은 코드를 import하므로 발견은
  `(config, 설치된 패키지)`의 결정적 함수가 되어, 정상적으로는 두 프로세스가 같은
  판정을 내린다.
- 그럼에도 한 프로세스에서 모듈 import/검증이 실패하면, 그 모듈은 두 프로세스 모두에서
  **동일하게 전체 skip**한다(등록·패널·card 모두 없음) + 오류 로그. 비대칭이 생겨도
  안전 측(액션 자체를 노출하지 않음)으로 수렴하며, WebUi 전체 페이지는 계속 열린다.

### 6.2 `_JIBOT_INSTANT_ACTIONS` 분해

현재 WebUi의 액션 목록·`motion`·라벨은 `core/registry.py`의 하드코딩된
`_JIBOT_INSTANT_ACTIONS`에서 온다. 이 목록에서 **clamp 항목을 제거**하고 clamp 모듈
발견 결과로 대체한다. 모듈이 아닌 built-in 액션(stateRequest, startPause, drive,
sound, gotoNearestNode 등)은 이 목록에 그대로 남는다. **PIO action type은 지금
`_JIBOT_INSTANT_ACTIONS`에 없으므로** PIO 조작 form은 신규이며 발견을 통해 추가된다.

WebUi는 모듈 전용 실행 endpoint를 만들지 않는다. 따라서 action whitelist,
CSRF, 기존 action별 확인 정책과 MQTT 발행 동작은 현재 경로에서 한 번만 유지된다.

## 7. 표시와 fallback

- 활성 모듈에 유효한 `panel.html`이 있으면 모듈 패널을 표시한다.
- HTML이 없으면 모듈의 활성 액션을 기존 일반 action card로 표시한다. card 라벨은
  `spec.label or spec.action_type`을 쓴다(결정 D1).
- 모듈 import, `action_specs()`, HTML 읽기 또는 template 치환이 실패하면 오류를
  로그에 남기고 패널 대신 유효한 활성 액션 card를 표시한다. WebUi 전체 페이지는
  계속 열린다.
- WebUi 쪽 모듈 발견 또는 검증이 실패하면 해당 모듈을 표시하지 않는다. adapter와
  WebUi는 6절의 같은 발견 함수를 사용해 서로 다른 판정을 내리지 않도록 한다.
- 모듈 전체를 비활성화하면(`[[action_modules]] enabled=false`, 또는 기본 모듈은
  기본 목록에서 제거) 액션과 패널이 모두 사라진다.
- 모듈 액션 중 하나라도 action별 설정으로 비활성화되면(기존 `[[actions]]
  action_type=... enabled=false` 경로, `disabled_action_types` 재사용) 패널 전체를
  숨기고, 남은 활성 액션만 일반 card로 표시한다. 정적 HTML에 비활성 버튼이 남는
  것을 막기 위한 보수적 fallback이다.
- 잘못된 action type POST는 현재 whitelist 검증으로 계속 거부한다.

## 8. 보안 경계

`panel.html`은 임의 사용자가 업로드하는 콘텐츠가 아니다. 같은 패키지의 Python
코드가 이미 adapter 프로세스에서 실행되므로 신뢰 수준도 그 코드와 같다.

그래도 다음 경계는 유지한다.

- 패널 경로는 패키지 내부의 고정 이름 `panel.html`만 허용한다.
- config의 임의 HTML 경로나 원격 URL은 읽지 않는다.
- 공통 치환값은 HTML escape한다.
- 패널 action도 기존 CSRF와 action별 확인 정책을 우회하지 않는다.
- subprocess 격리 액션의 코드는 패널 발견을 위해 import하지 않는다.

## 9. 기존 코드와의 관계

- `adaptor/extensions/pio.py`와 `clamp.py`는 import 이름을 유지하면서 패키지로
  옮기고 각각 `panel.html`을 갖게 한다. `ezio.py`도 같은 모듈 계약으로 이관하되
  panel.html은 선택(초기 없음 가능)이다. 세 모듈은 기본 first-party 모듈 목록을
  이루며, 지금 `extensions/hardware.py`가 하던 번들 역할을 대신한다.
- 현재 각 모듈의 `action_specs()`와 handler 구현은 그대로 재사용한다.
- `adaptor/web/render.py`에 하드코딩된 clamp 조작 form과 새 PIO module 조작 form은
  각 패키지의 `panel.html`이 소유한다.
- 공통 IO 상태 표시처럼 특정 action form이 아닌 대시보드 관측 정보는 기존
  공통 WebUi 영역에 남긴다. PIO/EZIO 출력 토글 badge는 `/action`이 아니라
  별도 endpoint(`/adapter/{key}/io/out`)로 가므로 panel.html 범위 밖이며 공통 IO
  패널에 남는다. panel.html의 form은 `/action` 경로만 사용한다.
- 기존 `[[actions]]`, `ActionRegistry.dispatch`, `/action` server route에는 호환성을
  깨는 변경을 하지 않는다.
- `return_to`는 이미 `/action`의 `_reserved` 키라 VDA5050 파라미터로 새지 않는다.
  다만 현재 `_post_action`은 이 값을 사용하지 않고 항상 `/adapter/{key}`로 redirect한다.
  패널 처리 후 상세 대시보드의 특정 뷰/앵커로 돌아가려면 이 redirect를 `return_to`
  존중하도록 최소 변경한다. 그렇지 않으면 `$return_to`는 현재 동작(상세 페이지 복귀)과
  동일한 no-op이다.
- **확인 정책 제약**: `/action` 경로의 confirm 게이트는 server의 하드코딩 튜플
  `_CONFIRM_REQUIRED_ACTIONS`(현재 `gotoNearestNode`만)에 의존한다. 모듈 계약에는
  "이 액션은 confirm 필요"를 선언할 수단이 없다. 이번 범위는 실행/confirm 경로 재설계를
  하지 않으므로(3절 비목표), 커스텀 모듈 액션에 서버 강제 confirm이 필요하면 별도로
  server를 수정해야 한다는 한계를 명시한다.

## 10. 테스트

작은 회귀 테스트로 다음을 검증한다.

- 한 모듈의 여러 `ActionSpec`이 모두 registry에 등록된다.
- 중복 action type 모듈은 로드되지 않는다.
- `panel.html`이 있는 모듈은 상세 대시보드에 한 번만 표시된다.
- 패널이 있는 모듈 액션은 일반 action card에 중복 표시되지 않는다.
- HTML이 없는 모듈은 기존 일반 action card로 표시된다.
- 모듈 비활성화 시 액션과 패널이 모두 사라진다.
- action 하나를 비활성화하면 패널 대신 남은 액션 card만 표시된다.
- template 오류가 WebUi 전체 렌더를 실패시키지 않는다.
- 패널 form이 기존 action endpoint, CSRF token과 action별 확인 경로를 사용한다.
- 기존 액션별 `[[actions]]`와 subprocess 테스트가 그대로 통과한다.
- panel.html 없는 모듈 액션의 fallback card 라벨이 `spec.label`을, `label` 미지정
  시 `action_type`을 쓴다(결정 D1).
- 예약(first-party·built-in) type을 잠식하는 모듈은 로드되지 않는다.
- 모듈 import 실패가 그 모듈만 skip시키고(등록·패널·card 없음) WebUi 전체 페이지는
  계속 열린다(결정 D2).
- adapter와 WebUi가 같은 발견 함수로 같은 모듈 집합을 얻는다(결정 D3, 발견 결정성).
- clamp를 모듈 발견으로 옮긴 뒤에도 clamp 버튼의 `motion` 플래그가 이전과 같다.

실제 PIO, clamp 하드웨어나 MQTT broker는 테스트에 필요하지 않다.

## 11. 완료 조건

- PIO와 clamp가 여러 액션과 패널을 각각 하나의 모듈 패키지로 제공한다.
- 예제 커스텀 모듈 또는 테스트 fixture가 같은 계약으로 여러 액션과 패널을
  제공한다.
- 상세 대시보드가 패널을 자동 발견하고 중복 없이 렌더한다.
- 패널이 없거나 실패해도 활성 액션을 사용할 수 있다.
- 기존 registry, WebUi action POST, 액션별 설정과 subprocess 동작의 회귀 테스트가
  통과한다.

## 12. 배포

- `[[action_modules]]`의 `module`은 import 가능한 패키지 이름이다(예:
  `custom_actions.air_shower`). config의 filesystem 경로가 아니다.
- 커스텀 모듈 패키지는 adapter와 WebUi **두 systemd 유닛 모두**의 sys.path에
  올라와야 한다. 두 유닛의 working dir/실행 방식이 다르므로, 저장소 공용 경로에
  두거나 두 유닛 환경 모두에 PYTHONPATH로 노출한다.
- first-party 모듈(pio/clamp/ezio)은 기본 모듈 목록에 있으므로 사용자 설정에서
  반복 등록하지 않는다.

## 13. 설계 결정 로그

리뷰에서 드러난 공백에 대한 결정이다. 반대 의견이 있으면 구현 전에 조정한다.

- **D1 (fallback 라벨 출처)**: `ActionSpec`에 선택 필드 `label: str = ""`을 추가하고,
  panel 없는/부분 비활성 fallback card는 `spec.label or action_type`을 쓴다.
  대안(action_type→meta 맵 유지)은 모듈 자기서술성이 떨어져 기각.
- **D2 (WebUi import 계약)**: 모듈 `__init__.py`를 import-light로 강제(하드웨어
  lib는 handler lazy import)하고, WebUi가 발견을 위해 패키지를 import하되 import
  실패는 두 프로세스에서 동일하게 그 모듈만 skip한다. 대안(adapter가 IPC로 발견
  결과·panel.html을 publish, WebUi는 소비만)은 더 견고하나 IPC/프로세스 경계를
  넓혀 이번 최소 범위와 비목표를 벗어나 보류.
- **D3 (공유 발견 함수)**: `core`에 순수 발견 함수를 두어 adapter와 WebUi가 config로
  같은 결과를 재계산한다. `_JIBOT_INSTANT_ACTIONS`에서 clamp를 떼어 모듈 발견으로
  대체하고, built-in 비모듈 액션은 그대로 둔다.
- 부수 결정: 예약 type 잠식 금지(`reserved_action_types` 확장), 중복 시 선발견 모듈
  우선, strict `Template.substitute`, `return_to` 존중 redirect 최소 변경, confirm은
  기존 server 하드코딩 게이트 유지(모듈 계약으로 confirm 선언 불가).
