# ----------- 범용 라이브러리 import -----------------------
# 시간 핸들링을 위한 time 라이브러리 import
import time
# ----------- custom 라이브러리 import ---------------------
# DIO모듈 제어를 위한 DIO_Commu 클래스 import
from custom_package.pio_commu import PIO_Commu

# 메인 함수 선언
def main():
    # PIO Commu 객체 선언
    pio = PIO_Commu(dio_ip='192.168.192.21',dio_port=2001)
    # 에러가 없으면
    try:
        # PIO와 연결된 DIO 모듈 선언
        pio.pio_connect()
        # Loading 시퀀스 실행
        loading_result = pio.pio_loading_sequence()
        # UnLoading 시퀀스 실행
        # unloading_result = pio.pio_unloading_sequence()
    # ctrl+c가 눌렸으면
    except KeyboardInterrupt:
        # 디버그 문구 print
        print("[PIO TEST] 키보드 입력 종료")
    # 기타 에러 발생 시 
    except Exception as e:
        # 디버그 문구 print
        print(f"[PIO TEST] 에러발생 : {e} ")
    # 최종적으로 
    finally:
        # PIO 출력 LOW로 설정
        pio.pio_close()

# 메인 함수 실행
if __name__ == '__main__':
    main()