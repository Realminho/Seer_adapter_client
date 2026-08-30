#  ----------- 범용 라이브러리 import ---------------------
# 시간 핸들링을 위해 time 라이브러리 import
import time
# ----------- custom 라이브러리 import ---------------------
# SEER AMR 제어를 위해 SEER_commu 클래스 import
from custom_package.seer_commu import SEER_commu
# 회피 주행을 위한 AvoidGraphNavigator 클래스 import
from custom_package.avoid_graph_nav import AvoidGraphNavigator
# --------------------- 상태 상수 선언 ---------------------
# 작업완료 상태 상수 선언
COMPLETED = 4

# 랜드마크 주행 중에 blocked 상태 감지 함수 선언
def drive_landmark_wit_check_block(seer, target_landmark, drive_interval=0.1):
    # 타겟 landmark로 주행 명령 송신
    seer.gotarget(target_landmark)
    # 디버그 문구 print
    print(f"[NAV] 랜드마크 주행 시작 : {target_landmark}")
    # 새 task가 실제로 시작되었는지 확인하는 변수
    task_started_flag = False
    # 명령 직후 상태 반영 시간을 조금 기다림
    time.sleep(0.3)
    # 상태 반복 확인
    while True:
        # 현재 blocked 상태 확인
        blocked_flag, block_reason = seer.get_amr_blocked()
        # blocked 상태면
        if blocked_flag is True:
            print(f"[NAV] 랜드마크 주행 중 Blocked 감지 : {target_landmark}")
            print(f"[NAV] block_reason : {block_reason}")
            # blocked return
            return "blocked"
        # 현재 task status 확인
        current_task_status = seer.get_task_status()
        # 디버그 문구 print
        print(f"[TASK] current_task_status : {current_task_status}")
        # task_status가 None이면
        if current_task_status is None:
            # 아직 task가 실행이 안되었을 수 있으므로 잠시 대기
            time.sleep(check_interval)
            # 다음 루프로 진행
            continue
        # 한 번이라도 COMPLETED가 아닌 값이 나오면
        if current_task_status != COMPLETED:
            # 새 task가 시작된 것으로 판단
            task_started_flag = True
        # 새 task가 시작된 이후에 COMPLETED가 나오면
        if task_started_flag is True and current_task_status == COMPLETED:
            # 완료로 처리
            print(f"[NAV] 랜드마크 주행 완료 : {target_landmark}")
            # completed return
            return "completed"
        # 인자로 받은 시간동안 대기
        time.sleep(drive_interval)

# 회피 주행 중 장애물 감지 함수 선언
def obstacle_check_during_avoid_drive(seer):
    # 현재 blocked 상태 확인
    blocked_flag, block_reason = seer.get_amr_blocked()
    # blocked 상태면 장애물 감지로 판단
    if blocked_flag is True:
        print("[TASK] Blocked 상태 감지 -> 현재 edge 차단")
        print(f"[TASK] block_reason : {block_reason}")
        return True
    # 그 외에는 장애물 없음으로 처리
    return False

# 회피 주행 중 blocked 발생 후 LM1 복귀 및 LM2 랜드마크 주행 함수 선언
def recover_to_lm1_and_landmark_to_lm2(seer):
    # 디버그 문구 print
    print("[NAV] 회피 주행 중 장애물 감지 -> LM1 복귀 시작")
    # 2.0초 대기
    time.sleep(2.0)
    # 랜드마크 주행으로 LM1 복귀
    seer.gotargetblock("LM1")
    # 디버그 문구 print
    print("[NAV] LM1 복귀 완료")
    # 0.5초 대기
    time.sleep(0.5)
    # LM2로 랜드마크 주행 재시도
    second_drive_result = drive_landmark_wit_check_block(
        seer=seer,
        target_landmark="LM2",
        drive_interval=0.2
    )
    # 랜드마크 주행 성공 시
    if second_drive_result == "completed":
        # 디버그 문구 print
        print("[NAV] LM2 랜드마크 주행 성공")
        # True return
        return True
    # 랜드마크 주행 중 다시 blocked 발생 시
    if second_drive_result == "blocked":
        # 디버그 문구 print
        print("[NAV] LM2 랜드마크 주행 중 다시 Blocked 감지")
        # False return
        return False
    # 그외 상황인 경우 False return
    return False

