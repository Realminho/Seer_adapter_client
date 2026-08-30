# Recipe library — 등록된 extension 액션을 순서대로 엮어 하나의 VDA5050 액션
# 타입으로 노출한다. recipe 자체가 액션 타입이므로 ACS는 node/edge order action
# 으로도, instant action으로도 같은 이름으로 부를 수 있다.
#
# config/recipes.py load_recipes()가 읽는다. robots.hcl의 robot 블록에
# recipes = "..." 키가 없으면 이 파일이 기본 경로로 쓰인다.
#
# step은 선언 순서대로 직렬 실행하고 첫 실패에서 멈춘다. cleanup은 성공·실패·
# 타임아웃과 무관하게 항상 돌며 본문과 별도의 시간 예산을 쓴다. 본문이 성공해도
# cleanup이 실패하면 recipe는 FAILED다 — 설비 자원을 쥔 채 성공을 보고하지 않는다.
#
# Key reference:
#   recipe "이름" { }         블록 라벨이 VDA5050 actionType이다. 이미 등록된 액션
#                             타입과 겹치거나 다른 recipe를 참조하면 부팅이 실패한다.
#   label = "..."             WebUI 표시 이름. 생략하면 액션 타입을 쓴다.
#   timeout_sec = 60          본문 전체 예산(초). 0이면 무제한.
#   cleanup_timeout_sec = 10  cleanup 블록이 timeout_sec를 생략했을 때의 기본값.
#   motion = false            true면 주행을 포함한다는 뜻이고 WebUI 확인 키 게이트가 걸린다.
#   step "액션타입" { }       순서 있는 한 단계. 없는/비활성 액션을 참조하면 부팅 실패.
#   cleanup "액션타입" { }    항상 실행되는 마무리 단계.
#   parameters = { ... }      자식 액션에 넘길 파라미터. ${var.NAME}은 부모 액션
#                             파라미터로 치환된다(문자열 전체면 타입 보존).
#   retry / retry_delay_sec   명시할 때만 재시도한다. 기본은 재시도 없음.
#   delay_sec = 0.2           이 step이 성공한 뒤 다음 step 전까지 쉰다. 남은
#                             timeout_sec 예산 안에서만 쉬고, step이 실패하면
#                             쉬지 않고 바로 cleanup으로 간다.

