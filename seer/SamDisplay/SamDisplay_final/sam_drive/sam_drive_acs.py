# ----------------------- 사용자 정의 라이브러리 import -----------------------
# ACS Controller 클래스 import
from sam_acs_package.sam_acs_controller_node import ACS_Controller

# ----------------------- 필요 변수 선언 -----------------------
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
    # ACS Controller 객체 생성
    # acs_ip : 통신할 acs ip
    # acs_port : 통신할 acs port
    # amr_id : 현재 amr의 id
    # carry_flag : 적재 여부 (0: 적재 x, 1: 적재 O)
    # s_period_sec : S 명령 송신 주기
    # t_period_sec : T 명령 송신 주기
    # c_recv_timeout_sec : C 명령 수신 여부 확인 주기
    # status_check_period_sec : 주행 상태 확인 주기
    # node_check_period_sec : 노드 위치 확인 주기
    acs_controller = ACS_Controller(
        acs_ip=ACS_IP,
        acs_port=ACS_PORT,
        amr_id=AMR_ID,
        carry_flag=CARRY_FLAG,
        s_period_sec=3.0,
        t_period_sec=3.0,
        c_recv_timeout_sec=0.2,
        status_check_period_sec=0.2,
        node_check_period_sec=0.2)
    # ACS 기반 주행 실행
    acs_controller.run()

# 메인 함수 실행
if __name__ == "__main__":
    main()