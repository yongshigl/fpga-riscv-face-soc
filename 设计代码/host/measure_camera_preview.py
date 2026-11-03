#!/usr/bin/env python3
"""Measure camera delivery and capture-complete age without interrupting preview."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import statistics
import time
import urllib.request


def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8086")
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--poll-interval", type=float, default=0.1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.seconds <= 0 or args.poll_interval <= 0:
        parser.error("Observation duration and polling interval must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    error_keys = ("crc_errors", "framing_errors", "decode_errors", "connection_retries")
    started = time.monotonic()
    initial = final = None
    channels = [[], []]
    last_received = [None, None]
    maximum_stale = [0.0, 0.0]
    failure = None
    next_progress = started
    try:
        while True:
            with urllib.request.urlopen(args.url + "/api/status", timeout=5) as response:
                final = json.load(response)
            if (not final["connected"] or not final["preview_enabled"] or final["error"]
                    or final["active_job"] or len(final["cameras"]) != 2):
                raise RuntimeError("Preview is not continuously ready or a job interrupted the measurement")
            if initial is None:
                initial = final
                (args.output / "initial_status.json").write_text(json.dumps(initial, indent=2) + "\n", encoding="ascii")
            if final["board"] != initial["board"]:
                raise RuntimeError("Board identity changed during observation")
            if any(final[key] != initial[key] for key in error_keys):
                raise RuntimeError("New communication or decode errors occurred during observation")
            elapsed = time.monotonic() - started
            for camera, info in enumerate(final["cameras"]):
                maximum_stale_seconds = 2.0
                if info.get("format") == 3:
                    frame_wire_seconds = (info["encoded_bytes"] + 32) * 10 / final["baud"]
                    maximum_stale_seconds = max(maximum_stale_seconds, 2 * frame_wire_seconds + 1)
                if (info["camera"] != camera or not info["sensor"]["verified"]
                        or info["stale_seconds"] is None or info["stale_seconds"] > maximum_stale_seconds):
                    raise RuntimeError("A verified camera stopped delivering frames")
                maximum_stale[camera] = max(maximum_stale[camera], info["stale_seconds"])
                if info["received_monotonic"] != last_received[camera]:
                    last_received[camera] = info["received_monotonic"]
                    channels[camera].append({key: info[key] for key in
                        ("frames", "sequence", "received_monotonic", "estimated_frame_age_ms", "encoded_bytes")})
            counts = [info["frames"] - initial["cameras"][index]["frames"]
                      for index, info in enumerate(final["cameras"])]
            if time.monotonic() >= next_progress:
                print(f"CAMERA_MEASUREMENT {elapsed:.1f}s frames={counts} new_errors=0", flush=True)
                next_progress = time.monotonic() + 30
            if elapsed >= args.seconds:
                break
            time.sleep(args.poll_interval)
    except Exception as error:
        failure = f"{type(error).__name__}: {error}"
    elapsed = time.monotonic() - started
    summaries = []
    if initial is not None and final is not None:
        for camera, channel in enumerate(channels):
            if not channel:
                continue
            ages = [frame["estimated_frame_age_ms"] for frame in channel]
            byte_counts = [frame["encoded_bytes"] for frame in channel]
            delivered = final["cameras"][camera]["frames"] - initial["cameras"][camera]["frames"]
            summaries.append({"camera": camera, "delivered_frames": delivered,
                              "fps": delivered / elapsed, "sampled_frames": len(channel),
                              "age_ms_mean": statistics.mean(ages), "age_ms_p50": percentile(ages, 0.5),
                              "age_ms_p95": percentile(ages, 0.95), "age_ms_max": max(ages),
                              "jpeg_bytes_mean": statistics.mean(byte_counts),
                              "maximum_stale_seconds": maximum_stale[camera],
                              "sensor": final["cameras"][camera]["sensor"]})
        (args.output / "final_status.json").write_text(json.dumps(final, indent=2) + "\n", encoding="ascii")
    report = {"measured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "execution": "Read-only HTTP observation of real dual-camera preview",
              "elapsed_seconds": elapsed, "requested_seconds": args.seconds,
              "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "board": initial["board"] if initial else None, "camera_summary": summaries,
              "initial_errors": {key: initial[key] for key in error_keys} if initial else None,
              "new_errors": {key: final[key] - initial[key] for key in error_keys} if initial and final else None,
              "frames": channels, "error": failure, "passed": failure is None,
              "timing_scope": "Capture-complete to UART-complete age; excludes sensor exposure, USB and browser rendering"}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")
    if failure:
        raise RuntimeError(failure)
    print(json.dumps({"passed": True, "elapsed_seconds": elapsed, "camera_summary": summaries}), flush=True)


if __name__ == "__main__":
    main()