# --- 엘리베이터 문 ----------------------------------------------------------
#
# 문 버튼은 설비로 나가는 PIO 신호선이다. 그래서 이 recipe는 pio* 이름을 쓰고
# step도 pioWriteOut을 쓴다 — PIO out 번호로 지시하면 그 신호를 실제로 구동하는
# 것은 내부적으로 EZI IO다 (extensions/pio pio_write_output이
# ezi_io.turn_on_output()을 부른다. utils/elevator.py:477-481, 505-513도 같은
# 핀을 같은 방식으로 친다). 직렬 PIO는 설비와의 station 페어링(send_bc/
# station_id) 전용이다.
#
# 어느 점을 칠지는 숫자가 아니라 signal 이름으로 적는다. recipe는 config를 읽지
# 못하므로(${var.X}는 액션 파라미터만 치환한다) 숫자를 적어 두면 배선이 바뀔 때마다
# 이 파일도 같이 고쳐야 한다. 이름으로 부르면 풀이는 전부 extensions.hcl에서 끝난다:
#
#   recipe: signal = "elevator1fOpen"
#     -> extension "pio" output_signals.elevator1fOpen = 4  (PIO out 번호, 1-based)
#     -> extension "pio" output_pins[3]                = 3  (EZI IO 출력 핀, 0-based)
#     = extension "elevator"의 open_door_pin (utils/elevator.py가 치는 그 핀)
#
# 문 신호가 층마다 갈라져 있는 이유: 같은 네 가닥이 어느 station에 붙었느냐에 따라
# 다른 뜻이 된다. 1층은 out1/out2가 층 호출이고 out3/out4가 문, 상층은 그 반대다.
# 그래서 extension "elevator"의 open_door_pin/close_door_pin은 **1층 배선만** 담고
# 있고, 상층 신호를 대조할 두 번째 출처는 없다.
#
# 배선이 바뀌면 output_signals와 extension "elevator"의 door pin을 고치고 여기는
# 그대로 둔다. 두 곳이 다른 점을 가리키면 tests/test_recipe_acceptance.py가 실패한다
# — 한 칸 밀려도 recipe는 FINISHED로 끝나므로(문 열기가 닫힘 버튼을 눌러도 성공)
# 사람이 눈으로 잡을 수 없다.
#
# EZI IO 핀 번호를 직접 쓰려면 ezioWriteOut을 쓴다(0~15, 매핑 없음). 설비로 나가는
# 신호는 PIO 관점이 원본이라 pioWriteOut을 기본으로 둔다.
#
# 버튼은 latch가 아니라 momentary pulse다. on 뒤에 반드시 off가 따라와야 버튼을
# 누른 채로 두지 않는다. 그래서 off를 본문 마지막 step과 cleanup에 모두 둔다.
# 본문 off가 실패해도 cleanup이 다시 내리고, 둘 다 성공했을 때의 중복 off는
# 무해하다. pulse 폭은 ON step의 delay_sec로 현장에 맞춰 설정한다.
#
# 문 열기(pioElevatorOpen1f/2f)는 이제 열림 입력을 보고 끝난다 — 눈감은 대기는
# 문이 안 열려도 FINISHED로 끝났다. 문 닫기(pioElevatorClose1f/2f)와 층 호출
# (pioElevatorMove*)은 아직 눈감은 대기다: 닫힘·도착을 읽는 입력 번호가 확인되면
# 같은 방식(pioScenario의 in 단계)으로 바꾼다.
#
# pioInit의 stationId는 층마다 다르므로 recipe 이름에 층을 박아 고정한다. extension
# "pio"의 station_id 폴백은 사라졌다 — 비워 두면 extensions/pio pio_link_params()가
# 그 자리에서 raise한다(엉뚱한 설비에 조용히 BC를 보내는 대신). 이 현장의 엘리베이터
# station은 extension "elevator" motion_rules의 000010/000020이다. required 표시는
# WebUI 폼 전용이지만, recipe 경로도 pio_link_params()의 같은 검사를 거치므로 비워
# 두면 여기서도 실행이 막힌다.
#
# channel은 BC 상대를 고르는 두 값 중 나머지 절반이라 station과 함께 적어 둔다.
# 지금은 두 층 모두 250이고 이는 extension "elevator"의 channel과 같은 값이다 —
# 복사본이 생겼으므로 무선 채널을 바꿀 때 두 곳을 함께 고쳐야 한다(extension "pio"는
# 더 이상 channel을 갖지 않는다). 어긋나면 tests/test_recipes_config.py가 실패한다
# (둘이 달라도 실행은 조용히 recipe 값을 쓰므로 사람이 눈으로 잡을 수 없다).
#
# media/port/ohtNumber는 그대로 두어 extension "pio" 값을 쓴다. ohtNumber는 설비가
# 아니라 이 로봇을 가리키는 값이라 recipe에 박으면 로봇마다 파일이 갈라진다.
#
# 이름 끝 1f/2f는 "로봇이 서 있는 층"이다: 1f = 1층 station 000010, 2f = 상층 station
# 000020(2·3층 공용이라 2f 하나로 받는다). 목표층을 고르는 pioElevatorMove1f/2f의
# 층 표기와 뜻이 다르다.
# station_id는 앞의 0이 살아 있어야 하므로(BC 페이로드에 문자열로 그대로 들어간다)
# 반드시 따옴표로 적는다.
# 아래 두 recipe는 위 TODO를 실제로 걷어낸 것이다: 눈감은 대기 대신 문 열림 입력을
# 보고 끝낸다. 그래서 pioWriteOut 여러 개가 아니라 pioScenario 하나를 쓴다 —
# 조건 분기(if)와 입력 대기(in)가 한 액션 안에 있어야 pairing을 유지한 채 읽는다.
#
# if는 조건 입력을 **한 번만** 읽고 then/else를 가른다. 갈래마다 다시 읽으면 카가
# 내려오는 중일 때 양쪽 다 거짓이 되어 문을 전혀 잡지 않은 채 열림 대기로 넘어간다.
# 조건 입력을 못 읽으면 그 자리에서 실패다(extensions/pio choose_pio_branch) —
# "못 읽음 = off"로 읽으면 카가 상층인데 1층 문을 열어 빈 승강로를 연다.
#
# timeout_sec가 180인 이유: 최악은 호출 갈래다. pairing 30(pair_timeout_sec) +
# 호출 pulse 15 + 층 이동 45(extension "elevator" elevating_timing_second) +
# 문 열림까지. 예전 60초는 이동 도중 recipe를 죽이고 cleanup을 태웠다.
#
# cleanup은 분기할 수 없으므로 두 갈래의 출력을 **모두** 내린다. 켜지도 않은 점을
# 내리는 것은 무해하고, 안 내리면 버튼을 누른 채로 남는다.
recipe "pioElevatorOpen1f" {
  label               = "Elevator — 1층에서 문 열기(센서 확인)"
  timeout_sec         = 180
  cleanup_timeout_sec = 10
  motion              = false

  step "pioScenario" {
    parameters = {
      stationId = "000010"
      channel   = 250
      scenario = [
        {
          type   = "if"
          signal = "elevator1fCarUpper"
          state  = "on"

          # 카가 상층에 있다 → 1층으로 부른다. 카가 오면서 문이 열린다.
          then = [
            { type = "out", signal = "elevator1f_1f", state = "on" },
            { type = "delay", sec = 15 },
            { type = "out", signal = "elevator1f_1f", state = "off" },
          ]

          # 이미 1층이다 → 문만 연다(otherwise = else, HCL 예약어라 이름이 다르다).
          otherwise = [
            { type = "out", signal = "elevator1fOpen", state = "on" },
            { type = "delay", sec = 1 },
            { type = "out", signal = "elevator1fOpen", state = "off" },
          ]
        },

        # 두 갈래 공통 완료 조건. 안 오면 실패다.
        { type = "in", signal = "elevator1fOpened", state = "on", timeoutSec = 120 },
      ]
    }
    timeout_sec = 170
  }

  cleanup "pioWriteOut" {
    parameters  = { signal = "elevator1fOpen", state = "off" }
    timeout_sec = 5
  }
  cleanup "pioWriteOut" {
    parameters  = { signal = "elevator1f_1f", state = "off" }
    timeout_sec = 5
  }
  cleanup "pioDisconnect" {
    timeout_sec = 5
  }
}

