#!/usr/bin/env python3
"""Verify exact pipe writes and UART reads while the downstream writer stalls."""
import io
import os
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from camera_stream import BRIDGE_READY
import serial_bridge


class BridgeWriteTests(unittest.TestCase):
    def test_partial_writes_preserve_every_byte(self):
        received = bytearray()

        def write(data):
            count = min(3, len(data))
            received.extend(data[:count])
            return count

        serial_bridge.write_all(SimpleNamespace(write=write, flush=lambda: None), bytes(range(32)))
        self.assertEqual(received, bytes(range(32)))

    def test_zero_length_write_is_rejected(self):
        with self.assertRaises(BrokenPipeError):
            serial_bridge.write_all(SimpleNamespace(write=lambda _: 0), b"pending")

    def test_nonblocking_write_without_progress_is_rejected(self):
        with self.assertRaises(BrokenPipeError):
            serial_bridge.write_all(SimpleNamespace(write=lambda _: None), b"pending")

    @unittest.skipIf(os.name == "nt", "Exercise the portable queue logic without touching a Windows COM port")
    def test_blocked_output_does_not_stop_uart_reads(self):
        payload = bytes(range(256)) * 256
        remaining = bytearray(payload)
        drained, blocked, release = threading.Event(), threading.Event(), threading.Event()
        received = bytearray()

        class Port:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def reset_input_buffer(self):
                pass

            @property
            def in_waiting(self):
                return len(remaining)

            def read(self, size):
                if not remaining:
                    drained.set()
                    time.sleep(0.005)
                    return b""
                count = min(size, 4096, len(remaining))
                data = bytes(remaining[:count])
                del remaining[:count]
                time.sleep(0.001)
                return data

        def write(data):
            if bytes(data) != BRIDGE_READY:
                blocked.set()
                if not release.wait(2):
                    raise TimeoutError("Test output was not released")
            received.extend(data)
            return len(data)

        def stdin_read(*_):
            try:
                self.assertTrue(blocked.wait(1), "No data reached the output writer")
                self.assertTrue(drained.wait(1), "Output backpressure stopped the UART reader")
            finally:
                release.set()
            return b""

        output = SimpleNamespace(buffer=SimpleNamespace(write=write, flush=lambda: None))
        with patch("serial_bridge.serial.Serial", return_value=Port()) as serial_factory, \
                patch("serial_bridge.sys.argv", ["serial_bridge.py"]), \
                patch("serial_bridge.sys.stdin", SimpleNamespace(fileno=lambda: 0)), \
                patch("serial_bridge.sys.stdout", output), \
                patch("serial_bridge.sys.stderr", io.StringIO()), \
                patch("serial_bridge.os.read", side_effect=stdin_read):
            serial_bridge.main()
        serial_factory.assert_called_once_with("COM7", 921600, timeout=0.05,
                                               inter_byte_timeout=None, write_timeout=5)
        self.assertEqual(received, BRIDGE_READY + payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
