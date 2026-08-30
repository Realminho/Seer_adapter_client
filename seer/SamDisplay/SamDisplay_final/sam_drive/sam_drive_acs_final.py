# ----------------------- 사용자 정의 라이브러리 import -----------------------
# ACS Controller 클래스 import
from sam_acs_package.sam_acs_controller_final import ACS_Controller


# ----------------------- 필요 변수 선언 -----------------------
# ACS IP 선언
ACS_IP = "192.168.0.102"

# ACS Port 선언
ACS_PORT = 1331

# AMR ID 선언
AMR_ID = "444"

# 적재 여부 선언
# 0 : 적재 X
# 1 : 적재 O
CARRY_FLAG = "0"

# S 명령 송신 주기 선언
S_PERIOD_SEC = 3.0

# T 명령 송신 주기 선언
T_PERIOD_SEC = 3.0

# C 명령 수신 여부 확인 주기 선언
C_RECV_TIMEOUT_SEC = 0.2

# 주행 상태 확인 주기 선언
STATUS_CHECK_PERIOD_SEC = 0.2

# 노드 위치 확인 주기 선언
NODE_CHECK_PERIOD_SEC = 0.2

# Dobot TCP Server 바인드 IP 선언
# 0.0.0.0 : 현재 장비의 모든 네트워크 인터페이스에서 접속 허용
DOBOT_SERVER_IP = "0.0.0.0"

# Dobot TCP Server Port 선언
DOBOT_SERVER_PORT = 12321

# Dobot 통신 Timeout 선언
DOBOT_TIMEOUT_SEC = 3600


# ----------------------- 메인 함수 선언 -----------------------
def main():
    # ACS Controller 객체 생성
    # acs_ip : 통신할 ACS IP
    # acs_port : 통신할 ACS Port
    # amr_id : 현재 AMR ID
    # carry_flag : 적재 여부 (0: 적재 X, 1: 적재 O)
    # s_period_sec : S 명령 송신 주기
    # t_period_sec : T 명령 송신 주기
    # c_recv_timeout_sec : C 명령 수신 여부 확인 주기
    # status_check_period_sec : 주행 상태 확인 주기
    # node_check_period_sec : 노드 위치 확인 주기
    # dobot_server_ip : Dobot 접속을 받을 AMR 서버 바인드 IP
    # dobot_server_port : Dobot 접속을 받을 AMR 서버 Port
    # dobot_timeout_sec : Dobot 통신 Timeout
    acs_controller = ACS_Controller(
        acs_ip=ACS_IP,
        acs_port=ACS_PORT,
        amr_id=AMR_ID,
        carry_flag=CARRY_FLAG,
        s_period_sec=S_PERIOD_SEC,
        t_period_sec=T_PERIOD_SEC,
        c_recv_timeout_sec=C_RECV_TIMEOUT_SEC,
        status_check_period_sec=STATUS_CHECK_PERIOD_SEC,
        node_check_period_sec=NODE_CHECK_PERIOD_SEC,
        dobot_server_ip=DOBOT_SERVER_IP,
        dobot_server_port=DOBOT_SERVER_PORT,
        dobot_timeout_sec=DOBOT_TIMEOUT_SEC
    )

    # ACS 기반 주행 실행
    acs_controller.run()


# ----------------------- Python 파일 직접 실행 시 main 함수 실행 -----------------------
if __name__ == "__main__":
    main()