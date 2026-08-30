# WebUI extension / recipe 조회·실행 페이지

작성일: 2026-07-28
상태: 설계 승인됨
범위: 1개 리포 — `unified-amr-adaptor`(어댑터 WebUI)

## 1. 배경

WebUI에서 지금 무엇이 되고 무엇이 안 되는지 실측했다.

**Extension은 이미 보이고 실행된다.** 23개 액션이 4개 모듈 패널(Clamp / PIO / EZIO /
Facility workflows)로 노출되고, `POST /adapter/<key>/action`이 VDA5050 instant action을
MQTT로 발행한다.

**Recipe는 전혀 보이지 않는다.** 두 가지 이유가 겹쳐 있다.

1. `config/recipes.hcl`이 없다(`.example`만 존재) → `config.recipes`가 0개.
2. 있더라도 `core/registry.py`가 `instant_actions`를 `discover_action_modules()`에서만
   조립한다. recipe spec은 `first_party_action_specs()` 경로로 등록되는데 registry가
   그 경로를 보지 않는다.

2번이 본질적인 결함이다. `_post_action`은 `spec.instant_actions`에 없는 액션 타입을
`rejected:unknown`으로 거부하므로, **recipe는 지금 WebUI에서 실행 자체가 불가능하다.**

또 recipe는 `${var.doorPin}` 같은 파라미터를 쓰는데 선언부가 없다 — step 안에서 참조만
한다. 실행 폼을 만들려면 정의를 훑어 변수 이름을 뽑아내야 한다.

## 2. 목표 / 비목표

**목표**

- 한 화면에서 사용 가능한 extension과 recipe를 모두 조회한다.
- 각각을 파라미터와 함께 그 자리에서 실행한다.
- recipe를 `instant_actions`에 연결해 실행 가능하게 만든다(위 2번 해소).

**비목표**

- 새 실행/전송 경로. 기존 `POST /adapter/<key>/action`을 그대로 쓴다.
- extension 파라미터 스키마 도입. 지금 없고, 이번에 만들지 않는다.
- recipe 편집 UI. 파일 직접 편집으로 남긴다.
- `config/recipes.hcl` 작성. 현장 설정의 몫이다.

## 3. 페이지

경로는 `/adapter/<key>/actions`. WebUI가 이미 쓰는
`/adapter/<key>/{control,manual,tests,logs}` 서브페이지 패턴을 그대로 따른다.
**로봇별 페이지여야 한다** — 액션은 특정 로봇을 향해 발행되기 때문이다.

두 묶음으로 나눈다.

```
Extensions                 모듈별 그룹 (PIO / EZIO / Clamp / Facility workflows)
Recipes                    recipes.hcl 정의
```

각 행에 액션 타입, 라벨, `motion` 배지, 출처(모듈명 또는 `recipes.hcl`)를 표시한다.

recipe가 0개면 빈 상태로 `config/recipes.hcl.example`을 복사하라는 안내를 띄운다.

## 4. 실행 폼

| 대상 | 입력 방식 | 근거 |
|---|---|---|
| recipe | 정의에서 뽑은 `${var.X}` 이름마다 입력칸 자동 생성 | 변수 목록이 정의에 있으므로 운영자가 외울 필요가 없다 |
| extension | 빈 key/value 행 3개 | 파라미터 스키마가 없어 이름을 알 방법이 없다 |

**전송은 기존 핸들러를 그대로 쓴다.** `POST /adapter/<key>/action`은 예약어
(`csrf_token`, `action_type`, `confirm`, `return_to`, `verb`, `armed`)를 뺀 나머지 폼
필드를 그대로 액션 파라미터로 만든다. 새 경로를 파면 confirm 게이트, 감사 로그,
CSRF, motion 확인 키를 다시 구현해야 하고 그 과정에서 빠뜨리기 쉽다.

## 5. 변수 추출

`config/recipes.py`에 `recipe_variables(recipe) -> tuple[str, ...]`를 둔다.
recipe의 모든 step과 cleanup의 `parameters`를 재귀적으로 훑어
`config.hcl.PARAM_PATTERN`(`${var.NAME}`)에 걸리는 이름을 선언 순서대로 중복 없이
모은다. 문자열 전체가 `${var.X}`인 경우와 문자열 중간에 삽입된 경우를 모두 잡는다 —
실행기(`extensions/recipes/_resolve`)가 둘 다 치환하기 때문이다.

## 6. 배선

`core/registry.py`가 recipe spec을 `instant_actions`에 포함한다. recipe는 모듈이
아니므로 `ActionModuleView`를 재사용하지 않고 별도 목록으로 전달해, 페이지가 두 묶음을
구분해 렌더할 수 있게 한다.

## 7. 손댈 곳

| 파일 | 내용 |
|---|---|
| `adaptor/config/recipes.py` | `recipe_variables()` |
| `adaptor/core/registry.py` | recipe를 `instant_actions`에 포함, recipe 뷰 전달 |
| `adaptor/web/render.py` | `actions_page()` |
| `adaptor/web/server.py` | GET 라우트, 상세 페이지 링크 |
| 테스트 | 변수 추출, 목록 노출, 페이지 렌더 |

## 8. 리스크

| 리스크 | 완화 |
|---|---|
| recipe를 `instant_actions`에 넣으면 factsheet 액션 목록도 바뀐다 | 설계상 의도된 동작이다(recipe는 VDA5050 액션 타입이다). factsheet 테스트로 확인한다 |
| motion recipe가 확인 키 없이 발행됨 | `_CONFIRM_REQUIRED_ACTIONS`와 motion 배지 동작을 그대로 따른다. recipe의 `motion` 플래그가 spec으로 전달되는지 테스트한다 |
| extension key/value 행이 오타를 그대로 전송 | 진단용 페이지이므로 허용한다. 잘못된 파라미터는 액션이 FAILED로 돌려준다 |
