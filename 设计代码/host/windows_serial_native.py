"""Bounded native COM reader; pending reads run independently of Python's GIL."""
import ctypes
from pathlib import Path
import sys

import serial


class WindowsNativeReceiver:
    def __init__(self, handle, *, library=None, diagnostic_handle=None, status_interval_ms=0, request_bytes=65536):
        if not 0 <= status_interval_ms <= 1000:
            raise ValueError("Native status polling must be within 0..1000 ms")
        if request_bytes not in (4096, 16384, 65536):
            raise ValueError("Native read request must be 4096, 16384 or 65536 bytes")
        if library is None:
            library = ctypes.CDLL(str(Path(__file__).with_name("windows_serial_receiver.dll")))
        if diagnostic_handle is None:
            import msvcrt
            diagnostic_handle = msvcrt.get_osfhandle(sys.stderr.fileno())
        library.pangu_rx_start.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        library.pangu_rx_start.restype = ctypes.c_void_p
        library.pangu_rx_read.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                                         ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32]
        library.pangu_rx_read.restype = ctypes.c_int
        library.pangu_rx_stats.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64)]
        library.pangu_rx_stats.restype = None
        library.pangu_rx_stop.argtypes = [ctypes.c_void_p]
        library.pangu_rx_stop.restype = None
        self.library = library
        if request_bytes != 65536:
            library.pangu_rx_start_options.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
            library.pangu_rx_start_options.restype = ctypes.c_void_p
            self.context = library.pangu_rx_start_options(handle, diagnostic_handle, status_interval_ms, request_bytes)
        elif status_interval_ms:
            library.pangu_rx_start_configured.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
            library.pangu_rx_start_configured.restype = ctypes.c_void_p
            self.context = library.pangu_rx_start_configured(handle, diagnostic_handle, status_interval_ms)
        else:
            self.context = library.pangu_rx_start(handle, diagnostic_handle)
        if not self.context:
            raise serial.SerialException("Native COM receiver initialization failed")
        self.buffer = ctypes.create_string_buffer(65536)
        self.saved_stats = None
        self.closed = False
        print(f"SERIAL_BRIDGE_NATIVE native_thread=1 depth=256 read_bytes={request_bytes} ring_bytes=16777216 status_interval_ms={status_interval_ms}",
              file=sys.stderr, flush=True)

    @property
    def stats(self):
        if self.closed:
            return dict(self.saved_stats)
        values = (ctypes.c_uint64 * 6)()
        self.library.pangu_rx_stats(self.context, values)
        return dict(zip(("completed", "bytes", "empty", "driver_error_events", "peak_buffer_bytes", "fatal"), values))

    def read(self):
        if self.closed:
            raise serial.SerialException("Native COM receiver is closed")
        count = ctypes.c_uint32()
        result = self.library.pangu_rx_read(self.context, self.buffer, len(self.buffer), ctypes.byref(count), 100)
        if result < 0 or count.value > len(self.buffer):
            raise serial.SerialException("Native COM receiver failed; inspect bridge diagnostics")
        return ctypes.string_at(self.buffer, count.value)

    def close(self):
        if not self.closed:
            self.saved_stats = self.stats
            self.library.pangu_rx_stop(self.context)
            self.closed = True
