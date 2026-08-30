# ----------------------------- 범용 라이브러리 import -----------------------------
# 시간 핸들링을 위한 time 라이브러리 import
import time
# json 파일 핸들링을 위한 json 라이브러리를 import
import json
# 파일 경로 처리를 위한 path 라이브러리 import
from pathlib import Path

# ======================================================================================================================
# 카메라로 인식한 아루코마커 저장, 로드 클래스 선언
# ======================================================================================================================
class Aruco_Answer_Commu:
    # 클래스 초기화 함수 선언
    def __init__(self,answer_path="/home/mic-711/Desktop/SamDisplay/side_docking_package/aruco_answer_json/test_aruco.json"):
         # 정답 ArUco 정보를 저장할 JSON 파일 경로를 Path 객체로 저장
         self.answer_path = Path(answer_path)
    # =========================================
    # 인식된 아루코마커 정답으로 저장 관련 함수 선언
    # =========================================
    # 현재 인식된 마커 정보 정답으로 저장 함수 선언
    def save_answer_marker(self,marker_info,path=None):
        # path 인자가 따로 들어오지 않았다면
        if path is None:
            # 클래스에 저장된 기본 정답 파일 경로 사용
            answer_aruco_path = self.answer_path
        else:
            # 아루코마커 정보 경로 변수 선언
            answer_aruco_path = Path(path)
        # 정답 아루코마커 경로가 존재하지 않는다면
        # marker_info가 없다면
        if marker_info is None:
            # 저장할 마커 정보가 없다는 로그 출력
            print("[ARUCO_ANSWER] 저장할 마커 정보 없음")
            # 저장 실패이므로 False 반환
            return False
        # 마커가 검출되지 않은 상태라면
        if not marker_info.get("found", False):
            # 마커 미검출 로그 출력
            print("[ARUCO_ANSWER] 마커가 검출되지 않아 저장 없음")
            # 저장 실패이므로 False 반환
            return False
        # yaw 값이 없다면
        if marker_info.get("yaw_deg") is None:
            # yaw 정보 부족 로그 출력
            print("[SIDE_ARUCO_ANSWER] yaw_deg 값이 없어 저장할 수 없습니다.")
            # 저장 실패이므로 False 반환
            return False
        # marker_x 값이 없다면
        if marker_info.get("marker_x") is None:
            # marker_x 정보 부족 로그 출력
            print("[SIDE_ARUCO_ANSWER] marker_x 값이 없어 저장할 수 없습니다.")
            # 저장 실패이므로 False 반환
            return False
        # 인자로 받은 marker_info에서 JSON으로 저장할 정답 데이터를 dict 형태로 구성
        answer_data = {
            # 저장 시간 기록
            "save_time": time.time(),
            # 정답 ArUco 마커 ID 저장
            "aruco_id": marker_info.get("aruco_id"),
            # 화면 중심 대비 마커 중심의 픽셀 차이 저장
            "target_offset_px": marker_info.get("offset_px"),
            # 카메라와 마커의 평행 정렬 기준이 되는 yaw 값 저장
            "target_yaw_deg": marker_info.get("yaw_deg"),
            # 측면 카메라 기준 x 좌표 저장(AMR 전진/후진 보정 기준으로 사용)
            "target_marker_x": marker_info.get("marker_x"),
            # 측면 카메라 기준 z 좌표를 저장
            "target_marker_z": marker_info.get("marker_z")}
        # 인자로 설정한 경로의 상위 폴더가 없다면 자동으로 생성
        answer_aruco_path.parent.mkdir(parents=True, exist_ok=True)
        # 정답 JSON 파일을 쓰기 모드 설정.
        with open(answer_aruco_path, "w", encoding="utf-8") as file:
            # answer_data를 JSON 파일로 저장
            json.dump(answer_data, file, ensure_ascii=False, indent=4)
        print(f"[ARUCO_ANSWER] 정답 마커 저장 완료 : {answer_aruco_path}")
        # 저장 성공이므로 True 반환
        return True

    # detector를 사용하여 현재 보이는 target_id 마커를 정답으로 저장하는 함수 선언
    def save_current_marker(self,detector,target_id,sample_count=10,show_window=True,path=None):
        # path 인자가 따로 들어오지 않았다면
        if path is None:
            # 클래스에 저장된 기본 정답 파일 경로 사용
            answer_aruco_path = self.answer_path
        else:
            # 아루코마커 정보 경로 변수 선언
            answer_aruco_path = Path(path)
        # detector에서 여러 프레임 평균 기반의 안정적인 마커 정보 취득
        marker_info = detector.get_stable_marker(
            target_id=target_id,
            sample_count=sample_count,
            show_window=show_window)
        # 안정적인 마커 정보를 얻지 못했다면
        if marker_info is None:
            # 저장 실패 로그 출력
            print("[ARUCO_ANSWER] 안정적인 마커 정보 저장 실패")
            # 저장 실패이므로 False를 반환합니다.
            return False
        # 얻은 마커 정보 반환
        return self.save_answer_marker(marker_info=marker_info,path=answer_aruco_path)

    # =========================================
    # 저장된 아루코마커 정보 로드 관련 함수 선언
    # =========================================
    def load_answer_aruco(self,path=None):
        # path 인자가 따로 들어오지 않았다면
        if path is None:
            # 클래스에 저장된 기본 정답 파일 경로 사용
            answer_aruco_path = self.answer_path
        else:
            # 아루코마커 정보 경로 변수 선언
            answer_aruco_path = Path(path)
        # 정답 아루코마커 경로가 존재하지 않는다면
        if not answer_aruco_path.exists():
            # 파일 없음 로그 출력
            print(f"[ARUCO_ANSWER] 정답 마커 파일 X : {answer_aruco_path}")
            # 로드 실패이므로 None 반환
            return None
        # 정답 JSON 파일을 읽기 모드로 불러오기
        with open(answer_aruco_path, "r", encoding="utf-8") as file:
            # JSON 파일 내용을 dict 형태로 로드
            answer_data = json.load(file)
        # 정답 아루코마커 로드 완료 로그를 출력
        print("[SIDE_ARUCO_ANSWER] 정답 마커 로드 완료")
        # 로드한 정답 데이터를 반환
        return answer_data
    
    
        