# 실제 환경에 맞게 작성한 회피 주행 함수 선언
def navigate_from_lm1_to_lm2_with_blocked_edge(navigator, seer):
    # 차단 edge 초기화
    navigator.clear_blocked_edges()
    # LM1 -> LM2 edge 차단
    navigator.block_edge("LM1", "LM2")
    # 차단 edge 반영 후 최단 경로 재탐색
    replanned_path = navigator.find_shortest_path("LM1", "LM2")
    # 재탐색 경로가 없으면
    if not replanned_path:
        # 디버그 문구 print
        print("[NAV] LM1 -> LM2 직통 edge 차단 후 재탐색 경로 없음")
        # False return
        return False
    # 디버그 문구 print
    print("##################################################")
    print(f"[NAV] LM1 -> LM2 직통 차단 후 회피 경로 : {replanned_path}")
    print("##################################################")
    # 재탐색 경로 주행
    nav_ok, blocked_edge = navigator.drive_path(path_node_list=replanned_path,obstacle_check_func=lambda: obstacle_check_during_avoid_drive(seer),drive_interval=0.2)
    # 주행 성공 시
    if nav_ok is True:
        print("[NAV] 회피 경로 주행 성공")
        return True
    # 회피 주행 중 장애물 감지로 실패한 경우
    if blocked_edge is not None:
        # 디버그 문구 print
        print(f"[NAV] 회피 주행 중 blocked 발생 : {blocked_edge}")
        # LM1 복귀 후 LM2 랜드마크 주행 수행
        return recover_to_lm1_and_landmark_to_lm2(seer)
    # 주행 실패 시
    print(f"[NAV] 회피 경로 주행 실패 : {blocked_edge}")
    return False

# 메인 함수 선언
def main():
    # SEER_commu 객체 선언
    seer = SEER_commu()
    # AvoidGraphNavigator 객체 선언
    navigator = AvoidGraphNavigator(
        seer=seer,
        graph_map_path="/home/fullmoon34213/Desktop/SamDisplay/drive_code/graph_map/sam_grah_test.json"
    )
    # 최종 주행 결과 저장 변수 선언
    nav_ok = False
    # 에러가 없으면
    try:
        # 1. 랜드마크 기반으로 LM2 주행 시작
        first_drive_result = drive_landmark_wit_check_block(
            seer=seer,
            target_landmark="LM2",
            drive_interval=0.2
        )

        # 처음 랜드마크 주행이 정상 완료되었으면
        if first_drive_result == "completed":
            print("[NAV] LM2 도착 완료")
            nav_ok = True
            print(f"[NAV] nav_ok : {nav_ok}")
            return

        # 2. 주행 중 Blocked 상태가 발생했으면
        if first_drive_result == "blocked":
            print("[NAV] 2초 대기 후 LM1 복귀 시작")
            time.sleep(2.0)
            # 랜드마크 주행으로 LM1 복귀
            seer.gotargetblock("LM1")
            print("[NAV] LM1 복귀 완료")

            # 3. LM1 -> LM2 직통 edge 차단 후 회피 주행 실행
            nav_ok = navigate_from_lm1_to_lm2_with_blocked_edge(
                navigator=navigator,
                seer=seer
            )
            # 회피 주행이 완료되었으면 종료
            print(f"[NAV] nav_ok : {nav_ok}")
            return
        # 예외적인 경우 디버그 문구 print
        print(f"[NAV] 알 수 없는 주행 결과 : {first_drive_result}")
        print(f"[NAV] nav_ok : {nav_ok}")
    # 최종적으로
    finally:
        # 통신 종료
        seer.socket_close()

# 메인 함수 실행
if __name__ == "__main__":
    main()
