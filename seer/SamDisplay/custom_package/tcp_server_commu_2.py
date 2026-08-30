# ----------------------- 범용 라이브러리 import -----------------------
import socket

# TCP 통신을 위한 TCP_commu 클래스 선언
class TCP_server_commu:
    # 클래스 초기화 함수 선언
    def __init__(self,ip,port):
        # 인자로 받은 ip를 접속허용할 ip 변수에 저장
        self.ip = ip
        # 인자로 받은 port를 접속허용할 port 변수에 저장
        self.port = port
        # timeout 시간 설정(3초)
        self.timeout_sec = 180
        # 서버 소켓 객체를 저장할 변수 선언
        self.server_socket = None
        # 클라이언트 소켓 객체를 저장할 변수 선언
        self.client_socket = None
        # 접속한 클라이언트 주소 및 포트를 저장할 변수 선언
        self.client_info = None

    # 서버 소켓 시작 함수 선언
    def start_server(self):
        # 이미 서버 소켓이 열려있다면
        if self.server_socket is not None:
            # True return
            return True
        # 에러가 없다면
        try:
            # 서버 소켓 객체 생성
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # 소켓 통신 타임아웃을 타임아웃 변수로 설정
            self.server_socket.settimeout(self.timeout_sec)
            # 인자로 받은 ip, port로 서버 소켓 바인드 준비
            # 바인드 : 해당 주소/포트에서 연결을 받을 준비
            self.server_socket.bind((self.ip,self.port))
            # listen 시작(client 1개 허용)
            self.server_socket.listen(1)
            # Server Open 디버그 문구 print
            print("----------------------------------------")
            print("---------- tcp 통신 Server Open -------- ")
            print("----------------------------------------")
            # True return
            return True
        # 에러가 발생하였으면
        except Exception as e:
            # Error 디버그 문구 print
            print(f"----- 서버 open 중 에러발생 : {e} -------")
            print("----------------------------------------")
            # 전체 소켓 닫기
            self.close_all()
            # False return
            return False
        
    # 클라이언트 접속 대기 함수 선언
    def wait_client(self):
        # 이미 서버 소켓이 없다면
        if self.server_socket is None:
            # 서버 open 수행
            server_ok = self.start_server()
            # 서버 시작이 실패하였다면
            if not server_ok:
                self.close_all(self)
                # 클라이언트 접속 대기도 실패
                return False
        # 에러가 없다면
        try:
            # 클라이언트 대기 문구 print
            print("---------- Client 접속 대기 중.. -------- ")
            print("----------------------------------------")
            # 클라이언트가 접속할 때까지 대기 후 클라이언트 소켓 객체 및 주소 반환
            self.client_socket, self.client_info = self.server_socket.accept()
            # 클라이언트 소켓 timeout 설정
            self.client_socket.settimeout(self.timeout_sec)
            # 클라이언트 접속 완료 문구 print
            print("---------- Client 접속 완료!! ---------- ")
            print("----------------------------------------")
            # Client 정보 print
            print(f"[Client] : {self.client_info}")
            print("----------------------------------------")
            # True return
            return True
        # timeout이 발생하였다면
        except socket.timeout:
            # timeout 문구 print
            print("------------- Time out 발생 ------------ ")
            print("----------------------------------------")
            self.close_all()
            return False
        # 그외 에러 발생 시 
        except Exception as e:
            # Error 디버그 문구 print
            print(f"---Client 접속 대기 중 에러발생 : {e} ----")
            print("----------------------------------------")
            # 전체 소켓 닫기
            self.close_all()
            # False return
            return False
        
    # 클라이언트가 송신한 데이터를 bytes로 수신하는 함수 선언
    def rx_client_data(self):
        # 아직 클라이언트 소켓이 없다면
        if self.client_socket is None:
            # 수신할 수 없으므로 빈 바이트 return
            return b""
        # 에러가 없다면
        try:
            # 클라이언트가 보낸 데이터를 최대 1024byte 만큼 수신
            chunk = self.client_socket.recv(1024)
            # 수신한 데이터가 비었다면
            if not chunk:
                # 디버그 문구 print
                print("----- Client 연결 종료 또는 빈 데이터 수신 -----")
                print("----------------------------------------")
                # 빈 바이트 return
                return b""
            # 디버그 문구 print
            print(f"- Client 데이터 수신 완료 : {chunk} -")
            print("----------------------------------------")
            # 수신한 데이터 return
            return chunk
        # 소켓 타임아웃이 발생하였으면
        except socket.timeout:
            # timeout 문구 print
            print("------------- Time out 발생 ------------")
            print("----------------------------------------")
            # timeout은 데이터가 아직 안 온 것일 수 있으므로 소켓은 닫지 않고 빈 바이트 return
            return b""
        # 데이터 수신 중 에러 발생 시
        except Exception as e:
            # Error 디버그 문구 print
            print(f"-- Client 데이터 수신 중 에러발생 : {e} --")
            print("----------------------------------------")
            # 전체 소켓 닫기
            self.close_all()
            # 수신할 수 없으므로 빈 바이트 return
            return b""
        
    # 클라이언트로 데이터를 송신하는 함수 선언
    def tx_client_data(self,data):
        # 아직 클라이언트 소켓이 없다면
        if self.client_socket is None:
            self.close_all(self)
            # 통신할 수 없으므로 False return
            return False
        # 에러가 없다면
        try:
            # 인자로 받은 data가 str이면
            if isinstance(data,str):
                # utf-8로 인코딩하여 bytes로 변환
                tx_data = data.encode("utf-8")
            # str이 아니라면 bytes로 변환
            else:
                tx_data = bytes(data)
            # 데이터 송신
            self.client_socket.sendall(tx_data)
            # 데이터 송신 완료 문구 print
            print(f"-- 데이터 송신 완료 : {tx_data}----------")
            print("----------------------------------------")
            # return True
            return True
        # 데이터 송신 중 에러 발생 시 
        except Exception as e:
            # Error 디버그 문구 print
            print(f"- Client로 데이터 송신 중 에러발생 : {e} --")
            print("----------------------------------------")
            # 전체 소켓 닫기
            self.close_all()
            # Fasle return
            return False
        
    # bytes 데이터를 str 형식으로 변환 함수 선언
    def decode_data(self,data):
        return data.decode("utf-8", errors="replace").strip()

    # 모든 소켓 닫는 함수 선언
    def close_all(self):
        # 클라이언트 소켓이 있으면
        if self.client_socket is not None:
            # 먼저 클라이언트 연결 정리
            self.client_socket.close()
        # 서버 소켓이 있으면
        if self.server_socket is not None:
            # 그 다음 서버 소켓 정리
            self.server_socket.close()
        

            
            




        
