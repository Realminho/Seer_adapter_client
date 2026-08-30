import datetime
import math
import sys


def get_timestamp():
    """Get current timestamp in ISO8601 format"""
    now = datetime.datetime.utcnow()
    return now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class _TimestampedStream:
    """Wrap a text stream so each output line is prefixed with a local timestamp.

    각 출력 라인 앞에 로컬 시각 prefix를 붙이는 텍스트 스트림 래퍼.

    Prefixing happens at line starts only, so multi-line and partial writes from
    ``print()`` keep one timestamp per line.
    라인 시작에서만 prefix를 붙이므로 ``print()``의 멀티라인/부분 출력도 한 줄에
    타임스탬프 하나만 갖는다.
    """

    def __init__(self, stream):
        self._stream = stream
        self._at_line_start = True

    @staticmethod
    def _prefix() -> str:
        now = datetime.datetime.now()
        return now.strftime("[%Y-%m-%d %H:%M:%S.%f")[:-3] + "] "

    def write(self, text):
        if not text:
            return
        out = []
        for ch in text:
            if self._at_line_start and ch != "\n":
                out.append(self._prefix())
                self._at_line_start = False
            out.append(ch)
            if ch == "\n":
                self._at_line_start = True
        self._stream.write("".join(out))

    def flush(self):
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def install_timestamped_logging() -> None:
    """Prefix every stdout/stderr line with a timestamp.

    모든 stdout/stderr 라인 앞에 타임스탬프를 붙인다.

    Idempotent: re-installing on an already wrapped stream is a no-op.
    멱등: 이미 래핑된 스트림에 다시 설치해도 아무 일도 하지 않는다.
    """
    if not isinstance(sys.stdout, _TimestampedStream):
        sys.stdout = _TimestampedStream(sys.stdout)
    if not isinstance(sys.stderr, _TimestampedStream):
        sys.stderr = _TimestampedStream(sys.stderr)

def get_topic_type(path):
    """Extract topic type from MQTT topic path"""
    parts = path.rsplit('/', 1)
    if len(parts) > 1:
        return parts[1]
    return path

def check_deviation_range(node_x, node_y, vehicle_x, vehicle_y, deviation_range):
    """Check if vehicle is within deviation range of node"""
    distance = math.sqrt((node_x - vehicle_x) ** 2 + (node_y - vehicle_y) ** 2)
    return distance <= deviation_range

def iterate_position(current_x, current_y, target_x, target_y, speed):
    """Calculate next position when moving towards target"""
    angle = math.atan2(target_y - current_y, target_x - current_x)
    next_x = current_x + speed * math.cos(angle)
    next_y = current_y + speed * math.sin(angle)
    
    return (next_x, next_y, angle)

def get_distance(x1, y1, x2, y2):
    """Calculate distance between two points"""
    return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)
