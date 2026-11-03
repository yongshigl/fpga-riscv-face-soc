#!/usr/bin/env python3
"""Check that console launch completion includes the asynchronous hardware startup."""
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from manage_console import launch_environment, main, wait_ready


class ConsoleEnvironmentTests(unittest.TestCase):
    def test_detached_server_uses_the_persistent_wsl_interop_socket(self):
        environment = {"WSL_INTEROP": "/run/WSL/temporary_interop", "PATH": "/usr/bin", "UNCHANGED": "value"}
        with patch("manage_console.os.environ", environment), patch("manage_console.Path.is_socket", return_value=True):
            actual = launch_environment()
        self.assertEqual(actual["WSL_INTEROP"], "/run/WSL/1_interop")
        self.assertEqual(actual["UNCHANGED"], "value")
        self.assertEqual(environment["WSL_INTEROP"], "/run/WSL/temporary_interop")

    def test_missing_persistent_socket_preserves_the_existing_environment(self):
        environment = {"WSL_INTEROP": "/run/WSL/temporary_interop", "PATH": "/usr/bin"}
        with patch("manage_console.os.environ", environment), patch("manage_console.Path.is_socket", return_value=False):
            self.assertEqual(launch_environment(), environment)

    def test_non_wsl_launch_keeps_its_environment(self):
        environment = {"PATH": "/usr/bin"}
        with patch("manage_console.os.environ", environment), patch("manage_console.Path.is_socket", return_value=True):
            self.assertEqual(launch_environment(), environment)


class ConsoleStartupTests(unittest.TestCase):
    def setUp(self):
        self.process = SimpleNamespace(poll=lambda: None, returncode=None)

    def status(self, **changes):
        value = {"connected": False, "mode": "connecting", "connection_retries": 0, "error": None}
        value.update(changes)
        return io.StringIO(json.dumps(value))

    def test_http_ready_does_not_complete_hardware_startup(self):
        responses = [self.status(), self.status(), self.status(connected=True, mode="vision")]
        with patch("manage_console.urllib.request.urlopen", side_effect=responses) as request, \
                patch("manage_console.time.sleep") as sleep:
            result = wait_ready(self.process, "http://localhost:8086", "vision", 1)
        self.assertTrue(result["connected"])
        self.assertEqual(request.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_offline_console_does_not_require_hardware(self):
        with patch("manage_console.urllib.request.urlopen", return_value=self.status(mode="offline")):
            self.assertFalse(wait_ready(self.process, "http://localhost:8086", "offline", 1)["connected"])

    def test_a_successful_retry_does_not_hide_a_failed_first_connection(self):
        with patch("manage_console.urllib.request.urlopen",
                   return_value=self.status(connected=True, mode="vision", connection_retries=1)):
            with self.assertRaisesRegex(RuntimeError, "retries=1"):
                wait_ready(self.process, "http://localhost:8086", "vision", 1)

    def test_connection_error_is_reported_before_retry(self):
        with patch("manage_console.urllib.request.urlopen", return_value=self.status(error="COM7 is busy")):
            with self.assertRaisesRegex(RuntimeError, "COM7 is busy"):
                wait_ready(self.process, "http://localhost:8086", "vision", 1)

    def test_explicit_disconnected_mode_keeps_the_http_console_available(self):
        with patch("manage_console.urllib.request.urlopen",
                   return_value=self.status(error="Board did not respond", connection_retries=2)):
            status = wait_ready(self.process, "http://localhost:8086", "vision", 1, allow_disconnected=True)
        self.assertFalse(status["connected"])
        self.assertEqual(status["error"], "Board did not respond")
        self.assertEqual(status["connection_retries"], 2)

    def test_disconnected_option_still_rejects_unexpected_connected_hardware(self):
        with patch("manage_console.urllib.request.urlopen", return_value=self.status(connected=True, mode="camera")):
            with self.assertRaisesRegex(RuntimeError, "unexpected mode"):
                wait_ready(self.process, "http://localhost:8086", "vision", 1, allow_disconnected=True)

    def test_explicit_mode_requires_the_matching_hardware(self):
        with patch("manage_console.urllib.request.urlopen", return_value=self.status(connected=True, mode="camera")):
            with self.assertRaisesRegex(RuntimeError, "unexpected mode"):
                wait_ready(self.process, "http://localhost:8086", "vision", 1)

    def test_hardware_startup_wait_is_bounded(self):
        with patch("manage_console.urllib.request.urlopen", return_value=self.status()), \
                patch("manage_console.time.monotonic", side_effect=[0, 0.1, 1.1]), \
                patch("manage_console.time.sleep"):
            with self.assertRaisesRegex(TimeoutError, "hardware startup"):
                wait_ready(self.process, "http://localhost:8086", "vision", 1)

    def test_exited_console_is_reported_without_polling_http(self):
        self.process = SimpleNamespace(poll=lambda: 2, returncode=2)
        with patch("manage_console.urllib.request.urlopen") as request:
            with self.assertRaisesRegex(RuntimeError, "exited with 2"):
                wait_ready(self.process, "http://localhost:8086", "vision", 1)
        request.assert_not_called()


class ConsolePortTests(unittest.TestCase):
    def test_manager_passes_explicit_and_default_serial_settings_to_the_server(self):
        for serial_arguments, port, baud in (([], "COM7", "921600"),
                                              (["--port", "COM19", "--baud", "460800"], "COM19", "460800"),
                                              (["--restore-models-on-reconnect"], "COM7", "921600")):
            with self.subTest(port=port), tempfile.TemporaryDirectory() as directory:
                process = SimpleNamespace(pid=12345)
                status = {"connected": True, "bridge_startup_ms": 15}
                arguments = ["manage_console.py", "--action", "start", "--mode", "vision",
                             "--output", directory, *serial_arguments]
                with patch("manage_console.sys.argv", arguments), \
                        patch("manage_console.subprocess.Popen", return_value=process) as launch, \
                        patch("manage_console.wait_ready", return_value=status) as ready, \
                        patch("manage_console.launch_environment", return_value={"WSL_INTEROP": "persistent"}), \
                        patch("manage_console.print"):
                    main()
                command = launch.call_args.args[0]
                self.assertEqual(command[command.index("--port") + 1], port)
                self.assertEqual(command[command.index("--baud") + 1], baud)
                self.assertEqual("--restore-models-on-reconnect" in command, "--restore-models-on-reconnect" in serial_arguments)
                self.assertTrue(launch.call_args.kwargs["start_new_session"])
                self.assertEqual(ready.call_args.args[3], 120)
                record = json.loads((Path(directory) / "console_process.json").read_text())
                self.assertEqual(record["command"], command)
                self.assertEqual(record["interop_socket"], "persistent")
                self.assertTrue(record["hardware_connected"])

    def test_invalid_baud_is_rejected_before_starting_a_server(self):
        with patch("manage_console.sys.argv", ["manage_console.py", "--action", "start", "--baud", "0"]), \
                patch("manage_console.subprocess.Popen") as launch, \
                patch("manage_console.sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                main()
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
