#!/usr/bin/env python3
"""Select a gallery threshold using DevTrain pairs, excluding all DevTest images."""
import argparse
import datetime
import hashlib
import json
import time
from pathlib import Path

import numpy as np

from evaluate_lfw import choose_face, score_report
from face_pipeline import normalize, read_image
from fpga_model import align_face, decode_yunet, detector_input, embedding_input
from quantize_face_models import pair_images, runtime

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=ROOT / "vision/datasets/lfw")
    parser.add_argument("--models", type=Path, default=ROOT / "build/vision/int8")
    parser.add_argument("--output", type=Path, default=ROOT / "build/vision/threshold_training")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    train = pair_images(args.dataset / "pairsDevTrain.txt")
    test = pair_images(args.dataset / "pairsDevTest.txt")
    excluded = {name for first, second, _ in test for name in (first, second)}
    pairs = [(first, second, same) for first, second, same in train if first not in excluded and second not in excluded]
    names = sorted({name for first, second, _ in pairs for name in (first, second)})
    detector = runtime(args.models / "yunet_u8s8.onnx")
    embedding = runtime(args.models / "mobilefacenet_u8s8.onnx")
    features = np.zeros((len(names), 128), dtype=np.float32)
    valid = np.zeros(len(names), dtype=bool)
    failures = []
    start = time.monotonic()
    for index, name in enumerate(names):
        image = read_image(args.dataset / "lfw_funneled" / name)
        try:
            values = detector.run(None, {detector.get_inputs()[0].name: detector_input(image)})
            faces = decode_yunet(dict(zip((value.name for value in detector.get_outputs()), values)), image.shape)
            aligned = align_face(image, choose_face(faces, image))
            values = embedding.run(None, {embedding.get_inputs()[0].name: embedding_input(aligned)})[0]
            features[index] = normalize(values)
            valid[index] = True
        except ValueError as error:
            failures.append({"image": name, "error": str(error)})
        if (index + 1) % 100 == 0:
            print(f"DevTrain threshold: {index+1}/{len(names)} images, {time.monotonic()-start:.1f} s", flush=True)
    baseline = score_report(pairs, names, features, valid, 0.55)
    cosines = np.asarray(baseline["pair_cosines"])
    truth = np.asarray([same for _, _, same in pairs], dtype=bool)
    usable = np.asarray(baseline["pair_valid"])
    candidates = np.unique(np.clip(np.ceil(cosines[usable] * 32768), 0, 32767).astype(np.int32))
    candidates = np.unique(np.concatenate(([0], candidates, np.minimum(32767, candidates + 1), [32767])))
    best = None
    for threshold in candidates:
        prediction = cosines >= threshold / 32768
        correct = int(np.count_nonzero(usable & (prediction == truth)))
        false_accepts = int(np.count_nonzero(usable & ~truth & prediction))
        choice = (correct, -false_accepts, int(threshold))
        if best is None or choice > best:
            best = choice
    selected = best[2] / 32768
    metric = score_report(pairs, names, features, valid, selected)
    model_hashes = {name: hashlib.sha256((args.models / f"{name}_u8s8.onnx").read_bytes()).hexdigest()
                    for name in ("yunet", "mobilefacenet")}
    report = {"trained_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "execution": "Host INT8 pipeline; threshold training only",
              "selection_rule": "Maximum DevTrain correct pairs; ties prefer fewer false accepts and then higher Q15 threshold",
              "source_pairs": "pairsDevTrain.txt", "original_train_pairs": len(train),
              "train_pairs_after_excluding_every_devtest_image": len(pairs),
              "excluded_devtest_images": len(excluded), "devtest_image_overlap": 0,
              "devtest_labels_used_for_selection": False, "threshold": selected, "threshold_q15": best[2],
              "model_hashes": model_hashes, "baseline_threshold": 0.55,
              "baseline_metrics": baseline, "selected_metrics": metric,
              "images": names, "failures": failures,
              "training_pairs_sha256": hashlib.sha256((args.dataset / "pairsDevTrain.txt").read_bytes()).hexdigest()}
    np.savez_compressed(args.output / "train_features.npz", features=features, valid=valid)
    (args.output / "threshold.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")
    print(json.dumps({"threshold": selected, "threshold_q15": best[2], "pairs": len(pairs),
                      "train_accuracy": metric["accuracy_all_pairs_with_failures_counted_wrong"],
                      "train_false_accept_rate": metric["false_accept_rate_valid_pairs"]}), flush=True)


if __name__ == "__main__":
    main()
