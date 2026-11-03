#!/usr/bin/env python3
"""Verify real continuous inference through the existing single-owner HTTP console."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

from board_evidence import validate_project
from transport_evidence import snapshot_transport
from console_integrity import (INTEGRITY_SCOPE, begin_observation, check_connection,
                               check_reconnect_transition, counter_snapshot)

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8086")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", choices=("camera0", "camera1"), default="camera0")
    parser.add_argument("--frames", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--min-seconds", type=float, default=0)
    parser.add_argument("--check-reconnect-restore", action="store_true",
                        help="Require --restore-models-on-reconnect and verify paused reconnect, full model/gallery readiness and resume")
    args = parser.parse_args(argv)
    if args.frames < 2 or args.timeout <= 0 or not 0 <= args.min_seconds < args.timeout:
        parser.error("Require at least two different camera frames and a positive phase timeout")
    if (args.output / "report.json").exists():
        parser.error("Use a new output directory to preserve an earlier result")
    args.output.mkdir(parents=True, exist_ok=True)
    transport_hashes = snapshot_transport(args.output)
    report = {
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "passed": False,
        "execution": "Real FPGA/RISC-V inference observed and controlled through the existing HTTP console; no direct serial access",
        "source": args.source, "requested_frames": args.frames, "check_reconnect_restore": args.check_reconnect_restore,
        "minimum_continuous_seconds": args.min_seconds, "compact_observations": bool(args.min_seconds),
        "source_sha256": {**transport_hashes, **{name: hashlib.sha256((ROOT / "vision" / name).read_bytes()).hexdigest()
                          for name in ("demo_server.py", "hardware_face_demo.py", "hardware_transport.py",
                                       "camera_stream.py", "serial_bridge.py", "windows_serial_pipeline.py", "console_integrity.py", Path(__file__).name)}},
        "integrity_scope": INTEGRITY_SCOPE,
        "phases": [], "error": None,
    }
    observed = []
    initial = None
    owns_control = False
    preview_changed = False
    next_progress = [time.monotonic()]
    baseline = [None]
    reconnect_baseline = [None]
    last_status = [None]

    def request(path, data=None):
        encoded = None if data is None else json.dumps(data).encode()
        headers = {} if data is None else {"Content-Type": "application/json"}
        operation = urllib.request.Request(args.url + path, data=encoded, headers=headers)
        try:
            with urllib.request.urlopen(operation, timeout=10) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {error.code}: {detail}") from error

    def status():
        value = request("/api/status")
        last_status[0] = value
        if time.monotonic() >= next_progress[0]:
            print(f"CONTINUOUS_VERIFY connected={value['connected']} models={value['models_ready']} "
                  f"enabled={value['continuous']['enabled']} active={value['active_job']} pending={value['pending_jobs']}", flush=True)
            next_progress[0] = time.monotonic() + 30
        observation = value if not args.min_seconds else {
            **counter_snapshot(value), **{key: value[key] for key in
                ("connected", "models_ready", "active_job", "pending_jobs", "continuous", "error")},
            "cameras": [{key: camera.get(key) for key in ("camera", "frames", "fps", "stale_seconds")}
                        for camera in value["cameras"]]}
        observed.append({"monotonic": time.monotonic(), "status": observation})
        if baseline[0] is not None:
            if reconnect_baseline[0] is not None:
                check_reconnect_transition(value, reconnect_baseline[0])
            else:
                check_connection(value, baseline[0])
        return value

    def wait(predicate, label, require_connected=True):
        deadline = time.monotonic() + args.timeout
        while True:
            value = status()
            if require_connected and (not value["connected"] or value["error"]):
                raise RuntimeError(f"{label}: board failed or disconnected: {value['error']}")
            if predicate(value):
                return value
            if time.monotonic() >= deadline:
                raise TimeoutError(label)
            time.sleep(0.1)

    def task(action, **extra):
        identifier = request("/api/jobs", {"action": action, **extra})["id"]

        def complete(value):
            job = next((item for item in value["jobs"] if item["id"] == identifier), None)
            if job and job["status"] == "failed":
                raise RuntimeError(f"{action}: {job['error']}")
            return job is not None and job["status"] == "done" and value["active_job"] != identifier

        return identifier, wait(complete, f"Task {action} did not finish", require_connected=action != "reconnect")

    def integrity(value, baseline):
        check_connection(value, baseline)
        if not 0 <= value["pending_jobs"] <= 4:
            raise RuntimeError("Console task queue exceeded its bound")

    def auto_jobs(value, after):
        return [job for job in value["jobs"] if job.get("trigger") == "continuous"
                and job["submitted_monotonic"] >= after]

    try:
        initial = wait(lambda value: value["models_ready"] and value["preview_enabled"] and not value["active_job"]
                       and not value["pending_jobs"], "Initial console readiness")
        if initial["mode"] != "vision" or not initial["capabilities"]["camera_preview"]:
            raise RuntimeError("Continuous verification requires the combined camera/AI system")
        if initial["continuous"]["enabled"]:
            raise RuntimeError("Stop an existing continuous session before starting isolated verification")
        if args.check_reconnect_restore and not initial["model_restore"]["enabled"]:
            raise RuntimeError("Restart the console with --restore-models-on-reconnect before this optional check")
        if args.check_reconnect_restore and not initial["model_restore"]["armed"]:
            raise RuntimeError("Complete one verified model load before checking automatic restoration")
        report["project_evidence"] = validate_project(args.project, initial["board"])
        report["initial_status"] = initial
        baseline[0] = begin_observation(initial)
        report["initial_integrity"] = dict(baseline[0])
        gallery_before = initial["gallery"]
        started = time.monotonic()
        owns_control = True
        request("/api/continuous", {"enabled": True, "source": args.source})
        completed = {}

        def enough_frames(value):
            integrity(value, initial)
            if not value["continuous"]["enabled"] or value["pending_jobs"]:
                raise RuntimeError("Automatic recognition stopped or accumulated queued work")
            for job in auto_jobs(value, started):
                if job["status"] == "failed":
                    raise RuntimeError(job["error"])
                if job["status"] == "done":
                    if job.get("source") != args.source or job.get("camera") != int(args.source[-1]):
                        raise RuntimeError("Automatic inference used a different camera")
                    if not isinstance(job.get("camera_sequence"), int):
                        raise RuntimeError("Camera input sequence evidence is absent")
                    completed[job["id"]] = job
            return len(completed) >= args.frames and time.monotonic() - started >= args.min_seconds

        wait(enough_frames, "Continuous camera frame target")
        ordered = sorted(completed.values(), key=lambda item: item["submitted_monotonic"])
        sequences = [job["camera_sequence"] for job in ordered]
        if any(left == right for left, right in zip(sequences, sequences[1:])):
            raise RuntimeError("Consecutive automatic jobs reused one camera frame")
        latest = last_status[0]
        preview_received = [camera["frames"] - initial["cameras"][index]["frames"]
                            for index, camera in enumerate(latest["cameras"])]
        if args.min_seconds and min(preview_received) < len(ordered) - 1:
            raise RuntimeError("Both camera previews must advance between continuous inference jobs")
        report["phases"].append({"phase": "continuous_frames", "jobs": ordered, "sequences": sequences,
                                 "elapsed_seconds": time.monotonic() - started,
                                 "preview_frames_received": preview_received})

        identifier, after_manual = task("infer", source="obama2")
        manual = next(job for job in after_manual["jobs"] if job["id"] == identifier)
        integrity(after_manual, initial)
        if after_manual["continuous"]["enabled"] or after_manual["result"]["input_source"] != "obama2":
            raise RuntimeError("Manual inference did not stop automatic recognition and receive its own input")
        if any(job["submitted_monotonic"] > manual["submitted_monotonic"] for job in auto_jobs(after_manual, started)):
            raise RuntimeError("An automatic task was scheduled after the manual task requested priority")
        report["phases"].append({"phase": "manual_priority", "job": manual, "stop_reason": after_manual["continuous"]["reason"]})

        request("/api/continuous", {"enabled": True, "source": args.source})
        wait(lambda value: bool(value["continuous"]["active_job"]), "Automatic task did not start")
        request("/api/continuous", {"enabled": False})
        stopped = wait(lambda value: not value["continuous"]["enabled"] and not value["continuous"]["active_job"],
                       "Automatic task did not finish after stopping")
        last_ids = {job["id"] for job in stopped["jobs"] if job.get("trigger") == "continuous"}
        until = time.monotonic() + 1.5
        while time.monotonic() < until:
            value = status()
            integrity(value, initial)
            if value["continuous"]["enabled"] or any(job["id"] not in last_ids for job in value["jobs"] if job.get("trigger") == "continuous"):
                raise RuntimeError("New automatic recognition was scheduled after stopping")
            time.sleep(0.1)
        report["phases"].append({"phase": "explicit_stop", "stop_reason": stopped["continuous"]["reason"],
                                 "quiet_window_seconds": 1.5})

        if args.check_reconnect_restore:
            preview_changed = True
            _, paused = task("preview", index=0)
            if paused["preview_enabled"]:
                raise RuntimeError("Requested preview pause did not take effect")
            generation = paused["connection_generation"]
            reconnect_baseline[0] = dict(baseline[0])
            report["requested_reconnect"] = {"before": dict(baseline[0]), "expected_generation": generation + 1}
            request("/api/jobs", {"action": "reconnect"})
            saw_loading = [False]

            def restored(value):
                if check_reconnect_transition(value, reconnect_baseline[0]):
                    if value["preview_enabled"]:
                        raise RuntimeError("Reconnection resumed a user-paused preview")
                    saw_loading[0] |= value["model_restore"]["state"] == "loading"
                    if value["model_restore"]["state"] == "failed":
                        raise RuntimeError(f"Model restoration failed: {value['model_restore']['error']}")
                    return value["models_ready"] and value["model_restore"]["state"] == "ready"
                return False

            recovered = wait(restored, "Full model/gallery restoration after reconnect", require_connected=False)
            baseline[0] = begin_observation(recovered)
            reconnect_baseline[0] = None
            report["requested_reconnect"]["restored_integrity"] = dict(baseline[0])
            if not saw_loading[0] or recovered["gallery"] != gallery_before:
                raise RuntimeError("Model-loading evidence or persisted gallery differs after reconnection")
            restore_jobs = [job for job in recovered["jobs"] if job.get("trigger") == "reconnect"
                            and job["connection_generation"] == recovered["connection_generation"]]
            if len(restore_jobs) != 1 or restore_jobs[0]["status"] != "done":
                raise RuntimeError("Completed restoration task evidence is absent")
            _, resumed = task("preview", index=1)
            preview_changed = False
            counts = [camera["frames"] for camera in resumed["cameras"]]
            recovered = wait(lambda value: all(camera["frames"] >= counts[index] + 2
                                              and camera["sensor"] and camera["sensor"]["verified"]
                                              for index, camera in enumerate(value["cameras"])),
                             "Verified dual-camera preview after restoration")
            report["phases"].append({"phase": "paused_reconnect_model_gallery_restore", "restore_job": restore_jobs[0],
                                     "loading_observed": saw_loading[0], "gallery": recovered["gallery"],
                                     "expected_generation": generation + 1, "restored_integrity": dict(baseline[0]),
                                     "final_camera_counts": [camera["frames"] for camera in recovered["cameras"]]})
        report["final_status"] = status()
        report["final_integrity"] = counter_snapshot(report["final_status"])
        report["passed"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        if owns_control:
            try:
                request("/api/continuous", {"enabled": False})
                if preview_changed:
                    request("/api/jobs", {"action": "preview", "index": 1})
            except Exception as error:
                report["cleanup_error"] = str(error)
                report["passed"] = False
                report["error"] = report["error"] or f"Cleanup failed: {error}"
        report["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        report["observed_statuses"] = observed
        report["last_observed_status"] = last_status[0]
        if observed:
            report["last_observed_integrity"] = counter_snapshot(observed[-1]["status"])
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if not report["passed"]:
        raise RuntimeError(report["error"])
    print("PASS real continuous recognition: new frames, bounded queue, manual priority, stop" +
          (", paused model/gallery recovery" if args.check_reconnect_restore else ""), flush=True)


if __name__ == "__main__":
    main()
