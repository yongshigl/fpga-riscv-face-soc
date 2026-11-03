"""Windows priority requests use native thread handles and report actual success."""
import contextlib
import ctypes
import io
import unittest
from unittest.mock import Mock

from serial_bridge import prioritize_windows_io, write_all, windows_timer_resolution


class PriorityTests(unittest.TestCase):
    def kernel(self, success=1):
        return Mock(GetCurrentProcess=Mock(return_value=0x123456789abcdef0),
                    GetCurrentThread=Mock(return_value=0x123456789abcdef1),
                    SetPriorityClass=Mock(return_value=success), SetThreadPriority=Mock(return_value=success))

    def test_process_uses_above_normal_without_realtime_or_high_class(self):
        kernel = self.kernel()
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            self.assertTrue(prioritize_windows_io("process", kernel))
        kernel.SetPriorityClass.assert_called_once_with(0x123456789abcdef0, 0x8000)
        self.assertEqual(kernel.GetCurrentProcess.restype, ctypes.c_void_p)
        self.assertEqual(kernel.SetPriorityClass.argtypes, [ctypes.c_void_p, ctypes.c_uint32])
        kernel.SetThreadPriority.assert_not_called()
        self.assertIn("applied=1", log.getvalue())

    def test_receiver_targets_current_native_thread_at_highest_normal_priority(self):
        kernel = self.kernel()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(prioritize_windows_io("receiver", kernel))
        kernel.SetThreadPriority.assert_called_once_with(0x123456789abcdef1, 2)
        self.assertEqual(kernel.GetCurrentThread.restype, ctypes.c_void_p)
        self.assertEqual(kernel.SetThreadPriority.argtypes, [ctypes.c_void_p, ctypes.c_int])
        kernel.SetPriorityClass.assert_not_called()

    def test_explicit_high_priority_uses_high_class(self):
        kernel = self.kernel()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(prioritize_windows_io("process", kernel, process_priority="high"))
        kernel.SetPriorityClass.assert_called_once_with(0x123456789abcdef0, 0x80)

    def test_timer_request_is_released_when_transport_raises(self):
        library = Mock(timeBeginPeriod=Mock(return_value=0), timeEndPeriod=Mock(return_value=0))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(ValueError):
            with windows_timer_resolution(1, library):
                raise ValueError("transport failed")
        library.timeBeginPeriod.assert_called_once_with(1)
        library.timeEndPeriod.assert_called_once_with(1)

    def test_rejected_timer_request_does_not_claim_an_active_request(self):
        library = Mock(timeBeginPeriod=Mock(return_value=97), timeEndPeriod=Mock())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(OSError):
            with windows_timer_resolution(1, library):
                self.fail("Rejected timer request continued")
        library.timeEndPeriod.assert_not_called()

    def test_denied_priority_reports_fallback_instead_of_claiming_success(self):
        for scope in ("process", "receiver"):
            with self.subTest(scope=scope):
                log = io.StringIO()
                with contextlib.redirect_stderr(log):
                    self.assertFalse(prioritize_windows_io(scope, self.kernel(success=0)))
                self.assertIn("applied=0", log.getvalue())
                self.assertIn("error=", log.getvalue())

    def test_priority_does_not_change_lossless_short_write_handling(self):
        class ShortStream:
            def __init__(self):
                self.output, self.flushed = bytearray(), False

            def write(self, data):
                size = min(3, len(data))
                self.output.extend(data[:size])
                return size

            def flush(self):
                self.flushed = True

        stream = ShortStream()
        payload = b"PG_BRIDGE_READY_v1\n\x00\xff camera data"
        write_all(stream, payload)
        self.assertEqual(bytes(stream.output), payload)
        self.assertTrue(stream.flushed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
