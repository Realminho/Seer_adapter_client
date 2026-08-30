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


# 카메라 화면을 계속 갱신하면서 목표 지점까지 주행하는 함수 선언
def gotarget_with_camera_view(seer, detector, target_landmark, target_id=None):
    # 주행 시작 문구 출력
    print(f"[AMR] {target_landmark} 주행 시작")
    # AMR에 목표 지점 주행 명령 전송
    seer.gotarget(target_landmark)
    # 새 주행 명령 직후 이전 작업 완료 상태가 남아있을 수 있으므로 초기 시간 저장
    start_time = time.time()
    # 새 작업 상태로 전환된 것을 확인했는지 저장하는 변수
    task_started = False
    # 완료 상태가 연속으로 몇 번 확인되었는지 저장하는 변수
    completed_count = 0
    # 완료 상태를 몇 번 연속 확인해야 진짜 도착으로 판단할지 설정
    completed_confirm_count = 3
    # 주행 명령 직후 최소 대기 시간 설정
    min_wait_sec = 0.3
    # 주행 완료될 때까지 반복
    while True:
        # 카메라 화면 갱신을 위해 아루코 검출 함수 반복 호출
        detector.detect_aruco(target_id=target_id, show_window=True)
        # 현재 작업 상태 확인
        task_status = seer.get_task_status()
        # 현재 시간 확인
        now_time = time.time()
        # 디버그 문구 Print
        # print(f"[AMR] target={target_landmark}, task_status={task_status}, task_started={task_started}, completed_count={completed_count}")
        # task_status가 4가 아니라면 새 작업이 실제로 시작/진행 중인 상태로 판단
        if task_status != 4:
            # 새 작업 상태 확인 완료
            task_started = True
            # 완료 연속 카운트 초기화
            completed_count = 0
        # task_status가 4라면
        else:
            # 새 작업 시작을 확인하기 전이면 이전 작업의 완료 상태일 수 있으므로 무시
            if not task_started:
                # 최소 대기 시간 전이면 이전 완료 상태로 보고 무시
                if now_time - start_time < min_wait_sec:
                    pass
                # 최소 대기 시간이 지나도 계속 4면 아직 새 작업 상태 갱신이 늦는 중일 수 있으므로 무시
                else:
                    pass
            # 새 작업이 시작된 것을 확인한 뒤 4가 들어오면 진짜 완료 후보
            else:
                # 완료 상태 연속 확인 횟수 증가
                completed_count += 1
                # 완료 상태가 지정 횟수 이상 연속 확인되면 도착 완료로 판단
                if completed_count >= completed_confirm_count:
                    # 도착 완료 문구 출력
                    print(f"[AMR] {target_landmark} 도착 완료")
                    # 반복 종료
                    break
        # 너무 빠른 반복 방지를 위한 짧은 대기
        time.sleep(0.1)

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
    # 카메라 시작
    detector.camera_start()
    # LM1로 주행하면서 카메라 화면 계속 갱신
    gotarget_with_camera_view(seer=seer,detector=detector,target_landmark="LM1",target_id=None)
    # 0.1초 대기
    time.sleep(0.1)
    # 아루코마커 기반 도킹 시작
    dock_ok = docker.align_to_saved_target(show_window=True)
    # 도킹 실패시
    if not dock_ok:
        # 디버그 문구 print
        print("[AMR] 도킹 실패")
    else:
        # 도킹 성공 문구 print
        print("[AMR] 도킹 성공")
    # 0.1초 대기
    time.sleep(0.1)
    # LM8로 주행하면서 카메라 화면 계속 갱신
    gotarget_with_camera_view(seer=seer,detector=detector,target_landmark="LM8",target_id=None)
    # 0.01초 대기
    time.sleep(0.01)
    # LM4로 주행하면서 카메라 화면 계속 갱신
    gotarget_with_camera_view(seer=seer,detector=detector,target_landmark="LM4",target_id=None)
    # 0.01초 대기
    time.sleep(0.01)
    # LM6으로 주행하면서 카메라 화면 계속 갱신
    gotarget_with_camera_view(seer=seer,detector=detector,target_landmark="LM6",target_id=None)
    # 카메라 종료
    detector.camera_stop()
    # 통신 종료
    seer.socket_close()

# 메인 함수 실행
if __name__ == '__main__':
    main()