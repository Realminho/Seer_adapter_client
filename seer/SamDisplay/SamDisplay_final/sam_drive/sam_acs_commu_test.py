# ----------- custom 라이브러리 import --------------------------------
# 삼성디스플레이 ACS와 연동하여 주행하기 위한 ACS_Driver 클래스 import
from sam_acs_package.sam_acs_driver import ACS_Driver
# ------------------ 필요 변수들 선언 ---------------------------------
# ACS IP 선언
ACS_IP = "192.168.192.43"
# ACS Port 선언
ACS_PORT = 1331
# AMR ID 선언
AMR_ID = "444"
# 현재 적재 상태 변수 선언
CARRY_FLAG = "0"

# 메인 함수 선언
def main():
    # ASC 연동 주행 클래스 객체 선언
    acs_driver = ACS_Driver(acs_ip=ACS_IP,acs_port=ACS_PORT,amr_id=AMR_ID)
    # S 명령에 사용할 적재 상태 초기 설정
    acs_driver.s_reporter.set_carry_flag(CARRY_FLAG)
    # ACS 기반 주행 반복 실행
    acs_driver.run_comm_test_once()

# 메인 함수 실행
if __name__ == "__main__":
    main()