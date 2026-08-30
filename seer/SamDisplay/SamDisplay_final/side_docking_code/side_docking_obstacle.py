# ----------------------- 범용 라이브러리 import -----------------------
# 시간 핸들링을 위해 time 라이브러리 import
import time
# ----------------------- custom 라이브러리 import -----------------------
# SEER AMR 제어를 위해 SEER_commu 클래스 import
from custom_package.seer_commu import SEER_commu
# Safety PLC 제어를 위해 Safety_PLC_Commu 클래스 import
from custom_package.safety_plc_commu import Safety_PLC_Commu
# D435 카메라 기반 ArUco 검출 클래스 import
from side_docking_package.aruco_detector import Aruco_Detector_Commu
# ArUco 정답 저장/로드 클래스 import
from side_docking_package.save_aruco_answer import Aruco_Answer_Commu
# 측면 카메라 기반 도킹 클래스 import
from side_docking_package.aruco_dock import Aruco_Dock_Commu
# =============================================================
# 사용자 설정값 선언
# =============================================================
# 순서대로 주행할 목적지 Landmark 목록 설정
TARGET_LANDMARKS = ["LM20","LM21","LM23","LM36","LM14"]
# ArUco 도킹을 실행할 목적지 설정
DOCKING_LANDMARK = "LM14"
# SEER AMR IP 설정
SEER_IP = "192.168.0.104"
# Safety PLC IP 설정
SAFETY_PLC_IP = "192.168.0.40"
# Safety PLC Modbus TCP Port 설정
SAFETY_PLC_PORT = 502
# Safety PLC 통신 Timeout 설정
SAFETY_PLC_TIMEOUT_SEC = 5.0
# Safety PLC Device ID 설정
SAFETY_PLC_DEVICE_ID = 1
# LM14 도킹에 사용할 마커 정보 저장 경로 설정
ANSWER_PATH = (
    "/home/mic-711/Desktop/SamDisplay/"
    "side_docking_package/aruco_answer_json/aruco_2.json"
)
# 실제 ArUco 마커 한 변 길이[m] 설정
ARUCO_LENGTH = 0.05
# SEER 주행 중 상태값
TASK_RUNNING = 2
# SEER 정지 또는 Block 상태값
TASK_STOPPED = 3
# SEER 주행 완료 상태값
TASK_COMPLETED = 4
# 주행 상태 확인 주기 설정
DRIVE_STATUS_CHECK_SEC = 0.2
# 새로운 gotarget 명령 송신 후 이전 완료 상태를 무시하기 위한 시간 설정
DRIVE_START_IGNORE_SEC = 0.5
# 목적지 도착 후 다음 동작 전 대기 시간 설정
ARRIVAL_WAIT_SEC = 0.5
# MOMA Lidar 장애물 감지 후 최소 정지 시간 설정
MOMA_LIDAR_MIN_STOP_SEC = 3.0
# 장애물 감지 중 Safety PLC 상태 확인 주기 설정
MOMA_LIDAR_STOP_CHECK_SEC = 0.5
# AMR 정지 Motion Control 유지 시간 설정
# SEER_commu에서 int(duration)으로 변환하므로 1 이상으로 설정
MOMA_LIDAR_STOP_MOTION_SEC = 1


# =============================================================
# MOMA Lidar 장애물 상태 확인 함수 선언
# =============================================================
def get_moma_lidar_warning_status(safety_plc):
    # Safety PLC Virtual Output 전체 읽기
    output_bits = safety_plc.read_all_virtual_outputs()
    # Safety PLC 출력 읽기 실패 시
    if output_bits is None:
        # 통신 실패 반환
        return None
    # MOMA1 정상 상태 O14 읽기
    moma1_ok = output_bits[safety_plc.OUTPUT_MOMA_LIDAR_01_OK]
    # MOMA1 Warning 상태 O15 읽기
    moma1_warning_signal = output_bits[safety_plc.OUTPUT_MOMA_LIDAR_01_WARNING_OK]
    # MOMA2 정상 상태 O16 읽기
    moma2_ok = output_bits[safety_plc.OUTPUT_MOMA_LIDAR_02_OK]
    # MOMA2 Warning 상태 O17 읽기
    moma2_warning_signal = output_bits[safety_plc.OUTPUT_MOMA_LIDAR_02_WARNING_OK]

    # MOMA1은 O14가 Low이고 O15가 High이면 장애물 감지
    moma1_warning = (
        moma1_ok is False
        and moma1_warning_signal is True
    )

    # MOMA2는 O16이 Low이고 O17이 High이면 장애물 감지
    moma2_warning = (
        moma2_ok is False
        and moma2_warning_signal is True
    )

    # 현재 Lidar 상태 출력
    print(
        "[Safety PLC] MOMA Lidar 상태 : "
        f"MOMA1 O14={moma1_ok}, O15={moma1_warning_signal}, "
        f"MOMA2 O16={moma2_ok}, O17={moma2_warning_signal}"
    )

    # 장애물 감지 상태 반환
    return {
        "moma1_warning": moma1_warning,
        "moma2_warning": moma2_warning
    }


