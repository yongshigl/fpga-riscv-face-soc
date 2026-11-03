import os
import unittest
from unittest.mock import Mock, patch

from hardware_transport import Board


@unittest.skipIf(os.name == "nt", "The Windows bridge is launched from WSL")
class BridgeOptionsTests(unittest.TestCase):
    def test_status_polling_is_passed_explicitly_across_windows_interop(self):
        process = Mock()
        with patch.dict(os.environ, {"FPGA_SERIAL_STATUS_INTERVAL_MS": "250"}), \
                patch("hardware_transport.subprocess.check_output", return_value="D:\\vision\\serial_bridge.py\n"), \
                patch("hardware_transport.subprocess.Popen", return_value=process) as launch, \
                patch.object(Board, "_wait_bridge_ready"):
            with Board():
                command = launch.call_args.args[0]
                self.assertEqual(command[-2:], ["--status-interval-ms", "250"])
                self.assertEqual(command[command.index("--port") + 1], "COM7")
        process.wait.assert_called_once_with(timeout=3)

    def test_missing_override_keeps_the_existing_bridge_command(self):
        with patch.dict(os.environ), patch("hardware_transport.subprocess.check_output", return_value="bridge.py"), \
                patch("hardware_transport.subprocess.Popen") as launch, patch.object(Board, "_wait_bridge_ready"):
            os.environ.pop("FPGA_SERIAL_STATUS_INTERVAL_MS", None)
            os.environ.pop("FPGA_SERIAL_IO_PRIORITY", None)
            os.environ.pop("FPGA_SERIAL_NATIVE_READ_BYTES", None)
            os.environ.pop("FPGA_SERIAL_TIMER_RESOLUTION_MS", None)
            with Board():
                self.assertNotIn("--status-interval-ms", launch.call_args.args[0])

    def test_invalid_override_cannot_open_the_serial_child(self):
        for value in ("-1", "999", "1001", "invalid"):
            with self.subTest(value=value), patch.dict(os.environ, {"FPGA_SERIAL_STATUS_INTERVAL_MS": value}), \
                    patch("hardware_transport.subprocess.Popen") as launch:
                with self.assertRaises(ValueError):
                    Board()
                launch.assert_not_called()

    def test_high_priority_is_passed_explicitly_with_status_interval(self):
        with patch.dict(os.environ, {"FPGA_SERIAL_STATUS_INTERVAL_MS": "250", "FPGA_SERIAL_IO_PRIORITY": "high"}), \
                patch("hardware_transport.subprocess.check_output", return_value="bridge.py"), \
                patch("hardware_transport.subprocess.Popen") as launch, patch.object(Board, "_wait_bridge_ready"):
            with Board():
                self.assertEqual(launch.call_args.args[0][-4:], ["--status-interval-ms", "250", "--io-priority", "high"])

    def test_invalid_priority_cannot_open_the_serial_child(self):
        with patch.dict(os.environ, {"FPGA_SERIAL_IO_PRIORITY": "realtime"}), \
                patch("hardware_transport.subprocess.Popen") as launch:
            with self.assertRaises(ValueError):
                Board()
            launch.assert_not_called()

    def test_native_request_size_crosses_interop_explicitly(self):
        with patch.dict(os.environ, {"FPGA_SERIAL_NATIVE_READ_BYTES": "4096"}), \
                patch("hardware_transport.subprocess.check_output", return_value="bridge.py"), \
                patch("hardware_transport.subprocess.Popen") as launch, patch.object(Board, "_wait_bridge_ready"):
            with Board():
                self.assertEqual(launch.call_args.args[0][-2:], ["--native-read-bytes", "4096"])

    def test_invalid_native_request_size_cannot_start_the_child(self):
        for size in ("0", "512", "65537", "invalid"):
            with self.subTest(size=size), patch.dict(os.environ, {"FPGA_SERIAL_NATIVE_READ_BYTES": size}), \
                    patch("hardware_transport.subprocess.Popen") as launch:
                with self.assertRaises(ValueError):
                    Board()
                launch.assert_not_called()

    def test_timer_resolution_crosses_interop_explicitly(self):
        for milliseconds in ("0", "1"):
            with self.subTest(milliseconds=milliseconds), \
                    patch.dict(os.environ, {"FPGA_SERIAL_TIMER_RESOLUTION_MS": milliseconds}), \
                    patch("hardware_transport.subprocess.check_output", return_value="bridge.py"), \
                    patch("hardware_transport.subprocess.Popen") as launch, patch.object(Board, "_wait_bridge_ready"):
                with Board():
                    self.assertEqual(launch.call_args.args[0][-2:], ["--timer-resolution-ms", milliseconds])

    def test_invalid_timer_resolution_cannot_start_the_child(self):
        for milliseconds in ("-1", "2", "invalid"):
            with self.subTest(milliseconds=milliseconds), \
                    patch.dict(os.environ, {"FPGA_SERIAL_TIMER_RESOLUTION_MS": milliseconds}), \
                    patch("hardware_transport.subprocess.Popen") as launch:
                with self.assertRaises(ValueError):
                    Board()
                launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
