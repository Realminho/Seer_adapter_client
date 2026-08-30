# ------------------- 범용 라이브러리 import ----------------------
# 시간 handle을 위한 time 라이브러리 import
import time
# ----------- custom 라이브러리 import ---------------------
# MQTT_Util 클래스 import
from custom_package.mqtt_commu import MQTT_Util

# 메인 함수 선언 
def main():
    # 브로커 서버 ip 선언
    broker_ip = "127.0.0.1"
    # 브로커 서버 port 선언
    broker_port = 1883
    # 테스트에서 사용할 topic 선언
    test_topic = "test/mqtt"

    # MQTT_Util 클래스 객체 선언
    mqtt_util = MQTT_Util(broker_ip,broker_port)
    # 연결 안정화를 위해 잠시 대기
    time.sleep(0.5)
    # 에러가 없으면
    try:
        # 무한 반복
        while True:
            # 송신할 메세지 입력 받기
            tx_message = input("[PUB] 송신할 메시지 : ")
            # 종료 메시지가 입력되면 송신 종료
            if tx_message.lower() == "q":
                print("[PUB] 송신 종료")
                # 루프 종료 
                break
            # 입력한 메시지 topic으로 publish
            mqtt_util.publish_string(test_topic,tx_message)
            # 0.1초 대기
            time.sleep(0.1)
    # Ctrl+C 종료 처리
    except KeyboardInterrupt:
        print("[PUB] Ctrl+C 감지, 프로그램 종료")
    # 최종적으로
    finally:
        # mqtt 종료
        mqtt_util.shutdown_mqtt()


# 메인 함수 선언
if __name__ =="__main__":
    main()
