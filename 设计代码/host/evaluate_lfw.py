#!/usr/bin/env python3
"""Evaluate fixed FP32/INT8 face pipelines on the independent LFW DevTest split."""
import argparse
import datetime
import hashlib
import json
import time
from pathlib import Path

import numpy as np

from face_pipeline import FacePipeline, normalize, read_image
from fpga_model import align_face, decode_yunet, detector_input, embedding_input
from quantize_face_models import pair_images, runtime

ROOT = Path(__file__).resolve().parents[1]


def score_report(pairs, names, embeddings, valid, threshold):
    lookup = {name: index for index, name in enumerate(names)}
    first = np.asarray([lookup[left] for left, _, _ in pairs])
    second = np.asarray([lookup[right] for _, right, _ in pairs])
    truth = np.asarray([same for _, _, same in pairs], dtype=bool)
    usable = valid[first] & valid[second]
    similarities = np.einsum("ij,ij->i", embeddings[first], embeddings[second])
    prediction = similarities >= threshold
    correct = usable & (prediction == truth)
    tp = int(np.count_nonzero(usable & truth & prediction))
    tn = int(np.count_nonzero(usable & ~truth & ~prediction))
    fp = int(np.count_nonzero(usable & ~truth & prediction))
    fn = int(np.count_nonzero(usable & truth & ~prediction))
    return {"pairs": len(pairs), "same_pairs": int(truth.sum()), "different_pairs": int((~truth).sum()),
            "valid_pairs": int(usable.sum()), "pairs_with_failed_face_processing": int((~usable).sum()),
            "threshold": threshold, "correct_pairs": int(correct.sum()),
            "accuracy_all_pairs_with_failures_counted_wrong": float(correct.mean()),
            "accuracy_valid_pairs": float(correct.sum() / usable.sum()) if usable.any() else None,
            "true_accepts": tp, "true_rejects": tn, "false_accepts_valid_pairs": fp, "false_rejects_valid_pairs": fn,
            "false_accept_rate_valid_pairs": fp / max(1, fp + tn),
            "false_reject_rate_valid_pairs": fn / max(1, fn + tp),
            "pair_cosines": similarities.tolist(), "pair_valid": usable.tolist()}


