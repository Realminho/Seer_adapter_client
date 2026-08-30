# ----------------------- 사용자 정의 라이브러리 import -----------------------
# ACS Controller 클래스 import
from sam_acs_package.sam_acs_controller_final import ACS_Controller

# 메인 함수 선언
def main():
    # ACS Controller 객체 생성
    controller = ACS_Controller(
        # ACS 서버 IP 입력
        acs_ip="192.168.0.103",
        # ACS 서버 Port 입력
        acs_port=1331,
        # AMR ID 입력
        amr_id="444",
        # 초기 적재 상태
        # 0 : 미적재
        # 1 : 적재
        carry_flag="0",
        # ACS S Command 보고 주기
        s_period_sec=3.0,
        # ACS T Command 보고 주기
        t_period_sec=3.0,
        # ACS C Command 수신 확인 주기
        c_recv_timeout_sec=0.2,
        # SEER 주행 상태 확인 주기
        status_check_period_sec=0.2,
        # SEER 현재 Node 확인 주기
        node_check_period_sec=0.2,
        # SEER AMR IP 입력
        seer_ip="192.168.0.104",
        # Dobot이 접속할 서버 IP
        # 보통 현재 PC/Jetson에서 모든 IP로 받으려면 0.0.0.0 사용
        dobot_server_ip="0.0.0.0",
        # Dobot TCP Server Port
        dobot_server_port=12321,
        # Dobot 작업 응답 대기 시간
        dobot_timeout_sec=3600
    )

    # ACS Controller 실행
    controller.run()


# 이 파일을 직접 실행했을 때만 main 함수 실행
if __name__ == "__main__":
    main()