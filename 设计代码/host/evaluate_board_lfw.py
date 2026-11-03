#!/usr/bin/env python3
"""Run LFW DevTest networks on FPGA and all pair decisions on RISC-V."""
import argparse
import datetime
import hashlib
import json
import time
from pathlib import Path

import numpy as np

from board_evidence import validate_project
from evaluate_lfw import choose_face
from face_pipeline import read_image
from fpga_model import pack_tensor, unpack_tensor
from hardware_face_demo import HardwareFacePipeline
from hardware_transport import Board
from quantize_face_models import pair_images
from transport_evidence import snapshot_transport

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_progress(output, features, raw, valid, records):
    temporary = output / "progress.npz.tmp"
    with temporary.open("wb") as stream:
        np.savez_compressed(stream, features_q15=features, raw_embedding=raw, valid=valid)
    temporary.replace(output / "progress.npz")
    temporary = output / "images.json.tmp"
    temporary.write_text(json.dumps(records, indent=2) + "\n", encoding="ascii")
    temporary.replace(output / "images.json")


def metrics(records, threshold):
    valid = [record for record in records if record["valid"]]
    correct = sum(record["valid"] and record["prediction"] == record["same"] for record in records)
    tp = sum(record["same"] and record["prediction"] for record in valid)
    tn = sum(not record["same"] and not record["prediction"] for record in valid)
    fp = sum(not record["same"] and record["prediction"] for record in valid)
    fn = sum(record["same"] and not record["prediction"] for record in valid)
    return {"pairs": len(records), "same_pairs": sum(record["same"] for record in records),
            "different_pairs": sum(not record["same"] for record in records), "valid_pairs": len(valid),
            "pairs_with_failed_face_processing": len(records) - len(valid), "correct_pairs": correct,
            "accuracy_all_pairs_with_failures_counted_wrong": correct / len(records),
            "accuracy_valid_pairs": correct / len(valid) if valid else None, "threshold": threshold,
            "true_accepts": tp, "true_rejects": tn, "false_accepts_valid_pairs": fp,
            "false_rejects_valid_pairs": fn, "false_accept_rate_valid_pairs": fp / max(1, fp + tn),
            "false_reject_rate_valid_pairs": fn / max(1, fn + tp)}


