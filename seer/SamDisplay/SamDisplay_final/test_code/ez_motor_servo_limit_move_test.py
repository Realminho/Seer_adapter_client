# ----------------------- 범용 라이브러리 import -----------------------
# 시간 처리를 위한 time 라이브러리 import
import time
# ----------------------- Ezi Motor 통신 클래스 import -----------------------
from custom_package.motor_commu import Ezi_Motor_Commu

# ----------------------- 모터 드라이버 접속 정보 선언 -----------------------
# Ezi Motor 드라이버 IP 주소 선언
MOTOR_IP = "192.168.0.14"
# Ezi-SERVOⅡ Plus-E ALL 사용자 Library용 UDP Port 선언
MOTOR_PORT = 3002
# 응답 대기 Timeout 시간 선언
TIMEOUT_SEC = 2.0
# ----------------------- Limit 이동 조건 선언 -----------------------
# Limit 방향 이동 속도 선언
LIMIT_MOVE_SPEED = 7000*2
# +Limit 감지 대기 Timeout 선언
PLUS_LIMIT_TIMEOUT_SEC = 300
# -Limit 감지 대기 Timeout 선언
MINUS_LIMIT_TIMEOUT_SEC = 300
# Limit 상태 확인 주기 선언
CHECK_INTERVAL_SEC = 0.05


# ----------------------- +Limit 감지까지 대기 함수 선언 -----------------------
def wait_plus_limit_on(motor, timeout_sec=60.0, check_interval=0.05):
    # 시작 시간 저장
    start_time = time.time()
    # Timeout까지 반복
    while True:
        # Axis Status 읽기
        axis_status, axis_status_bits = motor.get_axis_status()

        # +Limit 감지 여부 확인
        plus_limit_on = axis_status["FFLAG_HWPOSILMT"]

        # -Limit 감지 여부 확인
        minus_limit_on = axis_status["FFLAG_HWNEGALMT"]

        # 이동 중 여부 확인
        motioning = axis_status["FFLAG_MOTIONING"]

        # 현재 상태 출력
        print(
            "+Limit 대기:",
            f"0x{axis_status_bits:08X}",
            "PLUS_LIMIT:", plus_limit_on,
            "MINUS_LIMIT:", minus_limit_on,
            "MOTIONING:", motioning
        )

        # +Limit이 감지되면
        if plus_limit_on:
            # True return
            return True

        # Timeout 시간이 지나면
        if time.time() - start_time >= timeout_sec:
            # Timeout 출력
            print("[TEST] +Limit 감지 Timeout")

            # False return
            return False

        # 지정 시간 대기
        time.sleep(check_interval)


# ----------------------- -Limit 감지까지 대기 함수 선언 -----------------------
def wait_minus_limit_on(motor, timeout_sec=60.0, check_interval=0.05):
    # 시작 시간 저장
    start_time = time.time()

    # Timeout까지 반복
    while True:
        # Axis Status 읽기
        axis_status, axis_status_bits = motor.get_axis_status()
        # +Limit 감지 여부 확인
        plus_limit_on = axis_status["FFLAG_HWPOSILMT"]
        # -Limit 감지 여부 확인
        minus_limit_on = axis_status["FFLAG_HWNEGALMT"]
        # 이동 중 여부 확인
        motioning = axis_status["FFLAG_MOTIONING"]
        # 현재 상태 출력
        print(
            "-Limit 대기:",
            f"0x{axis_status_bits:08X}",
            "PLUS_LIMIT:", plus_limit_on,
            "MINUS_LIMIT:", minus_limit_on,
            "MOTIONING:", motioning
        )
        # -Limit이 감지되면
        if minus_limit_on:
            # True return
            return True
        # Timeout 시간이 지나면
        if time.time() - start_time >= timeout_sec:
            # Timeout 출력
            print("[TEST] -Limit 감지 Timeout")
            # False return
            return False
        # 지정 시간 대기
        time.sleep(check_interval)


# ----------------------- Axis Status 주요 상태 출력 함수 선언 -----------------------
def print_axis_summary(motor, title):
    # 제목 출력
    print(f"\n========== {title} ==========")
    # Axis Status 읽기
    axis_status, axis_status_bits = motor.get_axis_status()
    # 주요 상태 출력
    print("Status Hex :", f"0x{axis_status_bits:08X}")
    print("ERRORALL   :", axis_status["FFLAG_ERRORALL"])
    print("SERVOON    :", axis_status["FFLAG_SERVOON"])
    print("INPOSITION :", axis_status["FFLAG_INPOSITION"])
    print("MOTIONING  :", axis_status["FFLAG_MOTIONING"])
    print("HW +LIMIT  :", axis_status["FFLAG_HWPOSILMT"])
    print("HW -LIMIT  :", axis_status["FFLAG_HWNEGALMT"])


