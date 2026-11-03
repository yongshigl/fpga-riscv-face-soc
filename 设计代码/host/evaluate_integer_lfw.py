#!/usr/bin/env python3
"""Evaluate the exact FPGA arithmetic reference on every LFW DevTest portrait."""
import argparse
import datetime
import hashlib
import json
import time
from pathlib import Path

import numpy as np

from evaluate_lfw import choose_face, score_report
from face_pipeline import normalize, read_image
from fpga_model import Model, align_face, decode_yunet, detector_input, embedding_input
from quantize_face_models import pair_images

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=ROOT / "vision/datasets/lfw")
    parser.add_argument("--models", type=Path, default=ROOT / "build/vision/fpga")
    parser.add_argument("--threshold", type=Path, default=ROOT / "build/vision/threshold_training/threshold.json")
    parser.add_argument("--output", type=Path, default=ROOT / "build/vision/lfw_integer_devtest")
    parser.add_argument("--reuse", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    pairs = pair_images(args.dataset / "pairsDevTest.txt")
    if len(pairs) != 1000:
        raise ValueError("Expected all 1000 LFW DevTest pairs")
    names = sorted({name for first, second, _ in pairs for name in (first, second)})
    detection, embedding = Model(args.models, "yunet"), Model(args.models, "mobilefacenet")
    provenance = {model.name: model.manifest["image_sha256"] for model in (detection, embedding)}
    provenance["pairs_sha256"] = hashlib.sha256((args.dataset / "pairsDevTest.txt").read_bytes()).hexdigest()
    provenance["reference_sha256"] = hashlib.sha256((ROOT / "vision/fpga_model.py").read_bytes()).hexdigest()
    key = args.output / "cache_key.json"
    features_file = args.output / "features.npz"
    start = time.monotonic()
    if args.reuse and key.exists() and json.loads(key.read_text()) == provenance and features_file.exists():
        features = np.load(features_file)["features"]
        valid = np.load(features_file)["valid"]
        failures = json.loads((args.output / "failures.json").read_text())
    else:
        features = np.zeros((len(names), 128), dtype=np.float32)
        valid = np.zeros(len(names), dtype=bool)
        failures = []
        for index, name in enumerate(names):
            image = read_image(args.dataset / "lfw_funneled" / name)
            try:
                _, outputs, _ = detection.reference(detection.input_bytes(detector_input(image)))
                faces = decode_yunet(outputs, image.shape)
                face = choose_face(faces, image)
                aligned = align_face(image, face)
                _, outputs, _ = embedding.reference(embedding.input_bytes(embedding_input(aligned)))
                features[index] = normalize(next(iter(outputs.values())))
                valid[index] = True
            except ValueError as error:
                failures.append({"image": name, "error": str(error)})
            if (index + 1) % 25 == 0:
                print(f"Exact integer DevTest: {index+1}/{len(names)} images, {time.monotonic()-start:.1f} s", flush=True)
            if (index + 1) % 100 == 0:
                np.savez_compressed(args.output / "progress.npz", features=features, valid=valid, completed=index+1)
        np.savez_compressed(features_file, features=features, valid=valid)
        (args.output / "failures.json").write_text(json.dumps(failures, indent=2) + "\n", encoding="ascii")
        key.write_text(json.dumps(provenance, indent=2) + "\n", encoding="ascii")
    metrics = {"fixed_0_55": score_report(pairs, names, features, valid, 0.55)}
    training = None
    if args.threshold.exists():
        training = json.loads(args.threshold.read_text())
        if training["devtest_labels_used_for_selection"] or training["devtest_image_overlap"]:
            raise RuntimeError("Threshold was not independently trained")
        if any(training["model_hashes"][model.name] != model.manifest["model_sha256"] for model in (detection, embedding)):
            raise RuntimeError("Threshold belongs to other quantized models")
        metrics["devtrain_selected"] = score_report(pairs, names, features, valid, training["threshold"])
    report = {"evaluated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "execution": "Host exact FPGA integer arithmetic reference; not a board latency or dataset measurement",
              "dataset": "LFW DevTest; 500 same + 500 different pairs", "unique_images": len(names),
              "task": "Face verification", "provenance": provenance, "metrics": metrics,
              "threshold_training_file_sha256": hashlib.sha256(args.threshold.read_bytes()).hexdigest() if training else None,
              "image_order": names, "failures": failures, "wall_seconds": time.monotonic()-start}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")
    for name, metric in metrics.items():
        print(f"{name}: {metric['correct_pairs']}/1000, accuracy={metric['accuracy_all_pairs_with_failures_counted_wrong']:.4f}", flush=True)


if __name__ == "__main__":
    main()
