# ----------------------- Ezi Motor 통신 클래스 import -----------------------
from custom_package.motor_commu import Ezi_Motor_Commu
# ----------------------- 모터 드라이버 접속 정보 선언 -----------------------
MOTOR_IP = "192.168.0.14"
MOTOR_PORT = 3002
TIMEOUT_SEC = 2.0
# ----------------------- 테스트 이동 조건 선언 -----------------------
# 1 pulse = 0.00132mm 기준, 약 1cm 이동
TEST_DISTANCE_PULSE = 7576*10
# 이동 속도 pps
TEST_SPEED_PPS = 7000*2
# 이동 완료 대기 시간
MOTION_TIMEOUT_SEC = 20.0
# ----------------------- 메인 함수 선언 -----------------------
def main():
    # Ezi Motor 통신 객체 생성
    motor = Ezi_Motor_Commu(
        ip=MOTOR_IP,
        port=MOTOR_PORT,
        timeout=TIMEOUT_SEC)
    try:
        # 모터 드라이버 연결
        motor.motor_connect()
        # 테스트 전 Axis Status 출력
        print("\n========== 테스트 전 Axis Status ==========")
        motor.print_axis_summary()
        motor.print_true_axis_flags()
        # Servo ON 전 위치 출력
        motor.print_position_all("Servo ON 전 위치")
        # Servo ON 준비
        if not motor.ready_servo_on(timeout_sec=3.0):
            return
        # Servo ON 후 Axis Status 출력
        print("\n========== Servo ON 후 Axis Status ==========")
        motor.print_axis_summary()
        motor.print_true_axis_flags()
        # +방향 이동 후 -방향 복귀 테스트
        if not motor.move_relative_round_trip_test(
            pulse=TEST_DISTANCE_PULSE,
            speed=TEST_SPEED_PPS,
            motion_timeout_sec=MOTION_TIMEOUT_SEC,
            delay_sec=0.5):
            return
        # 테스트 완료 출력
        print("\n========== 테스트 완료 ==========")
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
            # 실패 출력
            print("Move Stop 실패 :", error)
        # Servo OFF
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
        print("\n========== Move Test Finished ==========")

# ----------------------- Python 파일 직접 실행 시 main 함수 실행 -----------------------
if __name__ == "__main__":
    main()