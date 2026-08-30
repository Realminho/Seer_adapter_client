# ---------------------------- custom 라이브러리 import -------------------
# SEER AMR 제어를 위해 SEER_commu 클래스 import
from custom_package.seer_commu import SEER_commu
# ----------------------------- 범용 라이브러리 import ---------------------
# 시간 핸들링을 위해 time 라이브러리 import
import time
# 날짜 및 시간 출력을 위한 datetime 라이브러리 import
from datetime import datetime

# 메인 함수 선언
def main():
    # SEER_commu 객체 선언
    seer = SEER_commu()
    # 충전 Flag 변수 선언
    charging_flag = None
    # 충전 시작 저장 변수 선언
    charge_start_time = None
    # 충전 종료 저장 변수 선언
    charge_end_time = None
    # 에러가 없다면
    try:
        # 무한 반복
        while True:
            # 배터리 정보 수신
            battery_level, battery_temp, battery_charging, battery_voltage, battery_current, battery_max_charge_voltage, battery_max_charge_current, battery_cycle, err_msg = seer.get_battery_info()
            # 현재 시간 저장
            now_time = datetime.now()
            # 배터리 level % 단위로 변환
            battery_level = battery_level*100
            # 구분자 print
            print("-------------------------------------------------------------")
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
            # 충전 Flag 값이 존재 한다면
            if charging_flag is not None:
                # 이전 충전 여부가 False이고 현재 충전 상태가 True이면  
                if (charging_flag is False) and (battery_charging is True):
                     # 충전 시작 시간 저장
                    charge_start_time = now_time
                    # 충전 시작 시간 출력
                    print(f"[Battery] 충전 시작 시간 : {charge_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
                # 이전 충전 여부가 True이고 현재 충전 상태가 False이면
                elif (charging_flag is True) and (battery_charging is False):
                    # 충전 종료 시간 저장
                    charge_end_time = now_time
                    # 충전 종료 시간 출력
                    print(f"[Battery] 충전 종료 시간 : {charge_end_time.strftime('%Y-%m-%d %H:%M:%S')}")
            # 현재 충전 상태를 이전 충전 Flag에 저장
            charging_flag = battery_charging
            # 저장된 최근 충전 종료 시간이 있으면 출력
            if charge_end_time is not None:
                print(f"[Battery] 마지막 충전 시간 : {charge_end_time.strftime('%Y-%m-%d %H:%M:%S')}")
            print("-------------------------------------------------------------")
            # 1초 딜레이
            time.sleep(1)
    # ctrl+c가 눌렸으면
    except KeyboardInterrupt:
        # 종료 안내 문구 출력
        print("[Battery] 배터리 모니터링 종료")
    # 최종적으로는
    finally:
        # SEER 소켓 종료
        seer.socket_close()

# 메인함수 실행
if __name__ == '__main__':
    main()