def compare_reference_embeddings(features, valid, reference_features, reference_valid):
    if (features.shape != reference_features.shape or features.ndim != 2 or features.shape[1] != 128
            or valid.shape != reference_valid.shape or valid.shape != features.shape[:1]):
        raise ValueError("Board and integer reference embedding dimensions differ")
    common = valid & reference_valid
    if not common.any():
        raise RuntimeError("No common valid embeddings to compare with the integer reference")
    actual = features[common].astype(np.float64)
    expected = reference_features[common].astype(np.float64)
    actual_norm = np.linalg.norm(actual, axis=1, keepdims=True)
    expected_norm = np.linalg.norm(expected, axis=1, keepdims=True)
    if (not np.isfinite(actual).all() or not np.isfinite(expected).all()
            or (actual_norm == 0).any() or (expected_norm == 0).any()):
        raise RuntimeError("Nonfinite or zero embedding in board/reference comparison")
    similarities = np.einsum("ij,ij->i", actual / actual_norm, expected / expected_norm)
    missing = int(np.count_nonzero(reference_valid & ~valid))
    unexpected = int(np.count_nonzero(valid & ~reference_valid))
    result = {"images": int(common.sum()), "mean_embedding_cosine": float(similarities.mean()),
              "minimum_embedding_cosine": float(similarities.min()),
              "minimum_required_cosine": 0.999999,
              "images_below_required_cosine": int(np.count_nonzero(similarities < 0.999999)),
              "reference_valid_images_missing_on_board": missing,
              "board_valid_images_missing_in_reference": unexpected}
    result["passed"] = not (missing or unexpected or result["images_below_required_cosine"])
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=ROOT / "vision/datasets/lfw")
    parser.add_argument("--models", type=Path, default=ROOT / "build/vision/fpga")
    parser.add_argument("--threshold", type=Path, default=ROOT / "build/vision/threshold_training/threshold.json")
    parser.add_argument("--output", type=Path, default=ROOT / "build/vision/lfw_board_devtest")
    parser.add_argument("--reference", type=Path, default=ROOT / "build/vision/lfw_integer_devtest")
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--skip-model-upload", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--limit-images", type=int, help="Diagnostic partial run; does not produce an accuracy report")
    args = parser.parse_args()
    if args.progress_every < 1:
        parser.error("Progress interval must be positive")
    pairs_path = args.dataset / "pairsDevTest.txt"
    pairs = pair_images(pairs_path)
    if len(pairs) != 1000:
        raise ValueError("Expected all 1000 official LFW DevTest pairs")
    names = sorted({name for first, second, _ in pairs for name in (first, second)})
    limit = len(names) if args.limit_images is None else args.limit_images
    if not 1 <= limit <= len(names):
        raise ValueError("Image limit is outside this dataset")
    training = json.loads(args.threshold.read_text())
    quantization = json.loads((ROOT / "build/vision/int8/quantization.json").read_text())
    calibration = {f"{Path(row['image']).parent.name}/{Path(row['image']).name}"
                   for row in quantization["calibration_images"]}
    if (training["devtest_labels_used_for_selection"] or training["devtest_image_overlap"]
            or quantization["bootstrap"] or calibration.intersection(names)):
        raise ValueError("Threshold training or model calibration overlaps DevTest")
    args.output.mkdir(parents=True, exist_ok=True)
    transport_sources = snapshot_transport(args.output)
    image_hashes = {name: sha256(args.dataset / "lfw_funneled" / name) for name in names}
    started = time.monotonic()
    features = np.zeros((len(names), 128), dtype=np.int16)
    raw = np.zeros((len(names), 128), dtype=np.uint8)
    valid = np.zeros(len(names), dtype=bool)
    records = []
    with Board(args.port, bridge_log=args.output / "bridge.log") as board:
        identity = board.ping()
        project = validate_project(args.project, identity)
        if board.capabilities()["camera_preview"]:
            board.preview(2)
        pipeline = HardwareFacePipeline(board, args.models, identity, training["threshold_q15"])
        models = (pipeline.detector, pipeline.embedding)
        if any(training["model_hashes"][model.name] != model.manifest["model_sha256"] for model in models):
            raise ValueError("Threshold was trained with different model files")
        key = {"bitstream_sha256": project["bitstream_sha256"], "firmware": identity["firmware_version"],
               "pairs_sha256": sha256(pairs_path), "threshold_sha256": sha256(args.threshold),
               "image_hashes": image_hashes, "names": names,
               "model_images": {model.name: model.manifest["image_sha256"] for model in models},
               "sources": {**transport_sources, **{name: sha256(ROOT / "vision" / name) for name in
                           ("evaluate_board_lfw.py", "hardware_face_demo.py", "hardware_transport.py", "fpga_model.py",
                            "face_pipeline.py", "evaluate_lfw.py", "quantize_face_models.py", "board_evidence.py")}}}
        key_path = args.output / "cache_key.json"
        if args.resume and (args.output / "progress.npz").exists():
            if json.loads(key_path.read_text()) != key:
                raise ValueError("Resume requires identical images, models, threshold, code and bitstream")
            with np.load(args.output / "progress.npz") as saved:
                features, raw, valid = saved["features_q15"], saved["raw_embedding"], saved["valid"]
            records = json.loads((args.output / "images.json").read_text())
            if (features.shape != (len(names), 128) or features.dtype != np.int16
                    or raw.shape != features.shape or raw.dtype != np.uint8
                    or valid.shape != (len(names),) or valid.dtype != bool
                    or [record["image"] for record in records] != names[:len(records)]):
                raise ValueError("Invalid board progress artifact")
        elif (args.output / "progress.npz").exists():
            raise ValueError("Existing board progress requires --resume or a new output directory")
        key_path.write_text(json.dumps(key, indent=2) + "\n", encoding="ascii")
        print(f"Board {project['project']}: {identity}; images={len(names)}, resumed={len(records)}", flush=True)
        for model in models:
            if args.skip_model_upload:
                if board.download(model.manifest["image_address"], len(model.image)) != model.image:
                    raise RuntimeError(f"{model.name}: DDR model bytes differ")
            else:
                print(f"Upload and verify {model.name}", flush=True)
                model.upload(board)
        board.command(8)
        tensor = pipeline.embedding.manifest["outputs"][0]["tensor"]
        try:
            for index in range(len(records), limit):
                name = names[index]
                image = read_image(args.dataset / "lfw_funneled" / name)
                start = time.monotonic()
                record = {"image": name, "image_sha256": image_hashes[name], "valid": False}
                try:
                    faces, detection = pipeline.detect(image)
                    face = choose_face(faces, image)
                    embedding = pipeline.embed_and_match(image, face)
                    values = unpack_tensor(board.download(tensor["address"], tensor["bytes"]), tensor).reshape(-1)
                    if values.shape != (128,):
                        raise RuntimeError("Unexpected hardware embedding dimensions")
                    raw[index] = values
                    features[index] = embedding["embedding_q15"]
                    reference = values.astype(np.float64) - tensor["zero"]
                    cosine = float(reference @ features[index] / (np.linalg.norm(reference) * np.linalg.norm(features[index])))
                    if cosine < 0.999999:
                        raise RuntimeError("CPU normalization differs from the raw FPGA embedding")
                    valid[index] = True
                    record.update(valid=True, cpu_normalization_cosine=cosine,
                        detection={key: detection[key] for key in ("input_sha256", "upload", "accelerator_ms", "cpu_schedule_ms", "wall_ms")},
                        embedding={key: embedding[key] for key in ("input_sha256", "upload", "accelerator_ms", "cpu_schedule_ms", "embedding_ms", "cpu_matching_ms")},
                        embedding_sha256=hashlib.sha256(values.tobytes()).hexdigest())
                except ValueError as error:
                    record["error"] = str(error)
                record["wall_ms"] = (time.monotonic() - start) * 1000
                records.append(record)
                if (index + 1) % 25 == 0 or index + 1 == limit:
                    save_progress(args.output, features, raw, valid, records)
                if (index + 1) % args.progress_every == 0 or index + 1 == limit:
                    print(f"FPGA DevTest images {index + 1}/{len(names)}, failures={sum(not row['valid'] for row in records)}, "
                          f"session_seconds={time.monotonic() - started:.1f}", flush=True)
        finally:
            save_progress(args.output, features, raw, valid, records)
        if len(records) != len(names):
            print(f"PARTIAL board inference: {len(records)}/{len(names)} images; accuracy not evaluated", flush=True)
            return
        lookup = {name: index for index, name in enumerate(names)}
        decisions = []
        for first, second, same in pairs:
            left, right = lookup[first], lookup[second]
            record = {"first": first, "second": second, "same": bool(same), "valid": bool(valid[left] and valid[right]),
                      "prediction": None}
            if record["valid"]:
                board.enroll(0, features[left].tolist())
                board.upload(tensor["address"], pack_tensor(raw[right].reshape(1, 128, 1, 1), tensor, quantized=True))
                match = board.match(pipeline.embedding.manifest)
                if not np.array_equal(match["embedding_q15"], features[right]) or match["gallery_valid"] != 1:
                    raise RuntimeError("CPU pair input does not match saved hardware embeddings")
                dot = int(features[left].astype(np.int64) @ features[right].astype(np.int64))
                expected_score = (1 if dot >= 0 else -1) * (abs(dot) // 32768)
                expected_identity = 0 if expected_score >= training["threshold_q15"] else None
                if match["cosine"] != expected_score / 32768 or match["identity_index"] != expected_identity:
                    raise RuntimeError("CPU pair decision differs from independent Q15 integer arithmetic")
                record.update(prediction=match["identity_index"] == 0, cosine=match["cosine"],
                              cpu_cycles=match["cpu_cycles"])
            decisions.append(record)
            if len(decisions) % 100 == 0:
                print(f"RISC-V DevTest pair decisions {len(decisions)}/1000", flush=True)
        counters = board.counters()
        if counters["rx_errors"] or board.decoder.crc_errors or board.decoder.framing_errors:
            raise RuntimeError("UART errors during board dataset evaluation")
        board.command(8)
    for name, expected in key["sources"].items():
        if sha256(ROOT / "vision" / name) != expected:
            raise RuntimeError("Evaluation code or transport changed during the board dataset run: " + name)
    diagnostics = (args.output / "bridge.log").read_text(errors="replace") if (args.output / "bridge.log").exists() else ""
    driver_errors = [line for line in diagnostics.splitlines()
                     if any(tag in line for tag in ("SERIAL_BRIDGE_COMM_ERROR", "SERIAL_BRIDGE_READ_ERROR",
                                                   "SERIAL_BRIDGE_WRITE_ERROR"))]
    if driver_errors:
        (args.output / "transport_failure.json").write_text(json.dumps({"complete": False,
            "driver_errors": driver_errors, "bridge_log_sha256": sha256(args.output / "bridge.log")}, indent=2) + "\n")
        raise RuntimeError("Serial driver reported errors during the board dataset evaluation")
    reference_comparison = None
    if args.reference and (args.reference / "report.json").exists():
        reference_report = json.loads((args.reference / "report.json").read_text())
        if (reference_report["image_order"] != names
                or reference_report["provenance"]["pairs_sha256"] != key["pairs_sha256"]
                or any(reference_report["provenance"][model.name] != key["model_images"][model.name] for model in models)):
            raise ValueError("Integer reference does not use the same dataset and exported model images")
        with np.load(args.reference / "features.npz") as saved:
            reference_comparison = compare_reference_embeddings(features, valid, saved["features"], saved["valid"])
        reference_comparison["report_sha256"] = sha256(args.reference / "report.json")
        (args.output / "reference_comparison.json").write_text(json.dumps(reference_comparison, indent=2) + "\n")
        if not reference_comparison["passed"]:
            raise RuntimeError("Board embeddings or valid-image coverage differ from the integer reference")
    report = {"evaluated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "execution": "FPGA YuNet/MobileFaceNet for every portrait; RISC-V normalization and every Q15 pair decision",
              "host_work": "Resize/quantization, YuNet decode/NMS, landmark alignment, report aggregation",
              "dataset": "LFW DevTest; 500 same + 500 different pairs", "unique_images": len(names),
              "task": "Face verification", "board": identity, "project_evidence": project, "provenance": key,
              "metrics": metrics(decisions, training["threshold"]), "pairs": decisions,
              "failures": [record for record in records if not record["valid"]], "counters": counters,
              "transport_driver_errors": driver_errors,
              "reference_comparison": reference_comparison,
              "image_processing_ms": {"mean": float(np.mean([record["wall_ms"] for record in records])),
                                      "p95": float(np.percentile([record["wall_ms"] for record in records], 95))},
              "timing_scope": "Per-image time includes UART, host work and a verification readback; excludes camera capture",
              "session_seconds": time.monotonic() - started, "board_progress_sha256": sha256(args.output / "progress.npz"),
              "board_image_records_sha256": sha256(args.output / "images.json"), "complete": True}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")
    print(f"PASS board LFW DevTest: {report['metrics']['correct_pairs']}/1000, "
          f"accuracy={report['metrics']['accuracy_all_pairs_with_failures_counted_wrong']:.4f}", flush=True)


if __name__ == "__main__":
    main()
