#!/usr/bin/env python3
"""Compare real Windows UART buffering while deliberately pausing camera reads."""
import argparse
import ctypes
import datetime
import hashlib
import json
import os
from pathlib import Path
import struct
import time

from board_evidence import validate_project
from camera_stream import CAMERA_MAGIC
from hardware_transport import Board

ROOT = Path(__file__).resolve().parents[1]


def exercise(buffer_bytes, pause_seconds, frame_target):
    from serial import win32

    errors = []
    with Board(rx_buffer_bytes=buffer_bytes) as board:
        identity = board.ping(timeout=10)
        board.preview(1)
        deadline = time.monotonic() + 15
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("No camera image header arrived before the injected pause")
            board.decoder.push(board.serial.read(1))
            prefix = board.decoder.buffer
            if len(prefix) >= 16 and prefix[:4] == CAMERA_MAGIC and prefix[4] == 1:
                length = struct.unpack_from("<I", prefix, 12)[0]
                if 4096 < length <= 1024 * 1024 and len(prefix) < length + 20:
                    break
            board.decoder.pop()
        started = time.monotonic()
        time.sleep(pause_seconds)
        counts = [0, 0]
        first_queue = None
        deadline = time.monotonic() + 30
        while min(counts) < frame_target:
            if time.monotonic() >= deadline:
                raise TimeoutError("Camera reception did not recover after the injected pause")
            flags, queued = win32.DWORD(), win32.COMSTAT()
            if not win32.ClearCommError(board.serial._port_handle, ctypes.byref(flags), ctypes.byref(queued)):
                raise RuntimeError(f"Windows serial diagnostic failed: {ctypes.WinError()}")
            if first_queue is None:
                first_queue = queued.cbInQue
            if flags.value:
                errors.append({"flags": flags.value, "queued_bytes": queued.cbInQue,
                               "elapsed_seconds": time.monotonic() - started})
            data = board.serial.read(max(1, min(65536, queued.cbInQue)))
            board.decoder.push(data)
            while (received := board.decoder.pop()) is not None:
                if received.magic == CAMERA_MAGIC and received.command == 1:
                    counts[received.camera] += 1
        result = {"requested_buffer_bytes": buffer_bytes, "read_pause_seconds": pause_seconds,
                  "queued_bytes_after_pause": first_queue, "driver_errors": errors,
                  "frames_received": counts, "crc_errors": board.decoder.crc_errors,
                  "framing_errors": board.decoder.framing_errors, "last_crc_error": board.decoder.last_crc_error,
                  "board": identity, "elapsed_seconds": time.monotonic() - started}
        board.preview(2)
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "build/vision/serial_buffer_validation")
    parser.add_argument("--pause-seconds", type=float, default=1)
    parser.add_argument("--frames-per-camera", type=int, default=12)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Run this hardware diagnostic with Windows Python while the console is stopped")
    if not 0 < args.pause_seconds <= 5 or args.frames_per_camera <= 0:
        parser.error("Require a positive pause of at most five seconds and a positive frame target")
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for size in (4096, 1048576):
        result = exercise(size, args.pause_seconds, args.frames_per_camera)
        results.append(result)
        print(f"SERIAL_BUFFER requested={size} queued={result['queued_bytes_after_pause']} "
              f"driver_errors={result['driver_errors']} crc_errors={result['crc_errors']} "
              f"frames={result['frames_received']}", flush=True)
    baseline, enlarged = results
    reproduced = bool(baseline["crc_errors"] or baseline["driver_errors"])
    passed = reproduced and not (enlarged["crc_errors"] or enlarged["framing_errors"] or enlarged["driver_errors"])
    report = {"measured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "execution": "Real FPGA camera bytes, Windows serial driver queues and a deliberate read pause",
              "source_hashes": {name: hashlib.sha256((ROOT / "vision" / name).read_bytes()).hexdigest()
                                for name in ("hardware_transport.py", "camera_stream.py", "verify_serial_buffer.py")},
              "project_evidence": validate_project(args.project, enlarged["board"]),
              "results": results, "small_buffer_loss_reproduced": reproduced,
              "driver_overflow_flag_observed": any(error["flags"] & 3 for error in baseline["driver_errors"]),
              "passed": passed}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")
    if not passed:
        raise RuntimeError("The real serial buffer comparison did not establish an error-free larger buffer")
    print("PASS real Windows serial buffer comparison", flush=True)


if __name__ == "__main__":
    main()
