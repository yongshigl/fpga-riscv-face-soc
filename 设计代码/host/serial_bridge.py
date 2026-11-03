#!/usr/bin/env python3
"""Expose a Windows COM port as binary stdin/stdout for the WSL vision tools."""
import argparse
import contextlib
import os
import queue
import sys
import struct
import threading
import time
import zlib

import serial

from camera_stream import BRIDGE_READY


class NativeReceivePolicy:
    """Observe complete host requests without altering any bytes on the wire."""
    def __init__(self, preview_mode="native_coalesced"):
        self.buffer = bytearray()
        self.mode = "native_first_byte"
        self.preview_mode = preview_mode

    def observe(self, data):
        self.buffer.extend(data)
        changes = []
        while True:
            offset = self.buffer.find(b"PGR1")
            if offset < 0:
                self.buffer[:] = self.buffer[-3:]
                break
            if offset:
                del self.buffer[:offset]
            if len(self.buffer) < 16:
                break
            command, _, length = struct.unpack_from("<III", self.buffer, 4)
            if length > 2048:
                del self.buffer[:4]
                continue
            size = length + 20
            if len(self.buffer) < size:
                break
            frame = bytes(self.buffer[:size])
            del self.buffer[:size]
            if zlib.crc32(frame[:-4]) != struct.unpack_from("<I", frame, size - 4)[0]:
                continue
            start_preview = command == 11 and length == 4 and struct.unpack_from("<I", frame, 16)[0] == 1
            mode = self.preview_mode if start_preview else "native_first_byte"
            if mode != self.mode:
                self.mode = mode
                changes.append(mode)
        return changes


def prioritize_windows_io(scope, kernel32=None, process_priority="above_normal"):
    """Give the bridge/receiver precedence over normal CPU-intensive PDS work.

    ABOVE_NORMAL process + HIGHEST receiver remain below real-time scheduling.
    A failed request is reported explicitly and the normal scheduling fallback
    keeps the bridge usable; hardware validation still checks driver errors.
    """
    import ctypes

    if kernel32 is None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if scope == "process":
        get_handle, set_priority = kernel32.GetCurrentProcess, kernel32.SetPriorityClass
        priority, label = ((0x80, "HIGH_PRIORITY_CLASS") if process_priority == "high" else
                           (0x8000, "ABOVE_NORMAL_PRIORITY_CLASS"))
    elif scope == "receiver":
        get_handle, set_priority = kernel32.GetCurrentThread, kernel32.SetThreadPriority
        priority, label = 2, "THREAD_PRIORITY_HIGHEST"
    else:
        raise ValueError("Unknown bridge priority scope")
    get_handle.argtypes, get_handle.restype = [], ctypes.c_void_p
    set_priority.argtypes = [ctypes.c_void_p, ctypes.c_uint32 if scope == "process" else ctypes.c_int]
    set_priority.restype = ctypes.c_int
    applied = bool(set_priority(get_handle(), priority))
    error = "" if applied else f" error={ctypes.get_last_error() if hasattr(ctypes, 'get_last_error') else 'unavailable'}"
    print(f"SERIAL_BRIDGE_PRIORITY scope={scope} requested={label} applied={int(applied)}{error}",
          file=sys.stderr, flush=True)
    return applied


@contextlib.contextmanager
def windows_timer_resolution(milliseconds=0, library=None):
    """Pair the optional process timer request with cleanup on every exit."""
    if not milliseconds or (os.name != "nt" and library is None):
        yield
        return
    import ctypes
    library = library or ctypes.WinDLL("winmm")
    for function in (library.timeBeginPeriod, library.timeEndPeriod):
        function.argtypes, function.restype = [ctypes.c_uint32], ctypes.c_uint32
    result = library.timeBeginPeriod(milliseconds)
    print(f"SERIAL_BRIDGE_TIMER requested_ms={milliseconds} applied={int(result == 0)} result={result}",
          file=sys.stderr, flush=True)
    if result:
        raise serial.SerialException("Windows timer resolution request failed")
    try:
        yield
    finally:
        result = library.timeEndPeriod(milliseconds)
        print(f"SERIAL_BRIDGE_TIMER released_ms={milliseconds} result={result}", file=sys.stderr, flush=True)


def write_all(stream, data):
    remaining = memoryview(data)
    while remaining:
        count = stream.write(remaining)
        if count is None or count <= 0:
            raise BrokenPipeError("Bridge output stopped accepting bytes")
        remaining = remaining[count:]
    stream.flush()


