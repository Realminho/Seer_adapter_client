# ----------------------- 범용 라이브러리 import -----------------------
# 시간 핸들링을 위해 time 라이브러리 import
import time
# ----------------------- custom 라이브러리 import -----------------------
# SEER AMR 제어를 위해 SEER_commu 클래스 import
from custom_package.seer_commu import SEER_commu
# D435 카메라 기반 ArUco 검출 클래스 import
from side_docking_package.aruco_detector import Aruco_Detector_Commu
# ArUco 정답 저장/로드 클래스 import
from side_docking_package.save_aruco_answer import Aruco_Answer_Commu
# 측면 카메라 기반 도킹 클래스 import
from side_docking_package.aruco_dock import Aruco_Dock_Commu
# Ezi Motor 통신을 위해 Ezi_Motor_Commu 클래스 import
from custom_package.motor_commu import Ezi_Motor_Commu
# =============================================================
# 모터 드라이버 접속 정보 선언
# =============================================================
# 모터 드라이버 IP 주소 선언
MOTOR_IP = "192.168.0.14"
# 모터 드라이버 Port 번호 선언
MOTOR_PORT = 3002
# 모터 통신 Timeout 시간 선언
MOTOR_TIMEOUT_SEC = 2.0
# =============================================================
# Table Open 동작 설정값 선언
# =============================================================
# Table Open 시 -Limit 방향으로 이동할 속도 선언
LIMIT_MOVE_SPEED = 7000 * 2
# -Limit 감지 대기 Timeout 선언
MINUS_LIMIT_TIMEOUT_SEC = 300.0
# -Limit 상태 확인 주기 선언
CHECK_INTERVAL_SEC = 0.05
# =============================================================
# -Limit 감지까지 대기하는 함수 선언
# =============================================================
def wait_minus_limit_on(
        motor,
        timeout_sec=60.0,
        check_interval=0.05):
    # 함수 시작 시간 저장
    start_time = time.time()
    # -Limit이 감지되거나 Timeout이 발생할 때까지 반복
    while True:
        # 모터 Axis Status 읽기
        axis_status, axis_status_bits = motor.get_axis_status()
        # +Limit 감지 상태 확인
        plus_limit_on = axis_status["FFLAG_HWPOSILMT"]
        # -Limit 감지 상태 확인
        minus_limit_on = axis_status["FFLAG_HWNEGALMT"]
        # 현재 모터 이동 중 상태 확인
        motioning = axis_status["FFLAG_MOTIONING"]
        # 현재 모터 상태 출력
        print(
            "[MOTOR] -Limit 대기 :",
            f"0x{axis_status_bits:08X}",
            "PLUS_LIMIT:", plus_limit_on,
            "MINUS_LIMIT:", minus_limit_on,
            "MOTIONING:", motioning
        )
        # -Limit이 감지된 경우
        if minus_limit_on:
            # -Limit 감지 로그 출력
            print("[MOTOR] -Limit 감지 완료")
            # 성공 결과 반환
            return True
        # 지정한 Timeout 시간이 지난 경우
        if time.time() - start_time >= timeout_sec:
            # Timeout 로그 출력
            print("[MOTOR] -Limit 감지 Timeout")
            # 실패 결과 반환
            return False
        # 지정한 주기만큼 대기
        time.sleep(check_interval)

# =============================================================
# Table Open 함수 선언
# =============================================================

