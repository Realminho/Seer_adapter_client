# netprotocol에 정의된 상수 import
from netprotocol.rbkNetProtoEnums import *
# netprotocol에 정의된 함수 import 
from netprotocol import rbkNetProtoEnums
# json 핸들링을 위한 json 라이브러리 import
import json
# TCP 통신을 위한 socket 라이브러리 import
import socket
# os 라이브러리 import
import os
# 시간 핸들링을 위한 time 라이브러리 import
import time

# 소켓 통신을 위한 통신 객체 선언
so = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
# AMR IP의 작업 할당 포트로 TCP 접속
so.connect(('192.168.2.132', API_PORT_TASK ))
# so.connect(('127.0.0.1', API_PORT_STATE))
# 타임아웃 5초로 설정
so.settimeout(5)

# packMsg 함수를 사용하여 id LM5로 이동 요청
# req_Id : 1
# 메시지 타입 : 목표점 이동
# json body : "id":"LM5"
so.send(packMsg(1, robot_task_gotarget_req ,
                {"id": "LM2" , "angle": 1.52}))
# 에러가 없으면
try:
    # 소켓으로 헤더길이만큼 16byte만 수신하여 data에 저장
    data = so.recv(16)
# 타임아웃이 발생하면 
except socket.timeout:
    # timeout print
    print('timeout')
    # 프로그램 종료
    quit()

# 응답의 json 길이 저장 변수 초기화
jsonDataLen = 0
# 응답의 reqNum 저장 변수 선언
backReqNum = 0

# 만약 패킷이 16byte미만이라면
if(len(data) < 16):
    # 헤더 길이다 부족하다는 에러 표출
    print('pack head error')
    # 수신한 데이터 출력
    print(data)
    # 윈도우에서 콘솔 정지 명령 실행
    os.system('pause')
    # 소켓 통신 종료
    so.close()
    quit()
# 헤더가 정상이라면
else:
    # 데이터에서 JSON 길이와 req_num 수신하여 저장
    jsonDataLen, backReqNum = unpackHead(data)
    # print
    print('json datalen: %d, backReqNum: %d' % (jsonDataLen, backReqNum))

# 만약 json Body가 있다면
if(jsonDataLen > 0):
    # 최대 1024 byte 수신
    data = so.recv(1024)
    # json으로 변환
    ret = json.loads(data)
    # payload print
    print(ret)

# 1초 정지
time.sleep(1)
# 소켓 통신 종료
so.close()
