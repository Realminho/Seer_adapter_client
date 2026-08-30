# ----------------------- Ezi Motor 통신 클래스 import -----------------------
from custom_package.motor_commu import Ezi_Motor_Commu


# ----------------------- 모터 드라이버 접속 정보 선언 -----------------------
# Ezi Motor 드라이버 IP 주소 선언
MOTOR_IP = "192.168.0.14"

# Ezi-SERVOⅡ Plus-E ALL 사용자 Library용 UDP Port 선언
MOTOR_PORT = 3002

# 응답 대기 Timeout 시간 선언
TIMEOUT_SEC = 50


# ----------------------- S/W Limit 이동 조건 선언 -----------------------
# S/W Limit 이동 속도 선언
# 처음 테스트는 너무 빠르지 않게 300~1000 정도 추천
SW_LIMIT_MOVE_SPEED = 7000

# S/W Limit 감지 대기 Timeout 선언
SW_LIMIT_TIMEOUT_SEC = 60.0

# +S/W Limit 도착 후 -S/W Limit 이동 전 대기 시간
LIMIT_DELAY_SEC = 1.0


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

        # 테스트 전 Axis Status 출력
        print("\n========== 테스트 전 Axis Status ==========")
        motor.print_axis_summary()
        motor.print_true_axis_flags()

        # 테스트 전 위치 출력
        motor.print_position_all("테스트 전 위치")

        # Servo ON 준비
        if not motor.ready_servo_on(timeout_sec=3.0):
            return

        # Servo ON 후 Axis Status 출력
        print("\n========== Servo ON 후 Axis Status ==========")
        motor.print_axis_summary()
        motor.print_true_axis_flags()

        # ----------------------- +S/W Limit 방향 이동 -----------------------
        print("\n========== +S/W Limit 이동 시작 ==========")

        # +S/W Limit까지 이동
        plus_sw_limit_ok = motor.goto_sw_limit_plus(
            speed=SW_LIMIT_MOVE_SPEED,
            timeout_sec=SW_LIMIT_TIMEOUT_SEC,
            check_interval=0.05
        )

        # +S/W Limit 이동 실패 시
        if not plus_sw_limit_ok:
            print("[TEST] +S/W Limit 이동 실패 또는 Timeout")
            return

        # +S/W Limit 도착 후 상태 출력
        print("\n========== +S/W Limit 도착 후 Axis Status ==========")
        motor.print_axis_summary()
        motor.print_true_axis_flags()

        # +S/W Limit 도착 후 위치 출력
        motor.print_position_all("+S/W Limit 도착 후 위치")

        # 잠시 대기
        print(f"\n[TEST] {LIMIT_DELAY_SEC}초 대기 후 -S/W Limit으로 이동합니다.")
        import time
        time.sleep(LIMIT_DELAY_SEC)

        # ----------------------- -S/W Limit 방향 이동 -----------------------
        print("\n========== -S/W Limit 이동 시작 ==========")

        # -S/W Limit까지 이동
        minus_sw_limit_ok = motor.goto_sw_limit_minus(
            speed=SW_LIMIT_MOVE_SPEED,
            timeout_sec=SW_LIMIT_TIMEOUT_SEC,
            check_interval=0.05
        )

        # -S/W Limit 이동 실패 시
        if not minus_sw_limit_ok:
            print("[TEST] -S/W Limit 이동 실패 또는 Timeout")
            return

        # -S/W Limit 도착 후 상태 출력
        print("\n========== -S/W Limit 도착 후 Axis Status ==========")
        motor.print_axis_summary()
        motor.print_true_axis_flags()

        # -S/W Limit 도착 후 위치 출력
        motor.print_position_all("-S/W Limit 도착 후 위치")

        # 테스트 완료 출력
        print("\n========== S/W Limit 왕복 이동 테스트 완료 ==========")

    except KeyboardInterrupt:
        # 사용자 중단 출력
        print("\n[TEST] 사용자가 중단했습니다.")

    except Exception as error:
        # 예외 출력
        print(f"\n[TEST] 예외 발생 : {error}")

    finally:
        # 안전 정지 출력
        print("\n========== 안전 정지 ==========")

        try:
            # 일반 정지 명령
            print("Move Stop :", motor.move_stop())
        except Exception as error:
            # 실패 출력
            print("Move Stop 실패 :", error)

        # Servo OFF 출력
        print("\n========== Servo OFF ==========")

        try:
            # Servo OFF 명령
            print("Servo OFF :", motor.servo_off())
        except Exception as error:
            # 실패 출력
            print("Servo OFF 실패 :", error)

        # 모터 드라이버 연결 종료
        motor.motor_close()

        # 종료 출력
        print("\n========== S/W Limit Move Test Finished ==========")


# ----------------------- Python 파일 직접 실행 시 main 함수 실행 -----------------------
if __name__ == "__main__":
    main()