#  ----------- 범용 라이브러리 import ---------------------
# 시간 핸들링을 위해 time 라이브러리 import
import time
# ----------- custom 라이브러리 import ---------------------
# SEER AMR 제어를 위해 SEER_commu 클래스 import
from custom_package.seer_commu import SEER_commu

# 메인 함수 선언
def main():
    # SEER_commu 객체 선언
    seer = SEER_commu()
    # AMR 노드 저장 변수 선언
    amr_node = None
    # 구분자 print
    print("-------------------------")
    # 에러가 없으면
    try:
        # 무한 반복
        while True:
            # amr_node 확인
            amr_node = seer.get_amr_node()
            # 디버그 문구 print
            print(f"AMR 현재 노드 : {amr_node}")
            print("-------------------------")
            # 1초 딜레이
            time.sleep(1.0)
    # Ctrl+C 발생 시
    except KeyboardInterrupt:
            # 로그 출력
            print("[ACS CONTROLLER] 사용자 종료 요청")

# 메인 함수 실행
if __name__ == '__main__':
    main()