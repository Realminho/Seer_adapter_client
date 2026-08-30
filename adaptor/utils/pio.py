import serial
import time
import asyncio


class PIOMaster:
    TERMINATOR = b"\n"

    def __init__(
        self,
        port="/dev/ttyUSB1",
        baudrate=38400,
        ezi_client=None,
        timeout=0.2,
        connect_delay_sec: float = 0.5,
        read_frame_poll_sec: float = 0.05,
        read_frames_wait_sec: float = 2.0,
        send_wait_sec: float = 2.0,
    ):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ezi = ezi_client
        self.ser = None
        self._connect_delay = connect_delay_sec
        self._read_frame_poll = read_frame_poll_sec
        self._read_frames_wait = read_frames_wait_sec
        self._send_wait = send_wait_sec


    def connect(self):
        # 다시 열면 이전 Serial 객체를 닫지 않고 덮어써 fd가 샌다. pyserial은
        # 기본이 비배타 open이라 두 번째 open이 조용히 성공해 버리므로,
        # 열려 있으면 그대로 재사용한다.
        if self.ser and self.ser.is_open:
            return
        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
        )
        time.sleep(self._connect_delay)

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    @staticmethod
    def checksum_hex(payload: str) -> str:
        return f"{sum(payload.encode('ascii')) & 0xFF:02X}"

    def make_frame(self, payload: str, head="<", tail=">") -> str:
        checksum = self.checksum_hex(payload)
        print(f"CHECKSUM: {checksum} for payload: {payload}")
        return f"{head}{payload}{checksum}{tail}"

    def send_payload(self, payload: str):
        if not self.ser or not self.ser.is_open:
            raise RuntimeError("Serial port is not connected")

        frame = self.make_frame(payload)
        self.ser.write(frame.encode("ascii") + self.TERMINATOR)
        self.ser.flush()

        print("TX:", frame)
        return frame

    def read_frames(self, wait_sec=None):
        if wait_sec is None:
            wait_sec = self._read_frames_wait
        if not self.ser or not self.ser.is_open:
            raise RuntimeError("Serial port is not connected")

        end_time = time.time() + wait_sec
        buffer = ""

        while time.time() < end_time:
            data = self.ser.read(self.ser.in_waiting or 1)

            if data:
                text = data.decode("ascii", errors="ignore")
                buffer += text
                print("RX RAW:", repr(text))

            time.sleep(self._read_frame_poll)

        return buffer

    def send_and_read(self, payload: str, wait_sec=None):
        if wait_sec is None:
            wait_sec = self._send_wait
        self.send_payload(payload)
        return self.read_frames(wait_sec)

    def send_bc(self, media, station_id, channel, port, oht_num, wait_sec=None):
        if wait_sec is None:
            wait_sec = self._send_wait
        payload = f"BC={media}:{station_id}:{channel}:{port}:{oht_num}"
        return self.send_and_read(payload, wait_sec)

    def send_channel(self, channel, wait_sec=None):
        if wait_sec is None:
            wait_sec = self._send_wait
        payload = f"C={channel}"
        return self.send_and_read(payload, wait_sec)

    def monitor_data(self, channel, wait_sec=None):
        if wait_sec is None:
            wait_sec = self._send_wait
        payload = f"D={channel}"
        return self.send_and_read(payload, wait_sec)

    def send_raw(self, data, wait_sec=None):
        if wait_sec is None:
            wait_sec = self._send_wait
        return self.send_and_read(data, wait_sec)

