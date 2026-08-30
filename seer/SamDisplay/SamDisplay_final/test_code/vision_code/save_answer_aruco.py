# ----------------------------- 범용 라이브러리 import -----------------------------
# 시간 핸들링을 위한 time 라이브러리 import
import time
# ----------- custom 라이브러리 import ---------------------
# 정답 아루코마커 정보 저장을 위한 Answer_Aruco_commu 클래스 import
from custom_package.dock_commu_2 import Answer_Aruco_commu
# 아루코마커 감지를 위한 Detect_Aruco_commu 클래스 import
from custom_package.dock_commu_2 import Detect_Aruco_commu

# 메인 함수 선언
def main():
    # 정답 아루코 저장 객체 선언
    storage = Answer_Aruco_commu()
    # 아루코 감지 객체 선언
    detector = Detect_Aruco_commu()
    # 에러가 없으면
    try:
        # 카메라 시작
        detector.camera_start()
        # 아루코 ID 4번 기준 현재 감지된 아루코마커 정보 저장
        detector.save_current_aruco_info(
            storage=storage,
            target_id=,
            sample_count=10,
            show_window=True
        )
    # 최종적으로
    finally:
        # 카메라 종료
        detector.camera_stop()

# 메인 함수 실행
if __name__ == '__main__':
    main()

