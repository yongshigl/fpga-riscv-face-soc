#!/usr/bin/env python3
"""Exercise an explicit receive mode using the existing fatal hardware gates."""
import argparse
import json
from pathlib import Path
import runpy
import sys
from unittest.mock import patch

from transport_evidence import snapshot_transport
from verify_fixed_bridge_candidate import candidate_transport, capture, sha256

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--read-mode", required=True,
                        choices=("native_dynamic_stream", "native_dynamic", "native_coalesced"))
    parser.add_argument("--verifier", required=True, choices=("capture", "latency", "backpressure"))
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--io-priority", choices=("above_normal", "high"), default="above_normal")
    parser.add_argument("--timer-resolution-ms", type=int, choices=(0, 1), default=0)
    parser.add_argument("--status-interval-ms", type=int, choices=(0, 100, 250, 500, 1000), default=0)
    args, extra = parser.parse_known_args()
    if extra and extra[0] == "--":
        extra = extra[1:]
    args.output.mkdir(parents=True, exist_ok=False)
    hashes = snapshot_transport(args.output)
    hashes[Path(__file__).name] = sha256(__file__)
    (args.output / "driver_sources" / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    recorder, launched = None, []
    try:
        with candidate_transport(args.output, ROOT / "vision/serial_bridge.py",
                                 ("--read-mode", args.read_mode, "--io-priority", args.io_priority,
                                  "--timer-resolution-ms", str(args.timer_resolution_ms),
                                  "--status-interval-ms", str(args.status_interval_ms))) as (recorder, launched):
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
        diagnostic = {"read_mode": args.read_mode, "io_priority": args.io_priority,
                      "timer_resolution_ms": args.timer_resolution_ms,
                      "status_interval_ms": args.status_interval_ms,
                      "launched": launched, "source_hashes": hashes,
                      "driver_errors": [line for line in lines if any(tag in line for tag in
                                        ("COMM_ERROR", "READ_ERROR", "WRITE_ERROR"))],
                      "fault_events": recorder.events if recorder else [],
                      "counter_policy": "Every CRC/framing/driver error remains visible and fatal"}
        (args.output / "candidate.json").write_text(json.dumps(diagnostic, indent=2) + "\n")
        report_path = args.output / "report.json"
        if report_path.exists():
            report = json.loads(report_path.read_text())
            report["bridge_candidate"] = diagnostic
            report["passed"] = bool(report.get("passed")) and not diagnostic["driver_errors"]
            report_path.write_text(json.dumps(report, indent=2) + "\n")
        if diagnostic["driver_errors"]:
            raise RuntimeError("The receive candidate reported a driver or transport error")


if __name__ == "__main__":
    main()