def windows_queue_properties(port_handle, stage, kernel32=None):
    """Log driver-reported queue sizes, rather than the SetupComm request.

    Some providers return zero for an unknown/unavailable current queue size.
    Such a value is diagnostic only, not proof that buffering is disabled.
    """
    import ctypes

    class COMMPROP(ctypes.Structure):
        _fields_ = [("wPacketLength", ctypes.c_uint16), ("wPacketVersion", ctypes.c_uint16),
                    *[(name, ctypes.c_uint32) for name in
                      ("dwServiceMask", "dwReserved1", "dwMaxTxQueue", "dwMaxRxQueue", "dwMaxBaud",
                       "dwProvSubType", "dwProvCapabilities", "dwSettableParams", "dwSettableBaud")],
                    ("wSettableData", ctypes.c_uint16), ("wSettableStopParity", ctypes.c_uint16),
                    *[(name, ctypes.c_uint32) for name in
                      ("dwCurrentTxQueue", "dwCurrentRxQueue", "dwProvSpec1", "dwProvSpec2")],
                    ("wcProvChar", ctypes.c_uint16 * 1)]

    if kernel32 is None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    query = kernel32.GetCommProperties
    query.argtypes, query.restype = [ctypes.c_void_p, ctypes.POINTER(COMMPROP)], ctypes.c_int
    properties = COMMPROP()
    properties.wPacketLength = ctypes.sizeof(properties)
    properties.dwProvSpec1 = 0x0E73CF52  # COMMPROP_INITIALIZED: wPacketLength is valid.
    if not query(port_handle, ctypes.byref(properties)):
        error = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else "unavailable"
        print(f"SERIAL_BRIDGE_QUEUE_PROPERTIES stage={stage} available=0 error={error}",
              file=sys.stderr, flush=True)
        return None
    result = {"current_rx": properties.dwCurrentRxQueue, "current_tx": properties.dwCurrentTxQueue,
              "max_rx": properties.dwMaxRxQueue, "max_tx": properties.dwMaxTxQueue}
    print(f"SERIAL_BRIDGE_QUEUE_PROPERTIES stage={stage} available=1 " +
          " ".join(f"{name}={value}" for name, value in result.items()), file=sys.stderr, flush=True)
    return result