def table_open(motor):
    # Table Open 시작 로그 출력
    print("\n========== Table Open 시작 ==========")
    try:
        # 모터 드라이버와 연결
        motor.motor_connect()
        # 연결 후 현재 모터 위치 출력
        motor.print_position_all("Table Open 시작 전 위치")
        # Servo ON 준비 및 모터 에러 상태 정리
        servo_ready = motor.ready_servo_on(timeout_sec=30.0)
        # Servo ON 준비에 실패한 경우
        if not servo_ready:
            # 실패 로그 출력
            print("[MOTOR] Servo ON 준비 실패")
            # 실패 결과 반환
            return False
        # Servo ON 후 Axis 상태 출력
        print("\n========== Servo ON 후 Axis Status ==========")
        # 핵심 Axis 상태 출력
        motor.print_axis_summary()
        # True 상태인 Axis Flag 출력
        motor.print_true_axis_flags()
        # 현재 상태에서 이미 -Limit이 감지되어 있는지 확인
        axis_status, axis_status_bits = motor.get_axis_status()
        # 현재 -Limit 상태 저장
        minus_limit_on = axis_status["FFLAG_HWNEGALMT"]
        # 현재 이미 -Limit이 감지되어 있는 경우
        if minus_limit_on:
            # 이미 Table Open 위치라는 로그 출력
            print("[MOTOR] 현재 이미 -Limit 위치입니다.")
            # 현재 위치 출력
            motor.print_position_all("현재 -Limit 위치")
            # 성공 결과 반환
            return True
        # -Limit 방향 이동 시작 로그 출력
        print("\n========== -Limit 방향 이동 시작 ==========")
        # -Limit 방향으로 연속 이동 명령 전송
        limit_result = motor.goto_limit_minus(LIMIT_MOVE_SPEED)
        # 이동 명령 결과 출력
        print("[MOTOR] Goto -Limit 명령 결과 :", limit_result)
        # 명령 결과가 성공이 아닌 경우
        if limit_result is not True:
            # 명령 실패 로그 출력
            print("[MOTOR] -Limit 이동 명령 실패")
            # 안전 정지 시도
            try:
                motor.move_stop()
            except Exception:
                pass
            # 실패 결과 반환
            return False
        # -Limit 감지까지 대기
        minus_limit_ok = wait_minus_limit_on(
            motor=motor,
            timeout_sec=MINUS_LIMIT_TIMEOUT_SEC,
            check_interval=CHECK_INTERVAL_SEC)
        # -Limit 감지 여부와 관계없이 이동 정지
        print("\n========== Table Open 이동 정지 ==========")
        # 모터 정지 명령 전송
        stop_result = motor.move_stop()
        # 정지 결과 출력
        print("[MOTOR] Move Stop :", stop_result)
        # -Limit 감지에 실패한 경우
        if not minus_limit_ok:
            # Table Open 실패 로그 출력
            print("[MOTOR] Table Open 실패 : -Limit을 감지하지 못했습니다.")
            # 실패 결과 반환
            return False
        # -Limit 도착 후 현재 위치 출력
        motor.print_position_all("Table Open 완료 후 위치")
        # Table Open 완료 로그 출력
        print("\n========== Table Open 완료 ==========")
        # 성공 결과 반환
        return True
    # Table Open 동작 중 예외가 발생한 경우
    except Exception as error:
        # 예외 로그 출력
        print(f"[MOTOR] Table Open 중 예외 발생 : {error}")
        # 안전 정지 시도
        try:
            motor.move_stop()
        except Exception:
            pass
        # 실패 결과 반환
        return False
    # Table Open 함수 종료 시 항상 실행
    finally:
        # 안전 정지 시도
        try:
            motor.move_stop()
        except Exception as error:
            print(f"[MOTOR] Move Stop 실패 : {error}")
        # Servo OFF 시도
        try:
            print("[MOTOR] Servo OFF :", motor.servo_off())
        except Exception as error:
            print(f"[MOTOR] Servo OFF 실패 : {error}")
        # 모터 연결 종료 시도
        try:
            motor.motor_close()
        except Exception as error:
            print(f"[MOTOR] 모터 연결 종료 실패 : {error}")


