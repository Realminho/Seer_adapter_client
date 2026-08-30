# ----------- 범용 라이브러리 import -----------------------
# 시간 핸들링을 위한 time 라이브러리 import
import time
# ----------- custom 라이브러리 import ---------------------
# DIO모듈 제어를 위한 DIO_Commu 클래스 import
from custom_package.sam_dio_commu import DIO_Commu

# main 함수 선언
def main():
    # Ezi-IO DIO 모듈 제어 객체 선언
    dio = DIO_Commu(ip="192.168.0.13", port=2001, timeout=5.0)
    # 에러가 없다면
    try:
        print("----- 컨베이어 테스트 시작 ----")
        # DIO 모듈과 TCP 연결
        dio.dio_connect()
        # 모듈 정보 읽기
        dio_type, version = dio.get_dio_module_info()
        # DIO 정보 출력
        print(f"[EZI-IO] dio_type : {dio_type}, version : {version}")
        print("-----------------------------------------")
        # 모든 dio low 상태로 제어
        for do_no in range(0, 32):
            # 현재 DO 번호 출력
            print(f"[EZI-IO] DO {do_no}번 LOW 설정")
            # 해당 DO LOW 설정
            dio.set_do_off(do_no)
            # 0.1초 대기
            time.sleep(0.1)
            # 해당 DO 상태 읽기
            do_info = dio.read_do(do_no)
            # 해당 DO 상태 출력
            print(f"[EZI-IO] DO {do_no}번 상태 : {do_info}")
            print("-----------------------------------------")
        # 컨베이어 AMR 쪽으로 5초 제어
        # 5초 동안 DO7 Low, DO8 High로 제어
        dio.set_do_off(7)
        dio.set_do_on(8)
        time.sleep(8)
        # 컨베이어 AMR 반대 쪽으로 5초 제어
        # 5초 동안 DO7 High, DO8 High로 제어
        dio.set_do_on(7)
        dio.set_do_on(8)
        time.sleep(8)
        # 컨베이어 정지
        dio.set_do_off(7)
        dio.set_do_off(8)
        print("----- 컨베이어 테스트 완료 ----")
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