def configure_windows_receive(port, rx_buffer, read_mode, win32_module=None, kernel32=None, *, resize_buffer=True):
    """Use first-byte completion for fixed pending reads; retain explicit comparisons."""
    import ctypes

    if win32_module is None:
        from serial import win32 as win32_module
    if resize_buffer:
        windows_queue_properties(port._port_handle, "opened", kernel32)
        port.set_buffer_size(rx_size=rx_buffer, tx_size=65536)
        windows_queue_properties(port._port_handle, "buffer_configured", kernel32)
    timeouts = win32_module.COMMTIMEOUTS()
    if not win32_module.GetCommTimeouts(port._port_handle, ctypes.byref(timeouts)):
        raise serial.SerialException("GetCommTimeouts failed")
    if read_mode in ("native_coalesced", "native_stream"):
        # Collect USB bursts in each already-posted 4096-byte buffer. Complete
        # short replies after 2 ms of silence, with a 100 ms idle ceiling.
        timeouts.ReadIntervalTimeout = 2
        timeouts.ReadTotalTimeoutMultiplier = 0
        # A stream request remains pending while idle. This avoids expiring
        # all queued preview reads together every 100ms. Shutdown is signaled
        # separately by the native receiver; the inter-byte gap still applies.
        timeouts.ReadTotalTimeoutConstant = 0 if read_mode == "native_stream" else 100
        if not win32_module.SetCommTimeouts(port._port_handle, ctypes.byref(timeouts)):
            raise serial.SerialException("SetCommTimeouts failed")
        if not win32_module.GetCommTimeouts(port._port_handle, ctypes.byref(timeouts)):
            raise serial.SerialException("GetCommTimeouts readback failed")
    elif read_mode in ("fixed_first_byte", "queued_first_byte", "pipelined_first_byte", "native_first_byte"):
        # Return queued bytes immediately, or complete when the first byte arrives.
        # A fixed 4096-byte request remains pending through the USB receive burst.
        timeouts.ReadIntervalTimeout = win32_module.MAXDWORD
        timeouts.ReadTotalTimeoutMultiplier = win32_module.MAXDWORD
        timeouts.ReadTotalTimeoutConstant = 100 if read_mode in ("pipelined_first_byte", "native_first_byte") else 10
        if not win32_module.SetCommTimeouts(port._port_handle, ctypes.byref(timeouts)):
            raise serial.SerialException("SetCommTimeouts failed")
        if not win32_module.GetCommTimeouts(port._port_handle, ctypes.byref(timeouts)):
            raise serial.SerialException("GetCommTimeouts readback failed")
    elif read_mode != "native_adaptive":
        raise ValueError("Unknown Windows bridge read mode")
    # For native_adaptive pySerial already set interval=0, multiplier=0, constant=50,
    # matching hardware_transport.Board on Windows. Do not change the driver path.
    windows_queue_properties(port._port_handle, "timeouts_configured", kernel32)
    print(f"SERIAL_BRIDGE_TIMEOUTS read_mode={read_mode} interval={timeouts.ReadIntervalTimeout} "
          f"multiplier={timeouts.ReadTotalTimeoutMultiplier} constant={timeouts.ReadTotalTimeoutConstant}",
          file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--rx-buffer", type=int, default=1048576)
    parser.add_argument("--io-priority", choices=("above_normal", "high"), default="above_normal")
    parser.add_argument("--timer-resolution-ms", type=int, choices=(0, 1), default=0)
    parser.add_argument("--status-interval-ms", type=int, choices=(0, 100, 250, 500, 1000), default=0)
    parser.add_argument("--native-read-bytes", type=int, choices=(4096, 16384, 65536), default=65536)
    parser.add_argument("--read-mode", choices=("native_dynamic_stream", "native_stream", "native_dynamic", "native_coalesced", "native_first_byte", "pipelined_first_byte", "fixed_first_byte", "native_adaptive", "queued_first_byte"),
                        default="native_dynamic_stream",
                        help="Windows receive mode; native_dynamic_stream keeps preview reads pending between bursts and uses first-byte CPU replies")
    args = parser.parse_args()
    if args.rx_buffer <= 0:
        parser.error("Serial receive buffer must be positive")
    if args.status_interval_ms and args.read_mode not in ("native_dynamic", "native_dynamic_stream",
                                                         "native_stream", "native_first_byte", "native_coalesced"):
        parser.error("Bounded status polling requires the native receiver")
    if args.native_read_bytes != 65536 and args.read_mode not in ("native_dynamic", "native_dynamic_stream",
                                                               "native_stream", "native_first_byte", "native_coalesced"):
        parser.error("Configurable read requests require the native receiver")
    if os.name == "nt":
        import ctypes
        import msvcrt
        from serial import win32
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
        prioritize_windows_io("process", process_priority=args.io_priority)
    stopped = threading.Event()
    outgoing = queue.Queue(maxsize=4096)
    stats = {"rx_bytes": 0, "tx_bytes": 0, "max_input_queue": 0, "peak_output_blocks": 0,
             "read_calls": 0, "requested_read_bytes": 0, "empty_reads": 0,
             "max_read_gap_ms": 0.0, "max_read_call_ms": 0.0,
             "max_stdout_write_ms": 0.0, "driver_error_events": 0}
    native = os.name != "nt" or args.read_mode == "native_adaptive"
    with windows_timer_resolution(args.timer_resolution_ms), serial.Serial(args.port, args.baud, timeout=0.05 if native else 0.01,
                       inter_byte_timeout=None if native else 0.002, write_timeout=5) as port:
        if os.name == "nt":
            configure_windows_receive(port, args.rx_buffer,
                                      "native_first_byte" if args.read_mode in ("native_dynamic", "native_dynamic_stream") else args.read_mode)
        port.reset_input_buffer()
        print(f"SERIAL_BRIDGE_READY {args.port} {args.baud} requested_rx={args.rx_buffer} requested_tx=65536 "
              f"rx_chunk=4096 output_queue_blocks=4096 read_mode={args.read_mode if os.name == 'nt' else 'adaptive'}",
              file=sys.stderr, flush=True)
        write_all(sys.stdout.buffer, BRIDGE_READY)

        # Keep COM reads posted even when WSL stalls its output pipe; memory stays bounded.
        def receive():
            pipeline = None
            if os.name == "nt":
                prioritize_windows_io("receiver")
            completed = time.perf_counter()
            try:
                if os.name == "nt":
                    if args.read_mode in ("native_dynamic", "native_dynamic_stream", "native_stream", "native_first_byte", "native_coalesced"):
                        from windows_serial_native import WindowsNativeReceiver
                        pipeline = WindowsNativeReceiver(port._port_handle, status_interval_ms=args.status_interval_ms,
                                                         request_bytes=args.native_read_bytes)
                    elif args.read_mode == "pipelined_first_byte":
                        from windows_serial_pipeline import WindowsReadPipeline
                        pipeline = WindowsReadPipeline(port._port_handle)
                        print("SERIAL_BRIDGE_PIPELINE depth=32 request_bytes=4096 idle_timeout_ms=100",
                              file=sys.stderr, flush=True)
                while not stopped.is_set():
                    gap_ms = (time.perf_counter() - completed) * 1000
                    stats["max_read_gap_ms"] = max(stats["max_read_gap_ms"], gap_ms)
                    if os.name == "nt" and not (args.status_interval_ms and isinstance(pipeline, WindowsNativeReceiver)):
                        # pySerial otherwise clears and ignores these driver error flags.
                        flags, queued = win32.DWORD(), win32.COMSTAT()
                        if not win32.ClearCommError(port._port_handle, ctypes.byref(flags), ctypes.byref(queued)):
                            raise serial.SerialException(f"ClearCommError failed: {ctypes.WinError()}")
                        if flags.value:
                            stats["driver_error_events"] += 1
                            print(f"SERIAL_BRIDGE_COMM_ERROR flags=0x{flags.value:x} queued={queued.cbInQue} "
                                  f"read_gap_ms={gap_ms:.3f} max_read_call_ms={stats['max_read_call_ms']:.3f} "
                                  f"max_stdout_write_ms={stats['max_stdout_write_ms']:.3f} "
                                  f"monotonic={time.monotonic():.6f}", file=sys.stderr, flush=True)
                        available = queued.cbInQue
                    elif os.name != "nt":
                        available = port.in_waiting
                    else:
                        # The native poller owns error inspection in this candidate;
                        # keep driver flags latched and avoid duplicate status IOCTLs.
                        available = 0
                    stats["max_input_queue"] = max(stats["max_input_queue"], available)
                    read_started = time.perf_counter()
                    request_bytes = (4096 if os.name == "nt" and args.read_mode in ("fixed_first_byte", "pipelined_first_byte", "native_dynamic", "native_dynamic_stream", "native_stream", "native_first_byte", "native_coalesced")
                                     else max(1, min(4096, available)))
                    stats["read_calls"] += 1
                    stats["requested_read_bytes"] += request_bytes
                    data = pipeline.read() if pipeline else port.read(request_bytes)
                    completed = time.perf_counter()
                    stats["max_read_call_ms"] = max(stats["max_read_call_ms"], (completed - read_started) * 1000)
                    stats["empty_reads"] += not data
                    if data:
                        stats["rx_bytes"] += len(data)
                        outgoing.put_nowait(data)
                        stats["peak_output_blocks"] = max(stats["peak_output_blocks"], outgoing.qsize())
            except (OSError, serial.SerialException, queue.Full) as error:
                print(f"SERIAL_BRIDGE_READ_ERROR {error}", file=sys.stderr, flush=True)
                stopped.set()
            finally:
                if pipeline:
                    try:
                        pipeline.close()
                    except serial.SerialException as error:
                        print(f"SERIAL_BRIDGE_READ_ERROR {error}", file=sys.stderr, flush=True)
                        stopped.set()
                    print("SERIAL_BRIDGE_PIPELINE_STATS " + " ".join(
                        f"{name}={value}" for name, value in pipeline.stats.items()), file=sys.stderr, flush=True)

        def transmit():
            try:
                while not stopped.is_set() or not outgoing.empty():
                    try:
                        data = bytearray(outgoing.get(timeout=0.05))
                    except queue.Empty:
                        continue
                    while len(data) < 16384:
                        try:
                            data.extend(outgoing.get_nowait())
                        except queue.Empty:
                            break
                    started = time.perf_counter()
                    write_all(sys.stdout.buffer, data)
                    stats["max_stdout_write_ms"] = max(stats["max_stdout_write_ms"],
                                                       (time.perf_counter() - started) * 1000)
                    stats["tx_bytes"] += len(data)
            except OSError as error:
                print(f"SERIAL_BRIDGE_WRITE_ERROR {error}", file=sys.stderr, flush=True)
                stopped.set()

        reader = threading.Thread(target=receive, daemon=True)
        writer = threading.Thread(target=transmit, daemon=True)
        reader.start()
        writer.start()
        policy = (NativeReceivePolicy("native_stream" if args.read_mode == "native_dynamic_stream" else "native_coalesced")
                  if os.name == "nt" and args.read_mode in ("native_dynamic", "native_dynamic_stream") else None)
        try:
            while not stopped.is_set():
                data = os.read(sys.stdin.fileno(), 65536)
                if not data:
                    break
                if policy:
                    for mode in policy.observe(data):
                        configure_windows_receive(port, args.rx_buffer, mode, resize_buffer=False)
                        print(f"SERIAL_BRIDGE_POLICY read_mode={mode}", file=sys.stderr, flush=True)
                port.write(data)
        finally:
            stopped.set()
            reader.join(timeout=1)
            writer.join(timeout=1)
            print("SERIAL_BRIDGE_STATS " + " ".join(f"{name}={value}" for name, value in stats.items()),
                  file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
