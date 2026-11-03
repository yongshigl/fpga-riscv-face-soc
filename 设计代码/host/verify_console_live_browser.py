#!/usr/bin/env python3
"""Validate the real hardware console in Chromium without response fixtures."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import time
import urllib.request

from playwright.sync_api import sync_playwright

from board_evidence import validate_project
from transport_evidence import snapshot_transport
from console_integrity import INTEGRITY_SCOPE, begin_observation, check_connection

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8086")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new browser evidence directory")
    args.output.mkdir(parents=True)
    sources = ("vision/demo_server.py", "vision/hardware_face_demo.py", "vision/hardware_transport.py",
               "vision/serial_bridge.py", "vision/windows_serial_pipeline.py", "vision/camera_stream.py",
               "vision/console_integrity.py", "vision/board_evidence.py", "vision/measurement_evidence.py",
               "vision/web/index.html", "vision/web/static/console.js",
               "vision/web/static/console.css", "vision/verify_console_live_browser.py")
    hashes = snapshot_transport(args.output)
    for name in sources:
        data = (ROOT / name).read_bytes()
        target = args.output / "driver_sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    report = {"started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "passed": False,
              "hardware_tested": True, "response_fixtures_used": False,
              "scope": "Chromium connected to the actual single-owner FPGA HTTP console; real MJPEG, UI controls and FPGA inference",
              "source_sha256": hashes, "integrity_scope": INTEGRITY_SCOPE,
              "layouts": [], "inference_checks": [], "continuous_checks": [],
              "observed_statuses": [], "screenshots": [], "javascript_errors": [], "error": None}
    baseline, page = None, None

    def request(path, data=None):
        encoded = None if data is None else json.dumps(data).encode()
        operation = urllib.request.Request(args.url + path, data=encoded,
            headers={} if data is None else {"Content-Type": "application/json"})
        with urllib.request.urlopen(operation, timeout=10) as response:
            return json.load(response)

    def status():
        value = request("/api/status")
        report["observed_statuses"].append({"monotonic": time.monotonic(), "status": value})
        if baseline is not None:
            check_connection(value, baseline)
        return value

    def screen(name):
        path = args.output / (name + ".png")
        page.screenshot(path=str(path), full_page=True)
        report["screenshots"].append({"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})

    def ready_camera_images():
        page.wait_for_function("[0,1].every(i => {const n=document.getElementById('camera'+i); "
                               "const camera=state.status?.cameras[i]; return n && !n.hidden && camera?.sensor?.verified "
                               "&& camera.width>0 && camera.height>0 && n.naturalWidth===camera.width "
                               "&& n.naturalHeight===camera.height})", timeout=30000)

    try:
        initial = status()
        if (not initial["connected"] or initial["mode"] != "vision" or not initial["models_ready"]
                or not initial["preview_enabled"] or initial["active_job"] or initial["pending_jobs"]
                or initial["continuous"]["enabled"] or initial["error"]):
            raise RuntimeError("The hardware browser requires an idle, loaded, real vision console")
        baseline = begin_observation(initial)
        report["initial_status"] = initial
        report["initial_integrity"] = dict(baseline)
        report["project_evidence"] = validate_project(args.project, initial["board"])
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for name, width, height in [("wide", 1440, 900), ("desktop", 1366, 768),
                                            ("mobile", 390, 844), ("small_mobile", 360, 640)]:
                    page = browser.new_page(viewport={"width": width, "height": height})
                    page.on("pageerror", lambda error: report["javascript_errors"].append(str(error)))
                    page.goto(args.url, wait_until="domcontentloaded")
                    page.wait_for_function("state.status?.connected && state.status?.models_ready")
                    ready_camera_images()
                    if (page.locator("#serial-port").inner_text() != initial["port"]
                            or page.locator("#serial-baud").inner_text() != str(initial["baud"])):
                        raise RuntimeError("The browser displays different hardware UART settings")
                    layout = page.evaluate("""() => ({documentWidth:document.documentElement.scrollWidth,
                        viewportWidth:innerWidth, overflowingControls:[...document.querySelectorAll('button,select,input')]
                        .filter(n=>n.offsetWidth && n.scrollWidth>n.clientWidth+1).map(n=>n.id),
                        cameraImages:[0,1].map(i=>{const n=document.getElementById('camera'+i);
                            return {camera:i,width:n.naturalWidth,height:n.naturalHeight}})})""")
                    if layout["documentWidth"] > width or layout["overflowingControls"]:
                        raise RuntimeError(f"Real browser layout overflows: {name}: {layout}")
                    report["layouts"].append({"name": name, "width": width, "height": height, **layout})
                    screen(name + "_real_preview")
                    status()
                    page.close()
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: report["javascript_errors"].append(str(error)))
                page.goto(args.url, wait_until="domcontentloaded")
                page.wait_for_function("state.status?.models_ready")
                for source, expected in [("obama2", "Obama"), ("biden", "Biden"), ("astronaut", "Unknown")]:
                    page.locator("#source").select_option(source)
                    with page.expect_response(lambda response: response.url.endswith("/api/jobs")
                                               and response.request.method == "POST") as submitted:
                        page.locator("#infer").click()
                    identifier = submitted.value.json()["id"]
                    page.wait_for_function("id => state.status?.jobs.some(j=>j.id===id && "
                                           "['done','failed'].includes(j.status)) && !state.status.active_job",
                                           arg=identifier, timeout=90000)
                    value = status()
                    job = next(j for j in value["jobs"] if j["id"] == identifier)
                    result = value["result"]
                    if (job["status"] != "done" or result["input_source"] != source
                            or result["board"] != initial["board"] or "FPGA" not in result["execution"]
                            or len(result["faces"]) != 1 or result["faces"][0]["identity"] != expected):
                        raise RuntimeError(f"Real UI inference failed: {source}: {job}")
                    page.wait_for_function("() => {const n=document.getElementById('result-image'); "
                                           "return !n.hidden && n.complete && n.naturalWidth>0}")
                    # The production UI is Chinese localized: the API keeps the
                    # stable English identity ``Unknown`` while the rendered
                    # label is ``未登记``.  Check the actual localized label so
                    # the browser gate validates what a user sees.
                    expected_label = "未登记" if expected == "Unknown" else expected
                    if expected_label not in page.locator("#faces").inner_text():
                        raise RuntimeError("The browser does not display the actual FPGA identity")
                    report["inference_checks"].append({"source": source, "expected_identity": expected,
                                                       "job": job, "result": result})
                    screen(source + "_real_recognition")
                    print(f"LIVE_BROWSER sample={source} identity={expected} total_ms={result['total_ms']:.2f}", flush=True)
                for source in ["camera0", "camera1"]:
                    page.locator("#source").select_option(source)
                    page.wait_for_function("!state.continuousBusy && !state.status.active_job")
                    started = time.monotonic()
                    page.locator("#continuous-toggle").click()
                    page.wait_for_function("state.status.continuous.enabled && !state.continuousBusy")
                    deadline = time.monotonic() + 90
                    jobs = []
                    while time.monotonic() < deadline:
                        value = status()
                        jobs = [job for job in value["jobs"] if job.get("trigger") == "continuous"
                                and job["source"] == source and job["submitted_monotonic"] >= started
                                and job["status"] == "done"]
                        if len(jobs) >= 2:
                            break
                        time.sleep(.25)
                    else:
                        raise TimeoutError("Real browser continuous inference did not complete")
                    if len({job["camera_sequence"] for job in jobs}) != len(jobs):
                        raise RuntimeError("Real browser automatic jobs reused a camera frame")
                    page.locator("#continuous-toggle").click()
                    page.wait_for_function("!state.status.continuous.enabled && !state.continuousBusy "
                                           "&& !state.status.active_job", timeout=90000)
                    final = status()
                    screen(source + "_real_continuous")
                    report["continuous_checks"].append({"source": source, "jobs": jobs,
                                                        "final_status": final})
                    print(f"LIVE_BROWSER source={source} automatic_frames={len(jobs)} start_stop=PASS", flush=True)
                page.locator('[data-view="preview"]').click()
                ready_camera_images()
                screen("final_real_dual_preview")
                report["final_status"] = status()
                if report["final_status"]["gallery"] != initial["gallery"] or report["javascript_errors"]:
                    raise RuntimeError("Gallery changed or JavaScript errors occurred during real UI verification")
                report["passed"] = True
            finally:
                browser.close()
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        try:
            request("/api/continuous", {"enabled": False})
        except Exception as error:
            report["cleanup_error"] = str(error)
            report["passed"] = False
        report["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if not report["passed"]:
        raise RuntimeError(report["error"])
    print("PASS actual hardware console in Chromium: dual MJPEG, public identities, both camera start/stop, four layouts", flush=True)


if __name__ == "__main__":
    main()
