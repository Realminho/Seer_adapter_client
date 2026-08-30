#  ----------- 범용 라이브러리 import ---------------------
# 시간 핸들링을 위해 time 라이브러리 import
import time
# ----------- custom 라이브러리 import ---------------------
# SEER AMR 제어를 위해 SEER_commu 클래스 import
from custom_package.seer_commu import SEER_commu
# Dobot과 통신을 위한 TCP_server_commu_sam 클래스 import
from custom_package.tcp_server_commu_sam import TCP_server_commu_sam
# ------------------------- DOBOT과 통신을 위한 TCP 파라미터 선언 -------------
# TCP 서버 IP 선언
TCP_SERVER_IP = "0.0.0.0"
# TCP 서버 Port 선언
TCP_SERVER_PORT = 12321
# ----------------------- AQ 작업 파라미터 선언 -----------------------
# AQ 작업 요청에 사용할 Tag 선언
AQ_TAG = "LTM01"
# AQ 작업 요청에 사용할 Slot 번호 선언
AQ_SLOT_NO = 1
# AQ 작업 요청에 사용할 Stage 번호 선언
AQ_STAGE_NO = 1
# AQ 작업 요청에 사용할 Material ID 선언
AQ_MATERIAL_ID = "LAB_2_MARKET"

# 주행 시라리오별 주행 함수 선언
def run_one_cycle(seer,tcp):
    # ----------------------------- Health Check 시작 -----------------------------
    print("[AMR] HEALTHCHECK 시작")
    print("----------------------------------------")
    # HEALTHCHECK 요청 메시지 송신
    send_ok, send_msg, health_cmd_id = tcp.request_healthcheck()
    # 송신에 실패하였다면
    if not send_ok:
        # 에러 발생
        raise RuntimeError(f"HEALTHCHECK 요청 실패 : {send_msg}")
    # HEALTHCHECK 응답이 올 때까지 반복
    while True:
        # Dobot 응답 수신
        rx_text = tcp.recv_data()
        # 응답이 수신되었으면
        if rx_text:
            # HEALTHCHECK 응답 체크
            health_ok, health_msg = tcp.check_work_result(rx_text, health_cmd_id)
            # 응답이 정상적이면
            if health_ok:
                # 수신한 메시지 print
                print(f"[AMR] HEALTHCHECK 성공 : {health_msg}")
                print("----------------------------------------")
                # 루프 종료
                break
            # 응답이 정상 응답이 아니라면
            else:
                # 수신한 메시지 print
                print(f"HEALTHCHECK 실패 : {health_msg}")
                print("----------------------------------------")
                # HEALTHCHECK 요청 재전송
                send_ok, send_msg, health_cmd_id = tcp.request_healthcheck()
        # 0.1초 대기
        time.sleep(0.1)
    # -------------------------------- Home 요청 ----------------------------------
    print("[AMR] HOME Position 시작")
    print("----------------------------------------")
    # Home 요청 메시지 송신
    send_ok, send_msg, home_cmd_id = tcp.request_home()
    # 송신 실패 시
    if not send_ok:
        # 에러 발생
        raise RuntimeError(f"HOME 요청 실패 : {send_msg}")
    # HOME 응답이 올 때까지 반복
    while True:
        # Dobot 응답 수신
        rx_text = tcp.recv_data()
        # 응답이 수신되었으면
        if rx_text:
            # HOME 응답 체크
            home_ok, home_msg = tcp.check_work_result(rx_text, home_cmd_id)
            # 응답이 정상적이라면
            if home_ok:
                # 수신한 메시지 print
                print(f"[AMR] HOME 성공 : {home_msg}")
                print("----------------------------------------")
                # 루프 종료
                break
            # 응답이 정상 응답이 아니라면
            else:
                # 에러 발생 후 수신한 메시지 print
                print(f"HOME 실패 : {home_msg}")
                print("----------------------------------------")
                # 홈요청 재전송
                send_ok, send_msg, home_cmd_id = tcp.request_home()
        # 0.1초 대기
        time.sleep(0.1)
    # ----------------------------- 첫번째 주행 시작(LM1 → LM2) ---------------------
    print("[AMR] 첫번쨰 주행 시작 LM1 → LM2")
    # # LM2로 자율주행
    seer.gotargetblock("LM2")
    # ----------------------------- AQ 요청 시작 -----------------------------------
    print("[AMR] AQ 작업 요청")
    print("----------------------------------------")
    # AQ 요청 메시지 송신
    send_ok, send_msg, aq_cmd_id = tcp.request_aq(tag=AQ_TAG,slot_no=AQ_SLOT_NO,stage_no=AQ_STAGE_NO,material_id=AQ_MATERIAL_ID)
    # 송신 실패 시 예외 발생
    if not send_ok:
        raise RuntimeError(f"AQ 요청 실패 : {send_msg}")
    # AQ 명령 응답이 올 때까지 반복
    while True:
        # Dobot 응답 수신
        rx_text = tcp.recv_data()
        # 응답이 수신되었으면
        if rx_text:
            # AQ 명령 응답 체크
            order_ok, order_msg = tcp.check_order_answer(rx_text, aq_cmd_id)
            # 정상적인 응답이면
            if order_ok:
                # 수신한 메시지 Print
                print(f"[AMR] AQ 명령 응답 성공 : {order_msg}")
                print("----------------------------------------")
                # 작업 시작 명령 송신
                tcp.start_aq(aq_cmd_id)
                # 루프 종료
                break
            # 정상적인 응답이 아니라면
            else:
                # 작업 요청 실패 명령 송신
                tcp.fail_aq(aq_cmd_id)
                # 에러 발생 후 수신한 메시지 print
                print(f"AQ 명령 응답 실패 : {order_msg}")
                print("----------------------------------------")
                # AQ 실행 명령 재송신
                send_ok, send_msg, aq_cmd_id = tcp.request_aq(tag=AQ_TAG,slot_no=AQ_SLOT_NO,stage_no=AQ_STAGE_NO,material_id=AQ_MATERIAL_ID)
        # 0.1초 대기
        time.sleep(0.1)
    # ---------------------------- 작업 종료까지 대기 ------------------------------
    print("[AMR] AQ 작업 종료 대기")
    print("----------------------------------------")
    # AQ 완료 응답이 올 때까지 반복
    while True:
        # Dobot 응답 수신
        rx_text = tcp.recv_data()
        # 응답이 수신되었으면
        if rx_text:
            # AQ 명령 결과 체크
            result_ok, result_msg = tcp.check_work_result(rx_text, aq_cmd_id)
            # 명령 결과가 성공이라면
            if result_ok:
                # 수신한 메시지 print
                print(f"[AMR] AQ 실행 결과 성공 : {result_msg}")
                print("----------------------------------------")
                # 작업 결과 재전송
                tcp.send_line(result_msg)
                # 루프 종료
                break
            # 명령 결과가 실패라면
            else:
                # 에러 발생 후 수신한 메시지 print
                raise RuntimeError(f"AQ 실행 실패 : {result_msg}")
                print("----------------------------------------")
    # -------------------------------- Home 요청 ----------------------------------
    print("[AMR] HOME Position 시작")
    print("----------------------------------------")
    # Home 요청 메시지 송신
    send_ok, send_msg, home_cmd_id = tcp.request_home()
    # 송신 실패 시
    if not send_ok:
        # 에러 발생
        raise RuntimeError(f"HOME 요청 실패 : {send_msg}")
    # HOME 응답이 올 때까지 반복
    while True:
        # Dobot 응답 수신
        rx_text = tcp.recv_data()
        # 응답이 수신되었으면
        if rx_text:
            # HOME 응답 체크
            home_ok, home_msg = tcp.check_work_result(rx_text, home_cmd_id)
            # 응답이 정상적이라면
            if home_ok:
                # 수신한 메시지 print
                print(f"[AMR] HOME 성공 : {home_msg}")
                print("----------------------------------------")
                # 루프 종료
                break
            # 응답이 정상 응답이 아니라면
            else:
                # 에러 발생 후 수신한 메시지 print
                print(f"HOME 실패 : {home_msg}")
                print("----------------------------------------")
                # 홈요청 재전송
                send_ok, send_msg, home_cmd_id = tcp.request_home()
        # 0.1초 대기
        time.sleep(0.1)            
    # ---------------------------- 두번째 주행 시작(LM2 → LM3) ---------------------
    print("[AMR] 두번쨰 주행 시작 LM2 → LM3")
    # # LM3로 자율주행
    seer.gotargetblock("LM3")
    print("[AMR] 시연 완료")
    print("----------------------------------------")

# main 함수 선언
def main():
    # SEER_commu 객체 선언
    seer = SEER_commu()
    # TCP_server_commu_sam 객체 선언
    tcp = TCP_server_commu_sam(ip=TCP_SERVER_IP, port=TCP_SERVER_PORT)
    # 에러가 없으면
    try:
        # TCP 서버 시작 실패 시
        if not tcp.start_server():
            # 에러 발생
            raise RuntimeError("TCP 서버 오픈 실패")
        # 클라이언트 접속 대기
        if not tcp.wait_client():
            # 접속 실패 시 에러 발생
            raise RuntimeError("Client 접속 실패")
        # 주행 시나리오 실행
        run_one_cycle(seer,tcp)
    # 최종적으로
    finally:
        # tcp 종료
        tcp.close_all()
        # 디버그 문구 print
        print("----------------------------------------")
        print("---------------- 시연 종료 --------------")
        print("----------------------------------------")

# 메인 함수 실행
if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Ctrl+C 감지 - 프로그램을 종료")
    except Exception as e:
        print(f"\n[ERROR] 예외 발생 : {e}")