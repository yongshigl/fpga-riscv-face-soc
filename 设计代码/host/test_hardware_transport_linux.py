"""Exercise real Linux tty transport and cumulative kernel fault reporting."""
import contextlib
import os
from pathlib import Path
import pty
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
import zlib

from hardware_transport import Board


@unittest.skipIf(os.name == "nt", "Linux tty backend")
class LinuxTransportTests(unittest.TestCase):
    def test_uart_request_reply_through_real_pseudoterminal(self):
        master, slave = pty.openpty()
        port = os.ttyname(slave)
        errors = []

        def firmware():
            try:
                request = bytearray()
                while len(request) < 20:
                    request.extend(os.read(master, 20 - len(request)))
                magic, command, sequence, length = struct.unpack("<4sIII", request[:16])
                self.assertEqual((magic, command, length), (b"PGR1", 1, 0))
                self.assertEqual(struct.unpack("<I", request[16:])[0], zlib.crc32(request[:16]))
                payload = struct.pack("<9I", 0, 0x26101401, 1, 81000000, 72000000, 921600, 0, 2048, 0)
                reply = struct.pack("<4sIII", b"PGS1", command, sequence, len(payload)) + payload
                os.write(master, reply + struct.pack("<I", zlib.crc32(reply)))
            except BaseException as error:
                errors.append(error)

        try:
            with tempfile.TemporaryDirectory() as directory:
                with patch("hardware_transport.subprocess.Popen") as windows_bridge:
                    with Board(port, bridge_log=Path(directory) / "bridge.log") as board:
                        worker = threading.Thread(target=firmware, daemon=True)
                        worker.start()
                        identity = board.ping()
                        worker.join(2)
                        self.assertFalse(worker.is_alive())
                        self.assertEqual(identity["ai_hz"], 72000000)
                        self.assertEqual(identity["rx_errors"], 0)
                    windows_bridge.assert_not_called()
                self.assertFalse(errors)
                self.assertIn("SERIAL_LINUX_NATIVE", (Path(directory) / "bridge.log").read_text())
        finally:
            os.close(master)
            os.close(slave)

    def test_cumulative_kernel_overrun_is_reported_and_still_fatal_at_close(self):
        board = Board.__new__(Board)
        board._linux_line_baseline = dict(frame=0, overrun=8, parity=0, **{"break": 0}, buffer_overrun=0)
        board._linux_next_line_check = 0
        counts = dict(board._linux_line_baseline, overrun=9)
        with patch.object(board, "_linux_line_counts", return_value=counts), \
                patch.object(board, "_linux_log_line_counts") as log:
            with self.assertRaisesRegex(RuntimeError, "overrun.*1"):
                board._check_linux_line_errors()
            with self.assertRaisesRegex(RuntimeError, "overrun.*1"):
                board._check_linux_line_errors(force=True)
            self.assertEqual(log.call_count, 2)


if __name__ == "__main__":
    unittest.main()
