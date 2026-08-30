# ----------- 범용 라이브러리 import -----------------------
# 시간 핸들링을 위한 time 라이브러리 import
import time
# ----------- custom 라이브러리 import ---------------------
# DIO모듈 제어를 위한 DIO_Commu 클래스 import
from custom_package.dio_commu import DIO_Commu

# main 함수 선언
def main():
    # Ezi-io DIO 모듈 제어 객체 선언
    dio = DIO_Commu(ip="192.168.192.2",port=2001,timeout=5.0)
    # 에러가 없다면
    try:
        # dio 모듈과 연결
        dio.dio_connect()
        # 모듈 정보 읽기
        dio_type,version = dio.get_dio_module_info()
        # DIO 정보 출력
        print(f"[EZI-IO] dio_type : {dio_type}, version : {version}")
        # DO 4번핀 정보 read
        dio_info=dio.read_do(4)
        # D0 4번핀 정보 print
        print("-----------------------------------------")
        print(f"[EZI-IO] dio 4번핀 정보 : {dio_info}")
        # DO 4번핀 Ture로 설정
        dio.set_do_on(4)
        # 0.1초 대기
        time.sleep(0.1)
        # DO 4번핀 True로 설정 문구 print
        print("-----------------------------------------")
        print(f"[EZI-IO] dio 4번핀 True로 설정")
        # DO 4번핀 상태 읽기
        dio_info=dio.read_do(4)
        # DO 4번핀 상태 다시 읽기
        print(f"[EZI-IO] dio 4번핀 정보 : {dio_info}")
        # DO 4번핀 False로 설정
        dio.set_do_off(4)
        # DO 4번핀 False로 설정 문구 print
        print("-----------------------------------------")
        print(f"[EZI-IO] dio 4번핀 True로 설정")
        # 0.1초 대기
        time.sleep(0.1)
        # DO 4번핀 상태 읽기
        dio_info=dio.read_do(4)
        # DO 4번핀 상태 다시 읽기
        print(f"[EZI-IO] dio 4번핀 정보 : {dio_info}")
        print("-----------------------------------------")
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
        # 연결 종료
        dio.dio_close()

# 메인 함수 실행
if __name__ == "__main__":
    main()

