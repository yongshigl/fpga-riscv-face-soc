#!/usr/bin/env python3
"""Calibrate U8/S8 face models with LFW DevTrain, excluding DevTest images."""
import argparse
import hashlib
import json
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
from onnxruntime.quantization import (
    CalibrationDataReader, CalibrationMethod, QuantFormat, QuantType, quantize_static,
)
from onnxruntime.quantization.shape_inference import quant_pre_process

from face_pipeline import FacePipeline, letterbox, normalize, read_image

ROOT = Path(__file__).resolve().parent
OPS = ["Conv", "Add", "Relu", "Clip", "MaxPool", "Resize", "Transpose", "Reshape"]


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hashlib.sha256(source.read()).hexdigest()


def pair_images(path):
    records = []
    lines = path.read_text(encoding="ascii").splitlines()
    for line in lines[1:]:
        values = line.split()
        if len(values) == 3:
            name, first, second = values
            records.append((f"{name}/{name}_{int(first):04d}.jpg", f"{name}/{name}_{int(second):04d}.jpg", True))
        elif len(values) == 4:
            left, first, right, second = values
            records.append((f"{left}/{left}_{int(first):04d}.jpg", f"{right}/{right}_{int(second):04d}.jpg", False))
        else:
            raise ValueError(f"Invalid LFW pair: {line}")
    per_class = int(lines[0])
    if len(records) != 2*per_class or sum(same for _, _, same in records) != per_class:
        raise ValueError(f"LFW split count mismatch: {path}")
    return records


class Reader(CalibrationDataReader):
    def __init__(self, name, tensors):
        self.name = name
        self.tensors = tensors
        self.rewind()

    def rewind(self):
        self.iterator = iter(self.tensors)

    def get_next(self):
        value = next(self.iterator, None)
        return None if value is None else {self.name: value}


def fixed_model(source, destination, shape):
    model = onnx.load(source)
    for axis, size in zip(model.graph.input[0].type.tensor_type.shape.dim, shape):
        axis.ClearField("dim_param")
        axis.dim_value = size
    for output in model.graph.output:
        output.type.tensor_type.ClearField("shape")
    del model.graph.value_info[:]
    onnx.save_model(onnx.shape_inference.infer_shapes(model), destination)


