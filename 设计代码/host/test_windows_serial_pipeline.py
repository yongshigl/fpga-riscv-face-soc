"""Driver doubles cover queued FIFO data, completion failure and safe cancellation."""
import ctypes
from types import SimpleNamespace
import unittest

import serial
from windows_serial_pipeline import WindowsReadPipeline


class Overlapped(ctypes.Structure):
    _fields_ = [("hEvent", ctypes.c_void_p)]


class Driver:
    DWORD, OVERLAPPED = ctypes.c_uint32, Overlapped
    ERROR_IO_PENDING, ERROR_OPERATION_ABORTED = 997, 995

    def __init__(self):
        self.next_handle, self.error, self.requests = 0, 997, {}
        self.posts, self.cancelled, self.closed, self.waited = [], [], [], []
        self.fail_post, self.fail_completion = None, None

    def CreateEvent(self, *_):
        self.next_handle += 1
        return self.next_handle

    def ResetEvent(self, _):
        return True

    def ReadFile(self, handle, buffer, size, count, overlap):
        event = overlap._obj.hEvent
        if len(self.posts) == self.fail_post:
            self.error = 5
            return False
        self.posts.append(event)
        self.requests[event] = SimpleNamespace(buffer=buffer, data=b"", cancelled=False)
        self.error = 997
        return False

    def GetLastError(self):
        return self.error

    def GetOverlappedResult(self, handle, overlap, count, wait):
        event = overlap._obj.hEvent
        self.waited.append(event)
        request = self.requests[event]
        if request.cancelled:
            self.error = 995
            return False
        if event == self.fail_completion:
            self.error = 1117
            return False
        request.buffer[:len(request.data)] = request.data
        count._obj.value = len(request.data)
        return True

    def CancelIoEx(self, handle, overlap):
        event = overlap._obj.hEvent
        self.cancelled.append(event)
        self.requests[event].cancelled = True
        return True

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return True


class PipelineTests(unittest.TestCase):
    def test_all_reads_posted_before_consumer_runs_and_recycled_in_fifo_order(self):
        driver = Driver()
        pipeline = WindowsReadPipeline(0x123456789abcdef0, depth=4, request_bytes=64, win32_module=driver)
        self.assertEqual(driver.posts, [1, 2, 3, 4])
        # All four complete during a consumer scheduling stall. Data must still
        # emerge in wire order while replacement reads remain pending.
        for event, data in enumerate([b"one", b"two", b"three", b"four"], 1):
            driver.requests[event].data = data
        self.assertEqual(b"".join(pipeline.read() for _ in range(4)), b"onetwothreefour")
        self.assertEqual(driver.posts, [1, 2, 3, 4, 1, 2, 3, 4])
        self.assertEqual(pipeline.stats["bytes"], 15)
        pipeline.close()
        self.assertEqual(driver.cancelled, [1, 2, 3, 4])
        self.assertEqual(driver.closed, [1, 2, 3, 4])
        self.assertEqual(driver.waited[-4:], [1, 2, 3, 4])

    def test_empty_timeout_reposts_without_losing_next_pending_data(self):
        driver = Driver()
        pipeline = WindowsReadPipeline(1, depth=2, win32_module=driver)
        driver.requests[2].data = b"next"
        self.assertEqual(pipeline.read(), b"")
        self.assertEqual(pipeline.read(), b"next")
        self.assertEqual(pipeline.stats["empty"], 1)
        pipeline.close()

    def test_completion_failure_is_fatal_and_cleanup_waits_for_every_cancelled_read(self):
        driver = Driver()
        pipeline = WindowsReadPipeline(1, depth=2, win32_module=driver)
        driver.fail_completion = 1
        with self.assertRaisesRegex(serial.SerialException, "completion failed: 1117"):
            pipeline.read()
        pipeline.close()
        self.assertEqual(driver.cancelled, [1, 2])
        self.assertEqual(driver.closed, [1, 2])

    def test_partial_initialization_cancels_posts_and_closes_all_created_events(self):
        driver = Driver()
        driver.fail_post = 2
        with self.assertRaisesRegex(serial.SerialException, "ReadFile failed: 5"):
            WindowsReadPipeline(1, depth=4, win32_module=driver)
        self.assertEqual(driver.cancelled, [1, 2])
        self.assertEqual(driver.closed, [1, 2, 3])

    def test_close_is_idempotent_and_future_read_is_rejected(self):
        driver = Driver()
        pipeline = WindowsReadPipeline(1, depth=2, win32_module=driver)
        pipeline.close()
        pipeline.close()
        self.assertEqual(driver.closed, [1, 2])
        with self.assertRaisesRegex(serial.SerialException, "closed"):
            pipeline.read()

    def test_invalid_geometry_rejected_before_touching_driver(self):
        for depth, size in [(1, 4096), (2, 0)]:
            with self.assertRaises(ValueError):
                WindowsReadPipeline(1, depth=depth, request_bytes=size, win32_module=Driver())


if __name__ == "__main__":
    unittest.main(verbosity=2)