# 1f와 같은 구조다. 다른 것은 station과 신호 이름뿐이다.
# 상층 입력 번호(elevator2fOpened / elevator2fCarLower)는 **실측 전 추정값**이라
# extensions.hcl에 현장 확인 표시와 함께 두었다 — 확인되면 그 두 줄만 고치고 이
# 파일은 그대로 둔다.
recipe "pioElevatorOpen2f" {
  label               = "Elevator — 상층에서 문 열기(센서 확인)"
  timeout_sec         = 180
  cleanup_timeout_sec = 10
  motion              = false

  step "pioScenario" {
    parameters = {
      stationId = "000020"
      channel   = 250
      scenario = [
        {
          type   = "if"
          signal = "elevator2fCarLower"
          state  = "on"

          # 카가 1층에 있다 → 이 층으로 부른다.
          then = [
            { type = "out", signal = "elevator2f_2f", state = "on" },
            { type = "delay", sec = 15 },
            { type = "out", signal = "elevator2f_2f", state = "off" },
          ]

          # 이미 이 층이다 → 문만 연다.
          otherwise = [
            { type = "out", signal = "elevator2fOpen", state = "on" },
            { type = "delay", sec = 1 },
            { type = "out", signal = "elevator2fOpen", state = "off" },
          ]
        },

        { type = "in", signal = "elevator2fOpened", state = "on", timeoutSec = 120 },
      ]
    }
    timeout_sec = 170
  }

  cleanup "pioWriteOut" {
    parameters  = { signal = "elevator2fOpen", state = "off" }
    timeout_sec = 5
  }
  cleanup "pioWriteOut" {
    parameters  = { signal = "elevator2f_2f", state = "off" }
    timeout_sec = 5
  }
  cleanup "pioDisconnect" {
    timeout_sec = 5
  }
}


recipe "pioElevatorClose1f" {
  label               = "Elevator — 1층에서 문 닫기"
  timeout_sec         = 60
  cleanup_timeout_sec = 10
  motion              = false

  step "pioInit" {
    parameters = { stationId = "000010", channel = 250 }
  }

  step "pioWriteOut" {
    parameters = { signal = "elevator1fClose", state = "on" }
    delay_sec  = 1
  }
  step "pioWriteOut" {
    parameters = { signal = "elevator1fClose", state = "off" }
    delay_sec  = 7
  }

  cleanup "pioWriteOut" {
    parameters  = { signal = "elevator1fClose", state = "off" }
    timeout_sec = 5
  }
  cleanup "pioDisconnect" {
    timeout_sec = 5
  }
}

recipe "pioElevatorClose2f" {
  label               = "Elevator — 상층에서 문 닫기"
  timeout_sec         = 60
  cleanup_timeout_sec = 10
  motion              = false

  step "pioInit" {
    parameters = { stationId = "000020", channel = 250 }
  }

  step "pioWriteOut" {
    parameters = { signal = "elevator2fClose", state = "on" }
    delay_sec  = 1
  }
  step "pioWriteOut" {
    parameters = { signal = "elevator2fClose", state = "off" }
    delay_sec  = 7
  }

  cleanup "pioWriteOut" {
    parameters  = { signal = "elevator2fClose", state = "off" }
    timeout_sec = 5
  }
  cleanup "pioDisconnect" {
    timeout_sec = 5
  }
}

