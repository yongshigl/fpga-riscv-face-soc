#!/usr/bin/env python3
"""Verify the local console with browser interactions and real camera pixels."""
import argparse
import datetime
import io
import json
from pathlib import Path
import urllib.request

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8086")
    parser.add_argument("--output", type=Path, default=ROOT / "build/vision/console_validation")
    parser.add_argument("--expect-cameras", action="store_true")
    browser_options = parser.add_mutually_exclusive_group()
    browser_options.add_argument("--browser-executable", type=Path)
    browser_options.add_argument("--browser-cdp-url")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(args.url + "/api/status", timeout=10) as response:
        status = json.load(response)
    with urllib.request.urlopen(args.url + "/api/report", timeout=10) as response:
        exported = json.load(response)
        assert 'attachment;' in response.headers["Content-Disposition"]
    assert exported["mode"] == status["mode"] and exported["board"] == status["board"]
    assert exported["exported_utc"] and len(exported["metrics"]["cpu"]) >= 3
    assert exported["metrics"]["host_accuracy"]["accuracy"] == 0.981
    board_accuracy = exported["metrics"].get("board_accuracy")
    if board_accuracy:
        assert board_accuracy["pairs"] == 1000 and board_accuracy["unique_images"] == 1549
        hardware = exported["metrics"]["hardware"]
        assert board_accuracy["matches_hardware"] == bool(hardware and
            board_accuracy["bitstream_sha256"].lower() == hardware["project_evidence"]["bitstream_sha256"].lower())
    frames = []
    if args.expect_cameras:
        assert status["connected"] and all(camera["frames"] > 2 for camera in status["cameras"])
        assert not status["crc_errors"] and not status["decode_errors"]
        for camera in range(2):
            with urllib.request.urlopen(args.url + f"/image/camera{camera}", timeout=10) as response:
                encoded = response.read()
            (args.output / f"camera{camera}.jpg").write_bytes(encoded)
            decoded = Image.open(io.BytesIO(encoded)).convert("RGB")
            image = np.asarray(decoded)
            assert image.shape == (360, 640, 3)
            gray = np.asarray(decoded.convert("L"), dtype=np.float64)
            assert gray.std() > 5 and gray.max() - gray.min() > 40, "Camera image is blank"
            laplacian = (gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2]
                         + gray[1:-1, 2:] - 4 * gray[1:-1, 1:-1])
            frames.append({"camera": camera, "width": image.shape[1], "height": image.shape[0],
                           "luma_mean": float(gray.mean()), "luma_stddev": float(gray.std()),
                           "laplacian_variance": float(laplacian.var())})
    layouts, errors = [], []
    with sync_playwright() as playwright:
        if args.browser_cdp_url:
            browser = playwright.chromium.connect_over_cdp(args.browser_cdp_url)
        else:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=str(args.browser_executable) if args.browser_executable else None)
        for name, width, height in [("wide", 1440, 900), ("desktop", 1366, 768),
                                    ("mobile", 390, 844), ("small_mobile", 360, 640)]:
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(args.url, wait_until="domcontentloaded")
            page.wait_for_function("document.querySelector('#cpu-results').children.length >= 3")
            if args.expect_cameras:
                page.wait_for_function("[0,1].every(i=>document.querySelector('#camera'+i).naturalWidth===640)", timeout=20000)
                page.wait_for_function("document.querySelector('#input-preview').naturalWidth===640", timeout=20000)
                assert page.locator("#input-preview").is_visible()
            assert page.locator("#host-accuracy").inner_text() == "98.1%"
            if status["mode"] == "camera":
                assert page.locator("#infer").is_disabled() and page.locator("#load-models").is_disabled()
                assert "OV5640" in page.locator("#hardware-kind").inner_text()
            if not status["connected"]:
                for control in ("infer", "enroll", "load-models", "preview-toggle"):
                    assert page.locator(f"#{control}").is_disabled()
                for camera in range(2):
                    assert page.locator(f"#camera{camera}").is_hidden()
                    assert page.locator(f"#empty{camera}").is_visible()
                    assert page.locator(f"#camera{camera}").get_attribute("src") is None
                assert page.locator("#input-preview").get_attribute("src") is None
                assert page.evaluate("""status => {
                    renderStatus({...status, cameras: status.cameras.map(camera => ({...camera, frames: 23}))});
                    return [0, 1].every(camera => document.querySelector('#camera' + camera).hidden);
                }""", status), "Cumulative frame counts exposed a stale disconnected image"
            layout = page.evaluate("""() => ({
                documentWidth: document.documentElement.scrollWidth,
                viewportWidth: innerWidth,
                overflowingControls: [...document.querySelectorAll('button,select,input')].filter(
                    node => node.offsetWidth && node.scrollWidth > node.clientWidth + 1).map(node => node.id),
                inputFrame: (() => {
                    const image = document.querySelector('#input-preview');
                    const node = image.hidden ? document.querySelector('#input-empty') : image;
                    const rect = node.getBoundingClientRect();
                    return {width: rect.width, height: rect.height};
                })(),
                cameras: [0,1].map(i => {const node = document.querySelector('#camera'+i);
                    const rect = node.getBoundingClientRect();
                    return {width: rect.width, height: rect.height, naturalWidth: node.naturalWidth};})
            })""")
            assert layout["documentWidth"] <= width, f"Page overflow at {name}: {layout}"
            assert not layout["overflowingControls"], f"Text overflow at {name}: {layout}"
            page.screenshot(path=str(args.output / f"{name}_preview.png"), full_page=True)
            page.locator('[data-view="metrics"]').click()
            assert page.locator("#view-metrics").is_visible()
            cpu_groups = [[row for row in exported["metrics"]["cpu"]
                           if bool(row["icache_bytes"] and row["dcache_bytes"]) == cached]
                          for cached in (True, False)]
            expected_best = [max(group, key=lambda row: row["coremark_per_lut_post_route"])["project"]
                             for group in cpu_groups if group]
            highlighted = page.locator("#cpu-results tr.best").all_text_contents()
            assert len(highlighted) == len(expected_best)
            for project in expected_best:
                label = project.removeprefix("riscv_").replace("_", " ")
                assert any(label in row for row in highlighted)
            if cpu_groups[0]:
                first_configuration = page.locator("#cpu-results tr").first.locator("td").first.inner_text()
                assert "KiB / D" in first_configuration
            assert page.locator("#cpu-results tr").count() == len(exported["metrics"]["cpu"])
            if board_accuracy:
                assert page.locator("#board-accuracy").inner_text() == f"{board_accuracy['accuracy'] * 100:.1f}%"
                assert "1000 pairs" in page.locator("#board-accuracy-scope").inner_text()
                assert ("\u5386\u53f2" in page.locator('[data-text="boardAccuracy"]').inner_text()) == (not board_accuracy["matches_hardware"])
            page.screenshot(path=str(args.output / f"{name}_metrics.png"), full_page=True)
            page.locator('[data-view="recognition"]').click()
            assert page.locator("#view-recognition").is_visible()
            page.locator('[data-view="preview"]').click()
            page.locator("#source").select_option("obama2")
            page.wait_for_function("document.querySelector('#input-preview').naturalWidth>0")
            assert page.locator("#input-preview").evaluate("node => ({width: node.getBoundingClientRect().width, height: node.getBoundingClientRect().height})") == layout["inputFrame"], "Selecting an image changed the preview frame dimensions"
            page.locator("#image-file").set_input_files(ROOT / "vision/test_images/biden.jpg")
            assert page.locator("#source").input_value() == "uploaded"
            page.wait_for_function("document.querySelector('#input-preview').naturalWidth>0")
            page.locator("#reconnect").hover()
            assert page.locator("#tooltip").is_visible()
            layouts.append({"name": name, "width": width, "height": height, **layout})
            page.close()
        browser.close()
    assert not errors, errors
    report = {"measured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "passed": True,
              "scope": "Actual browser rendering and interactions; live camera pixels where requested; no simulated FPGA inference",
              "layouts": layouts, "camera_pixels": frames, "status": status, "javascript_errors": errors,
              "report_export_verified": True, "board_accuracy_display_verified": bool(board_accuracy)}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")
    camera_check = "real nonblank cameras" if args.expect_cameras else "camera connection states"
    print(f"PASS console: four desktop/mobile layouts, {camera_check}, tabs, sources, upload, tooltips, "
          "measured metrics and unavailable-hardware controls")


if __name__ == "__main__":
    main()
