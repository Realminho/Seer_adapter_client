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
    # 토픽을 저장할 변수 선언
    saved_topic_payload = None

    # MQTT_Util 클래스 객체 선언
    mqtt_util = MQTT_Util(broker_ip,broker_port)
    # 연결 안정화를 위해 0.5초 대기
    time.sleep(0.5)
    # 테스트 topic 구독
    mqtt_util.subcribe_topic(test_topic)
    # 디버그 문구 print
    print(f"[SUB] Topic subscribe 완료 : {test_topic}")
    print("[SUB] 메시지 수신 대기 중... ")
    # 에러가 없으면
    try:
        # 무한 반복
        while True:
            # 현재 수신한 토픽의 payload 저장
            receive_topic_payload = mqtt_util.retrun_topic_payload(test_topic)
            if receive_topic_payload == "종료":
                print("[SUB] 종료 명령 수신")
                break
            # 수신한 토픽이 None이 아니면
            if receive_topic_payload is not None and receive_topic_payload != saved_topic_payload:
                # 수신한 토픽 print
                print(f"[SUB] 수신한 Topic의 payload : {receive_topic_payload}")
                # 현재 payload를 이전 payload로 저장
                saved_topic_payload = receive_topic_payload
            # 1.0초 대기
            time.sleep(1.0)
    # Ctrl+C 종료 처리
    except KeyboardInterrupt:
        print("[SUB] Ctrl+C 감지, 프로그램 종료")
    # 최종적으로
    finally:
        # mqtt 종료
        mqtt_util.shutdown_mqtt()


# 메인 함수 실행
if __name__ == "__main__":
    main()
