#  ----------- 범용 라이브러리 import ---------------------
# 시간 핸들링을 위해 time 라이브러리 import
import time
# ----------- custom 라이브러리 import ---------------------
# SEER AMR 제어를 위해 SEER_commu 클래스 import
from custom_package.seer_commu import SEER_commu
# 정답 아루코마커 정보 로드을 위한 Answer_Aruco_commu 클래스 import
from custom_package.dock_commu_2 import Answer_Aruco_commu
# 아루코마커 감지를 위한 Detect_Aruco_commu 클래스 import
from custom_package.dock_commu_2 import Detect_Aruco_commu
# 아루코마커 도킹을 위한 AMR_Aruco_dock_commu 클래스 import
from custom_package.dock_commu_2 import AMR_Aruco_dock_commu

# 메인 함수 선언
def main():
    # SEER_commu 객체 선언
    seer = SEER_commu()
    # 정답 아루코마커 정보 로드 객체 선언
    storage = Answer_Aruco_commu()
    # 아루코마커 인식 객체 선언
    detector = Detect_Aruco_commu()
    # 아루코 도킹 객체 선언
    docker = AMR_Aruco_dock_commu(seer=seer,detector=detector,storage=storage)
    # 카메라 시작
    detector.camera_start()    
    # LM2로 자율주행
    seer.gotargetblock("LM1")
    # 0.1초 대기
    time.sleep(0.1)
    # 아루코마커 기반 도킹 시작
    dock_ok = docker.align_to_saved_target()
    # 도킹 실패시
    if not dock_ok:
        # 디버그 문구 print
        print("[AMR] 도킹 실패")
    else:
        # 도킹 성공 문구 print
        print("[AMR] 도킹 성공")
    # 카메라 종료
    detector.camera_stop()  
    # 0.1초 대기
    time.sleep(1.5)
    # LM1으로 자율주행
    # seer.gotargetblock("LM1")
    # 통신 종료
    seer.socket_close()
   
# 메인 함수 실행
if __name__ == '__main__':
    main()