def runtime(path):
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def quantize(source, output, tensors, shape):
    fixed = output.with_name(output.stem + "_fixed.onnx")
    prepared = output.with_name(output.stem + "_prepared.onnx")
    fixed_model(source, fixed, shape)
    quant_pre_process(str(fixed), str(prepared), skip_symbolic_shape=True)
    name = onnx.load(prepared).graph.input[0].name
    quantize_static(str(prepared), str(output), Reader(name, tensors),
                    quant_format=QuantFormat.QOperator, per_channel=True,
                    activation_type=QuantType.QUInt8, weight_type=QuantType.QInt8,
                    calibrate_method=CalibrationMethod.MinMax,
                    op_types_to_quantize=OPS, extra_options={
                        "ActivationSymmetric": False, "WeightSymmetric": True,
                        "ForceQuantizeNoInputCheck": True,
                    })
    model = onnx.load(output)
    onnx.checker.check_model(model)
    session = runtime(output)
    output_values = session.run(None, {session.get_inputs()[0].name: tensors[0]})
    if not all(np.all(np.isfinite(value)) for value in output_values):
        raise RuntimeError("Nonfinite quantized model output")
    return {
        "file": output.name, "sha256": digest(output), "bytes": output.stat().st_size,
        "input_shape": shape, "operators": dict(Counter(node.op_type for node in model.graph.node)),
        "quantized_convolutions": sum(node.op_type == "QLinearConv" for node in model.graph.node),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=ROOT / "datasets/lfw")
    parser.add_argument("--output", type=Path, default=ROOT.parent / "build/vision/int8")
    parser.add_argument("--calibration-images", type=int, default=64)
    parser.add_argument("--seed", type=int, default=261001)
    parser.add_argument("--bootstrap", action="store_true", help="Provisional calibration with the local demonstration images")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    pipeline = FacePipeline()
    if args.bootstrap:
        paths = [ROOT / "test_images" / name for name in ("obama.jpg", "obama2.jpg", "biden.jpg", "astronaut.png")]
        excluded = set()
        split = "Provisional demonstration calibration; no dataset accuracy claim"
    else:
        source_manifest = json.loads((args.dataset / "sources.json").read_text())
        for record in source_manifest["files"]:
            if digest(args.dataset / record["path"]) != record["sha256"]:
                raise RuntimeError(f"LFW source changed: {record['path']}")
        train = pair_images(args.dataset / "pairsDevTrain.txt")
        evaluation = pair_images(args.dataset / "pairsDevTest.txt")
        excluded = {name for first, second, _ in evaluation for name in (first, second)}
        names = sorted({name for first, second, _ in train for name in (first, second)} - excluded)
        random.Random(args.seed).shuffle(names)
        paths = [args.dataset / "lfw_funneled" / name for name in names]
        split = "LFW DevTrain images excluding every DevTest image"
    records, skipped, detection_tensors, embedding_tensors = [], [], [], []
    for path in paths:
        image = read_image(path)
        faces = pipeline.detect(image)
        if len(faces) != 1:
            skipped.append({"image": str(path), "faces": len(faces)})
            continue
        padded, _ = letterbox(image, (320, 192))
        aligned = pipeline.align(image, faces[0])
        detection_tensors.append(np.ascontiguousarray(padded.transpose(2, 0, 1)[None], dtype=np.float32))
        embedding_tensors.append(np.ascontiguousarray(((aligned.astype(np.float32)-127.5)/128.0).transpose(2, 0, 1)[None]))
        records.append({"image": str(path), "sha256": digest(path)})
        if len(records) == args.calibration_images:
            break
    if not records or (not args.bootstrap and len(records) < args.calibration_images):
        raise RuntimeError("Insufficient separate calibration faces")
    np.savez_compressed(args.output / "calibration_inputs.npz",
                        yunet=np.concatenate(detection_tensors), mobilefacenet=np.concatenate(embedding_tensors))
    print(f"Calibrating {len(records)} separate faces; skipped {len(skipped)}", flush=True)
    models = [
        quantize(ROOT / "models/face_detection_yunet_2023mar.onnx", args.output / "yunet_u8s8.onnx",
                 detection_tensors, [1, 3, 192, 320]),
        quantize(ROOT / "models/MobileFaceNet.onnx", args.output / "mobilefacenet_u8s8.onnx",
                 embedding_tensors, [1, 3, 112, 112]),
    ]
    reference = runtime(ROOT / "models/MobileFaceNet.onnx")
    quantized = runtime(args.output / models[1]["file"])
    similarities = []
    for tensor in embedding_tensors[:8]:
        original = normalize(reference.run(None, {reference.get_inputs()[0].name: tensor})[0])
        fixed = normalize(quantized.run(None, {quantized.get_inputs()[0].name: tensor})[0])
        similarities.append(float(original @ fixed))
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(), "runtime": ort.__version__,
        "calibration_method": "ONNX Runtime static MinMax; per-channel S8 weights and asymmetric U8 activations",
        "calibration_split": split, "calibration_images": records, "skipped_images": skipped,
        "excluded_evaluation_images": len(excluded), "bootstrap": args.bootstrap, "seed": args.seed,
        "models": models, "calibration_embedding_cosine_fp32_vs_int8": similarities,
        "execution": "Host model calibration and validation; this report does not measure FPGA inference",
    }
    (args.output / "quantization.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"models": models, "calibration_embedding_cosine": similarities}, indent=2), flush=True)


if __name__ == "__main__":
    main()
