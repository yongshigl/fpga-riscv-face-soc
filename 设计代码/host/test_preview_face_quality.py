#!/usr/bin/env python3
"""Functional face fixtures through the old and new preview sampling/codec paths."""
import argparse
import json
from pathlib import Path

import cv2
import imagecodecs
import numpy as np

from face_pipeline import ROOT, FacePipeline, annotate, letterbox, read_image, write_image


def preview(image, compressed):
    source, _ = letterbox(image, (1280, 720))
    codes = (source[:, :, ::-1] // np.array([8, 4, 8], dtype=np.uint8)).astype(np.uint16)
    if compressed:
        codes = ((codes.reshape(360, 2, 640, 2, 3).sum(axis=(1, 3)) + 2) // 4).astype(np.uint8)
        planes = np.concatenate([codes[:, :, index] for index in range(3)], axis=0)
        stream = imagecodecs.jpegls_encode(planes, level=1)
        decoded = imagecodecs.jpegls_decode(stream)
        difference = np.abs(decoded.astype(np.int16) - planes.astype(np.int16))
        assert difference.max() <= 1, "NEAR error bound exceeded"
        codes = np.stack(np.split(decoded, 3, axis=0), axis=2)
        metadata = {"width": 640, "height": 360, "encoded_bytes": len(stream),
                    "maximum_channel_code_error": int(difference.max())}
    else:
        codes = codes[::4, ::4]
        metadata = {"width": 320, "height": 180, "encoded_bytes": 115200}
    codes = np.minimum(codes, np.array([31, 63, 31], dtype=np.uint8)).astype(np.uint16)
    rgb = ((codes << np.array([3, 2, 3])) | (codes >> np.array([2, 4, 2]))).astype(np.uint8)
    return rgb[:, :, ::-1], metadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, default=ROOT.parent / "build/vision/reference")
    parser.add_argument("--output", type=Path, default=ROOT.parent / "build/vision/preview-quality")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    pipeline = FacePipeline()
    for name, filename in [("Astronaut", "astronaut.png"), ("Obama", "obama.jpg"), ("Biden", "biden.jpg")]:
        pipeline.enroll(name, read_image(ROOT / "test_images" / filename))
    fixtures = json.loads((args.reference / "summary.json").read_text())["results"]
    records, rows = [], []
    for fixture in fixtures:
        name = fixture["fixture"]
        source = read_image(args.reference / f"{name}_input.png")
        rendered = []
        for compressed in [False, True]:
            mode = "v4_jls" if compressed else "v3_raw"
            image, metadata = preview(source, compressed)
            result = pipeline.run(image)
            identities = sorted(face["identity"] for face in result["faces"])
            passed = identities == fixture["expected_identities"]
            record = {"fixture": name, "mode": mode, "expected": fixture["expected_identities"],
                      "identities": identities, "pass": passed, "preview": metadata, "result": result}
            records.append(record)
            write_image(args.output / f"{name}_{mode}_input.png", image)
            display = annotate(image, result, f"{name} / {mode} / Host Reference")
            write_image(args.output / f"{name}_{mode}.png", display)
            rendered.append(cv2.resize(display, (480, 323), interpolation=cv2.INTER_AREA))
            print(f"FIXTURE {name} {mode}: {identities} pass={passed}", flush=True)
        rows.append(np.concatenate(rendered, axis=1))
    write_image(args.output / "comparison.png", np.concatenate(rows, axis=0))
    summary = {"execution": "host CPU functional reference", "codec": "independent CharLS NEAR=1",
               "scope": "Eight functional fixtures and simulated camera sampling; no accuracy or real-camera latency claim.",
               "checks_passed": sum(record["pass"] for record in records), "checks_total": len(records),
               "results": records}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    if summary["checks_passed"] != summary["checks_total"]:
        raise SystemExit("Face preview fixtures failed; inspect the saved comparison")
    print("PASS face preview sampling and JPEG-LS functional fixtures", flush=True)


if __name__ == "__main__":
    main()
