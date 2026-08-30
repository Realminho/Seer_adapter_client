#  ----------- 범용 라이브러리 import ---------------------
# 시간 핸들링을 위해 time 라이브러리 import
import time
# ----------- custom 라이브러리 import ---------------------
# SEER AMR 제어를 위해 SEER_commu 클래스 import
from custom_package.seer_commu import SEER_commu

# 메인 함수 선언
def main():
    # SEER_commu 객체 선언
    seer = SEER_commu()
    # LM2로 자율주행
    seer.gotarget_speed_block(point="LM2",method="backward",max_speed=0.24)
    # seer.gotarget_speed_block(point="LM1",method="forward",max_speed=0.30)
    # 0.1초 대기
    time.sleep(0.1)
    # 통신 종료
    seer.socket_close()
   
# 메인 함수 실행
if __name__ == '__main__':
    main()