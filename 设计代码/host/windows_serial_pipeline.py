"""FIFO Win32 reads with several requests posted before Python consumes bytes.

Buffers and OVERLAPPED records remain alive until their I/O completes, including
cancellation. No UART errors are cleared here: the bridge records them itself.
"""
import ctypes
import threading

import serial


class WindowsReadPipeline:
    def __init__(self, handle, depth=32, request_bytes=4096, win32_module=None):
        if depth < 2 or request_bytes <= 0:
            raise ValueError("Require at least two positive-size pending reads")
        if win32_module is None:
            from serial import win32 as win32_module
        self.driver, self.handle = win32_module, handle
        self.request_bytes, self.slots, self.cursor = request_bytes, [], 0
        self.closed, self.lock = False, threading.RLock()
        self.stats = {"depth": depth, "request_bytes": request_bytes,
                      "posted": 0, "completed": 0, "bytes": 0, "empty": 0}
        try:
            for _ in range(depth):
                overlapped = self.driver.OVERLAPPED()
                overlapped.hEvent = self.driver.CreateEvent(None, True, False, None)
                if not overlapped.hEvent:
                    raise serial.SerialException("Pipeline CreateEvent failed")
                slot = {"overlapped": overlapped,
                        "buffer": ctypes.create_string_buffer(request_bytes), "active": False}
                self.slots.append(slot)
                self._post(slot)
        except Exception:
            self.close()
            raise

    def _post(self, slot):
        self.driver.ResetEvent(slot["overlapped"].hEvent)
        count = self.driver.DWORD()
        ok = self.driver.ReadFile(self.handle, slot["buffer"], self.request_bytes,
                                  ctypes.byref(count), ctypes.byref(slot["overlapped"]))
        if not ok and self.driver.GetLastError() not in (0, self.driver.ERROR_IO_PENDING):
            raise serial.SerialException("Pipeline ReadFile failed: %s" % self.driver.GetLastError())
        slot["active"] = True
        self.stats["posted"] += 1

    def read(self, _requested=None):
        with self.lock:
            if self.closed:
                raise serial.SerialException("Pipeline is closed")
            slot = self.slots[self.cursor]
            count = self.driver.DWORD()
            if not self.driver.GetOverlappedResult(self.handle, ctypes.byref(slot["overlapped"]),
                                                   ctypes.byref(count), True):
                raise serial.SerialException("Pipeline completion failed: %s" % self.driver.GetLastError())
            slot["active"] = False
            if count.value > self.request_bytes:
                raise serial.SerialException("Pipeline returned an invalid byte count")
            data = slot["buffer"].raw[:count.value]
            self.stats["completed"] += 1
            self.stats["bytes"] += count.value
            self.stats["empty"] += not data
            # Repost before returning bytes to the output queue; the other slots
            # already cover the Python/pipe scheduling gap.
            self._post(slot)
            self.cursor = (self.cursor + 1) % len(self.slots)
            return data

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            for slot in self.slots:
                if slot["active"]:
                    self.driver.CancelIoEx(self.handle, ctypes.byref(slot["overlapped"]))
            errors = []
            for slot in self.slots:
                if slot["active"]:
                    count = self.driver.DWORD()
                    ok = self.driver.GetOverlappedResult(self.handle, ctypes.byref(slot["overlapped"]),
                                                         ctypes.byref(count), True)
                    if not ok and self.driver.GetLastError() != self.driver.ERROR_OPERATION_ABORTED:
                        errors.append(self.driver.GetLastError())
                    slot["active"] = False
                self.driver.CloseHandle(slot["overlapped"].hEvent)
            if errors:
                raise serial.SerialException("Pipeline cancellation failed: %s" % errors)
