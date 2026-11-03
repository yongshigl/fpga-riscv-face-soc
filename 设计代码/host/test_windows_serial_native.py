"""Check the 64-bit native ABI, ordered reads, surfaced faults and teardown."""
import contextlib
import ctypes
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import serial
from windows_serial_native import WindowsNativeReceiver


class NativeReceiverTests(unittest.TestCase):
    def library(self, payloads=()):
        payloads = iter(payloads)

        def read(context, buffer, capacity, count, timeout):
            self.assertEqual(context, 0x123456789abcdef0)
            self.assertEqual((capacity, timeout), (65536, 100))
            data = next(payloads)
            buffer[:len(data)] = data
            count._obj.value = len(data)
            return 1 if data else 0

        def stats(context, values):
            values[:] = (7, 11, 1, 2, 8192, 0)

        return SimpleNamespace(pangu_rx_start=Mock(return_value=0x123456789abcdef0),
                               pangu_rx_read=Mock(side_effect=read), pangu_rx_stats=Mock(side_effect=stats),
                               pangu_rx_stop=Mock())

    def open(self, library):
        with contextlib.redirect_stderr(io.StringIO()):
            return WindowsNativeReceiver(0xfedcba9876543210, library=library, diagnostic_handle=29)

    def test_fifo_bytes_and_timeout_preserve_data_and_64_bit_context(self):
        library = self.library([b"one", b"", b"two\0three"])
        receiver = self.open(library)
        self.assertIs(library.pangu_rx_start.restype, ctypes.c_void_p)
        self.assertEqual(library.pangu_rx_read.argtypes[0], ctypes.c_void_p)
        library.pangu_rx_start.assert_called_once_with(0xfedcba9876543210, 29)
        self.assertEqual(b"".join(receiver.read() for _ in range(3)), b"onetwo\0three")
        receiver.close()

    def test_faults_remain_visible_and_cleanup_happens_once(self):
        library = self.library()
        receiver = self.open(library)
        library.pangu_rx_read.side_effect = None
        library.pangu_rx_read.return_value = -1
        with self.assertRaisesRegex(serial.SerialException, "failed"):
            receiver.read()
        receiver.close()
        receiver.close()
        library.pangu_rx_stop.assert_called_once_with(0x123456789abcdef0)
        self.assertEqual(receiver.stats["driver_error_events"], 2)
        with self.assertRaisesRegex(serial.SerialException, "closed"):
            receiver.read()

    def test_start_failure_rejects_the_receiver(self):
        library = self.library()
        library.pangu_rx_start.return_value = None
        with self.assertRaisesRegex(serial.SerialException, "initialization"):
            self.open(library)
        library.pangu_rx_stop.assert_not_called()

    def test_configured_polling_uses_64_bit_handle_and_retains_faults(self):
        library = self.library([b"payload"])
        library.pangu_rx_start_configured = Mock(return_value=0x123456789abcdef0)
        with contextlib.redirect_stderr(io.StringIO()):
            receiver = WindowsNativeReceiver(0xfedcba9876543210, library=library,
                                              diagnostic_handle=29, status_interval_ms=250)
        library.pangu_rx_start_configured.assert_called_once_with(0xfedcba9876543210, 29, 250)
        library.pangu_rx_start.assert_not_called()
        self.assertEqual(receiver.read(), b"payload")
        self.assertEqual(receiver.stats["driver_error_events"], 2)
        receiver.close()

    def test_invalid_poll_interval_is_rejected_before_opening_port(self):
        for interval in (-1, 1001):
            library = self.library()
            with self.assertRaises(ValueError):
                WindowsNativeReceiver(17, library=library, status_interval_ms=interval)
            library.pangu_rx_start.assert_not_called()

    def test_request_size_options_preserve_the_64_bit_abi_and_poll_interval(self):
        for request_bytes in (4096, 16384):
            with self.subTest(request_bytes=request_bytes):
                library = self.library([b"payload"])
                library.pangu_rx_start_options = Mock(return_value=0x123456789abcdef0)
                with contextlib.redirect_stderr(io.StringIO()):
                    receiver = WindowsNativeReceiver(0xfedcba9876543210, library=library,
                        diagnostic_handle=29, status_interval_ms=250, request_bytes=request_bytes)
                self.assertEqual(library.pangu_rx_start_options.argtypes[:2], [ctypes.c_void_p, ctypes.c_void_p])
                self.assertIs(library.pangu_rx_start_options.restype, ctypes.c_void_p)
                library.pangu_rx_start_options.assert_called_once_with(0xfedcba9876543210, 29, 250, request_bytes)
                library.pangu_rx_start.assert_not_called()
                self.assertEqual(receiver.read(), b"payload")
                receiver.close()

    def test_invalid_request_size_never_calls_the_native_library(self):
        for request_bytes in (0, 512, 65537):
            with self.subTest(request_bytes=request_bytes):
                library = self.library()
                with self.assertRaises(ValueError):
                    WindowsNativeReceiver(17, library=library, request_bytes=request_bytes)
                library.pangu_rx_start.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