# ----------------------- 현재 위치 출력 함수 선언 -----------------------
def print_position(motor, title):
    # 제목 출력
    print(f"\n========== {title} ==========")

    # 현재 위치 읽기
    actual_position = motor.get_actual_position()

    # Command 위치 읽기
    command_position = motor.get_command_position()

    # 위치 오차 읽기
    position_error = motor.get_position_error()

    # 실제 속도 읽기
    actual_velocity = motor.get_actual_velocity()

    # 위치 정보 출력
    print("Actual Position :", actual_position)
    print("Command Position:", command_position)
    print("Position Error  :", position_error)
    print("Actual Velocity :", actual_velocity)


# ----------------------- 메인 함수 선언 -----------------------
def main():
    # Ezi Motor 통신 객체 생성
    motor = Ezi_Motor_Commu(
        ip=MOTOR_IP,
        port=MOTOR_PORT,
        timeout=TIMEOUT_SEC
    )

    # 예외 발생 여부와 상관없이 마지막에 정지 및 연결 종료하기 위해 try/finally 사용
    try:
        # 모터 드라이버 연결
        motor.motor_connect()

        # 테스트 전 Axis Status 출력
        print_axis_summary(motor, "테스트 전 Axis Status")

        # 테스트 전 위치 출력
        print_position(motor, "테스트 전 위치")

        # Alarm 상태 확인
        print("\n========== Alarm Type 확인 ==========")

        # Alarm Type 읽기
        alarm_type, alarm_text = motor.get_alarm_type()

        # Alarm 출력
        print("Alarm Type :", alarm_type)
        print("Alarm Text :", alarm_text)

        # 알람이 있으면
        if alarm_type != 0:
            # 테스트 중단
            print("[TEST] 알람 상태이므로 테스트를 중단합니다.")
            return

        # Servo ON
        print("\n========== Servo ON ==========")

        # Servo ON 명령
        print(motor.servo_on())

        # Servo ON 대기
        if not motor.wait_servo_on(timeout_sec=3.0):
            # 실패 출력
            print("[TEST] Servo ON 실패")
            return

        # Servo ON 후 Axis Status 출력
        print_axis_summary(motor, "Servo ON 후 Axis Status")

        # ----------------------- +Limit 방향 이동 -----------------------
        # print("\n========== +Limit 방향 이동 시작 ==========")

        # # +Limit 방향으로 이동 시작
        # print(motor.goto_limit_plus(LIMIT_MOVE_SPEED))

        # # +Limit 감지까지 대기
        # plus_limit_ok = wait_plus_limit_on(
        #     motor=motor,
        #     timeout_sec=PLUS_LIMIT_TIMEOUT_SEC,
        #     check_interval=CHECK_INTERVAL_SEC
        # )

        # # +Limit 감지 후 정지
        # print("\n========== +Limit 감지 후 정지 ==========")
        # print(motor.move_stop())

        # # +Limit 감지 실패 시
        # if not plus_limit_ok:
        #     # 테스트 중단
        #     print("[TEST] +Limit 감지 실패")
        #     return

        # # +Limit 위치 출력
        # print_position(motor, "+Limit 도착 후 위치")

        # # 잠시 대기
        # time.sleep(1.0)

        # ----------------------- -Limit 방향 이동 -----------------------
        print("\n========== -Limit 방향 이동 시작 ==========")

        # -Limit 방향으로 이동 시작
        print(motor.goto_limit_minus(LIMIT_MOVE_SPEED))

        # -Limit 감지까지 대기
        minus_limit_ok = wait_minus_limit_on(
            motor=motor,
            timeout_sec=MINUS_LIMIT_TIMEOUT_SEC,
            check_interval=CHECK_INTERVAL_SEC
        )

        # -Limit 감지 후 정지
        print("\n========== -Limit 감지 후 정지 ==========")
        print(motor.move_stop())

        # -Limit 감지 실패 시
        if not minus_limit_ok:
            # 테스트 중단
            print("[TEST] -Limit 감지 실패")
            return

        # -Limit 위치 출력
        print_position(motor, "-Limit 도착 후 위치")

        # 테스트 완료 출력
        print("\n========== Limit 왕복 테스트 완료 ==========")

    # Ctrl+C로 중단한 경우
    except KeyboardInterrupt:
        # 중단 출력
        print("\n[TEST] 사용자가 테스트를 중단했습니다.")

    # 예외 발생 시
    except Exception as error:
        # 예외 출력
        print(f"\n[TEST] 예외 발생 : {error}")

    # 마지막에 반드시 실행
    finally:
        # 안전 정지 출력
        print("\n========== 안전 정지 ==========")

        # 일반 정지 시도
        try:
            # 일반 정지 명령
            print("Move Stop :", motor.move_stop())
        except Exception as error:
            # 정지 실패 출력
            print("Move Stop 실패 :", error)

        # Servo OFF 출력
        print("\n========== Servo OFF ==========")

        # Servo OFF 시도
        try:
            # Servo OFF 명령
            print("Servo OFF :", motor.servo_off())
        except Exception as error:
            # Servo OFF 실패 출력
            print("Servo OFF 실패 :", error)

        # 모터 드라이버 연결 종료
        motor.motor_close()

        # 종료 출력
        print("\n========== Limit Move Test Finished ==========")


# ----------------------- Python 파일 직접 실행 시 main 함수 실행 -----------------------
if __name__ == "__main__":
    main()