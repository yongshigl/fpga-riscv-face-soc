#!/usr/bin/env python3
"""Experimental fixed 4096-byte requests on the previous first-byte driver mode.

Imports the frozen production bridge, preserving its queues, timeout setup,
priority and error gates. Only the size passed to pySerial.read changes. This
is an evidence candidate, not a verified replacement for serial_bridge.py.
"""
import collections
import sys
import time
from unittest.mock import patch

import serial_bridge


READ_BYTES = 4096


def wrap_fixed_reads(port, stats):
    original_read = port.read

    def read(available_request):
        stats["calls"] += 1
        stats["available_request_1_calls"] += available_request == 1
        stats["available_request_bytes"] += available_request
        stats["requested_bytes"] += READ_BYTES
        started = time.perf_counter()
        data = original_read(READ_BYTES)
        stats["read_seconds"] += time.perf_counter() - started
        stats["returned_bytes"] += len(data)
        stats["empty_calls"] += not data
        return data

    port.read = read
    return port


def main():
    # Keep the previous timeout mode while changing only pending request size.
    if "--read-mode" in sys.argv:
        index = sys.argv.index("--read-mode")
        if index + 1 == len(sys.argv) or sys.argv[index + 1] != "queued_first_byte":
            raise ValueError("The fixed-read candidate requires --read-mode queued_first_byte")
    else:
        sys.argv.extend(["--read-mode", "queued_first_byte"])
    stats = collections.defaultdict(int)
    original_factory = serial_bridge.serial.Serial

    def factory(*args, **kwargs):
        return wrap_fixed_reads(original_factory(*args, **kwargs), stats)

    print("SERIAL_BRIDGE_CANDIDATE read_request_bytes=4096 read_mode=queued_first_byte "
          "production_source=serial_bridge.py", file=sys.stderr, flush=True)
    try:
        with patch.object(serial_bridge.serial, "Serial", factory):
            serial_bridge.main()
    finally:
        count = stats["calls"]
        stats["average_returned_bytes"] = stats["returned_bytes"] / count if count else 0
        stats["average_read_call_ms"] = stats["read_seconds"] * 1000 / count if count else 0
        print("SERIAL_BRIDGE_READ_CALLS " + " ".join(f"{key}={value}" for key, value in stats.items()),
              file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
