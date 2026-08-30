# amr-client-contract

The **common contract** between the `adaptor` and any AMR client
(`jibot-client`, `seer-client`, …). Interfaces only — **zero runtime
dependencies**, imports no client and no adaptor code.

어댑터와 모든 AMR 클라이언트(`jibot-client`, `seer-client`, …) 사이의 **공용 계약**.
인터페이스만 있고 **런타임 의존성 0**, 어떤 클라이언트/어댑터도 import 하지 않는다.

## Why this package exists / 존재 이유

The adaptor used to depend on the client *implicitly* (duck typing): it just
called `vehicle.goto_xyz(...)` and read `vehicle._x` on a concrete `JIBOT`
object. Adding a second robot (SEER) makes that implicit contract a liability.
This package makes it **explicit** so one adaptor can drive many clients.

어댑터는 클라이언트에 *암묵적으로*(덕타이핑) 의존했다: 구체 `JIBOT` 객체에 대해
`vehicle.goto_xyz(...)`를 호출하고 `vehicle._x`를 읽었다. 두 번째 로봇(SEER)이
생기면 그 암묵 계약이 부담이 된다. 이 패키지가 계약을 **명시화**해서, 하나의 어댑터가
여러 클라이언트를 구동할 수 있게 한다.

## Dependency direction / 의존 방향

```
        adaptor  ───►  amr-client-contract  ◄───  seer-client
                              ▲
                              └──────────  jibot-client (구조적, 변경 0)
```

The contract depends on nothing, so there are no cycles and **clients never
depend on the adaptor**.

계약은 아무것도 의존하지 않으므로 순환이 없고 **클라이언트가 어댑터에 역의존하지
않는다**.

## Two kinds of interface / 두 종류의 인터페이스

| Kind | Defined with | Satisfied | Use |
|---|---|---|---|
| **Interface** (`contract.py`) | `typing.Protocol` | **structurally** (name match) | the always-present core: connection / motion / state / injection |
| **Marker interface** (`markers.py`) | empty `abc.ABC` | **nominally** (explicit inherit) | optional capabilities: `SupportsDocking`, `SupportsRelocation`, … detected via `isinstance` |

- **인터페이스** = `Protocol`, *구조적* 충족(이름만 맞으면 됨) → 항상 존재하는 코어.
- **마크 인터페이스** = 빈 `ABC`, *명목적* 충족(명시 상속) → 선택적 능력, `isinstance`로 탐지.

## Conformance status / 정합 현황

| Client | Core Protocols (`AmrClient`) | Notes |
|---|---|---|
| `jibot_client.JIBOT` | **partial / structural** | Public wrappers + injectors already match. Clean names `dock`/`localize`/`drive` and state *properties* (`x`,`y`,…) are not yet present (JIBOT has `um_dock`/… and private `_x`). Full nominal conformance is a future **purely-additive** shim — **no runtime change**, **out of scope** now. |
| `seer_client.SeerClient` | **direct (skeleton)** | Built clean-name-first; implements the Protocols and inherits the markers. Method bodies are skeleton (`NotImplementedError`). |

| 클라이언트 | 코어 Protocol | 비고 |
|---|---|---|
| `JIBOT` | **부분/구조적** | 공개 래퍼+주입자는 이미 일치. `dock`/`localize`/`drive`와 상태 *property*(`x`,`y`,…)는 아직 없음(JIBOT 은 `um_dock`/…과 private `_x`). 완전 명목 정합은 후속 **가산적** shim — **런타임 변화 0**, 지금은 **범위 밖**. |
| `SeerClient` | **직접(스켈레톤)** | 깔끔-우선으로 구현, Protocol 충족 + marker 상속. 메서드 본문은 스켈레톤(`NotImplementedError`). |

## Usage / 사용

```python
from amr_client_contract import AmrClient, SupportsDocking

def attach_vehicle(vehicle: AmrClient) -> None:
    # works for any conforming client / 정합하는 모든 클라이언트에 동작
    ...
    if isinstance(vehicle, SupportsDocking):
        # only call dock() when the capability is declared
        # 능력이 선언된 경우에만 dock() 호출
        ...
```

Install editable alongside the clients (both must be importable):

```bash
pip install -e amr-client-contract -e seer-client
```