# =============================================================
# 메인 함수 선언
# =============================================================
def main():
    # -------------------------------
    # 사용자 설정값 선언
    # -------------------------------
    # 주행 목적지 랜드마크 변수 선언
    target_landmark = "LM14"
    # 도킹에 사용할 마커 정보 저장 경로 선언
    answer_path = (
        "/home/mic-711/Desktop/SamDisplay/"
        "side_docking_package/aruco_answer_json/aruco_2.json")
    # 실제 ArUco 마커 한 변 길이[m] 선언
    aruco_length = 0.05
    # -------------------------------
    # 제어 객체 선언
    # -------------------------------
    # SEER AMR 제어 객체 선언
    seer = SEER_commu(
        ip="192.168.0.104")
    # 측면 D435 ArUco 검출 객체 생성
    detector = Aruco_Detector_Commu(
        aruco_length=aruco_length)
    # 정답 ArUco 저장/로드 객체 생성
    answer = Aruco_Answer_Commu(
        answer_path=answer_path)
    # 측면 카메라 기반 ArUco 도킹 객체 생성
    docker = Aruco_Dock_Commu(
        seer=seer,
        detector=detector,
        answer=answer,
        # yaw 허용 오차[deg]
        yaw_tolerance_deg=0.3,
        # marker_x 허용 오차[m]
        x_tolerance_m=0.02,
        # 회전 방향이 반대면 -1.0으로 변경
        yaw_direction_sign=1.0,
        # 전진/후진 방향이 반대면 -1.0으로 변경
        move_direction_sign=1.0)
    # Ezi Motor 통신 객체 생성
    motor = Ezi_Motor_Commu(
        ip=MOTOR_IP,
        port=MOTOR_PORT,
        timeout=MOTOR_TIMEOUT_SEC)
    # 카메라 시작 여부를 저장하는 변수 선언
    camera_started = False
    # -----------------------------
    # 동작 실행
    # -----------------------------
    # 에러가 발생해도 카메라와 소켓이 정리되도록 try-finally 사용
    try:
        # 자율주행 시작 로그 출력
        print(f"\n[AMR] {target_landmark} 위치로 자율주행 시작")
        # 지정한 Landmark로 Block 방식 자율주행 실행
        drive_result = seer.gotargetblock(target_landmark)
        # 주행 명령 결과 출력
        print(f"[AMR] gotargetblock 결과 : {drive_result}")
        # 목적지 도착 후 안정화를 위해 잠시 대기
        time.sleep(0.5)
        # 자율주행 완료 로그 출력
        print(f"[AMR] {target_landmark} 위치 도착 완료")
        # =====================================================
        # ArUco 도킹 실행
        # =====================================================
        # D435 카메라 시작
        detector.camera_start()
        # 카메라 시작 상태 저장
        camera_started = True
        # 카메라 영상 안정화를 위해 잠시 대기
        time.sleep(0.5)
        # 측면 ArUco 도킹 시작 로그 출력
        print("\n[AMR] 측면 ArUco 도킹 시작")
        # 저장된 정답 ArUco JSON 기준으로 도킹 실행
        dock_ok = docker.align_to_answer_marker(
            max_step=300,
            search_w=0.06,
            show_window=True,
            path=answer_path)
        # 도킹 실패 시
        if not dock_ok:
            # 도킹 실패 로그 출력
            print("[AMR] 도킹 실패")
        # 도킹 성공 시
        else:
            # 도킹 성공 로그 출력
            print("[AMR] 도킹 성공")
        # 도킹 후 안정화를 위해 잠시 대기
        time.sleep(1.0)
        # =====================================================
        # 목적지 도착 후 Table Open 실행
        # =====================================================
        # Table Open 시작 로그 출력
        print("\n[AMR] 목적지 도착 후 Table Open 시작.")
        # Table Open 함수 실행
        table_open_ok = table_open(motor)
        # Table Open에 실패한 경우
        if not table_open_ok:
            # 실패 로그 출력
            print("[AMR] Table Open 실패")
            # Table이 열리지 않은 상태에서 도킹하지 않도록 함수 종료
            return
        # Table Open 성공 로그 출력
        print("[AMR] Table Open 성공")
        # Table Open 후 기구 안정화를 위해 잠시 대기
        time.sleep(1.0)
        # 도킹 완료 후에 다음 위치로 이동
        # seer.gotargetblock("LM20")
        # time.sleep(0.1)
        # seer.gotargetblock("LM21")
        # time.sleep(0.1)
        # seer.gotargetblock("LM2")
    # 사용자가 Ctrl+C로 중단한 경우
    except KeyboardInterrupt:
        # 사용자 중단 로그 출력
        print("\n[MAIN] 사용자가 프로그램을 중단했습니다.")
    # 그 외 예외가 발생한 경우
    except Exception as error:
        # 예외 로그 출력
        print(f"\n[MAIN] 예외 발생 : {error}")
    # try 내부에서 에러가 발생하더라도 항상 실행
    finally:
        # 카메라가 시작된 경우에만 카메라 종료 시도
        if camera_started:
            try:
                # D435 카메라 종료
                detector.camera_end()
            # 카메라 종료 중 에러 발생 시
            except Exception as error:
                # 카메라 종료 실패 로그 출력
                print(f"[AMR] 카메라 종료 중 에러 : {error}")
        # SEER 안전 정지 시도
        try:
            # AMR 이동 정지
            seer.socket_close()
        # AMR 정지 중 에러 발생 시
        except Exception as error:
            # 정지 실패 로그 출력
            print(f"[AMR] SEER 정지 중 에러 : {error}")
        # SEER 통신 종료 시도
        try:
            # SEER 소켓 종료
            seer.socket_close()
        # SEER 통신 종료 중 에러 발생 시
        except Exception as error:
            # 소켓 종료 실패 로그 출력
            print(f"[AMR] SEER 소켓 종료 중 에러 : {error}")
        # 프로그램 종료 로그 출력
        print("\n========== AMR Table Open 및 도킹 동작 종료 ==========")

# =============================================================
# Python 파일 직접 실행 시 main 함수 실행
# =============================================================
if __name__ == "__main__":
    main()