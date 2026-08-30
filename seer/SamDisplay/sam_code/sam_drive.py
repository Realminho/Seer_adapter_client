# ----------------------- 범용 라이브러리 import -----------------------
# 시간 핸들링을 위한 time 라이브러리 import
import time
# ----------- custom 라이브러리 import --------------------------------
# 삼성디스플레이 ACS와 연동하여 주행하기 위한 ACS_Driver 클래스 import
from sam_acs_package.sam_acs_driver import ACS_Driver
# ------------------ 필요 변수들 선언 ---------------------------------
# ACS IP 선언
ACS_IP = "192.168.2.135"
# ACS Port 선언
ACS_PORT = 1331
# AMR ID 선언
AMR_ID = "444"

# 메인 함수 선언
def main():
    # ASC 연동 주행 클래스 객체 선언
    acs_driver = ACS_Driver(acs_ip=ACS_IP,acs_port=ACS_PORT,amr_id=AMR_ID)
    # ACS 기반 주행 1회 실행
    acs_driver.run_once()

# 메인 함수 실행
if __name__ == "__main__":
    main()
