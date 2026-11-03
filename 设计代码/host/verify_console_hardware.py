#!/usr/bin/env python3
"""Exercise the PC console against real FPGA inference and persistent galleries."""
import argparse
import datetime
import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from board_evidence import validate_project
from transport_evidence import snapshot_transport

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8086")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "build/vision/console_hardware_validation")
    parser.add_argument("--stage", choices=("exercise", "persistence", "restored"), default="exercise")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    transport_hashes = snapshot_transport(args.output)
    records = []

    def request(path, payload=None):
        body = None if payload is None else json.dumps(payload).encode("ascii")
        req = urllib.request.Request(args.url + path, data=body,
                                     headers={"Content-Type": "application/json"} if body else {})
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)

    def wait(predicate, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = request("/api/status")
            if predicate(status):
                return status
            time.sleep(0.5)
        raise TimeoutError("The console did not reach the expected hardware state")

    def job(action, fail=False, **options):
        start = time.monotonic()
        identifier = request("/api/jobs", {"action": action, **options})["id"]
        status = wait(lambda status: any(job["id"] == identifier and job["status"] in ("done", "failed")
                                        for job in status["jobs"]), timeout=900)
        result = next(job for job in status["jobs"] if job["id"] == identifier)
        if (result["status"] == "failed") != fail:
            raise RuntimeError(f"Unexpected console task result: {result}")
        records.append({**result, "elapsed_ms": (time.monotonic() - start) * 1000})
        print(f"Console hardware {action}: {result['status']}", flush=True)
        return status

    def infer(source, expected=None):
        status = job("infer", source=source)
        result = status["result"]
        if ("FPGA" not in result["execution"] or result["board"] != identity
                or result["match_threshold_q15"] != 9612):
            raise RuntimeError(f"Unexpected real console inference: {source}")
        if expected is not None and (len(result["faces"]) != 1 or result["faces"][0]["identity"] != expected):
            raise RuntimeError(f"Unexpected real console identity: {source}")
        if source.startswith("camera") and (result["image_width"], result["image_height"]) != (640, 360):
            raise RuntimeError(f"Camera inference used unexpected image dimensions: {source}")
        records[-1]["result"] = result
        with urllib.request.urlopen(args.url + "/image/result", timeout=15) as response:
            (args.output / f"{args.stage}_{source}.jpg").write_bytes(response.read())
        return status

    initial = wait(lambda status: status["connected"] and status["mode"] == "vision", timeout=60)
    if (initial["connection_retries"] or initial["error"]
            or any(initial[name] for name in ("crc_errors", "framing_errors", "decode_errors"))):
        raise RuntimeError("The console did not reach its initial hardware state without errors or unexpected retries")
    identity = initial["board"]
    project = validate_project(args.project, identity)
    if not all(initial["capabilities"][name] for name in ("camera_preview", "compressed_write")):
        raise RuntimeError("The console requires combined camera/inference hardware")
    if args.stage == "exercise":
        if initial["gallery"]:
            raise ValueError("Run the exercise with an empty, isolated console output directory")
        job("load_models")
        job("enroll", source="obama", name="Obama")
        enrolled = job("enroll", source="biden", name="Biden")
        if [entry["name"] for entry in enrolled["gallery"]] != ["Obama", "Biden"]:
            raise RuntimeError("Console enrollment did not update the gallery")
        infer("obama2", "Obama")
        infer("astronaut", "Unknown")
        duplicate = job("enroll", fail=True, source="obama", name="Obama")
        if duplicate["gallery"] != enrolled["gallery"]:
            raise RuntimeError("Rejected enrollment changed the gallery")
        removed = job("remove_identity", index=0)
        if [(entry["name"], entry["index"]) for entry in removed["gallery"]] != [("Biden", 0)]:
            raise RuntimeError("Removing an identity did not reindex the CPU gallery")
        infer("biden", "Biden")
        job("clear_gallery")
        job("enroll", source="obama", name="Obama")
        saved = job("enroll", source="biden", name="Biden")["gallery"]
        job("reconnect")
        reconnected = wait(lambda status: status["connected"] and not status["models_ready"], timeout=60)
        if reconnected["gallery"] != saved:
            raise RuntimeError("Reconnect lost the persistent gallery")
        job("load_models")
        infer("obama2", "Obama")
    else:
        previous = json.loads((args.output / "exercise.json").read_text())
        saved = previous["final_status"]["gallery"]
        if initial["gallery"] != saved:
            raise RuntimeError("Restored console gallery differs from the saved identities")
        if args.stage == "persistence":
            if initial["models_ready"]:
                raise RuntimeError("Restarted console did not reset model readiness")
            job("load_models")
        elif not initial["models_ready"]:
            raise RuntimeError("Checkpoint restoration did not automatically load the FPGA models")
        infer("obama2", "Obama")
        infer("biden", "Biden")
    wait(lambda status: all(camera["stale_seconds"] is not None and camera["stale_seconds"] < 2
                            for camera in status["cameras"]))
    for source in ("camera0", "camera1"):
        infer(source)
    paused = job("preview", index=0)
    if paused["preview_enabled"]:
        raise RuntimeError("Preview pause did not take effect")
    job("reconnect")
    reconnected = wait(lambda status: status["connected"] and not status["models_ready"], timeout=60)
    if reconnected["preview_enabled"]:
        raise RuntimeError("Reconnect did not preserve the paused preview")
    paused_counts = [camera["frames"] for camera in reconnected["cameras"]]
    time.sleep(1.5)
    if [camera["frames"] for camera in request("/api/status")["cameras"]] != paused_counts:
        raise RuntimeError("Hardware preview resumed while the console was paused")
    job("load_models")
    paused = request("/api/status")
    if paused["preview_enabled"]:
        raise RuntimeError("Model loading resumed the paused preview")
    counts = [camera["frames"] for camera in paused["cameras"]]
    job("preview", index=1)
    final = wait(lambda status: all(camera["frames"] > counts[index] + 3
                                   for index, camera in enumerate(status["cameras"])))
    if (final["crc_errors"] or final["framing_errors"] or final["decode_errors"] or final["error"]
            or final["connection_retries"]
            or not all(camera["sensor"]["verified"] for camera in final["cameras"])):
        raise RuntimeError("Camera/UART errors remained after console workflows")
    report = {"measured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "stage": args.stage, "execution": "Real FPGA CNN and RISC-V gallery decisions through the local HTTP console",
              "source_hashes": {**transport_hashes, **{name: hashlib.sha256((ROOT / "vision" / name).read_bytes()).hexdigest()
                                for name in ("hardware_transport.py", "hardware_face_demo.py", "demo_server.py",
                                             "verify_console_hardware.py", "serial_bridge.py", "windows_serial_pipeline.py", "camera_stream.py")}},
              "project_evidence": project, "initial_status": initial,
              "tasks": records, "final_status": final, "passed": True}
    (args.output / f"{args.stage}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")
    print(f"PASS console real hardware {args.stage}", flush=True)


if __name__ == "__main__":
    main()
