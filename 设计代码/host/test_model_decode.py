#!/usr/bin/env python3
"""Check exported descriptors, independent integer models and YuNet decoding."""
import json
import struct
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from face_pipeline import FacePipeline, read_image
from fpga_model import Model, decode_yunet, detector_input
from quantize_face_models import runtime

ROOT = Path(__file__).resolve().parents[1]


def main():
    directory = ROOT / "build/vision/fpga"
    results = []
    for name in ("yunet", "mobilefacenet"):
        model = Model(directory, name)
        header = struct.unpack_from("<16I", model.image)
        assert header[:4] == (0x4e4e4350, 1, len(model.manifest["layers"]), model.manifest["descriptor_address"])
        for layer in model.manifest["layers"]:
            offset = header[3] - model.manifest["image_address"] + 96 * layer["index"]
            descriptor = struct.unpack_from("<24I", model.image, offset)
            assert list(descriptor[:17]) == layer["registers"]
            assert descriptor[17:19] == (layer["output"]["bytes"], layer["index"])
        tensors, outputs, milliseconds = model.reference((directory / f"{name}_input.bin").read_bytes())
        precise, _, _ = model.reference((directory / f"{name}_input.bin").read_bytes(), force_float64=True)
        assert all(np.array_equal(tensors[layer["target"]], precise[layer["target"]]) for layer in model.manifest["layers"])
        official = np.load(directory / f"{name}_reference.npz")
        isolated, _, _ = model.reference((directory / f"{name}_input.bin").read_bytes(), source_overrides=official)
        isolated_errors = {layer["index"]: int(np.max(np.abs(isolated[layer["target"]].astype(np.int16) -
                                                           official[layer["target"]].astype(np.int16))))
                           for layer in model.manifest["layers"]}
        assert max(isolated_errors.values()) <= 1, isolated_errors
        differences = {output_name: float(np.max(np.abs(value - official[output_name]))) for output_name, value in outputs.items()}
        record = {"model": name, "integer_reference_ms": milliseconds,
                  "isolated_layer_maximum_quantized_differences_vs_ort": isolated_errors,
                  "output_maximum_absolute_differences_vs_ort": differences,
                  "bounded_float32_matches_float64_at_every_layer": True,
                  "rounding": "Exact rational multipliers versus ORT floating scaling may differ by one quantized level per layer"}
        if name == "mobilefacenet":
            first = next(iter(outputs.values())).reshape(-1)
            second = official[next(iter(outputs))].reshape(-1)
            cosine = float(first @ second / np.linalg.norm(first) / np.linalg.norm(second))
            assert cosine > 0.99, cosine
            record["embedding_cosine_vs_ort"] = cosine
        results.append(record)
        print(f"PASS: {name} descriptor format and independent integer model; {milliseconds:.1f} ms", flush=True)
    pipeline = FacePipeline()
    original = runtime(ROOT / "build/vision/int8/yunet_u8s8_fixed.onnx")
    quantized = runtime(ROOT / "build/vision/int8/yunet_u8s8.onnx")
    for name in ("obama.jpg", "obama2.jpg", "biden.jpg", "astronaut.png", "blank"):
        image = np.zeros((360, 640, 3), np.uint8) if name == "blank" else read_image(ROOT / "vision/test_images" / name)
        inputs = detector_input(image)
        detected = pipeline.detect(image)
        values = original.run(None, {original.get_inputs()[0].name: inputs})
        decoded = decode_yunet(dict(zip((item.name for item in original.get_outputs()), values)), image.shape)
        assert len(decoded) == len(detected), (name, len(decoded), len(detected))
        if len(detected):
            assert np.max(np.abs(decoded - detected)) < 0.02, (name, decoded, detected)
        values = quantized.run(None, {quantized.get_inputs()[0].name: inputs})
        fixed = decode_yunet(dict(zip((item.name for item in quantized.get_outputs()), values)), image.shape)
        assert len(fixed) == (0 if name == "blank" else 1), (name, len(fixed))
        print(f"PASS: YuNet decode {name}; fp32={len(decoded)}, int8={len(fixed)}", flush=True)
    output = ROOT / "build/vision/model_format_validation.json"
    output.write_text(json.dumps({"models": results, "decoder_matches_opencv": True, "passed": True}, indent=2) + "\n", encoding="ascii")


if __name__ == "__main__":
    main()
