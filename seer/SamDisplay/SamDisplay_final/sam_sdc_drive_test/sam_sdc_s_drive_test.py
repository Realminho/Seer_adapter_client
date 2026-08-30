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
    # LM16으로 자율주행
    seer.gotargetblock("LM16")
    # 0.1초 대기
    time.sleep(0.01)
    # LM6으로 자율주행
    seer.gotargetblock("LM6")
    # 0.1초 대기
    time.sleep(0.01)
    # LM5로 자율주행
    seer.gotargetblock("LM5")
    # 0.1초 대기
    time.sleep(0.01)
    # LM2로 자율주행
    seer.gotargetblock("LM2")
    # 0.1초 대기
    time.sleep(0.01)
    # LM16으로 자율주행
    seer.gotargetblock("LM16")
    # 0.1초 대기
    time.sleep(0.01)
    # LM6으로 자율주행
    seer.gotargetblock("LM6")
    # 0.1초 대기
    time.sleep(0.01)
    # LM14로 자율주행
    seer.gotargetblock("LM14")
    # 0.1초 대기
    time.sleep(0.01)
    # LM15로 자율주행
    seer.gotargetblock("LM15")
    # 0.1초 대기
    time.sleep(0.01)
    # LM1로 자율주행
    seer.gotargetblock("LM1")
    # 0.1초 대기
    time.sleep(0.01)
    # 통신 종료
    seer.socket_close()
   
# 메인 함수 실행
if __name__ == '__main__':
    main()