# 층 호출선은 station마다 다른 자리에 있다: 1층에서는 out1(1층 호출)/out2(상층 호출),
# 상층에서는 out3(1층 호출)/out4(상층 호출)다. 그래서 recipe는 out 번호를 적지 않고
# elevator{출발}_{도착} 이름만 적는다 — 번호 풀이는 extension "pio"에서 끝난다.
# 호출하는 쪽에서 층 파라미터를 조립하지 않도록 구간마다 별도 recipe로 노출한다.
# 층 요청도 문 버튼처럼 momentary pulse이며 cleanup에서 반드시 출력을 내린다.
#
# 이름은 출발층-도착층이다. 앞의 층이 곧 로봇이 붙어 있는 station이라, 문 recipe처럼
# pioInit에 stationId를 박을 수 있다. 목표층만 담던 예전 이름(pioElevatorMove1f/2f)은
# pairing 상대를 정하지 못해 pioInit이 비어 있었고, 그래서 extension "pio"의
# station_id로 폴백했다.
#
# 출발층 2개 × 도착층 2개 = 4개다. 같은 층끼리인 1f-1f/2f-2f는 "서 있는 층으로
# 카를 부른다"는 뜻이라 타기 전에 쓰고, 1f-2f/2f-1f는 타고 나서 목적층을 누르는 것이다.
#
# 이름의 층이 어긋나기 쉬우니 주의: station은 **앞**(출발층)을 따르고, 신호 이름은
# elevator{앞}_{뒤}다. 한 칸 밀려도 recipe는 출력만 내리고 조용히 FINISHED로
# 끝나므로 사람이 눈으로 잡을 수 없다 — tests/test_recipes_config.py가 이 짝을 검사한다.
recipe "pioElevatorMove1f-1f" {
  label               = "Elevator — 1층에서 1층 호출"
  timeout_sec         = 60
  cleanup_timeout_sec = 10
  motion              = false

  step "pioInit" {
    parameters = { stationId = "000010", channel = 250 }
  }

  step "pioWriteOut" {
    parameters = { signal = "elevator1f_1f", state = "on" }
    delay_sec  = 15
  }
  step "pioWriteOut" {
    parameters = { signal = "elevator1f_1f", state = "off" }
    delay_sec  = 1
  }

  cleanup "pioWriteOut" {
    parameters  = { signal = "elevator1f_1f", state = "off" }
    timeout_sec = 5
  }
  cleanup "pioDisconnect" {
    timeout_sec = 5
  }
}

recipe "pioElevatorMove1f-2f" {
  label               = "Elevator — 1층에서 2층 호출"
  timeout_sec         = 60
  cleanup_timeout_sec = 10
  motion              = false

  step "pioInit" {
    parameters = { stationId = "000010", channel = 250 }
  }

  step "pioWriteOut" {
    parameters = { signal = "elevator1f_2f", state = "on" }
    delay_sec  = 15
  }
  step "pioWriteOut" {
    parameters = { signal = "elevator1f_2f", state = "off" }
    delay_sec  = 1
  }

  cleanup "pioWriteOut" {
    parameters  = { signal = "elevator1f_2f", state = "off" }
    timeout_sec = 5
  }
  cleanup "pioDisconnect" {
    timeout_sec = 5
  }
}

recipe "pioElevatorMove2f-1f" {
  label               = "Elevator — 상층에서 1층 호출"
  timeout_sec         = 60
  cleanup_timeout_sec = 10
  motion              = false

  step "pioInit" {
    parameters = { stationId = "000020", channel = 250 }
  }

  step "pioWriteOut" {
    parameters = { signal = "elevator2f_1f", state = "on" }
    delay_sec  = 15
  }
  step "pioWriteOut" {
    parameters = { signal = "elevator2f_1f", state = "off" }
    delay_sec  = 1
  }

  cleanup "pioWriteOut" {
    parameters  = { signal = "elevator2f_1f", state = "off" }
    timeout_sec = 5
  }
  cleanup "pioDisconnect" {
    timeout_sec = 5
  }
}

recipe "pioElevatorMove2f-2f" {
  label               = "Elevator — 상층에서 상층 호출"
  timeout_sec         = 60
  cleanup_timeout_sec = 10
  motion              = false

  step "pioInit" {
    parameters = { stationId = "000020", channel = 250 }
  }

  step "pioWriteOut" {
    parameters = { signal = "elevator2f_2f", state = "on" }
    delay_sec  = 15
  }
  step "pioWriteOut" {
    parameters = { signal = "elevator2f_2f", state = "off" }
    delay_sec  = 1
  }

  cleanup "pioWriteOut" {
    parameters = { signal = "elevator2f_2f", state = "off" }
    timeout_sec = 5
  }
  cleanup "pioDisconnect" {
    timeout_sec = 5
  }
}

# --- 엘리베이터 단계별 단위 시험 --------------------------------------------
#
# elevatorUp/Down은 주행(manualMove)과 switchMap이 함께 묶여 있어 한 단계만
# 떼어 볼 수 없다. 아래 셋은 상태 머신 한 단계씩만 돌려 "열고 / 타고 / 닫고"를
# 따로 확인하기 위한 것이다. motion = false — 주행이 없다.
#
# utils/elevator.py:10-12 기준으로 각 단계가 하는 일:
#   elevatorEnter  ENTER   pairing → 층 호출 → 문 열림 요청까지
#   elevatorInside INSIDE  문 닫힘 요청 → 목표층 이동 → unpair → 대기 → 재pairing
#   elevatorPassed PASSED  문 닫힘 요청 → unpair
#
# station은 recipe마다 고정하지 않고 ${var.station}으로 남긴다. 어느 층에서
# 시험하느냐에 따라 달라지므로 액션 파라미터로 넘긴다 (1층 000010, 상층 000020).
# 빠지면 "missing recipe parameter: station"으로 실패한다.
#
# 단위 시험이라 cleanup에서 pioDisconnect로 반드시 설비를 놓아준다. 중간에
# 실패해도 pairing을 쥔 채 끝나지 않는다.

