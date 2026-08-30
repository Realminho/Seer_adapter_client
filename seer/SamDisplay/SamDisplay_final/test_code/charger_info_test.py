# 시간 핸들링을 위한 time 라이브러리 import
import time

# 충전기 통신 클래스 import
from custom_package.charger_commu import Charger_commu


# 메인 함수 선언
def main():
    # 충전기 통신 객체 생성
    charger = Charger_commu(
        ip="192.168.0.83",
        status_port=20204,
        timeout=5.0
    )

    # 에러가 발생해도 소켓 종료를 하기 위한 try문
    try:
        # 충전기 연결
        if charger.charger_connect() is not True:
            # 연결 실패 시 종료
            return

        # 반복 조회
        while True:
            # 충전기 원본 상태 조회
            raw_data = charger.get_charger_status_raw()

            # 원본 데이터 출력
            print("[RAW]", raw_data)

            # 충전기 상태 보기 좋게 출력
            charger.get_charger_info()

            # 1초 대기
            time.sleep(1.0)

    # Ctrl+C 입력 시
    except KeyboardInterrupt:
        # 사용자 종료 로그 출력
        print("\n[Charger Test] 사용자 종료")

    # 마지막 종료 처리
    finally:
        # 충전기 소켓 종료
        charger.socket_close()


# 메인 함수 실행
if __name__ == "__main__":
    main()