# =============================================================
# AMR 정지 명령 함수 선언
# =============================================================
def stop_amr_by_moma_lidar(seer):
    """
    SEER Motion Control을 이용해 AMR에 속도 0 명령을 전송합니다.
    """

    # 에러가 없으면
    try:
        # AMR 정지 Motion Control 명령 송신
        stop_result = seer.motion_control(
            vx=0.0,
            vy=0.0,
            w=0.0,
            duration=MOMA_LIDAR_STOP_MOTION_SEC
        )

        # 정지 명령 결과 출력
        print(
            "[SEER] MOMA Lidar 정지 명령 : "
            f"result={stop_result}"
        )

        # 정지 명령 결과가 False이면
        if stop_result is False:
            # 정지 실패 반환
            return False

        # 정지 성공 반환
        return True

    # 에러 발생 시
    except Exception as error:
        # 정지 명령 실패 로그 출력
        print(
            "[SEER] MOMA Lidar 정지 명령 실패 : "
            f"{error}"
        )

        # 정지 실패 반환
        return False


# =============================================================
# 장애물 감지 후 정지 및 현재 목적지 재주행 함수 선언
# =============================================================
def process_moma_lidar_stop(
    seer,
    safety_plc,
    target_landmark,
    first_warning_status
):
    """
    장애물 감지 시 AMR을 정지합니다.

    최소 3초가 지난 후 MOMA1과 MOMA2 장애물이 모두 해제되면
    현재 주행 중이었던 목적지로 gotarget 명령을 다시 전송합니다.
    """

    # 최초 감지된 Lidar 이름을 저장할 리스트 선언
    warning_lidars = []

    # 최초 Lidar 상태가 정상적으로 들어왔으면
    if first_warning_status is not None:
        # MOMA1에서 장애물을 감지했으면
        if first_warning_status["moma1_warning"]:
            # MOMA1 이름 추가
            warning_lidars.append("MOMA1")

        # MOMA2에서 장애물을 감지했으면
        if first_warning_status["moma2_warning"]:
            # MOMA2 이름 추가
            warning_lidars.append("MOMA2")

    # 감지된 Lidar가 없으면
    if not warning_lidars:
        # Safety PLC 통신 실패 상태로 표시
        warning_text = "Safety PLC 통신 실패"

    # 감지된 Lidar가 있으면
    else:
        # 리스트를 문자열로 변환
        warning_text = ", ".join(warning_lidars)

    # 장애물 정지 처리 시작 로그 출력
    print("==================================================")
    print("[Safety PLC] MOMA Lidar 정지 처리 시작")
    print(f"[Safety PLC] 감지 상태 : {warning_text}")
    print(f"[Safety PLC] 현재 목적지 : {target_landmark}")
    print("==================================================")

    # 정지 시작 시간 저장
    stop_start_time = time.time()

    # 장애물이 해제될 때까지 반복
    while True:
        # AMR 정지 명령 송신
        if not stop_amr_by_moma_lidar(seer):
            # 정지 명령 실패 로그 출력
            print("[AMR] 장애물 감지 후 AMR 정지 명령 실패")

            # 장애물 처리 실패 반환
            return False
        # Safety PLC MOMA Lidar 상태 다시 확인
        warning_status = get_moma_lidar_warning_status(
            safety_plc=safety_plc
        )

        # Safety PLC 상태 읽기 실패 시
        if warning_status is None:
            # 안전을 위해 정지 상태 유지
            print(
                "[Safety PLC] Lidar 상태 읽기 실패 "
                "-> AMR 정지 유지"
            )

            # 지정한 확인 주기만큼 대기
            time.sleep(MOMA_LIDAR_STOP_CHECK_SEC)

            # 다음 반복으로 이동
            continue

        # MOMA1 장애물 상태 저장
        moma1_warning = warning_status["moma1_warning"]

        # MOMA2 장애물 상태 저장
        moma2_warning = warning_status["moma2_warning"]

        # 정지 후 경과 시간 계산
        stopped_time = time.time() - stop_start_time

        # 최소 3초가 지났고 두 Lidar 장애물이 모두 해제되었으면
        if (
            stopped_time >= MOMA_LIDAR_MIN_STOP_SEC
            and not moma1_warning
            and not moma2_warning
        ):
            # 정지 반복 종료
            break

        # 최소 정지 시간이 지났지만 장애물이 계속 감지되면
        if stopped_time >= MOMA_LIDAR_MIN_STOP_SEC:
            # 정지 유지 로그 출력
            print(
                "[Safety PLC] 장애물이 계속 감지되고 있습니다. "
                "AMR 정지 상태를 유지합니다."
            )

        # 지정한 확인 주기만큼 대기
        time.sleep(MOMA_LIDAR_STOP_CHECK_SEC)

    # 장애물 해제 로그 출력
    print("==================================================")
    print("[Safety PLC] MOMA Lidar 장애물 해제")
    print(f"[AMR] 현재 목적지 {target_landmark} 재주행")
    print("==================================================")

    # 장애물 감지 전에 주행 중이던 현재 목적지로 명령 재전송
    seer.gotarget(target_landmark)

    # 이전 task_status 완료값을 잘못 읽는 것을 방지하기 위해 대기
    time.sleep(DRIVE_START_IGNORE_SEC)

    # 장애물 처리 성공 반환
    return True


