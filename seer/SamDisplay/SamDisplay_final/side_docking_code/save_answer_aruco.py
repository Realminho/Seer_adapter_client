# ----------------------------- 범용 라이브러리 import -----------------------------
# OpenCV 창에서 키보드 입력을 받기 위해 cv2 라이브러리 import
import cv2
# 짧은 대기 시간을 사용하기 위해 time 라이브러리 import
import time
# ----------------------------- 사용자 정의 클래스 import -----------------------------
# D435 카메라 기반 ArUco 검출 클래스 import
from side_docking_package.aruco_detector import Aruco_Detector_Commu
# ArUco 정답 저장/로드 클래스 import
from side_docking_package.save_aruco_answer import Aruco_Answer_Commu

# main 함수 실행
def main():
    # 저장할 ArUco 마커 ID 선언
    target_id = 0
    # 정답 ArUco JSON 저장 경로 선언
    answer_path = "/home/mic-711/Desktop/SamDisplay/side_docking_package/aruco_answer_json/aruco_6.json"
    # D435 ArUco 검출 객체 생성
    detector = Aruco_Detector_Commu(aruco_length=0.05)
    # ArUco 정답 저장/로드 객체 생성
    answer = Aruco_Answer_Commu(answer_path=answer_path)
    # 사용 방법 안내 출력
    print("============================================================")
    print("[SAVE_ARUCO] 카메라 화면에서 마커를 확인하세요.")
    print("[SAVE_ARUCO] SPACE : 현재 마커 정보를 정답으로 저장")
    print("[SAVE_ARUCO] q     : 종료")
    print("============================================================")
    # 에러가 없으면
    try:
        # D435 카메라 시작
        detector.camera_start()
        # 사용자가 종료할 때까지 반복
        while True:
            # 현재 프레임에서 target_id에 해당하는 ArUco 마커 검출
            marker_info = detector.detect_marker(target_id=target_id,show_window=True)
            # 마커가 정상 검출되었다면
            if marker_info is not None and marker_info.get("found", False):
                # 현재 검출 정보를 터미널에 간단히 출력
                print(
                    f"[SAVE_ARUCO] ID={marker_info.get('aruco_id')} "
                    f"yaw={marker_info.get('yaw_deg')} "
                    f"x={marker_info.get('marker_x')} "
                    f"z={marker_info.get('marker_z')}")

            # OpenCV 창에서 키보드 입력을 받음
            key = cv2.waitKey(1) & 0xFF
            # 스페이스바를 눌렀다면
            if key == 32:
                # 저장 시작 로그 출력
                print("[SAVE_ARUCO] SPACE 입력 감지 -> 정답 마커 저장 시작")
                # 여러 프레임을 다시 읽어서 안정적인 중앙값 기반 마커 정보 저장
                save_result = answer.save_current_marker(
                    detector=detector,
                    target_id=target_id,
                    sample_count=10,
                    show_window=True,
                    path=answer_path)
                # 저장 성공이라면
                if save_result:
                    # 저장 성공 로그 출력
                    print(f"[SAVE_ARUCO] 정답 마커 저장 성공 : {answer_path}")
                # 저장 실패라면
                else:
                    # 저장 실패 로그 출력
                    print("[RUN_SAVE_ARUCO] 정답 마커 저장 실패")
                # 저장 후 너무 빠르게 중복 입력되는 것을 막기 위해 0.5초 대기
                time.sleep(0.5)
            # q 키를 눌렀다면
            elif key == ord("q"):
                # 종료 로그 출력
                print("[RUN_SAVE_ARUCO] q 입력 감지 -> 종료")
                # while 반복 종료
                break
    # 최종적으로
    finally:
        # D435 카메라 종료
        detector.camera_end()

# 이 파일을 직접 실행했을 때만 main 함수 실행
if __name__ == "__main__":
    # 메인 함수 실행
    main()    