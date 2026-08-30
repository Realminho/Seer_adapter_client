# ----------------------- 범용 라이브러리 import -----------------------
# 시간 처리를 위한 time 라이브러리 import
import time
# ----------------------- Ezi Motor 통신 클래스 import -----------------------
from custom_package.motor_commu import Ezi_Motor_Commu
# ----------------------- 모터 드라이버 접속 정보 선언 -----------------------
MOTOR_IP = "192.168.0.14"
MOTOR_PORT = 3002
TIMEOUT_SEC = 2.0
# ----------------------- + 방향 제어 파라미터 선언 -----------------------
# Table Close 이동 Pulse
# TEST_DISTANCE_PULSE = 184199+12000
TEST_DISTANCE_PULSE = 260000
# 이동 속도 pps
TEST_SPEED_PPS = 7000 * 2
# +방향 이동 완료 대기 시간
MOTION_TIMEOUT_SEC = 30.0
# ----------------------- Limit 이동 조건 선언 -----------------------
# Limit 방향 이동 속도
LIMIT_MOVE_SPEED = 7000 * 2
# -Limit 감지 대기 Timeout
MINUS_LIMIT_TIMEOUT_SEC = 300.0
# Limit 상태 확인 주기
CHECK_INTERVAL_SEC = 0.05
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
            "MOTIONING:", motioning)
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


# ----------------------- 메인 함수 선언 -----------------------
def main():
    # Ezi Motor 통신 객체 생성
    motor = Ezi_Motor_Commu(
        ip=MOTOR_IP,
        port=MOTOR_PORT,
        timeout=TIMEOUT_SEC
    )

    try:
        # 모터 드라이버 연결
        motor.motor_connect()

        # Servo ON 전 위치 출력
        motor.print_position_all("Servo ON 전 위치")

        # Servo ON 준비
        if not motor.ready_servo_on(timeout_sec=30.0):
            return

        # Servo ON 후 상태 출력
        print("\n========== Servo ON 후 Axis Status ==========")
        motor.print_axis_summary()
        motor.print_true_axis_flags()

        # ----------------------- + 방향으로 Table Close -----------------------
        print("\n========== + 방향 Table Close 시작 ==========")

        # + 방향 상대 이동 명령
        close_result = motor.move_inc_position(
            TEST_DISTANCE_PULSE,
            TEST_SPEED_PPS
        )

        # 이동 명령 결과 출력
        print("Table Close 이동 명령 결과 :", close_result)

        # 이동 명령 실패 시
        if close_result != True:
            print("[TEST] Table Close 이동 명령 실패")
            return

        # +방향 이동 완료 대기
        close_done = motor.wait_motion_done_with_log(
            timeout_sec=MOTION_TIMEOUT_SEC,
            check_interval=0.1
        )

        # +방향 이동 완료 실패 시
        if not close_done:
            print("[TEST] Table Close 이동 완료 Timeout")
            return

        # +방향 이동 후 위치 출력
        motor.print_position_all("Table Close 완료 후 위치")

        # 잠시 대기
        time.sleep(1.0)

        # ----------------------- -Limit 방향으로 Table Open -----------------------
        print("\n========== -Limit 방향 Table Open 시작 ==========")

        # - 방향 Limit 이동 시작
        limit_result = motor.goto_limit_minus(LIMIT_MOVE_SPEED)

        # Limit 이동 명령 결과 출력
        print("Goto -Limit 명령 결과 :", limit_result)

        # -Limit 감지까지 대기
        minus_limit_ok = wait_minus_limit_on(
            motor=motor,
            timeout_sec=MINUS_LIMIT_TIMEOUT_SEC,
            check_interval=CHECK_INTERVAL_SEC
        )

        # -Limit 감지 후 정지
        print("\n========== -Limit 감지 후 정지 ==========")
        print("Move Stop :", motor.move_stop())

        # -Limit 감지 실패 시
        if not minus_limit_ok:
            print("[TEST] -Limit 감지 실패")
            return

        # -Limit 위치 출력
        motor.print_position_all("-Limit 도착 후 위치")

        # 테스트 완료 출력
        print("\n========== Table open/close 테스트 완료 ==========")

    except KeyboardInterrupt:
        # 사용자 중단 출력
        print("\n[TEST] 사용자가 중단했습니다.")

    except Exception as error:
        # 예외 출력
        print(f"\n[TEST] 예외 발생 : {error}")

    finally:
        # 안전 정지
        print("\n========== 안전 정지 ==========")

        try:
            # 일반 정지 명령
            print("Move Stop :", motor.move_stop())
        except Exception as error:
            print("Move Stop 실패 :", error)

        # Servo OFF
        print("\n========== Servo OFF ==========")

        try:
            # Servo OFF 명령
            print("Servo OFF :", motor.servo_off())
        except Exception as error:
            print("Servo OFF 실패 :", error)

        # 모터 드라이버 연결 종료
        motor.motor_close()

        # 종료 출력
        print("\n========== Move Test Finished ==========")


# ----------------------- Python 파일 직접 실행 시 main 함수 실행 -----------------------
if __name__ == "__main__":
    main()