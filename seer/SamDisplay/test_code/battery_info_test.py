# ----------- custom 라이브러리 import ---------------------
# SEER AMR 제어를 위해 SEER_commu 클래스 import
from custom_package.seer_commu import SEER_commu

# 메인 함수 선언
def main():
    # SEER_commu 객체 선언
    seer = SEER_commu()
    # 배터리 level 저장 변수 선언
    battery_level = 0
    # 배터리 온도 저장 변수 선언
    battery_temp = 0
    # 배터리 충전 여부 저장 변수 선언
    battery_charging = 0
    # 현재 배터리 전압 저장 변수 선언
    battery_voltage = 0
    # 햔제 배터리 전류 저장 변수 선언
    battery_current = 0
    # 배터리 최대 허용 충전 전압 저장 변수 선언
    battery_max_charge_voltage = 0
    # 배터리 최대 허용 충전 전류 저장 변수 선언
    battery_max_charge_current = 0
    # 배터리 사이클 저장 변수 선언
    battery_cycle = 0
    # 발생 에러 저장 변수 선언
    err_msg = None
    # 배터리 정보 수신
    battery_level, battery_temp, battery_charging, battery_voltage, battery_current, battery_max_charge_voltage, battery_max_charge_current, battery_cycle, err_msg = seer.get_battery_info()
    # 구분자 print
    print("-------------------------------------------------------------")
    # 배터리 레벨 print
    battery_level = battery_level*100
    print(f"[Battery] 배터리 레벨 : {battery_level} [%]")
    # 배터리 온도 print
    print(f"[Battery] 배터리 온도 : {battery_temp} [도]")
    # 배터리 충전 여부
    print(f"[Battery] 배터리 충전 여부 : {battery_charging}")
    # 배터리 전압 print
    print(f"[Battery] 배터리 전압 : {battery_voltage} [V]")
    # 배터리 전압 print
    print(f"[Battery] 배터리 전류 : {battery_current} [A]")
    # 배터리 최대 허용 충전 전압 print
    print(f"[Battery] 배터리 허용 최대 충전 전압 : {battery_max_charge_voltage} [V]")
    # 배터리 최대 허용 충전 전류 print
    print(f"[Battery] 배터리 허용 최대 충전 전류 : {battery_max_charge_current} [A]")
    # 배터리 cycle print
    print(f"[Battery] 배터리 cycle (정확도 보장 x) : {battery_cycle}")
    # 에러가 있으면
    if err_msg is not None:
        # 에러 print
        print(f"[Battery] 에러 발생 : {err_msg}")
    print("-------------------------------------------------------------")

# 메인 함수 실행
if __name__ == '__main__':
    main()