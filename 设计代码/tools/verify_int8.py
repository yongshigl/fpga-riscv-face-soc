#!/usr/bin/env python3
"""Validate the complete RTL layer engine against PyTorch integer-exact sums."""
import argparse
import hashlib
import json
import re
import struct
import subprocess
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as functional

ROOT = Path(__file__).resolve().parents[1]
torch.set_num_threads(2)


def round_even(value, shift):
    value = np.asarray(value, dtype=np.int64)
    magnitude = np.abs(value).astype(np.uint64)
    divisor = np.uint64(1 << int(shift))
    whole, fraction = magnitude // divisor, magnitude % divisor
    increment = (fraction > divisor//2) | ((fraction == divisor//2) & ((whole & 1) != 0)) if shift else 0
    rounded = (whole + increment).astype(np.int64)
    return np.where(value < 0, -rounded, rounded)


def packed(value, zero):
    _, channels, height, width = value.shape
    row = (width+31)//32*32
    result = np.full((channels, height, row), zero, np.uint8)
    result[:, :, :width] = value[0]
    return result.tobytes()


def segments(path, values):
    with path.open("wb") as target:
        target.write(struct.pack("<I", len(values)))
        for address, data in values:
            target.write(struct.pack("<II", address, len(data)))
            target.write(data)


def case(directory, name, registers, source, second, weights, parameters, error=0, fault=0):
    output = directory / name
    output.mkdir(parents=True, exist_ok=True)
    op, src, src2, dst, weight_addr, param_addr, _, channels, outshape, rows, _, plane, geometry, zeros, ma, mb, shift = registers
    cin, cout = channels & 65535, channels >> 16
    oh, ow = outshape >> 16, outshape & 65535
    za, zy, zb = zeros & 255, (zeros >> 8) & 255, (zeros >> 16) & 255
    kh, kw, stride, padding = (geometry >> 4) & 15, geometry & 15, (geometry >> 8) & 15, (geometry >> 16) & 15
    initial = [(src, packed(source, za))]
    if second is not None:
        initial.append((src2, packed(second, zb)))
    if op in (1, 2):
        reduction = weights.shape[1]*kh*kw
        flat = weights.reshape(cout, reduction)
        padded_weights = np.pad(flat, ((0, ((cout+15)//16)*16-cout), (0, 0)))
        weight_bytes = np.ascontiguousarray(padded_weights.reshape(-1, 16, reduction).transpose(0, 2, 1)).tobytes()
        parameter_bytes = b"".join(struct.pack("<iiBBBBI", *record, 0, 0) for record in parameters)
        initial.extend([(weight_addr, weight_bytes), (param_addr, parameter_bytes)])
        centered = source.astype(np.float64)-za
        bias = torch.tensor([record[0] for record in parameters], dtype=torch.float64)
        sums = functional.conv2d(torch.from_numpy(centered), torch.from_numpy(weights.astype(np.float64)), bias,
                                 stride=stride, padding=padding, groups=cin if op == 2 else 1).numpy().astype(np.int64)
        expected = np.empty((1, cout, oh, ow), np.uint8)
        for channel, (_, multiplier, bits, low, high) in enumerate(parameters):
            expected[:, channel] = np.clip(round_even(sums[:, channel]*multiplier, bits)+zy, low, high).astype(np.uint8)
    elif op == 3:
        numerator = (source.astype(np.int64)-za)*ma + (second.astype(np.int64)-zb)*mb
        expected = np.clip(round_even(numerator, shift)+zy, 0, 255).astype(np.uint8)
    elif op == 4:
        expected = functional.max_pool2d(torch.from_numpy(source.astype(np.float64)), 2, 2).numpy().astype(np.uint8)
    else:
        expected = functional.interpolate(torch.from_numpy(source.astype(np.float64)), scale_factor=2, mode="nearest").numpy().astype(np.uint8)
    output_bytes = packed(expected, zy)
    if len(output_bytes) != cout*plane:
        raise RuntimeError("Invalid reference output packing")
    segments(output / "segments.bin", initial)
    (output / "config.bin").write_bytes(struct.pack("<24I", *(registers+[error, fault, dst, len(output_bytes), 0, 0, 0])))
    (output / "expected.bin").write_bytes(output_bytes)
    return {"name": name, "config": registers, "expected_error": error, "fault_injection": fault,
            "expected_sha256": hashlib.sha256(output_bytes).hexdigest(), "output_bytes": len(output_bytes)}


def random_case(directory, name, rng, cin, height, width, cout=None, kernel=1, stride=1, padding=0, op=1, error=0, fault=0):
    cout = cout or cin
    za, zy, zb = [int(value) for value in rng.integers(0, 256, 3)]
    source = rng.integers(0, 256, (1, cin, height, width), dtype=np.uint8)
    second = rng.integers(0, 256, source.shape, dtype=np.uint8) if op == 3 else None
    if op in (1, 2):
        oh, ow = (height+2*padding-kernel)//stride+1, (width+2*padding-kernel)//stride+1
    elif op == 4:
        oh, ow, kernel, stride, zy = height//2, width//2, 2, 2, za
    elif op == 5:
        oh, ow, kernel, stride, zy = height*2, width*2, 1, 2, za
    else:
        oh, ow = height, width
    row, outrow = (width+31)//32*32, (ow+31)//32*32
    registers = [op, 0x80100000, 0x80140000, 0x80180000, 0x80001000, 0x80011000,
                 width | height << 16, cin | cout << 16, ow | oh << 16, row | outrow << 16,
                 row*height, outrow*oh, kernel | kernel << 4 | stride << 8 | stride << 12 | padding << 16 | padding << 20,
                 za | zy << 8 | zb << 16, 201326592, 469762048, 29]
    weights = rng.integers(-128, 128, (cout, 1 if op == 2 else cin, kernel, kernel), dtype=np.int8)
    parameters = [(int(rng.integers(-50000, 50000)), int(rng.integers(1 << 27, 1 << 30)),
                   int(rng.integers(27, 35)), 0, 255) for _ in range(cout)]
    return case(directory, name, registers, source, second, weights, parameters, error, fault)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", type=Path, default=Path("/home/yongshi/fpga-sim/int8_engine"))
    parser.add_argument("--cases", type=Path, default=ROOT / "build/int8_tests")
    parser.add_argument("--models", type=Path, default=ROOT / "build/vision/fpga")
    parser.add_argument("--mac-pixels", type=int, choices=[16, 32], default=16)
    args = parser.parse_args()
    args.build.mkdir(parents=True, exist_ok=True)
    args.cases.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(261001)
    entries = []
    specs = [
        ("pointwise_tail", 3, 3, 37, 17, 1, 1, 0, 1),
        ("spatial_stride2", 3, 9, 67, 17, 3, 2, 1, 1),
        ("spatial_kernel7", 3, 9, 37, 17, 7, 1, 3, 1),
        ("maximum_reduction", 64, 3, 35, 32, 3, 1, 1, 1),
        ("maximum_weights", 512, 1, 1, 128, 1, 1, 0, 1),
        ("depthwise_tail", 17, 7, 65, 17, 3, 1, 1, 2),
        ("depthwise_stride2", 3, 9, 67, 3, 3, 2, 1, 2),
        ("depthwise_global7", 17, 7, 7, 17, 7, 1, 0, 2),
        ("residual_zeros", 17, 5, 67, 17, 1, 1, 0, 3),
        ("pool_odd_width", 3, 7, 67, 3, 2, 2, 0, 4),
        ("resize_tail", 3, 5, 35, 3, 1, 2, 0, 5),
    ]
    for name, cin, height, width, cout, kernel, stride, padding, op in specs:
        entries.append(random_case(args.cases, name, rng, cin, height, width, cout, kernel, stride, padding, op))
    entries.append(random_case(args.cases, "unsupported_kernel2", rng, 3, 5, 35, 17, 2, 1, 0, 1, 1))
    for name, fault in (("ddr_read_fault", 1), ("ddr_write_fault", 2)):
        entries.append(random_case(args.cases, name, rng, 3, 5, 35, 17, 3, 1, 1, 1, 2, fault))
    if args.models.exists():
        for name in ("yunet", "mobilefacenet"):
            model = json.loads((args.models / f"{name}.json").read_text())
            references = np.load(args.models / f"{name}_reference.npz")
            image = (args.models / f"{name}_image.bin").read_bytes()
            tensor_map = {tensor["name"]: tensor for tensor in model["tensors"]}
            selected = [model["layers"][0], model["layers"][-1]]
            if name == "mobilefacenet":
                selected.append(next(layer for layer in model["layers"] if layer["op"] == 2 and layer["registers"][12] & 15 == 7))
            for layer in selected:
                registers = layer["registers"]
                source = references[layer["source"]]
                cout = registers[7] >> 16
                kernel = registers[12] & 15
                reduction = (1 if layer["op"] == 2 else source.shape[1])*kernel*kernel
                offset = registers[4]-model["image_address"]
                packed_weights = np.frombuffer(image[offset:offset+((cout+15)//16)*reduction*16], np.int8)
                weights = packed_weights.reshape(-1, reduction, 16).transpose(0, 2, 1).reshape(-1, reduction)[:cout]
                weights = weights.reshape(cout, 1 if layer["op"] == 2 else source.shape[1], kernel, kernel)
                offset = registers[5]-model["image_address"]
                parameters = [struct.unpack_from("<iiBBBBI", image, offset+channel*16)[:5] for channel in range(cout)]
                entries.append(case(args.cases, f"{name}_layer_{layer['index']}", registers, source, None, weights, parameters))
    rtl = [ROOT / "rtl" / name for name in ("int8_mac_array.v", "int8_requantize.v", "int8_accelerator.v")]
    command = ["verilator", "--cc", "--exe", "--build", "-j", "2", "-Wall", "--Wno-WIDTH", "--Wno-UNUSED",
               "--Wno-DECLFILENAME", "--Wno-SYNCASYNCNET", "--top-module", "int8_accelerator",
               "--Mdir", str(args.build), f"-GMAC_PIXELS={args.mac_pixels}", *map(str, rtl), str(ROOT / "tb/sim_int8.cpp")]
    with (args.build / "compile.log").open("w") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    for entry in entries:
        result = subprocess.run([str(args.build / "Vint8_accelerator"), str((args.cases / entry["name"]).resolve())],
                                capture_output=True, text=True)
        (args.cases / entry["name"] / "simulation.log").write_text(result.stdout+result.stderr)
        if result.returncode:
            raise RuntimeError(f"{entry['name']}: {result.stdout}{result.stderr}")
        metrics = re.search(r"cycles=(\d+) reads=(\d+) writes=(\d+) compute=(\d+) error=(\d+)", result.stdout)
        entry["cycles"], entry["read_transactions"], entry["write_transactions"], entry["compute_cycles"], _ = map(int, metrics.groups())
        print(entry["name"]+": "+result.stdout.strip(), flush=True)
    (args.cases / "results.json").write_text(json.dumps({"reference": "PyTorch float64 convolution, exact integer products/sums; rational ties-to-even quantization",
        "rtl_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in rtl},
        "memory": "64 MiB, randomized request and response delays, complete byte comparisons",
        "mac_pixels": args.mac_pixels, "logical_tile_pixels": 32,
        "cases": entries}, indent=2)+"\n")


if __name__ == "__main__":
    main()