# =============================================================
# MOMA Lidar 감시 기반 단일 목적지 주행 함수 선언
# =============================================================
def drive_with_moma_lidar_watch(
    seer,
    safety_plc,
    target_landmark
):
    """
    전달받은 목적지로 주행하면서
    MOMA1과 MOMA2 Lidar 장애물 상태를 계속 확인합니다.
    """

    # 주행 시작 로그 출력
    print("==================================================")
    print(f"[AMR] {target_landmark} 위치로 자율주행 시작")
    print("[AMR] MOMA1 / MOMA2 Lidar 감시 시작")
    print("==================================================")

    # 지정한 목적지로 자율주행 시작
    seer.gotarget(target_landmark)

    # 이전 완료 상태가 남아 있는 것을 방지하기 위해 대기
    time.sleep(DRIVE_START_IGNORE_SEC)

    # 목적지에 도착할 때까지 반복
    while True:
        # Safety PLC MOMA Lidar 상태 확인
        warning_status = get_moma_lidar_warning_status(
            safety_plc=safety_plc
        )

        # Safety PLC 통신 실패 시
        if warning_status is None:
            # 통신 실패 로그 출력
            print(
                "[Safety PLC] 상태 읽기 실패 "
                "-> AMR 정지 후 통신 복구 대기"
            )

            # AMR 정지 후 PLC 통신 복구 시 현재 목적지 재주행
            restart_ok = process_moma_lidar_stop(
                seer=seer,
                safety_plc=safety_plc,
                target_landmark=target_landmark,
                first_warning_status=None
            )

            # 정지 및 재주행 처리 실패 시
            if not restart_ok:
                # 현재 목적지 주행 실패 반환
                return False

            # 재주행 후 다음 반복으로 이동
            continue

        # MOMA1 장애물 감지 여부 저장
        moma1_warning = warning_status["moma1_warning"]

        # MOMA2 장애물 감지 여부 저장
        moma2_warning = warning_status["moma2_warning"]

        # MOMA1 또는 MOMA2에서 장애물이 감지되었으면
        if moma1_warning or moma2_warning:
            # 장애물 정지 후 현재 목적지 재주행 처리
            restart_ok = process_moma_lidar_stop(
                seer=seer,
                safety_plc=safety_plc,
                target_landmark=target_landmark,
                first_warning_status=warning_status
            )

            # 장애물 처리 실패 시
            if not restart_ok:
                # 현재 목적지 주행 실패 반환
                return False

            # 현재 목적지 재주행 후 다음 반복으로 이동
            continue

        # 현재 SEER task_status 확인
        task_status = seer.get_task_status()

        # 현재 task_status 출력
        print(f"[SEER] task_status : {task_status}")

        # task_status 읽기 실패 시
        if task_status is None:
            # 읽기 실패 로그 출력
            print("[SEER] task_status 읽기 실패")

            # 현재 목적지 주행 실패 반환
            return False

        # task_status를 정수로 변환 시도
        try:
            # 정수 상태값으로 변환
            task_status = int(task_status)

        # 정수 변환 실패 시
        except Exception:
            # 알 수 없는 상태 로그 출력
            print(
                "[SEER] 알 수 없는 task_status : "
                f"{task_status}"
            )

            # 현재 목적지 주행 실패 반환
            return False

        # 목적지 도착 완료 상태이면
        if task_status == TASK_COMPLETED:
            # 목적지 도착 로그 출력
            print("==================================================")
            print(f"[AMR] {target_landmark} 위치 도착 완료")
            print("==================================================")

            # 현재 목적지 주행 성공 반환
            return True

        # AMR 주행 중 상태이면
        if task_status == TASK_RUNNING:
            # 주행 중 로그 출력
            print(f"[AMR] {target_landmark} 위치로 주행 중")

        # AMR 정지 또는 Block 상태이면
        elif task_status == TASK_STOPPED:
            # 정지 상태 로그 출력
            print(
                f"[AMR] {target_landmark} 주행 중 "
                "SEER 정지 또는 Block 상태"
            )

        # 다음 상태 확인 전 대기
        time.sleep(DRIVE_STATUS_CHECK_SEC)


