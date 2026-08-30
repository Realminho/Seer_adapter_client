# Robot inventory / adapter identity source.
#
# amr-adaptor.service는 항상 이 파일을 본다. robot 블록이 1개면 그 로봇을
# main.py --robot <id>로 실행하고, 2개 이상이면 run_multi.py로 모든 로봇을
# 같은 서비스 아래에서 실행한다. 로봇별 identity/IP/EZI/MQTT override는 이
# 파일이 단일 출처이고, config/config.toml은 공통 기본값을 담는다.
#
# 경로 키(config 등)의 상대 경로는 이 파일이 있는 디렉터리를 기준으로 푼다.
#
# Key reference:
#   robot "HN-SH6-TR-001" { ... }
#     JIBOT 한 대의 inventory entry. 블록 라벨이 로봇 id이며 robot identity의
#     단일 출처다. VDA5050 serialNumber와 MQTT topic suffix가 되므로 동시에
#     뜨는 adapter 사이에서 반드시 고유해야 한다.
#
#   vehicle_ip = "10.0.0.11"     실차 권장. JIBOT TCP 제어 주소.
#   vehicle_port = 7273          선택. 생략하면 7273 기본값.
#   ezi_io = "10.8.8.87"         실차 권장. EZI IO module 주소.
#   ezi_motor = "10.8.8.2"       실차 권장. EZI motor driver 주소.
#   mqtt_host = "192.168.3.108"  선택. config.toml [mqtt_broker].host를 대체.
#   mqtt_port = 11883            선택. config.toml [mqtt_broker].port를 대체.
#   config = "robot-a.toml"      선택. 인스턴스 전용 config.toml 경로.
#   extensions = "a-ext.hcl"     선택. 인스턴스 전용 extensions.hcl 경로.
#   recipes = "a-recipes.hcl"     선택. 인스턴스 전용 recipes.hcl 경로.
#   simulator = true             선택. true면 Python JIBOT simulator를 쓴다.
#   extra_args = ["--x"]         선택. main.py에 추가로 붙일 CLI 인자 배열.

robot "HN-SH6-TR-001" {
  simulator = true
  mqtt_host = "192.168.2.61"
  # vehicle_ip   = "127.0.0.1"
  # vehicle_port = 7273
  # ezi_io       = "10.8.8.87"
  # ezi_motor    = "10.8.8.2"
  # mqtt_port    = 11883
  # config       = "HN-SH6-TR-001.toml"
  # extra_args   = []
}

robot "HN-SH6-TR-002" {
  simulator = true
  mqtt_host = "192.168.2.61"
  # vehicle_ip   = "127.0.0.1"
  # ezi_io       = "10.8.8.88"
  # ezi_motor    = "10.8.8.3"
  # config       = "HN-SH6-TR-002.toml"
  # extra_args   = []
}
