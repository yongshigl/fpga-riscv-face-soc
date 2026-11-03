#!/usr/bin/env python3
"""Start and stop only the recorded local FPGA console process."""
import argparse
import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def launch_environment():
    environment = os.environ.copy()
    # Detached servers outlive the temporary wsl.exe launcher and its interop channel.
    persistent_interop = Path("/run/WSL/1_interop")
    if environment.get("WSL_INTEROP") and persistent_interop.is_socket():
        environment["WSL_INTEROP"] = str(persistent_interop)
    return environment


def wait_ready(process, url, mode, timeout, allow_disconnected=False):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Console exited with {process.returncode}")
        try:
            with urllib.request.urlopen(url + "/api/status", timeout=1) as response:
                status = json.load(response)
        except (OSError, ValueError):
            time.sleep(0.25)
            continue
        if not allow_disconnected and (status.get("connection_retries") or status.get("error")):
            raise RuntimeError(f"Console startup failed: retries={status.get('connection_retries')} error={status.get('error')}")
        if allow_disconnected and not status.get("connected"):
            return status
        if mode == "offline" or status.get("connected"):
            if mode != "auto" and status.get("mode") != mode:
                raise RuntimeError(f"Console started in unexpected mode: {status.get('mode')}")
            return status
        time.sleep(0.25)
    raise TimeoutError("Console did not finish its HTTP and hardware startup")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=("start", "stop", "status"), default="status")
    parser.add_argument("--output", type=Path, default=Path("/mnt/d/FPGA_Work/runtime/pangu-vision"))
    parser.add_argument("--http-port", type=int, default=8086)
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--restore-models-on-reconnect", action="store_true")
    parser.add_argument("--mode", choices=("auto", "camera", "vision", "offline"), default="auto")
    parser.add_argument("--startup-timeout", type=float, default=120)
    parser.add_argument("--allow-disconnected", action="store_true",
                        help="Keep the HTTP console available while its worker reconnects to the board")
    parser.add_argument("--hardware-report", type=Path)
    args = parser.parse_args()
    if os.name == "nt":
        parser.error("Use this console manager from WSL")
    if args.startup_timeout <= 0:
        parser.error("Startup timeout must be positive")
    if args.baud <= 0:
        parser.error("Serial baud rate must be positive")
    args.output = args.output.resolve()
    record_path = args.output / "console_process.json"
    previous = json.loads(record_path.read_text()) if record_path.exists() else None

    def running():
        if not previous or not previous.get("pid"):
            return False
        command_path = Path(f"/proc/{previous['pid']}/cmdline")
        try:
            command = command_path.read_bytes().decode().rstrip("\0").split("\0")
        except (OSError, UnicodeError):
            return False
        return (command == previous["command"] and len(command) > 2
                and Path(command[2]).name == "demo_server.py" and str(args.output) in command)

    active = running()
    if args.action == "stop":
        if active:
            os.kill(previous["pid"], signal.SIGINT)
            deadline = time.monotonic() + 12
            while running() and time.monotonic() < deadline:
                time.sleep(0.1)
            if running():
                raise RuntimeError("The owned console did not close; inspect its log before programming")
            previous["stopped_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            record_path.write_text(json.dumps(previous, indent=2) + "\n", encoding="ascii")
        print(f"CONSOLE_STOPPED owned_process={active}", flush=True)
        return
    if args.action == "status" or active:
        print(json.dumps({"running": active, "process": previous}), flush=True)
        return
    args.output.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-u", str(ROOT / "vision/demo_server.py"), "--mode", args.mode,
               "--output", str(args.output), "--http-port", str(args.http_port),
               "--port", args.port, "--baud", str(args.baud)]
    if args.hardware_report:
        command.extend(["--hardware-report", str(args.hardware_report.resolve())])
    if args.restore_models_on_reconnect:
        command.append("--restore-models-on-reconnect")
    environment = launch_environment()
    with (args.output / "console.log").open("ab") as log:
        process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=environment)
    previous = {"pid": process.pid, "command": command, "url": f"http://localhost:{args.http_port}",
                "interop_socket": environment.get("WSL_INTEROP"),
                "bridge_environment": {name: environment[name] for name in (
                    "FPGA_SERIAL_STATUS_INTERVAL_MS", "FPGA_SERIAL_IO_PRIORITY", "FPGA_SERIAL_NATIVE_READ_BYTES",
                    "FPGA_SERIAL_TIMER_RESOLUTION_MS", "FPGA_WIRE_ERROR_DIRECTORY")
                                       if name in environment},
                "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    record_path.write_text(json.dumps(previous, indent=2) + "\n", encoding="ascii")
    try:
        # Keep the launching WSL entrypoint alive until its Windows serial child is ready.
        status = wait_ready(process, f"http://127.0.0.1:{args.http_port}", args.mode,
                            min(20, args.startup_timeout) if args.mode == "offline" else args.startup_timeout,
                            allow_disconnected=args.allow_disconnected)
    except Exception:
        if process.poll() is None:
            os.kill(process.pid, signal.SIGINT)
            process.wait(timeout=10)
        raise
    previous["ready_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    previous["hardware_connected"] = status["connected"]
    previous["bridge_startup_ms"] = status.get("bridge_startup_ms")
    record_path.write_text(json.dumps(previous, indent=2) + "\n", encoding="ascii")
    print(f"CONSOLE_STARTED pid={process.pid} hardware_connected={status['connected']} {previous['url']}", flush=True)


if __name__ == "__main__":
    main()
