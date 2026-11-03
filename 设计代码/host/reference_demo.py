#!/usr/bin/env python3
"""Run reference face fixtures and write images, timing, and gallery vectors."""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from face_pipeline import ROOT, FacePipeline, annotate, read_image, sha256, write_image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT.parent / "build/vision/reference")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    pipeline = FacePipeline()
    images = {name: read_image(ROOT / f"test_images/{filename}") for name, filename in [
        ("Astronaut", "astronaut.png"), ("Obama", "obama.jpg"), ("Biden", "biden.jpg"),
    ]}
    for name, image in images.items():
        pipeline.enroll(name, image)
    pipeline.save_gallery(args.output / "gallery.json")

    fixtures = [(name.lower(), image, [name]) for name, image in images.items()]
    fixtures.append(("obama_second", read_image(ROOT / "test_images/obama2.jpg"), ["Obama"]))
    darker = np.clip(images["Astronaut"].astype(np.float32) * 0.72 + 12, 0, 255).astype(np.uint8)
    fixtures.append(("astronaut_illumination", darker, ["Astronaut"]))
    fixtures.append(("blank", np.zeros((180, 320, 3), dtype=np.uint8), []))
    room = ROOT.parent / "build/camera/rgb-v3-20261001/cam0.png"
    if room.exists():
        fixtures.append(("real_camera_room", read_image(room), []))

    crops = []
    for name in ["Obama", "Biden"]:
        image = images[name]
        face = pipeline.detect(image)[0]
        x, y, width, height = face[:4]
        x0 = max(0, round(x - width * 0.4))
        x1 = min(image.shape[1], round(x + width * 1.4))
        y0 = max(0, round(y - height * 0.25))
        y1 = min(image.shape[0], round(y + height * 1.25))
        crops.append(letterbox_crop(image[y0:y1, x0:x1]))
    fixtures.append(("two_faces", np.concatenate(crops, axis=1), ["Biden", "Obama"]))

    records = []
    thumbnails = []
    for name, image, expected in fixtures:
        result = pipeline.run(image)
        result["fixture"] = name
        identities = sorted(face["identity"] for face in result["faces"])
        result["expected_identities"] = sorted(expected)
        result["fixture_pass"] = identities == sorted(expected)
        write_image(args.output / f"{name}_input.png", image)
        rendered = annotate(image, result, f"{name} / Host Reference")
        write_image(args.output / f"{name}.png", rendered)
        thumbnails.append(cv2.resize(rendered, (480, 323), interpolation=cv2.INTER_AREA))
        (args.output / f"{name}.json").write_text(json.dumps(result, indent=2) + "\n")
        records.append(result)
        print(f"FIXTURE {name}: {identities} expected={sorted(expected)} "
              f"pass={result['fixture_pass']} {result['total_ms']:.2f} ms", flush=True)

    while len(thumbnails) % 2:
        thumbnails.append(np.full_like(thumbnails[0], 247))
    montage = np.concatenate([np.concatenate(thumbnails[i:i + 2], axis=1)
                              for i in range(0, len(thumbnails), 2)], axis=0)
    write_image(args.output / "reference_montage.png", montage)
    summary = {
        "execution": "host CPU reference", "opencv": cv2.__version__,
        "detector_sha256": sha256(pipeline.detector_path),
        "embedding_sha256": sha256(pipeline.embedding_path),
        "fixtures_passed": sum(result["fixture_pass"] for result in records),
        "fixtures_total": len(records), "results": records,
        "scope": "Functional fixtures only; not an accuracy benchmark or a calibrated biometric threshold.",
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    if summary["fixtures_passed"] != summary["fixtures_total"]:
        raise SystemExit("Reference fixtures did not all pass; inspect the saved results")
    print("PASS reference face fixtures", flush=True)


def letterbox_crop(image):
    from face_pipeline import letterbox
    return letterbox(image, (160, 192))[0]


if __name__ == "__main__":
    main()
