# ----------- 범용 라이브러리 import -----------------------
# 시간 핸들링을 위한 time 라이브러리 import
import time

# ----------- custom 라이브러리 import ---------------------
# DIO모듈 제어를 위한 DIO_Commu 클래스 import
from custom_package.dio_commu import DIO_Commu


# main 함수 선언
def main():
    # Ezi-IO DIO 모듈 제어 객체 선언
    dio = DIO_Commu(ip="192.168.0.13", port=2001, timeout=5.0)

    # 에러가 없다면
    try:
        # DIO 모듈과 TCP 연결
        dio.dio_connect()

        # 모듈 정보 읽기
        dio_type, version = dio.get_dio_module_info()

        # DIO 정보 출력
        print(f"[EZI-IO] dio_type : {dio_type}, version : {version}")
        print("-----------------------------------------")
        
        # AMR Break lock 해제
        dio.set_do_on(31)
        # AMR Break lock 설정
        # dio.set_do_off(31)
        # 해당 DO 상태 읽기
        do_info = dio.read_do(31)
        # 해당 DO 상태 출력
        print(f"[EZI-IO] DO {31}번 상태 : {do_info}")
        time.sleep(1)

    # Ctrl+C 입력 시
    except KeyboardInterrupt:
        # 사용자 종료 출력
        print("사용자 종료 키 입력")

    # 예외 발생 시
    except Exception as e:
        # 에러 출력
        print(f"[ERROR] {e}")

    # 최종적으로
    finally:
        # DIO 연결 종료
        dio.dio_close()


# 메인 함수 실행
if __name__ == "__main__":
    main()