recipe "elevatorStepEnter" {
  label               = "Elevator 단위 — 호출·문 열기"
  timeout_sec         = 120
  cleanup_timeout_sec = 10
  motion              = false

  step "elevatorEnter" {
    parameters = { station = var.station }
  }

  cleanup "pioDisconnect" { timeout_sec = 5 }
}

recipe "elevatorStepInside" {
  label               = "Elevator 단위 — 타기(층 이동)"
  timeout_sec         = 300
  cleanup_timeout_sec = 10
  motion              = false

  step "elevatorInside" {
    parameters = { station = var.station }
  }

  cleanup "pioDisconnect" { timeout_sec = 5 }
}

recipe "elevatorStepPassed" {
  label               = "Elevator 단위 — 문 닫고 해제"
  timeout_sec         = 120
  cleanup_timeout_sec = 10
  motion              = false

  step "elevatorPassed" {
    parameters = { station = var.station }
  }

  cleanup "pioDisconnect" { timeout_sec = 5 }
}



# --- 에어샤워 통과 (FMS가 주행과 엮는 4단계) --------------------------------
#
# 아래 airShower3l-4l/airShowerPassage는 주행까지 recipe가 들고 있다. 이쪽 네 개는
# 주행을 FMS에 맡기고 PIO만 담당한다 — FMS가 edge action으로 순서대로 걸고, 이동은
# order가 시킨다. 방향마다 네 단계다:
#
#   edge 진입 (에어샤워 앞 -> 안)  OpenIn   HARD  정차 후 들어갈 문 열고 열림 확인
#                                  CloseIn  HARD  들어온 뒤 그 문 요청 해제
#   edge 진출 (안 -> 에어샤워 밖)  OpenOut  HARD  정차 후 나갈 문 열고 열림 확인
#                                  CloseOut HARD  나온 뒤 요청 해제 + 링크 해제
#
# HARD는 차를 먼저 세우고 실행한다. edge action은 그 엣지를 **주행하기 전에** 돈다
# (adapter_jibot.py _process_v3_order_step) — 그래서 문을 연 다음에 통과한다.
# node에 걸면 도착한 **뒤에** 도므로 나갈 문이 한 박자 늦는다.
#
# 깜박임(blink)은 걷어냈다. 설비가 문 열림 요청을 반복 신호로 읽는 줄 알고 blink
# 단계를 넣었는데, 현장 시험에서 센서에 유지력이 있어 한 번 켜 두면 통과하는 동안
# 열려 있는 것이 확인됐다(2026-08-21). 그래서 out을 켜 둔 채 통과하고 Close*에서
# 내린다 — 레벨 유지지 pulse가 아니다. blink 단계 자체는 extensions/pio에 남아 있고
# tests/test_pio_scenario_blink.py가 지킨다. 다른 설비가 반복 신호를 요구하면 다시
# 쓸 수 있다.
#
# pairing은 OpenIn 하나가 걸고(pair = true) 네 단계가 이어 쓰다가 CloseOut이
# 놓는다(disconnect = true → SELECT 토글 unpair + 포트 close). 중간 세 개가
# pair = false인 것은 다시 걸면 SELECT를 올렸다 내리고 BC를 새로 보내는 데
# 0.7~2.7초가 들기 때문이다. 대신 넷을 다 갖고 있어야 성립한다 — 진출 절반만
# 걸리거나 오더가 중간에 깨지면 OpenOut이 pairing 없이 출력만 내보내고, 출력은
# 나가므로 recipe는 FINISHED로 끝난다. 실패로 보이지 않는 것이 이 고장의 성질이다
# (2026-08-20 현장 사례). stationId/channel은 넷 다 갖고 있으므로, 그런 운용이
# 생기면 해당 recipe의 pair를 true로 바꾸면 된다.
# 절차 도중에 오더가 깨져 pairing이나 문 출력이 남으면 airShowerRelease로 놓는다.
#
# cleanup은 두지 않는다. cleanup은 성공·실패와 무관하게 항상 도는데, 여기서 문
# 출력을 내리면 성공했을 때도 내려가 통과 중에 문이 닫힌다. 대신 실패 경로에서는
# 출력과 pairing이 남는다 — 그 복구가 airShowerRelease다.
#
# 입출력 번호:
#   문 열기 출력  airShower3lOpen = PIO out1, airShower4lOpen = PIO out2
#                 (extension "pio" output_signals. 숫자는 거기서만 고친다)
#   문 열림 확인  doorSensor3l = PIO in1, doorSensor4l = PIO in2 ← **현장 확인 필요**
#                 (extension "pio" input_signals. 숫자는 거기서만 고친다)
#                 출력이 out1/out2라 입력도 대칭이라고 본 값이다. 이 확인이 틀린 핀을
#                 보면 문이 열려도 60초 timeout으로 FAILED이고, HARD라 오더가 선다.

