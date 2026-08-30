# ----------- custom 라이브러리 import ---------------------
# SEER AMR 제어를 위해 SEER_commu 클래스 import
from custom_package.seer_commu import SEER_commu

# 메인 함수 선언
def main():
    # SEER_commu 객체 선언
    seer = SEER_commu()
    # AMR X 좌표 저장 변수 선언
    amr_x = 0
    # AMR Y 좌표 저장 변수 선언
    amr_y = 0
    # AMR 각도 저장 변수 선언
    amr_theta = 0
    # 발생 에러 저장 변수 선언
    err_msg = None
    # AMR 위치 정보 수신
    amr_x,amr_y,amr_theta = seer.get_amr_cord()
    # 구분자 print
    print("-------------------------------------------------------------")
    # AMR X 좌표 print
    print(f"[Coord] AMR X 좌표 : {round(amr_x,3)} [m]")
    # AMR Y 좌표 print
    print(f"[Coord] AMR Y 좌표 : {round(amr_y,3)} [m]")
    # AMR theta print
    print(f"[Coord] AMR theta : {round(amr_theta,3)} [degree]")
    # 에러가 있으면
    if err_msg is not None:
        # 에러 print
        print(f"[Coord] 에러 발생 : {err_msg}")
    print("-------------------------------------------------------------")

# 메인 함수 실행
if __name__ == '__main__':
    main()