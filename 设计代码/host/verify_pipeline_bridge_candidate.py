#!/usr/bin/env python3
"""Use existing fatal-error hardware gates with an independent pending-read ring."""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import sys
from unittest.mock import patch

from verify_fixed_bridge_candidate import candidate_transport, capture

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "vision/serial_bridge_pipeline_candidate.py"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verifier", choices=("capture", "backpressure", "latency"), required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--read-pause-ms", type=float, default=0)
    args, extra = parser.parse_known_args()
    if extra and extra[0] == "--":
        extra = extra[1:]
    if args.output.exists():
        parser.error("Use a new evidence directory")
    args.output.mkdir(parents=True)
    sources = ("serial_bridge.py", BRIDGE.name, "windows_serial_pipeline.py",
               "verify_fixed_bridge_candidate.py", Path(__file__).name)
    source_hashes = {}
    snapshot = args.output / "source_snapshot"
    snapshot.mkdir()
    for name in sources:
        data = (ROOT / "vision" / name).read_bytes()
        (snapshot / name).write_bytes(data)
        source_hashes[name] = hashlib.sha256(data).hexdigest()
    recorder, launched = None, []
    try:
        bridge_arguments = (["--inject-read-pause-ms", str(args.read_pause_ms)] if args.read_pause_ms else [])
        with candidate_transport(args.output, BRIDGE, bridge_arguments) as (recorder, launched):
            if args.verifier == "capture":
                capture(args.project, args.output, extra)
            else:
                script = ROOT / "vision" / f"verify_bridge_{args.verifier}.py"
                with patch.object(sys, "argv", [str(script), "--project", str(args.project),
                                                "--output", str(args.output), *extra]):
                    runpy.run_path(str(script), run_name="__main__")
    finally:
        log = args.output / "bridge.log"
        lines = log.read_text(errors="replace").splitlines() if log.exists() else []
        diagnostic = {"read_mode": "pipelined_first_byte", "pending_read_depth": 32,
                      "read_request_bytes": 4096, "idle_timeout_ms": 100,
                      "injected_read_pause_ms": args.read_pause_ms,
                      "launched": launched, "source_hashes": source_hashes,
                      "fault_events": recorder.events if recorder else [],
                      "driver_errors": [line for line in lines if any(tag in line for tag in
                                        ("SERIAL_BRIDGE_COMM_ERROR", "SERIAL_BRIDGE_READ_ERROR",
                                         "SERIAL_BRIDGE_WRITE_ERROR"))],
                      "pipeline_diagnostics": [line for line in lines if "SERIAL_BRIDGE_PIPELINE" in line],
                      "injected_pauses": [line for line in lines if "SERIAL_BRIDGE_INJECT_READ_PAUSE" in line],
                      "counter_policy": "CRC, framing and driver errors remain fatal and visible"}
        (args.output / "candidate.json").write_text(json.dumps(diagnostic, indent=2) + "\n")
        report_path = args.output / "report.json"
        if report_path.exists():
            report = json.loads(report_path.read_text())
            report["execution"] = report["execution"].replace("fixed4096/first-byte", "32-pending-read/first-byte")
            report["bridge_candidate"] = diagnostic
            report.setdefault("source_hashes", {}).update(source_hashes)
            report["passed"] = report["passed"] and not diagnostic["driver_errors"]
            report_path.write_text(json.dumps(report, indent=2) + "\n")
            if not report["passed"]:
                raise RuntimeError("Pipelined bridge hardware gate failed")


if __name__ == "__main__":
    main()