recipe "airShower3l-4lOpenIn" {
  label               = "Air shower 3L→4L ① 3L 문 열기 (HARD)"
  timeout_sec         = 90
  cleanup_timeout_sec = 10
  motion              = false

  step "pioScenario" {
    parameters = {
      stationId  = "000030"
      channel    = 250
      pair       = true
      disconnect = false
      scenario = [
        { type = "out", signal = "airShower3lOpen", state = "on" },
        { type = "delay", sec = 1 },
        { type = "in", signal = "doorSensor3l", state = "on", timeoutSec = 60 },
      ]
    }
    timeout_sec = 90
  }
}

recipe "airShower3l-4lCloseIn" {
  label               = "Air shower 3L→4L ② 3L 문 요청 해제 (HARD)"
  timeout_sec         = 90
  cleanup_timeout_sec = 10
  motion              = false

  step "pioScenario" {
    parameters = {
      stationId  = "000030"
      channel    = 250
      pair       = false
      disconnect = true
      scenario = [
        { type = "out", signal = "airShower3lOpen", state = "off" },
        { type = "out", signal = "airShower4lOpen", state = "off" },
        { type = "delay", sec = 1 },
      ]
    }
    timeout_sec = 90
  }
}

recipe "airShower3l-4lOpenOut" {
  label               = "Air shower 3L→4L ③ 4L 문 열기 (HARD)"
  timeout_sec         = 90
  cleanup_timeout_sec = 10
  motion              = false

  step "pioScenario" {
    parameters = {
      stationId  = "000030"
      channel    = 250
      pair       = true
      disconnect = false
      scenario = [
        { type = "out", signal = "airShower4lOpen", state = "on" },
        { type = "delay", sec = 1 },
        { type = "in", signal = "doorSensor4l", state = "on", timeoutSec = 60 },
      ]
    }
    timeout_sec = 90
  }
}

recipe "airShower3l-4lCloseOut" {
  label               = "Air shower 3L→4L ④ 4L 문 요청 해제 + 링크 해제 (HARD)"
  timeout_sec         = 90
  cleanup_timeout_sec = 10
  motion              = false

  step "pioScenario" {
    parameters = {
      stationId  = "000030"
      channel    = 250
      pair       = false
      disconnect = true
      scenario = [
        { type = "out", signal = "airShower4lOpen", state = "off" },
      ]
    }
    timeout_sec = 90
  }
}


# 반대 방향. 문과 확인 입력만 뒤바뀐다: 들어갈 때 4L 문(out2/in2), 나갈 때 3L
# 문(out1/in1). pairing 규칙과 단계 구성은 위와 같다.

recipe "airShower4l-3lOpenIn" {
  label               = "Air shower 4L→3L ① 4L 문 열기 (HARD)"
  timeout_sec         = 90
  cleanup_timeout_sec = 10
  motion              = false

  step "pioScenario" {
    parameters = {
      stationId  = "000030"
      channel    = 250
      pair       = true
      disconnect = false
      scenario = [
        { type = "out", signal = "airShower4lOpen", state = "on" },
        { type = "delay", sec = 1 },
        { type = "in", signal = "doorSensor4l", state = "on", timeoutSec = 60 },
      ]
    }
    timeout_sec = 90
  }
}

recipe "airShower4l-3lCloseIn" {
  label               = "Air shower 4L→3L ② 4L 문 요청 해제 (HARD)"
  timeout_sec         = 90
  cleanup_timeout_sec = 10
  motion              = false

  step "pioScenario" {
    parameters = {
      stationId  = "000030"
      channel    = 250
      pair       = false
      disconnect = true
      scenario = [
        { type = "out", signal = "airShower3lOpen", state = "off" },
        { type = "out", signal = "airShower4lOpen", state = "off" },
        { type = "delay", sec = 1 },
      ]
    }
    timeout_sec = 90
  }
}

recipe "airShower4l-3lOpenOut" {
  label               = "Air shower 4L→3L ③ 3L 문 열기 (HARD)"
  timeout_sec         = 90
  cleanup_timeout_sec = 10
  motion              = false

  step "pioScenario" {
    parameters = {
      stationId  = "000030"
      channel    = 250
      pair       = true
      disconnect = false
      scenario = [
        { type = "out", signal = "airShower3lOpen", state = "on" },
        { type = "delay", sec = 1 },
        { type = "in", signal = "doorSensor3l", state = "on", timeoutSec = 60 },
      ]
    }
    timeout_sec = 90
  }
}

