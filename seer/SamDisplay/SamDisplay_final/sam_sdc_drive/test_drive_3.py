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
# Dobot과 통신을 위한 TCP_server_commu_sam 클래스 import
from custom_package.tcp_server_commu_sam import TCP_server_commu_sam

# -------------------------- Dobot과 통신 설정값 선언 --------------------------
# Dobot Robot이 접속할 AMR TCP Server IP 선언
TCP_SERVER_IP = "192.168.192.49"
# Dobot Robot이 접속할 AMR TCP Server Port 선언
TCP_SERVER_PORT = 12321
# -------------------------- NEW : AQ 작업 요청값 선언 --------------------------
# AQ 작업을 요청할 설비 Tag 선언
AQ_TAG = "LTM01"
# AQ 작업에서 사용할 AMR Slot No 선언
AQ_SLOT_NO = 1
# AQ 작업에서 사용할 Robot Stage No 선언
AQ_STAGE_NO = 1
# AQ 작업에서 사용할 Material ID 선언
AQ_MATERIAL_ID = "LAB_2_MARKET"
# -------------------------- NEW : DP 작업 요청값 선언 --------------------------
# DP 작업을 요청할 설비 Tag 선언
DP_TAG = "LTM01"
# DP 작업에서 사용할 AMR Slot No 선언
DP_SLOT_NO = 1
# DP 작업에서 사용할 Robot Stage No 선언
DP_STAGE_NO = 1
# DP 작업에서 사용할 Material ID 선언
DP_MATERIAL_ID = "LAB_2_MARKET"

# 카메라 화면을 계속 갱신하면서 목표 지점까지 주행하는 함수 선언
def gotarget_with_camera_view(seer, detector, target_landmark, target_id=None):
    # 주행 시작 문구 출력
    print(f"[AMR] {target_landmark} 주행 시작")
    # AMR에 목표 지점 주행 명령 전송
    seer.gotarget(target_landmark)
    # 주행 완료될 때까지 반복
    while True:
        # 카메라 화면 갱신을 위해 아루코 검출 함수 반복 호출
        detector.detect_aruco(target_id=target_id, show_window=True)
        # 현재 작업 상태 확인
        task_status = seer.get_task_status()
        # 작업 상태가 4이면 도착 완료로 판단
        if task_status == 4:
            # 도착 완료 문구 출력
            print(f"[AMR] {target_landmark} 도착 완료")
            # 반복 종료
            break
        # 너무 빠른 반복 방지를 위한 짧은 대기
        time.sleep(0.07)
# -------------------------- 작업 시작 응답 송신 함수 선언 --------------------------
def send_task_start(tcp, cmd_id):
    # Robot에 작업 시작 가능 응답 송신
    return tcp.send_line(f"{cmd_id},100")
# -------------------------- 작업 실패 응답 송신 함수 선언 --------------------------
def send_task_fail(tcp, cmd_id):
    # Robot에 작업 실패 응답 송신
    return tcp.send_line(f"{cmd_id},200")

