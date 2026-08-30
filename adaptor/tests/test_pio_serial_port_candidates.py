# adaptor/tests/test_pio_serial_port_candidates.py
"""포트가 틀렸을 때 실패 메시지가 올바른 후보를 스스로 알려 준다.

`/dev/ttyUSBn`은 열거 순서가 만들어 내는 이름이라 로봇마다 다르다 — 같은 PL2303이
1호기에서는 ttyUSB4, 2호기에서는 ttyUSB0이었고, USB를 옮겨 꽂을 때마다 또 바뀌었다.
udev가 만드는 `/dev/serial/by-id/`는 그 순서와 무관하므로 설정은 그쪽을 가리켜야
한다. 다만 값을 알아야 고칠 수 있으므로, 프레임 검증이 실패해 "포트가 틀렸다"고
말하는 바로 그 자리에서 실제 후보를 함께 보여 준다.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from extensions.pio import serial_port_candidates, describe_serial_port_candidates


class SerialPortCandidatesTest(unittest.TestCase):
    def test_lists_by_id_entries_sorted(self):
        with_dir = self._tmp_by_id(
            "usb-Prolific_Technology_Inc._USB-Serial_Controller_D-if00-port0",
            "usb-FTDI_Quad_RS232-HS-if00-port0",
        )
        self.assertEqual(
            serial_port_candidates(by_id_dir=with_dir),
            (
                "/dev/serial/by-id/usb-FTDI_Quad_RS232-HS-if00-port0",
                "/dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller_D-if00-port0",
            ),
        )

    def test_missing_directory_is_not_an_error(self):
        """by-id는 udev가 만든다. 없는 시스템에서도 pioInit 실패 경로가 살아야 한다."""
        self.assertEqual(
            serial_port_candidates(by_id_dir=Path("/nonexistent/by-id")), ()
        )

    def test_description_is_empty_when_nothing_to_suggest(self):
        """후보가 없으면 문장을 만들지 않는다 — 빈 목록을 보여 주는 편이 더 헷갈린다."""
        self.assertEqual(
            describe_serial_port_candidates(by_id_dir=Path("/nonexistent/by-id")), ""
        )

    def test_description_names_the_stable_paths(self):
        with_dir = self._tmp_by_id("usb-Prolific_Technology_Inc._USB-Serial_Controller_D-if00-port0")
        text = describe_serial_port_candidates(by_id_dir=with_dir)
        self.assertIn("/dev/serial/by-id/", text)
        self.assertIn("USB-Serial_Controller_D", text)

    def _tmp_by_id(self, *names):
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        for name in names:
            (tmp / name).symlink_to("/dev/null")
        return tmp


if __name__ == "__main__":
    unittest.main()