# =============================================================
# LM14 ArUco 도킹 함수 선언
# =============================================================
def run_lm14_docking(
    seer,
    detector,
    answer_path
):
    """
    LM14에 도착했을 때만 측면 ArUco 도킹을 실행합니다.
    """

    # 측면 카메라 기반 ArUco 도킹 객체 생성
    docker = Aruco_Dock_Commu(
        seer=seer,
        detector=detector,

        # 저장된 정답 파일을 사용하는 객체 전달
        answer=Aruco_Answer_Commu(
            answer_path=answer_path
        ),

        # yaw 허용 오차[deg]
        yaw_tolerance_deg=0.1,

        # marker_x 허용 오차[m]
        x_tolerance_m=0.05,

        # 회전 방향이 반대면 -1.0으로 변경
        yaw_direction_sign=1.0,

        # 전진/후진 방향이 반대면 -1.0으로 변경
        move_direction_sign=1.0
    )

    # 카메라 시작 여부 저장
    camera_started = False

    # 에러가 발생해도 카메라가 종료되도록 try-finally 사용
    try:
        # D435 카메라 시작
        detector.camera_start()

        # 카메라 시작 상태 저장
        camera_started = True

        # LM14 도킹 시작 로그 출력
        print("==================================================")
        print("[AMR] LM14 측면 ArUco 도킹 시작")
        print("==================================================")

        # 저장된 정답 ArUco JSON 기준으로 도킹 실행
        dock_ok = docker.align_to_answer_marker(
            max_step=300,
            search_w=0.06,
            show_window=True,
            path=answer_path
        )

        # 도킹 실패 시
        if not dock_ok:
            # 도킹 실패 로그 출력
            print("[AMR] LM14 도킹 실패")

            # 도킹 실패 반환
            return False

        # 도킹 성공 로그 출력
        print("[AMR] LM14 도킹 성공")

        # 도킹 후 안정화를 위해 대기
        time.sleep(1.0)

        # 도킹 성공 반환
        return True

    # 도킹 중 에러 발생 시
    except Exception as error:
        # 에러 로그 출력
        print(f"[AMR] LM14 도킹 중 에러 발생 : {error}")

        # 도킹 실패 반환
        return False

    # 최종적으로
    finally:
        # 카메라가 시작되었으면
        if camera_started:
            # 카메라 종료 시도
            try:
                # D435 카메라 종료
                detector.camera_end()

                # 카메라 종료 로그 출력
                print("[AMR] D435 카메라 종료")

            # 카메라 종료 중 에러 발생 시
            except Exception as error:
                # 카메라 종료 실패 로그 출력
                print(
                    "[AMR] 카메라 종료 중 에러 : "
                    f"{error}"
                )