# 주행 시나리오별 주행 함수 선언
def run_one_cycle(seer, tcp, detector, docker):
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
    # ----------------------- 첫번째 주행 시작(LM6 → LM1) ---------------------------
    print("[AMR] 첫번째 주행 시작 LM6 → LM1")
    # LM1로 주행하면서 카메라 화면 계속 갱신
    # gotarget_with_camera_view(seer=seer,detector=detector,target_landmark="LM1",target_id=None)
    # -------------------------- 아루코마커 도킹 시작 --------------------------------
    print("[AMR] 도킹 시작")
    # 0.5초 대기
    time.sleep(0.5)
    # 아루코마커 기반 도킹 시작
    # dock_ok = docker.align_to_saved_target()
    # 도킹 실패시
    # if not dock_ok:
        # 디버그 문구 print
        # print("[AMR] 도킹 실패, AQ 작업 요청 시작")
    # else:
        # 도킹 성공 문구 print
        # print("[AMR] 도킹 성공, AQ 작업 요청 시작")
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
                send_task_start(tcp, aq_cmd_id)
                # 루프 종료
                break
            # 정상적인 응답이 아니라면
            else:
                # 작업 요청 실패 명령 송신
                send_task_fail(tcp, aq_cmd_id)
                # 에러 발생 후 수신한 메시지 print
                print(f"AQ 명령 응답 실패 : {order_msg}")
                print("----------------------------------------")
                # AQ 실행 명령 재송신
                send_ok, send_msg, aq_cmd_id = tcp.request_aq(tag=AQ_TAG,slot_no=AQ_SLOT_NO,stage_no=AQ_STAGE_NO,material_id=AQ_MATERIAL_ID)
        # 0.1초 대기
        time.sleep(0.1)
    # ---------------------------- AQ 작업 종료까지 대기 ------------------------------
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
                # 에러 발생
                raise RuntimeError(f"AQ 실행 실패 : {result_msg}")
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
    # ---------------------------- 두번째 주행 시작(LM1 → LM4) ---------------------
    print("[AMR] 두번째 주행 시작 LM1 → LM4")
    # LM4로 주행하면서 카메라 화면 계속 갱신
    # gotarget_with_camera_view(seer=seer,detector=detector,target_landmark="LM4",target_id=None)
    # ---------------------------- 세번째 주행 시작(LM4 → LM5) ---------------------
    print("[AMR] 세번째 주행 시작 LM4 → LM5")
    # LM5로 주행하면서 카메라 화면 계속 갱신
    # gotarget_with_camera_view(seer=seer,detector=detector,target_landmark="LM5",target_id=None)
    # ---------------------------- 네번째 주행 시작(LM5 → LM1) ---------------------
    print("[AMR] 네번째 주행 시작 LM5 → LM1")
    # LM1로 주행하면서 카메라 화면 계속 갱신
    # gotarget_with_camera_view(seer=seer,detector=detector,target_landmark="LM1",target_id=None)
    # -------------------------- 아루코마커 도킹 시작 --------------------------------
    print("[AMR] 도킹 시작")
    # 0.5초 대기
    time.sleep(0.5)
    # 아루코마커 기반 도킹 시작
    # dock_ok = docker.align_to_saved_target()
    # 도킹 실패시
    # if not dock_ok:
        # 디버그 문구 print
        # print("[AMR] 도킹 실패, DP 작업 요청 시작")
    # else:
        # 도킹 성공 문구 print
        # print("[AMR] 도킹 성공, DP 작업 요청 시작")
    # ----------------------------- DP 요청 시작 -----------------------------------
    print("[AMR] DP 작업 요청")
    print("----------------------------------------")
    # DP 요청 메시지 송신
    send_ok, send_msg, dp_cmd_id = tcp.request_dp(tag=DP_TAG,slot_no=DP_SLOT_NO,stage_no=DP_STAGE_NO,material_id=DP_MATERIAL_ID)
    # 송신 실패 시 예외 발생
    if not send_ok:
        raise RuntimeError(f"DP 요청 실패 : {send_msg}")
    # DP 명령 응답이 올 때까지 반복
    while True:
        # Dobot 응답 수신
        rx_text = tcp.recv_data()
        # 응답이 수신되었으면
        if rx_text:
            # DP 명령 응답 체크
            order_ok, order_msg = tcp.check_order_answer(rx_text, dp_cmd_id)
            # 정상적인 응답이면
            if order_ok:
                # 수신한 메시지 print
                print(f"[AMR] DP 명령 응답 성공 : {order_msg}")
                print("----------------------------------------")
                # 작업 시작 명령 송신
                send_task_start(tcp, dp_cmd_id)
                # 루프 종료
                break
            # 정상적인 응답이 아니라면
            else:
                # 작업 요청 실패 명령 송신
                send_task_fail(tcp, dp_cmd_id)
                # 에러 발생 후 수신한 메시지 print
                print(f"DP 명령 응답 실패 : {order_msg}")
                print("----------------------------------------")
                # DP 실행 명령 재송신
                send_ok, send_msg, dp_cmd_id = tcp.request_dp(tag=DP_TAG,slot_no=DP_SLOT_NO,stage_no=DP_STAGE_NO,material_id=DP_MATERIAL_ID)
        # 0.1초 대기
        time.sleep(0.1)
    # ---------------------------- DP 작업 종료까지 대기 ------------------------------
    print("[AMR] DP 작업 종료 대기")
    print("----------------------------------------")
    # DP 완료 응답이 올 때까지 반복
    while True:
        # Dobot 응답 수신
        rx_text = tcp.recv_data()
        # 응답이 수신되었으면
        if rx_text:
            # DP 명령 결과 체크
            result_ok, result_msg = tcp.check_work_result(rx_text, dp_cmd_id)
            # 명령 결과가 성공이라면
            if result_ok:
                # 수신한 메시지 print
                print(f"[AMR] DP 실행 결과 성공 : {result_msg}")
                print("----------------------------------------")
                # 작업 결과 재전송
                tcp.send_line(result_msg)
                # 루프 종료
                break
            # 명령 결과가 실패라면
            else:
                # 에러 발생
                raise RuntimeError(f"DP 실행 실패 : {result_msg}")
    # ---------------------------- 마지막 주행 시작(LM1 → LM6) ---------------------
    print("[AMR] 마지막 주행 시작 LM1 → LM6")
    # LM6으로 주행하면서 카메라 화면 계속 갱신
    # gotarget_with_camera_view(seer=seer,detector=detector,target_landmark="LM6",target_id=None)
    # ----------------------------- 시연 종료 문구 print -----------------------------------
    print("[AMR] 시연 주헹 완료")
    print("----------------------------------------")

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
    # Dobot 통신 TCP Server 객체 선언
    tcp = TCP_server_commu_sam(ip=TCP_SERVER_IP,port=TCP_SERVER_PORT)
    # 에러가 없다면
    try:
        # TCP Server 시작
        if not tcp.start_server():
            # 서버 시작 실패 시 종료
            return
        # Robot Client 접속 대기
        if not tcp.wait_client():
            # Client 접속 실패 시 종료
            return
        # 카메라 시작
        detector.camera_start()
        # 시연 사이클 주행 실행
        run_one_cycle(seer=seer,tcp=tcp,detector=detector,docker=docker)
    # Ctrl+C 입력 시
    except KeyboardInterrupt:
        # 종료 문구 출력
        print("[AMR] 사용자 종료 요청")
    # 예외 발생 시
    except Exception as e:
        # 에러 내용 출력
        print(f"[AMR] 시연 주행 중 에러 발생 : {e}")
    # 항상 실행
    finally:
        # 카메라 종료
        detector.camera_stop()
        # TCP 통신 종료
        tcp.close_all()
        # SEER 소켓 종료
        seer.socket_close()
        # 종료 문구 출력
        print("[AMR] 전체 종료 완료")

# 메인 함수 실행
if __name__ == '__main__':
    main()