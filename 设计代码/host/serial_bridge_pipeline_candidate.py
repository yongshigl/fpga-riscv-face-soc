#!/usr/bin/env python3
"""Independent 32-request first-byte receiver; production default stays intact."""
import argparse
import ctypes
import os
import sys
import time
from unittest.mock import patch

import serial_bridge
from windows_serial_pipeline import WindowsReadPipeline


def main():
    if os.name != "nt":
        raise RuntimeError("The pipelined receiver candidate requires Windows")
    from serial import win32
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--inject-read-pause-ms", type=float, default=0)
    parser.add_argument("--inject-every-reads", type=int, default=1000)
    injection, remaining = parser.parse_known_args()
    if not 0 <= injection.inject_read_pause_ms <= 100 or injection.inject_every_reads <= 0:
        raise ValueError("Use a bounded receive-thread pause and a positive read interval")
    if "--read-mode" in remaining:
        index = remaining.index("--read-mode")
        if index + 1 == len(remaining) or remaining[index + 1] != "fixed_first_byte":
            raise ValueError("The independent pipeline candidate requires --read-mode fixed_first_byte")
    else:
        remaining.extend(["--read-mode", "fixed_first_byte"])
    sys.argv = [sys.argv[0], *remaining]
    original_factory = serial_bridge.serial.Serial
    original_configure = serial_bridge.configure_windows_receive

    class Port:
        def __init__(self, *args, **kwargs):
            self.serial = original_factory(*args, **kwargs)
            self.pipeline = None

        def __getattr__(self, name):
            return getattr(self.serial, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            try:
                if self.pipeline:
                    self.pipeline.close()
                    print("SERIAL_BRIDGE_PIPELINE_STATS " + " ".join(
                        f"{key}={value}" for key, value in self.pipeline.stats.items()),
                        file=sys.stderr, flush=True)
            finally:
                self.serial.close()

        def read(self, size):
            if self.pipeline is None:
                self.pipeline = WindowsReadPipeline(self.serial._port_handle)
                print("SERIAL_BRIDGE_PIPELINE depth=32 request_bytes=4096 idle_timeout_ms=100",
                      file=sys.stderr, flush=True)
            count = self.pipeline.stats["completed"]
            if (injection.inject_read_pause_ms and count and count % injection.inject_every_reads == 0):
                print(f"SERIAL_BRIDGE_INJECT_READ_PAUSE completed={count} "
                      f"milliseconds={injection.inject_read_pause_ms}", file=sys.stderr, flush=True)
                time.sleep(injection.inject_read_pause_ms / 1000)
            return self.pipeline.read(size)

    def configure(port, rx_buffer, read_mode):
        original_configure(port, rx_buffer, read_mode)
        timeouts = win32.COMMTIMEOUTS()
        if not win32.GetCommTimeouts(port._port_handle, ctypes.byref(timeouts)):
            raise RuntimeError("Pipeline timeout query failed")
        timeouts.ReadTotalTimeoutConstant = 100
        if not win32.SetCommTimeouts(port._port_handle, ctypes.byref(timeouts)):
            raise RuntimeError("Pipeline timeout configuration failed")
        if not win32.GetCommTimeouts(port._port_handle, ctypes.byref(timeouts)):
            raise RuntimeError("Pipeline timeout readback failed")
        print(f"SERIAL_BRIDGE_PIPELINE_TIMEOUTS interval={timeouts.ReadIntervalTimeout} "
              f"multiplier={timeouts.ReadTotalTimeoutMultiplier} constant={timeouts.ReadTotalTimeoutConstant}",
              file=sys.stderr, flush=True)

    print("SERIAL_BRIDGE_CANDIDATE read_mode=pipelined_first_byte depth=32", file=sys.stderr, flush=True)
    with patch.object(serial_bridge.serial, "Serial", Port), \
            patch.object(serial_bridge, "configure_windows_receive", configure):
        serial_bridge.main()


if __name__ == "__main__":
    main()
