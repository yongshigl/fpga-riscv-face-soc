#!/usr/bin/env python3
"""Verify detached hardware startup through the Windows PowerShell/WSL restore path."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vision"))
from board_evidence import validate_project


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "build/vision/console_lifecycle_validation")
    parser.add_argument("--http-port", type=int, default=8086)
    parser.add_argument("--cycles", type=int, default=3)
    args = parser.parse_args()
    if args.cycles < 3:
        parser.error("At least three independent starts are required")
    args.runtime = args.runtime.resolve()
    if args.runtime.exists():
        parser.error("Use a new isolated runtime directory")
    args.output.mkdir(parents=True, exist_ok=True)
    powershell = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    manager = ROOT / "tools/manage_console.py"
    report = {"measured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "execution": "Windows PowerShell -> wsl.exe -> console manager -> detached WSL server -> Windows serial bridge",
              "source_hashes": {relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
                                for relative in ("tools/manage_console.py", "tools/verify_console_lifecycle.py",
                                                 "board/pg2l200h_mini/pds/restore_final_checkpoint.ps1")},
              "results": [], "passed": False}

    def invoke(action, runtime):
        values = ["wsl.exe", "--exec", sys.executable, str(manager), "--action", action,
                  "--mode", "vision", "--output", str(runtime), "--http-port", str(args.http_port)]
        command = "& " + " ".join("'" + value.replace("'", "''") + "'" for value in values) + "; exit $LASTEXITCODE"
        return subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", command],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=140)

    def status():
        with urllib.request.urlopen(f"http://127.0.0.1:{args.http_port}/api/status", timeout=5) as response:
            return json.load(response)

    def clean(value):
        return (value["connected"] and value["mode"] == "vision" and not value["error"]
                and not any(value[name] for name in ("connection_retries", "crc_errors", "framing_errors", "decode_errors")))

    try:
        for cycle in range(args.cycles):
            runtime = args.runtime / f"cycle{cycle + 1}"
            row = {"cycle": cycle + 1, "runtime": str(runtime), "passed": False}
            report["results"].append(row)
            try:
                started = time.monotonic()
                launch = invoke("start", runtime)
                row.update(launch_exit_code=launch.returncode, launch_output=launch.stdout,
                           launch_seconds=time.monotonic() - started)
                if launch.returncode:
                    raise RuntimeError(f"Windows/WSL console start failed: {launch.stdout}")
                row["process"] = json.loads((runtime / "console_process.json").read_text())
                row["initial_status"] = initial = status()
                if not row["process"]["hardware_connected"] or not clean(initial):
                    raise RuntimeError("Manager returned before a clean hardware connection")
                evidence = validate_project(args.project, initial["board"])
                report["project_evidence"] = evidence
                counts = [camera["frames"] for camera in initial["cameras"]]
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    current = status()
                    row["final_status"] = current
                    if not clean(current):
                        raise RuntimeError("Detached console developed hardware errors after its launcher exited")
                    if all(camera["frames"] >= counts[index] + 8 and camera["sensor"]["verified"]
                           for index, camera in enumerate(current["cameras"])):
                        break
                    time.sleep(0.5)
                else:
                    raise TimeoutError("Detached console did not deliver verified images from both sensors")
                row["frames_received"] = [camera["frames"] - counts[index]
                                          for index, camera in enumerate(current["cameras"])]
                row["passed"] = True
                print(f"PASS Windows/WSL console startup cycle {cycle + 1}: bridge={current['bridge_startup_ms']:.1f} ms frames={row['frames_received']}",
                      flush=True)
            finally:
                stopped = invoke("stop", runtime)
                row.update(stop_exit_code=stopped.returncode, stop_output=stopped.stdout)
                if stopped.returncode:
                    raise RuntimeError(f"Owned console did not close: {stopped.stdout}")
        report["passed"] = True
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")


if __name__ == "__main__":
    main()
