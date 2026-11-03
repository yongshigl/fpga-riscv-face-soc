#!/usr/bin/env python3
"""Inspect real console assets using an offline server and explicit UI state fixtures."""
import argparse
import copy
import datetime
import hashlib
import json
from pathlib import Path
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8097")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(args.url + "/api/status", timeout=5) as response:
        offline = json.load(response)
    if offline["connected"] or offline["mode"] != "offline":
        raise RuntimeError("Use an isolated offline server for UI fixtures")
    errors, layouts = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for name, width, height in (("wide", 1440, 900), ("desktop", 1366, 768),
                                    ("mobile", 390, 844), ("small_mobile", 360, 640)):
            page = browser.new_page(viewport={"width": width, "height": height})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/metrics", lambda route: route.fulfill(json={
                "cpu": [], "hardware": None, "host_accuracy": None, "board_accuracy": None}))
            page.goto(args.url, wait_until="domcontentloaded")
            page.wait_for_function("state.status?.mode === 'offline'")
            assert page.locator("#continuous-toggle").is_disabled()
            assert page.locator("#continuous-toggle").get_attribute("aria-pressed") == "false"
            assert page.locator("#serial-port").inner_text() == offline["port"]
            assert page.locator("#serial-baud").inner_text() == str(offline["baud"])
            layout = page.evaluate("""() => ({documentWidth: document.documentElement.scrollWidth,
                viewportWidth: innerWidth, overflowingControls: [...document.querySelectorAll('button,select,input')]
                .filter(node => node.offsetWidth && node.scrollWidth > node.clientWidth + 1).map(node => node.id)})""")
            assert layout["documentWidth"] <= width, (name, layout)
            assert not layout["overflowingControls"], (name, layout)
            page.screenshot(path=str(args.output / f"{name}_offline.png"), full_page=True)
            layouts.append({"name": name, "width": width, "height": height, **layout})
            page.close()

        page = browser.new_page(viewport={"width": 1366, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        fixture = copy.deepcopy(offline)
        fixture.update(connected=True, mode="vision", models_ready=True, preview_enabled=True,
                       board={"firmware_version": 0x26100133, "cpu_hz": 81000000, "ai_hz": 63000000},
                       capabilities={"camera_preview": True, "compressed_write": True})
        requests, manual_requests, held = [], [], []
        hold_start = [False]

        def continuous(route):
            data = route.request.post_data_json
            requests.append(data)
            if data["enabled"]:
                fixture["continuous"].update(enabled=True, source=data["source"], reason=None)
                if hold_start[0]:
                    held.append(route)
                    return
            else:
                fixture["continuous"].update(enabled=False, reason=(
                    "Image source changed; continuous recognition stopped" if data.get("reason") == "source_changed"
                    else "Continuous recognition stopped"))
            route.fulfill(json=fixture["continuous"])

        def manual(route):
            data = route.request.post_data_json
            manual_requests.append(data)
            fixture["continuous"].update(enabled=False, reason="Manual board task requested; continuous recognition stopped")
            route.fulfill(status=202, json={"id": "manual-fixture"})

        page.route("**/api/status", lambda route: route.fulfill(json=fixture))
        page.route("**/api/metrics", lambda route: route.fulfill(json={
            "cpu": [], "hardware": None, "host_accuracy": None, "board_accuracy": None}))
        page.route("**/api/continuous", continuous)
        page.route("**/api/jobs", manual)
        page.route("**/image/result*", lambda route: route.fulfill(
            content_type="image/jpeg", body=(ROOT / "vision/test_images/obama.jpg").read_bytes()))
        page.goto(args.url, wait_until="domcontentloaded")
        page.wait_for_function("state.status?.models_ready")
        assert page.locator("#continuous-toggle").is_enabled()
        page.locator("#continuous-toggle").click()
        page.wait_for_function("state.status?.continuous.enabled && !state.continuousBusy")
        assert page.locator("#continuous-label").inner_text() == "停止连续识别"
        page.wait_for_timeout(1600)
        assert not manual_requests, "Browser polling must not enqueue automatic recognition"
        page.locator("#continuous-toggle").click()
        page.wait_for_function("!state.status.continuous.enabled && !state.continuousBusy")
        assert page.locator("#continuous-label").inner_text() == "开始连续识别"
        page.locator("#continuous-toggle").click()
        page.wait_for_function("state.status.continuous.enabled && !state.continuousBusy")

        fixture["jobs"] = [{"id": "automatic-fixture", "action": "infer", "status": "running",
                            "progress": 0, "automatic": True, "trigger": "continuous"}]
        fixture["active_job"] = fixture["continuous"]["active_job"] = "automatic-fixture"
        page.evaluate("renderStatus", fixture)
        assert page.locator("#enroll").is_enabled()
        assert page.locator("#load-models").is_enabled()
        assert page.locator("#preview-toggle").is_enabled()
        page.locator("#identity-name").fill("Fixture identity")
        page.locator("#enroll").click()
        page.wait_for_function("!state.status.continuous.enabled")
        assert manual_requests[-1]["action"] == "enroll"
        assert "已让位给手动操作" in page.locator("#continuous-state").inner_text()
        assert "当前任务结束后停止" in page.locator("#continuous-state").inner_text()

        fixture["jobs"] = []
        fixture["active_job"] = fixture["continuous"]["active_job"] = None
        page.evaluate("renderStatus", fixture)
        page.locator("#continuous-toggle").click()
        page.wait_for_function("state.status.continuous.enabled && !state.continuousBusy")
        page.locator("#source").select_option("obama")
        page.wait_for_function("!state.status.continuous.enabled && !state.continuousBusy")
        assert requests[-1]["reason"] == "source_changed"
        assert page.locator("#continuous-toggle").is_disabled()

        page.locator("#source").select_option("camera0")
        page.evaluate("renderStatus", fixture)
        hold_start[0] = True
        page.locator("#continuous-toggle").click()
        page.wait_for_function("state.continuousBusy")
        page.locator("#source").select_option("biden")
        page.wait_for_function("state.continuousStopReason === 'source_changed'")
        assert len(held) == 1
        hold_start[0] = False
        held.pop().fulfill(json=fixture["continuous"])
        page.wait_for_function("!state.continuousBusy && !state.status.continuous.enabled")
        assert requests[-1]["enabled"] is False and requests[-1]["reason"] == "source_changed"

        page.locator("#source").select_option("camera1")
        page.evaluate("renderStatus", fixture)
        page.locator("#continuous-toggle").click()
        page.wait_for_function("state.status.continuous.enabled && !state.continuousBusy")
        page.locator("#image-file").set_input_files(ROOT / "vision/test_images/biden.jpg")
        page.wait_for_function("!state.status.continuous.enabled && !state.continuousBusy")
        assert page.locator("#source").input_value() == "uploaded"
        assert requests[-1]["reason"] == "source_changed"

        result = {"measured_utc": "2026-10-13T00:00:00+00:00", "total_ms": 980,
                  "detection": {"accelerator_ms": 243, "upload_ms": 300, "download_ms": 200},
                  "faces": [], "match_threshold": 9612 / 32768, "queue_wait_ms": 12.3,
                  "camera_input": {"camera": 0, "camera_sequence": 27,
                                   "camera_estimated_age_at_selection_ms": 123.4}}
        fixture["result"] = result
        page.evaluate("renderStatus", fixture)
        assert page.locator("#queue-time").inner_text() == "12.3 ms"
        assert page.locator("#input-age").inner_text() == "123 ms"
        assert page.locator("#input-sequence").inner_text() == "CAM 0 / #27"
        page.evaluate("switchView('recognition')")
        page.screenshot(path=str(args.output / "desktop_timing_fixture.png"), full_page=True)

        fixture.update(connected=False, models_ready=False, board=None, active_job=None, jobs=[])
        fixture["continuous"].update(enabled=False, reason="Board disconnected; continuous recognition stopped")
        page.evaluate("renderStatus", fixture)
        assert page.locator("#continuous-toggle").is_disabled()
        assert "开发板已断开" in page.locator("#continuous-state").inner_text()
        fixture["continuous"]["reason"] = "Recognition failed: Model DDR readback differs"
        page.evaluate("renderStatus", fixture)
        assert "识别失败" in page.locator("#continuous-state").inner_text()

        fixture.update(connected=True, board={"firmware_version": 0x26100133, "cpu_hz": 81000000, "ai_hz": 63000000})
        fixture["model_restore"].update(enabled=True, state="loading", error=None)
        page.evaluate("renderStatus", fixture)
        assert page.locator("#execution-state").inner_text() == "正在恢复模型与人脸库"
        assert page.locator("#infer").is_disabled()
        fixture["model_restore"].update(state="failed", error="Model DDR readback differs")
        fixture["error"] = "Model DDR readback differs"
        page.evaluate("renderStatus", fixture)
        assert page.locator("#execution-state").inner_text() == "模型恢复失败"
        assert page.locator("#error").is_visible()
        page.close()
        browser.close()
    assert not errors, errors
    report = {
        "validated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "passed": True,
        "scope": "Real HTML/CSS/JavaScript in Chromium; isolated offline HTTP server and explicit simulated connected/recognition states; no camera or FPGA inference measurement",
        "hardware_tested": False, "layouts": layouts, "javascript_errors": errors,
        "checks": ["default disabled", "explicit start/stop", "one endpoint control without automatic browser job submissions",
                   "manual operations remain available during automatic inference", "source and upload switch stop",
                   "source changes during start handshake still stop", "queue/frame timing display", "disconnect/failure reasons",
                   "model restoration loading/failure display"],
        "continuous_requests": requests, "manual_requests": manual_requests,
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                          ("vision/demo_server.py", "vision/web/index.html", "vision/web/static/console.js",
                           "vision/verify_console_continuous_browser.py")},
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": True, "layouts": len(layouts), "javascript_errors": errors}))


if __name__ == "__main__":
    main()
