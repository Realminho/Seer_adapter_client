# Ubuntu 명령줄 Wi-Fi 연결 매뉴얼

이 문서는 화면(GUI)이 없거나 뜨지 않는 Ubuntu 장비에서 터미널만으로 Wi-Fi를 켜고,
주변 AP 목록을 확인하고, 접속하는 절차를 설명한다. 콘솔 직접 접속, 유선 SSH,
시리얼 접속 등 명령을 입력할 수 있는 상태를 전제로 한다.

Ubuntu는 무선 관리 방식이 장비마다 다르다. 데스크톱과 일부 서버는
**NetworkManager(`nmcli`)** 를 쓰고, 최소 설치 서버와 로봇 온보드 PC는
**netplan + wpa_supplicant** 를 쓴다. 명령이 서로 호환되지 않으므로 먼저 어느
방식인지 확인한 뒤 해당 장을 따른다.

## 1. 어느 방식인지 확인

```bash
systemctl is-active NetworkManager
ip -br link                       # 무선 인터페이스 이름 확인 (wlan0, wlp2s0 ...)
```

- `active`가 나오면 → [2장 nmcli](#2-nmcli-networkmanager-장비)
- `inactive` / `unknown`이 나오면 → [3장 netplan + wpa_supplicant](#3-netplan--wpa_supplicant-networkmanager-없는-장비)

무선 인터페이스 이름은 장비마다 다르다. 이 문서는 `wlan0`으로 표기하므로,
`ip -br link` 결과에 맞춰 바꿔 입력한다.

## 2. nmcli (NetworkManager 장비)

### 2.1 무선 켜기

```bash
rfkill list                       # 차단 상태 확인
sudo rfkill unblock wifi          # 소프트 블록 해제
nmcli radio wifi on
nmcli radio wifi                  # "enabled" 가 나와야 한다
```

`rfkill list` 결과에서 `Hard blocked: yes`이면 명령으로 풀 수 없다. 노트북의 물리
무선 스위치나 `Fn` 조합 키로 해제해야 한다.

### 2.2 주변 AP 목록 확인

```bash
nmcli device wifi rescan
nmcli device wifi list
```

`SSID`, `SIGNAL`(신호 세기), `SECURITY`(보안 방식) 열을 확인한다. 스캔은 몇 초
걸리므로 목록이 비어 있으면 `rescan` 후 잠시 뒤 다시 실행한다.

### 2.3 접속

최초 1회 접속하면 프로파일이 자동 생성되어 저장된다.

```bash
nmcli device wifi connect "SSID이름" password "비밀번호"
```

| 상황 | 명령 |
|---|---|
| 무선 카드가 여러 개 | `nmcli device wifi connect "SSID이름" password "비밀번호" ifname wlan0` |
| 숨김 SSID | `nmcli device wifi connect "SSID이름" password "비밀번호" hidden yes` |
| 비밀번호를 셸 기록에 남기기 싫을 때 | `nmcli --ask device wifi connect "SSID이름"` |

`--ask`는 비밀번호를 화면에 표시하지 않고 입력받는다. 명령줄에 직접 적은 비밀번호는
`~/.bash_history`와 프로세스 목록에 노출되므로, 공용 장비에서는 `--ask`를 쓴다.

### 2.4 접속 확인

```bash
nmcli -t -f NAME,DEVICE,STATE connection show --active
ip -br addr show wlan0            # IP를 받았는지
ping -c3 1.1.1.1                  # 외부 통신
```

### 2.5 저장된 프로파일 관리

두 번째부터는 SSID를 다시 입력할 필요 없이 프로파일 이름으로 붙인다.

```bash
nmcli connection show                                          # 저장된 프로파일 목록
nmcli connection up "SSID이름"
nmcli connection down "SSID이름"
nmcli connection modify "SSID이름" connection.autoconnect yes   # 부팅 시 자동 접속
nmcli connection delete "SSID이름"                              # 잘못 저장된 프로파일 삭제
```

비밀번호를 바꾼 AP에 계속 실패한다면 기존 프로파일이 옛 비밀번호를 들고 있는
것이므로, `delete` 후 2.3을 다시 수행한다.

### 2.6 터미널 UI로 선택하기

명령을 외우지 않고 커서로 골라 접속하려면 다음을 쓴다.

```bash
nmtui
```

`Activate a connection` 메뉴에서 SSID를 선택하고 비밀번호를 입력한다.

## 3. netplan + wpa_supplicant (NetworkManager 없는 장비)

로봇 온보드 PC를 포함한 최소 설치 서버가 여기에 해당한다.

### 3.1 무선 켜기와 스캔

최소 설치 장비에는 `iw`, `rfkill`, `wpa_supplicant`가 없을 수 있다. 없으면 먼저
설치한다.

```bash
sudo apt install -y iw rfkill wpasupplicant
```

```bash
sudo rfkill unblock wifi
sudo ip link set wlan0 up
sudo iw dev wlan0 scan | grep -E "SSID|signal"
```

### 3.2 netplan으로 영구 설정

`/etc/netplan/50-wifi.yaml`을 만든다. 기존 netplan 파일이 있으면 새로 만들지 말고
해당 파일에 `wifis` 블록을 추가한다. 같은 인터페이스를 두 파일에서 정의하면 적용
결과를 예측할 수 없다.

```yaml
network:
  version: 2
  renderer: networkd
  wifis:
    wlan0:
      dhcp4: true
      access-points:
        "SSID이름":
          password: "비밀번호"
```

```bash
sudo chmod 600 /etc/netplan/50-wifi.yaml   # 평문 비밀번호가 들어가므로 권한 필수
sudo netplan try                           # 적용 후 확인 대기, Enter로 확정
sudo netplan apply
```

`netplan try`는 설정이 잘못돼 접속이 끊겨도 제한 시간이 지나면 이전 설정으로
자동 복구한다. 원격 접속 중인 장비에서는 `apply` 대신 `try`를 먼저 쓴다.

### 3.3 일회성 접속 (설정 파일을 남기지 않을 때)

```bash
wpa_passphrase "SSID이름" "비밀번호" | sudo tee /etc/wpa_supplicant/wpa_supplicant-wlan0.conf
sudo wpa_supplicant -B -i wlan0 -c /etc/wpa_supplicant/wpa_supplicant-wlan0.conf
sudo dhclient wlan0
iw dev wlan0 link                          # "Connected to ..." 가 나오면 성공
```

## 4. 문제 해결

| 증상 | 확인 명령 | 조치 |
|---|---|---|
| `nmcli radio wifi`가 `disabled` | `rfkill list` | soft block은 `rfkill unblock wifi`, hard block은 물리 스위치 |
| 무선 장치가 목록에 없음 | `lspci -k \| grep -A3 -i network`, `dmesg \| grep -i firmware` | 드라이버/펌웨어 미로딩. 펌웨어 패키지 설치 필요 |
| `nmcli device status`가 `unmanaged` | `cat /etc/netplan/*.yaml` | netplan이 `networkd`로 잡고 있음. NetworkManager로 넘기려면 `renderer: NetworkManager` |
| 스캔 결과가 계속 비어 있음 | `journalctl -u wpa_supplicant -n 50`, `dmesg \| tail` | `SCAN-FAILED ret=-22`면 드라이버/펌웨어 wedge (5장 참고) |
| 접속은 되는데 IP가 없음 | `ip -br addr show wlan0`, `journalctl -u systemd-networkd -n 30` | DHCP 실패. AP의 DHCP 설정 또는 고정 IP 설정 확인 |
| 붙었다 끊기기를 반복 | `journalctl -u wpa_supplicant \| grep reason` | `reason=4`(DISASSOC_DUE_TO_INACTIVITY)면 전원 절약이 원인. `sudo iw dev wlan0 set power_save off` |
| 국가 코드 문제로 특정 채널이 안 보임 | `iw reg get` | `sudo iw reg set KR` |

## 5. AMR 온보드 PC 참고

JIBOT 온보드 PC(`192.168.101.61`, `.62`)는 NetworkManager 없이 wpa_supplicant를
쓰며, AX210 카드의 알려진 결함(전원 절약으로 인한 끊김, 부팅 경합, 스캔 wedge)
때문에 별도 keeper가 설치되어 있다.

```bash
systemctl status amr-wifi-keeper       # 동작 확인
sudo tail -50 /var/log/amr-wifi-diag.log   # 링크 유실 시점의 진단 스냅샷
```

- 설치: `scripts/setup-wifi-keeper-over-ssh.sh`
- 동작 본체: `scripts/amr-wifi-keeper.sh`

이 장비에서 Wi-Fi 문제가 발생하면 수동으로 `wpa_supplicant`를 재시작하지 않는다.
복구 중인 그 Wi-Fi로 SSH 접속해 있는 경우 링크가 끊기면서 콘솔 없이는 복구할 수
없는 상태가 된다. keeper가 단계적으로 복구하므로 `/var/log/amr-wifi-diag.log`를
먼저 확인한다.
