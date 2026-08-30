#  ----------- 범용 라이브러리 import ---------------------
# 시간 핸들링을 위해 time 라이브러리 import
import time
# ----------- custom 라이브러리 import ---------------------
# 아루코마커 감지를 위한 Detect_Aruco_commu 클래스 import
from custom_package.dock_commu import Detect_Aruco_commu

def main():
    # 아루코마커 인식 객체 선언
    detector = Detect_Aruco_commu()
    # 카메라 시작
    detector.camera_start()
    # 에러가 없으면
    try:
        # 무한 반복
        while True:
            # 아루코마커 검출
            detector.detect_aruco()
    # Ctrl+C 가 눌리면
    except KeyboardInterrupt:
        print("Ctrl+C 감지, 프로그램 종료")
    # 최종적으로
    finally:
        # 카메라 종료
        detector.camera_stop()

# 메인 함수 실행
if __name__ =="__main__":
    main()
