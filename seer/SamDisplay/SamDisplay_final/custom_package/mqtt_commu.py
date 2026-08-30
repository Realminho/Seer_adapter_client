# ------------------- 범용 라이브러리 import ----------------------
# mqtt 통신을 위한 paho.mqtt.client 라이브러리 import
import paho.mqtt.client as mqtt
# 시간 handle을 위한 time 라이브러리 import
import time

# mqtt 통신을 위한 클래스 선언
class MQTT_Util():
    # 클래스 초기화 함수 선언
    def __init__(self, broker_ip="127.0.0.1", broker_port=1883, keepalive=60):
        # 브로커 서버 ip 주소 설정
        self.broker_ip = broker_ip
        # 브로커 서버 포트 번호 설정
        self.broker_port = broker_port
        # 데이터 송수신이 없어도 mqtt 브로커 서버와 연결 유지 시간
        self.keepalive = keepalive
        # mqtt 클라이언트 생성
        self.client = mqtt.Client()
        # 마지막으로 수신한 topic별 payload 저장 dict 선언
        self.topic_payload_dict = {}
        # 마지막으로 mqtt 통신 진행 시간 저장 dict 선언
        self.mqtt_topic_last_time = {}
        # mqtt 메시지를 수신하였을 때 호출되는 콜백 함수 message_receive_callback 함수로 설정
        self.client.on_message = self.message_receive_callback
        # 에러가 없다면
        try:
            # 설정한 브로커 서버 ip, port,60초마다 broker server에 접속하게 설정
            self.client.connect(self.broker_ip,self.broker_port,keepalive=self.keepalive)
            # debug 문구 print
            print(f"---- [MQTT] Connected to broker server , ip : {self.broker_ip}, port : {self.broker_port} ----")
        # 에러 발생 시 
        except Exception as e:
            # 디버그 문구 print
            print(f"---- [MQTT] Connect to borker server failed : {e} ----")

        # mqtt loop start
        self.client.loop_start()

    # 메시지 수신 시 실행될 콜백 함수 선언
    def message_receive_callback(self,client,userdata,msg):
        # 수신된 데이터의 payload를 UTF-8로 디코딩
        payload = msg.payload.decode("utf-8")
        # 현재 시간 저장
        now = time.time()
        # 수신 로그 출력
        print(f"[MQTT] Received topic : {msg.topic} , Received payload : {payload}")
        # 토픽별 마지막 payload 저장
        self.topic_payload_dict[msg.topic] = payload
        # 마지막 토픽 수신 시간 저장
        self.mqtt_topic_last_time[msg.topic] = now

    # mqtt 문자열 발행 함수 선언 
    def publish_string(self,topic,payload):
        # 에러가 없으면
        try:
            # 함수 인자로 받은 토픽, 문자열로 변환한 payload 데이터로 mqtt 메시지 publish
            self.client.publish(topic, str(payload))
            # 디버그 문구 print
            print(f"[MQTT] Publish topic: {topic}, Publish payload: {payload}")
        # 에러 발생 시 
        except Exception as e:
            # publish failed 출력
            print(f"[MQTT] Publish failed : {e}")

    # 토픽 구독 함수 선언
    def subcribe_topic(self,topic):
        # 에러가 없으면
        try:
            # 함수 인자로 받은 토픽 구독
            self.client.subscribe(topic)
        # 에러 발생 시 
        except Exception as e:
            # subscribe failed 출력
            print(f"[MQTT] Subscribe failed : {e}")

    # 토픽별 payload return 함수 선언
    def retrun_topic_payload(self,topic):
        return self.topic_payload_dict.get(topic, None)
    
    # 토픽별 마지막 수신 시간 return 함수 선언
    def return_topic_last_time(self, topic):
        return self.mqtt_topic_last_time.get(topic, 0.0)

    # mqtt 종료 함수 선언
    def shutdown_mqtt(self):
        # 에러가 없으면
        try:
            # mqtt 루프 중지
            self.client.loop_stop()
            # 브로커 서버와 연결 중지
            self.client.disconnect()
            # 디버그 문구 print
            print(f"---- [MQTT] Disconnectd to broker ----")
        # 에러 발생 시 
        except Exception as e:
            # publish failed 출력
            print(f"---- [MQTT] 종료 중 에러 발생 : {e} ----")