recipe "airShower4l-3lCloseOut" {
  label               = "Air shower 4L→3L ④ 3L 문 요청 해제 + 링크 해제 (HARD)"
  timeout_sec         = 90
  cleanup_timeout_sec = 10
  motion              = false

  step "pioScenario" {
    parameters = {
      stationId  = "000030"
      channel    = 250
      pair       = false
      disconnect = true
      scenario = [
        { type = "out", signal = "airShower3lOpen", state = "off" },
      ]
    }
    timeout_sec = 90
  }
}


recipe "airShowerRelease" {
  label               = "Air shower — 문 내리고 pairing 해제"
  timeout_sec         = 30
  cleanup_timeout_sec = 10
  motion              = false

  step "pioWriteOut" {
    parameters = { signal = "airShower3lOpen", state = "off" }
  }
  step "pioWriteOut" {
    parameters = { signal = "airShower4lOpen", state = "off" }
  }
  step "pioDisconnect" { timeout_sec = 5 }
}


# --- 설비 통과 절차 ---------------------------------------------------------
#
# AIR SHOWER 통과

recipe "airShower3l-4l" {
  label               = "Air shower 3L -> 4L — 통과"
  timeout_sec         = 300
  cleanup_timeout_sec = 10
  motion              = true

  step "airShowerEnter" {
    parameters = { doorPin = 0 }
  }
  
  # step "manualMove" {
  #   parameters = {
  #     distance = var.enterDistanceMm
  #     speed    = var.moveSpeed
  #   }
  #   timeout_sec = 60
  # }

  step "airShowerInside" {
    parameters = { doorPin = 1 }
  }
  step "manualMove" {
    parameters = {
      distance = var.exitDistanceMm
      speed    = var.moveSpeed
    }
    timeout_sec = 60
  }
  step "airShowerPassed" {
    parameters = { doorPin = 1 }
  }

  cleanup "manualStop" { timeout_sec = 5 }
  cleanup "pioDisconnect" { timeout_sec = 5 }
}




# --- 설비 통과 절차 ---------------------------------------------------------
#
# 위 문 recipe와 달리 이쪽은 주행을 포함한 전체 절차다. 조건 대기(문이 실제로
# 열렸는지, 층에 도착했는지)는 airShowerEnter/elevatorEnter 같은 상태 머신
# extension이 자기 안에서 처리한다 — recipe는 그것들을 순서대로 엮기만 한다.
#
# 핀·station ID는 extensions.hcl에서 확인된 이 현장 값을 리터럴로 고정했다:
#   에어샤워 문: entry=0, exit=1
#   1층:  station=000010   상층: station=000020
#   floor pin은 extension이 extensions.hcl의 motion rule에서 끌어온다 —
#   여기에 옮겨 적으면 두 곳이 어긋난다.
#
# 반면 이동 거리·속도와 map ID는 ${var.X}로 남겨 ACS가 넘기게 한다. 저장소에
# 측정된 안전값이 없어서다 — 현장 측정 없이 임의값을 고정하지 않는다. 따라서
# 이 세 recipe를 부르려면 액션 파라미터가 반드시 따라와야 한다:
#   airShowerPassage: enterDistanceMm, exitDistanceMm, moveSpeed
#   elevatorUp/Down:  enterDistanceMm, exitDistanceMm, moveSpeed, targetMapId
# 빠지면 그 step에서 "missing recipe parameter: <이름>"으로 실패한다.
#
# motion = true라 WebUI 확인 키 게이트가 걸린다.

recipe "airShowerPassage" {
  label               = "Air shower — 통과"
  timeout_sec         = 300
  cleanup_timeout_sec = 10
  motion              = true

  step "airShowerEnter" {
    parameters = { doorPin = 0 }
  }
  step "manualMove" {
    parameters = {
      distance = var.enterDistanceMm
      speed    = var.moveSpeed
    }
    timeout_sec = 60
  }
  step "airShowerInside" {
    parameters = { doorPin = 1 }
  }
  step "manualMove" {
    parameters = {
      distance = var.exitDistanceMm
      speed    = var.moveSpeed
    }
    timeout_sec = 60
  }
  step "airShowerPassed" {
    parameters = { doorPin = 1 }
  }

  cleanup "manualStop" { timeout_sec = 5 }
  cleanup "pioDisconnect" { timeout_sec = 5 }
}

recipe "elevatorUp" {
  label               = "Elevator — 1층에서 상층으로"
  timeout_sec         = 600
  cleanup_timeout_sec = 10
  motion              = true

  step "elevatorEnter" {
    parameters = { station = "000010" }
  }
  step "manualMove" {
    parameters = {
      distance = var.enterDistanceMm
      speed    = var.moveSpeed
    }
    timeout_sec = 60
  }
  step "elevatorInside" {
    parameters = { station = "000020" }
  }
  step "switchMap" {
    parameters  = { mapId = var.targetMapId }
    timeout_sec = 45
  }
  step "manualMove" {
    parameters = {
      distance = var.exitDistanceMm
      speed    = var.moveSpeed
    }
    timeout_sec = 60
  }
  step "elevatorPassed" {
    parameters = { station = "000020" }
  }

  cleanup "manualStop" { timeout_sec = 5 }
  cleanup "pioDisconnect" { timeout_sec = 5 }
}

