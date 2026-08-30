#  ----------- 범용 라이브러리 import ---------------------
# 시간 핸들링을 위해 time 라이브러리 import
import time
# ----------- custom 라이브러리 import ---------------------
# SEER AMR 제어를 위해 SEER_commu 클래스 import
from custom_package.seer_commu import SEER_commu
# D435 카메라 기반 ArUco 검출 클래스 import
from side_docking_package.aruco_detector import Aruco_Detector_Commu
# ArUco 정답 저장/로드 클래스 import
from side_docking_package.save_aruco_answer import Aruco_Answer_Commu
# 측면 카메라 기반 도킹 클래스 import
from side_docking_package.aruco_dock import Aruco_Dock_Commu

# 메인 함수 선언
def main():
    # -------------------------------
    # 사용자 설정값 선언
    # -------------------------------
    # 주행 목적지 랜드마크 변수 선언
    target_landmark = "LM36"
    # 도킹에 사용할 마커 정보 저장 경로 선언
    answer_path = "/home/mic-711/Desktop/SamDisplay/side_docking_package/aruco_answer_json/aruco_5.json"
    # 실제 ArUco 마커 한 변 길이[m] 선언
    aruco_length = 0.05
    # -------------------------------
    # 제어 객체 선언
    # -------------------------------
    # SEER AMR 제어 객체 선언
    seer = SEER_commu(ip="192.168.0.104")
    # 측면 D435 ArUco 검출 객체 생성
    detector = Aruco_Detector_Commu(aruco_length=aruco_length)
    # 정답 ArUco 저장/로드 객체 생성
    answer = Aruco_Answer_Commu(answer_path=answer_path)
    # 측면 카메라 기반 ArUco 도킹 객체 생성
    docker = Aruco_Dock_Commu(seer=seer,detector=detector,answer=answer,
        # yaw 허용 오차[deg]
        yaw_tolerance_deg=0.1,
        # marker_x 허용 오차[m]
        x_tolerance_m=0.05,
        # 회전 방향이 반대면 -1.0으로 변경
        yaw_direction_sign=1.0,
        # 전진/후진 방향이 반대면 -1.0으로 변경
        move_direction_sign=1.0)
    # -----------------------------
    # 동작 실행
    # -----------------------------
    # 에러가 발생해도 카메라와 소켓이 정리되도록 try-finally 사용
    try:
        # D435 카메라 시작
        detector.camera_start()
        # 자율주행 시작 로그 출력
        print(f"[AMR] {target_landmark} 위치로 자율주행 시작")
        # 목적지 설정
        seer.gotargetblock("LM14")
        time.sleep(0.1)
        seer.gotargetblock("LM20")
        time.sleep(0.1)
        seer.gotargetblock("LM21")
        time.sleep(0.1)
        seer.gotargetblock("LM23")
        time.sleep(0.1)
        # 지정한 Landmark로 block 방식 자율주행 실행
        seer.gotargetblock(target_landmark)
        # 목적지 도착 후 안정화를 위해 잠시 대기
        time.sleep(0.5)
        # 자율주행 완료 로그 출력
        print(f"[AMR] {target_landmark} 위치 도착 완료")
        # 측면 ArUco 도킹 시작 로그 출력
        print("[AMR] 측면 ArUco 도킹 시작")
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
    # try 내부에서 에러가 발생하더라도 항상 실행
    finally:
        # 카메라 종료 시도
        try:
            # D435 카메라 종료
            detector.camera_end()
        # 카메라 종료 중 에러 발생 시
        except Exception as error:
            # 카메라 종료 실패 로그 출력
            print(f"[AMR] 카메라 종료 중 에러 : {error}")
        # SEER 통신 종료 시도
        try:
            # SEER 소켓 종료
            seer.socket_close()
        # SEER 통신 종료 중 에러 발생 시
        except Exception as error:
            # 소켓 종료 실패 로그 출력
            print(f"[AMR] SEER 소켓 종료 중 에러 : {error}")

# 메인 함수 실행
if __name__ == '__main__':
    main()