def choose_face(faces, image):
    if not len(faces):
        raise ValueError("No face detected")
    height, width = image.shape[:2]
    area = faces[:, 2] * faces[:, 3]
    distance = ((faces[:, 0] + faces[:, 2] / 2 - width / 2) / width) ** 2 + ((faces[:, 1] + faces[:, 3] / 2 - height / 2) / height) ** 2
    return faces[np.argmax(area / (1 + 4 * distance))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=ROOT / "vision/datasets/lfw")
    parser.add_argument("--models", type=Path, default=ROOT / "build/vision/int8")
    parser.add_argument("--output", type=Path, default=ROOT / "build/vision/lfw_devtest")
    parser.add_argument("--reuse", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    pairs_path = args.dataset / "pairsDevTest.txt"
    pairs = pair_images(pairs_path)
    if len(pairs) != 1000:
        raise ValueError("Expected all 1000 official LFW DevTest pairs")
    names = sorted({name for left, right, _ in pairs for name in (left, right)})
    quantization = json.loads((args.models / "quantization.json").read_text())
    calibrated = {f"{Path(record['image']).parent.name}/{Path(record['image']).name}" for record in quantization["calibration_images"]}
    if quantization["bootstrap"] or calibrated.intersection(names):
        raise RuntimeError("Evaluation images overlap model calibration")
    pipeline = FacePipeline()
    detection = runtime(args.models / "yunet_u8s8.onnx")
    embedding = runtime(args.models / "mobilefacenet_u8s8.onnx")
    provenance = {"pairs_sha256": hashlib.sha256(pairs_path.read_bytes()).hexdigest(),
                  "quantization_sha256": hashlib.sha256((args.models / "quantization.json").read_bytes()).hexdigest(),
                  "yunet_int8_sha256": hashlib.sha256((args.models / "yunet_u8s8.onnx").read_bytes()).hexdigest(),
                  "mobilefacenet_int8_sha256": hashlib.sha256((args.models / "mobilefacenet_u8s8.onnx").read_bytes()).hexdigest(),
                  "mobilefacenet_fp32_sha256": hashlib.sha256(pipeline.embedding_path.read_bytes()).hexdigest()}
    key_path = args.output / "cache_key.json"
    feature_path = args.output / "features.npz"
    failures_path = args.output / "failures.json"
    start = time.monotonic()
    modes = ("fp32", "int8_same_alignment", "int8_pipeline")
    if args.reuse and key_path.exists() and json.loads(key_path.read_text()) == provenance and feature_path.exists():
        saved = np.load(feature_path)
        features = {mode: saved[mode] for mode in modes}
        valid = {mode: saved[f"{mode}_valid"] for mode in modes}
        failures = json.loads(failures_path.read_text())
    else:
        features = {mode: np.zeros((len(names), 128), dtype=np.float32) for mode in modes}
        valid = {mode: np.zeros(len(names), dtype=bool) for mode in modes}
        failures = []
        for index, name in enumerate(names):
            image = read_image(args.dataset / "lfw_funneled" / name)
            try:
                face = choose_face(pipeline.detect(image), image)
                aligned = pipeline.align(image, face)
                features["fp32"][index] = pipeline.embed_aligned(aligned)
                valid["fp32"][index] = True
                value = embedding.run(None, {embedding.get_inputs()[0].name: embedding_input(aligned)})[0]
                features["int8_same_alignment"][index] = normalize(value)
                valid["int8_same_alignment"][index] = True
            except ValueError as error:
                failures.append({"image": name, "mode": "fp32/same-alignment", "error": str(error)})
            try:
                values = detection.run(None, {detection.get_inputs()[0].name: detector_input(image)})
                faces = decode_yunet(dict(zip((value.name for value in detection.get_outputs()), values)), image.shape)
                face = choose_face(faces, image)
                aligned = align_face(image, face)
                value = embedding.run(None, {embedding.get_inputs()[0].name: embedding_input(aligned)})[0]
                features["int8_pipeline"][index] = normalize(value)
                valid["int8_pipeline"][index] = True
            except ValueError as error:
                failures.append({"image": name, "mode": "int8-pipeline", "error": str(error)})
            if (index + 1) % 50 == 0:
                print(f"LFW DevTest: {index+1}/{len(names)} images, {time.monotonic()-start:.1f} s, failures={len(failures)}", flush=True)
        np.savez_compressed(feature_path, **features, **{f"{mode}_valid": value for mode, value in valid.items()})
        key_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="ascii")
        failures_path.write_text(json.dumps(failures, indent=2) + "\n", encoding="ascii")
    metrics = {mode: score_report(pairs, names, features[mode], valid[mode], 0.55) for mode in modes}
    common = valid["fp32"] & valid["int8_same_alignment"]
    similarities = np.einsum("ij,ij->i", features["fp32"][common], features["int8_same_alignment"][common])
    report = {"evaluated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "execution": "Host ONNX Runtime reference; these figures are not FPGA latency or board dataset accuracy",
              "dataset": "LFW funneled; official pairsDevTest, 500 same + 500 different",
              "task": "Face verification, not closed-set identification",
              "unique_images": len(names), "calibration_overlap": 0, "fixed_threshold": 0.55,
              "threshold_tuned_on_devtest": False,
              "face_selection": "Largest central portrait if multiple detections; failed processing is counted wrong",
              "provenance": provenance, "metrics": metrics,
              "embedding_cosine_fp32_vs_int8": {"count": int(common.sum()), "mean": float(similarities.mean()),
                                               "minimum": float(similarities.min()), "p05": float(np.percentile(similarities, 5))},
              "image_order": names, "failures": failures, "wall_seconds": time.monotonic()-start}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")
    for mode, metric in metrics.items():
        print(f"{mode}: {metric['correct_pairs']}/1000, valid={metric['valid_pairs']}, accuracy={metric['accuracy_all_pairs_with_failures_counted_wrong']:.4f}", flush=True)


if __name__ == "__main__":
    main()