recipe "elevatorDown" {
  label               = "Elevator — 상층에서 1층으로"
  timeout_sec         = 600
  cleanup_timeout_sec = 10
  motion              = true

  step "elevatorEnter" {
    parameters = { station = "000020" }
  }
  step "manualMove" {
    parameters = {
      distance = var.enterDistanceMm
      speed    = var.moveSpeed
    }
    timeout_sec = 60
  }
  step "elevatorInside" {
    parameters = { station = "000010" }
  }
  step "switchMap" {
    parameters  = { mapId = var.targetMapId }
    timeout_sec = 45
  }
  step "manualMove" {
    parameters = {
      distance = var.exitDistanceMm
      speed    = var.moveSpeed
    }
    timeout_sec = 60
  }
  step "elevatorPassed" {
    parameters = { station = "000010" }
  }

  cleanup "manualStop" { timeout_sec = 5 }
  cleanup "pioDisconnect" { timeout_sec = 5 }
}




# --- 재위치(localization) ---------------------------------------------------
#
# 로봇을 UmLocalize 로 재위치한다. step "localize"는 core/action_bridge.py 가
# 등록한 composable action이고, 공통 contract _vehicle.localize()를 부른다 —
# JIBOT은 UmLocalize, SEER는 relocation(2002)로 자동 분기하므로 client가 바뀌어도
# 이 recipe는 그대로다. (값·의미는 client = jibot 기준으로 잡았다.)
#
# node = "..." 는 맵의 노드 이름이다. 핸들러가 로봇 맵에서 그 노드의 절대 pose 를 읽어
# 앵커로 쓴다(adapter_jibot.py _handle_localize_instant_action). 좌표를 여기 박지 않으므로
# 맵을 다시 뜨면 값이 따라오고, 로봇의 '추정' 위치에도 의존하지 않는다 — 엘리베이터를
# 타고 난 직후처럼 추정이 못 믿을 때가 이 recipe 가 필요한 바로 그 순간이다.
#
# Goal/Dock 이 먼저고 PathPoint 가 그다음이다. Goal/Dock 만 heading 을 갖는다.
# PathPoint 는 위치만 준다 — 이 사이트 맵의 PathPoint theta 는 44개 전부 0.00 이라
# heading 을 조용히 0 으로 만들어 버리므로, PathPoint 를 쓰면 theta 를 반드시 적어야 한다
# (안 적으면 FAILED). 카 안 자리는 Goal "2_01" 과 PathPoint "p2" 가 좌표까지 같아서
# (16377 1666) 어느 쪽을 써도 결과가 같다. FMS 주행 그래프가 PathPoint 기준이라
# (settings.nearest_node_mode) p2 가 익숙하면 p2 로 적어도 된다.
#
# theta 는 도(°) 단위이고 node 의 맵 heading 을 덮어쓴다. 카 안 노드 2_01 의 맵 theta 는
# 0.00 이지만 실제 heading 은 진행 방향에 달렸다 — 좌표상 상행이 +90, 하행이 -90 이라
# (1_05 -> 2_01 = +89°, 2_01 -> 1_05 = -91°) 1층 도착은 -90, 상층 도착은 +90 이다.
# mapId 를 비우면 현재 맵을 유지한다. 주행이 없어 motion=false 다(WebUI 확인 키 게이트
# 미적용). FMS 가 이름만으로 부르는 자동 실행이라 pose 파라미터를 넘길 필요가 없다.
#
# 주의: node 는 "재위치를 실행하는 시점에 로봇이 실제로 서 있는 자리"여야 한다. 여기서는
# 엘리베이터 카 안 노드(2_01, extensions.hcl elevator motion_rules 의 inside 노드)를
# 가정했다. 카에서 내린 뒤에 부른다면 1_05(1층)/3_01(상층)로 바꿔야 한다 — 틀린 노드는
# 로봇의 추정 위치를 1.5m 씩 옮겨 놓는다.
recipe "localization1f" {
  label               = "재위치 — elevator로 1층 도착(2_01 기준, heading -90)"
  timeout_sec         = 60
  cleanup_timeout_sec = 10
  motion              = false

  step "localize" {
    parameters = { node = "p2", theta = -90 }
  }
}

recipe "localization2f" {
  label               = "재위치 — elevator로 상층 도착(2_01 기준, heading +90)"
  timeout_sec         = 60
  cleanup_timeout_sec = 10
  motion              = false

  step "localize" {
    parameters = { node = "p2", theta = 90 }
  }
}
