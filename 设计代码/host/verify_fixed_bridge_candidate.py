#!/usr/bin/env python3
"""Run an existing hardware verifier with an explicit experimental bridge.

The harness records raw decoder input when CRC counters increase and binds
the candidate and harness hashes to the original report. It never resets or
ignores decoder errors. Example (only the parent owns actual hardware):

  python vision/verify_fixed_bridge_candidate.py --verifier backpressure \
    --project <project> --output <new-output> -- --pause-seconds 5 --require-clean

  python vision/verify_fixed_bridge_candidate.py --verifier capture \
    --project <project> --output <new-output> -- --seconds 180 --frames 256
"""
import argparse
import contextlib
import datetime
import hashlib
import json
from pathlib import Path
import runpy
import sys
import time
from unittest.mock import patch

import camera_stream
import hardware_transport

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "vision/serial_bridge_fixed_candidate.py"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class FaultRecorder:
    """Capture buffers before failed decoding; at most 16 raw snapshots per run."""
    def __init__(self, directory, maximum=16):
        self.directory = Path(directory)
        self.maximum = maximum
        self.events = []
        self.snapshot_bytes = 0
        self.suppressed_snapshots = 0
        self.pending = None

    def save_index(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / "faults.json.tmp"
        temporary.write_text(json.dumps({"events": self.events, "snapshot_bytes": self.snapshot_bytes,
                                         "suppressed_snapshots": self.suppressed_snapshots,
                                         "scope": "Raw buffered UART bytes before WireDecoder.pop plus recovery context; CRC counts unchanged"},
                                        indent=2) + "\n", encoding="ascii")
        temporary.replace(self.directory / "faults.json")

    def append(self, data):
        if self.pending is None:
            return
        self.pending[1].extend(data)
        # One maximum-length failed packet and one recovery packet, plus one read.
        if len(self.pending[1]) > 2 * 1024 * 1024 + 65576:
            self.finish_context(reason="context_byte_limit")

    def finish_context(self, packet=None, reason="next_crc_valid_packet"):
        if self.pending is None:
            return
        event, context = self.pending
        self.pending = None
        path = self.directory / f"crc_{event['snapshot_index']:03d}_recovery_context.bin"
        with path.open("xb") as stream:
            stream.write(context)
        event.update(context_path=str(path), context_sha256=sha256(path),
                     context_bytes=len(context), context_end_reason=reason)
        if packet is not None:
            event["recovered_packet"] = {"magic": packet.magic.decode("ascii"), "command": packet.command,
                                         "sequence": packet.sequence, "camera": packet.camera,
                                         "format": packet.format, "payload_bytes": len(packet.payload)}
        self.save_index()

    def record(self, decoder, before, previous_crc):
        if decoder.crc_errors <= previous_crc:
            return
        self.finish_context(reason="another_crc_error")
        event = {"observed_monotonic": time.monotonic(), "previous_crc_errors": previous_crc,
                 "crc_errors": decoder.crc_errors, "framing_errors": decoder.framing_errors,
                 "bytes_received": decoder.bytes_received, "before_buffer_bytes": len(before),
                 "remaining_buffer_bytes": len(decoder.buffer), "last_crc_error": decoder.last_crc_error}
        if len(self.events) < self.maximum:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"crc_{len(self.events) + 1:03d}_decoder_input.bin"
            with path.open("xb") as stream:
                stream.write(before)
            event.update(raw_path=str(path), raw_sha256=sha256(path))
            event["snapshot_index"] = len(self.events) + 1
            self.pending = event, bytearray(before)
            self.snapshot_bytes += len(before)
        else:
            self.suppressed_snapshots += 1
        self.events.append(event)
        self.save_index()


@contextlib.contextmanager
def candidate_transport(output, bridge=BRIDGE, bridge_arguments=()):
    """Redirect only the board bridge child; wslpath and other processes stay intact."""
    output, bridge = Path(output), Path(bridge).resolve()
    recorder = FaultRecorder(output / "decoder_faults")
    original_popen = hardware_transport.subprocess.Popen
    original_pop = camera_stream.WireDecoder.pop
    original_push = camera_stream.WireDecoder.push
    launched = []

    def popen(command, *args, **kwargs):
        command = list(command) if isinstance(command, (list, tuple)) else command
        if (isinstance(command, list) and len(command) >= 3 and command[1] == "-u"
                and str(command[2]).replace("\\", "/").endswith("/vision/serial_bridge.py")):
            # Keep the already converted Windows directory, replacing only the filename.
            command[2] = str(command[2]).rsplit("serial_bridge.py", 1)[0] + bridge.name
            command.extend(bridge_arguments)
            launched.append({"script": str(bridge), "source_sha256": sha256(bridge), "command": command})
        return original_popen(command, *args, **kwargs)

    def pop(decoder):
        previous_crc, before = decoder.crc_errors, bytes(decoder.buffer)
        result = None
        try:
            result = original_pop(decoder)
            return result
        finally:
            recorder.record(decoder, before, previous_crc)
            if result is not None:
                recorder.finish_context(result)

    def push(decoder, data):
        original_push(decoder, data)
        recorder.append(data)

    with patch.object(hardware_transport.subprocess, "Popen", popen), \
            patch.object(camera_stream.WireDecoder, "pop", pop), \
            patch.object(camera_stream.WireDecoder, "push", push):
        try:
            yield recorder, launched
        finally:
            recorder.finish_context(reason="verification_exit")


def capture(project, output, extra):
    """Sustained direct Board reads without model uploads or preview handoff tasks."""
    from board_evidence import validate_project

    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=180)
    parser.add_argument("--frames", type=int, default=256)
    args = parser.parse_args(extra)
    if args.seconds <= 0 or args.frames <= 0:
        parser.error("Use positive duration and frame target")
    counts = [0, 0]
    started = time.monotonic()
    with hardware_transport.Board(bridge_log=output / "bridge.log") as board:
        identity = board.ping(timeout=10)
        board.preview(1)
        try:
            while time.monotonic() - started < args.seconds or min(counts) < args.frames:
                packet = board.read_camera(0.5)
                if packet and packet.magic == camera_stream.CAMERA_MAGIC and packet.command == 1:
                    counts[packet.camera] += 1
                if time.monotonic() - started > max(args.seconds, args.frames * 3) + 30:
                    raise TimeoutError("Sustained camera target did not complete")
        finally:
            board.preview(2)
        result = {"measured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  "execution": "Real dual-camera UART through independent fixed4096/first-byte bridge candidate",
                  "project_evidence": validate_project(project, identity),
                  "target_seconds": args.seconds, "target_frames_per_camera": args.frames,
                  "elapsed_seconds": time.monotonic() - started, "frames_received": counts,
                  "crc_errors": board.decoder.crc_errors, "framing_errors": board.decoder.framing_errors,
                  "last_crc_error": board.decoder.last_crc_error,
                  "source_hashes": {name: sha256(ROOT / "vision" / name) for name in
                                    ("hardware_transport.py", "serial_bridge.py", "camera_stream.py")}}
    diagnostics = (output / "bridge.log").read_text(encoding="utf-8", errors="replace").splitlines()
    result["driver_errors"] = [line for line in diagnostics if any(tag in line for tag in
                              ("SERIAL_BRIDGE_COMM_ERROR", "SERIAL_BRIDGE_READ_ERROR", "SERIAL_BRIDGE_WRITE_ERROR"))]
    result["passed"] = not (result["crc_errors"] or result["framing_errors"] or result["driver_errors"])
    (output / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="ascii")
    print(f"FIXED_BRIDGE_CAPTURE frames={counts} crc={result['crc_errors']} passed={result['passed']}", flush=True)
    if not result["passed"]:
        raise RuntimeError("Sustained fixed-read candidate contained UART errors")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verifier", choices=("backpressure", "latency", "capture"), required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args, extra = parser.parse_known_args()
    if extra and extra[0] == "--":
        extra = extra[1:]
    if args.output.exists():
        parser.error("Use a new evidence directory; previous evidence cannot be overwritten")
    args.output.mkdir(parents=True)
    recorder, launched = None, []
    try:
        with candidate_transport(args.output) as (recorder, launched):
            if args.verifier == "capture":
                capture(args.project, args.output, extra)
            else:
                verifier = ROOT / "vision" / ("verify_bridge_" + args.verifier + ".py")
                with patch.object(sys, "argv", [str(verifier), "--project", str(args.project),
                                               "--output", str(args.output), *extra]):
                    runpy.run_path(str(verifier), run_name="__main__")
    finally:
        diagnostic = {"candidate_source_sha256": sha256(BRIDGE), "harness_source_sha256": sha256(__file__),
                      "production_bridge_source_sha256": sha256(ROOT / "vision/serial_bridge.py"),
                      "read_mode": "queued_first_byte", "read_request_bytes": 4096,
                      "launched": launched, "fault_events": recorder.events if recorder else [],
                      "single_factor": "Fixed pending ReadFile request4096 instead of queued-adaptive max1/min4096",
                      "counter_policy": "All CRC/framing/driver errors remain fatal and visible"}
        (args.output / "candidate.json").write_text(json.dumps(diagnostic, indent=2) + "\n", encoding="ascii")
        report_path = args.output / "report.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text())
            report["bridge_candidate"] = diagnostic
            report.setdefault("source_hashes", {}).update({BRIDGE.name: sha256(BRIDGE),
                                                          Path(__file__).name: sha256(__file__)})
            report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")


if __name__ == "__main__":
    main()