# =============================================================
# 메인 함수 선언
# =============================================================
def main():
    # SEER AMR 객체 초기값 선언
    seer = None

    # Safety PLC 객체 초기값 선언
    safety_plc = None

    # D435 ArUco 검출 객체 초기값 선언
    detector = None

    # 에러가 발생해도 모든 연결이 정리되도록 try-finally 사용
    try:
        # SEER AMR 제어 객체 생성
        seer = SEER_commu(
            ip=SEER_IP
        )

        # Safety PLC 제어 객체 생성
        safety_plc = Safety_PLC_Commu(
            ip=SAFETY_PLC_IP,
            port=SAFETY_PLC_PORT,
            timeout=SAFETY_PLC_TIMEOUT_SEC,
            device_id=SAFETY_PLC_DEVICE_ID
        )

        # Safety PLC 연결 시도 후 실패 시
        if not safety_plc.plc_connect():
            # 연결 실패 로그 출력
            print("[Safety PLC] 연결 실패")

            # 메인 함수 종료
            return

        # LM14 도킹용 D435 ArUco 검출 객체 생성
        # 실제 카메라는 LM14에 도착했을 때만 시작
        detector = Aruco_Detector_Commu(
            aruco_length=ARUCO_LENGTH
        )

        # 전체 목적지 개수 저장
        total_landmark_count = len(TARGET_LANDMARKS)

        # 목적지 목록을 순서대로 반복
        for landmark_index, target_landmark in enumerate(
            TARGET_LANDMARKS,
            start=1
        ):
            # 현재 목적지 정보 출력
            print("##################################################")
            print(
                f"[AMR] 목적지 "
                f"{landmark_index}/{total_landmark_count}"
            )
            print(f"[AMR] 현재 목적지 : {target_landmark}")
            print("##################################################")

            # 현재 목적지에 대해 MOMA Lidar 감시 기반 주행 실행
            drive_ok = drive_with_moma_lidar_watch(
                seer=seer,
                safety_plc=safety_plc,
                target_landmark=target_landmark
            )

            # 현재 목적지 주행 실패 시
            if not drive_ok:
                # 주행 실패 로그 출력
                print("==================================================")
                print(f"[AMR] {target_landmark} 자율주행 실패")
                print("[AMR] 다음 목적지로 이동하지 않고 종료합니다.")
                print("==================================================")

                # 메인 함수 종료
                return

            # 목적지 도착 후 안정화를 위해 대기
            time.sleep(ARRIVAL_WAIT_SEC)

            # 현재 목적지가 LM14이면
            if target_landmark == DOCKING_LANDMARK:
                # LM14 ArUco 도킹 실행
                dock_ok = run_lm14_docking(
                    seer=seer,
                    detector=detector,
                    answer_path=ANSWER_PATH
                )

                # LM14 도킹 실패 시
                if not dock_ok:
                    # 도킹 실패 로그 출력
                    print("[AMR] LM14 도킹 실패로 전체 동작을 종료합니다.")

                    # 메인 함수 종료
                    return

        # 모든 목적지 주행 완료 로그 출력
        print("##################################################")
        print("[AMR] 등록된 모든 목적지 주행 완료")
        print(
            "[AMR] 완료 경로 : "
            + " -> ".join(TARGET_LANDMARKS)
        )
        print("##################################################")

    # 사용자가 Ctrl+C를 누르면
    except KeyboardInterrupt:
        # 사용자 종료 로그 출력
        print("[AMR] 사용자 종료 요청")

    # 예상하지 못한 에러가 발생하면
    except Exception as error:
        # 에러 로그 출력
        print(f"[AMR] 실행 중 에러 발생 : {error}")

    # 최종적으로 항상 실행
    finally:
        # Safety PLC 객체가 생성되었으면
        if safety_plc is not None:
            # Safety PLC 연결 종료 시도
            try:
                # Safety PLC 연결 종료
                safety_plc.plc_close()

            # Safety PLC 연결 종료 중 에러 발생 시
            except Exception as error:
                # 연결 종료 실패 로그 출력
                print(
                    "[Safety PLC] 연결 종료 중 에러 : "
                    f"{error}"
                )

        # SEER 객체가 생성되었으면
        if seer is not None:
            # SEER 소켓 종료 시도
            try:
                # SEER 소켓 종료
                seer.socket_close()

            # SEER 소켓 종료 중 에러 발생 시
            except Exception as error:
                # SEER 소켓 종료 실패 로그 출력
                print(
                    "[AMR] SEER 소켓 종료 중 에러 : "
                    f"{error}"
                )


# =============================================================
# 메인 함수 실행
# =============================================================
if __name__ == "__main__":
    main()