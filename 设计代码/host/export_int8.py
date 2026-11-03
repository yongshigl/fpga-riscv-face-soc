#!/usr/bin/env python3
"""Compile ONNX QOperator face graphs into the FPGA layer and DDR format."""
import argparse
import hashlib
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
from onnx import numpy_helper


def align(value, boundary=32):
    return (value + boundary - 1) // boundary * boundary


def fixed_multiplier(value):
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Invalid requantization scale")
    fraction, exponent = math.frexp(value)
    coefficient = round(fraction * (1 << 31))
    if coefficient == 1 << 31:
        coefficient >>= 1
        exponent += 1
    shift = 31 - exponent
    if not 0 <= shift <= 63:
        raise ValueError("Requantization scale is outside the hardware range")
    return coefficient, shift


def add_multipliers(first, second):
    largest = max(first, second)
    shift = min(30, 30 - math.ceil(math.log2(largest)))
    if shift < 0:
        raise ValueError("Residual scale exceeds the hardware range")
    return round(first * (1 << shift)), round(second * (1 << shift)), shift


@dataclass
class Tensor:
    name: str
    shape: tuple
    scale: float
    zero: int
    address: int
    row: int
    plane: int

    @property
    def size(self):
        return self.shape[1] * self.plane

    def record(self):
        return {"name": self.name, "shape": list(self.shape), "scale": self.scale,
                "zero": self.zero, "address": self.address, "row_stride": self.row,
                "plane_stride": self.plane, "bytes": self.size}


class Exporter:
    def __init__(self, path, image_base, tensor_base):
        self.path = path
        self.model = onnx.load(path)
        self.constants = {value.name: numpy_helper.to_array(value) for value in self.model.graph.initializer}
        self.image_base, self.tensor_next = image_base, tensor_base
        self.image = bytearray(align(64 + 64 * len(self.model.graph.output)))
        self.quantized, self.views, self.tensors, self.layers = {}, {}, [], []
        self.input_tensor = None
        self.input_shape = tuple(axis.dim_value for axis in self.model.graph.input[0].type.tensor_type.shape.dim)
        if len(self.input_shape) != 4 or self.input_shape[0] != 1:
            raise ValueError("Only batch-one NCHW inputs are supported")
        self.quantizers = {}
        for node in self.model.graph.node:
            if node.op_type == "QuantizeLinear":
                if node.input[0] in self.quantizers:
                    raise ValueError("A float island has multiple quantization consumers")
                self.quantizers[node.input[0]] = node

    def scalar(self, name):
        value = self.constants[name]
        if value.size != 1:
            raise ValueError(f"Expected a scalar: {name}")
        return value.reshape(-1)[0].item()

    def attributes(self, node):
        return {attr.name: onnx.helper.get_attribute_value(attr) for attr in node.attribute}

    def blob(self, data):
        address = self.image_base + len(self.image)
        self.image.extend(data)
        self.image.extend(bytes(align(len(self.image)) - len(self.image)))
        return address

    def tensor(self, name, shape, scale, zero):
        if len(shape) != 4 or shape[0] != 1 or not all(0 < axis <= 640 for axis in shape[1:]):
            raise ValueError(f"Unsupported tensor shape: {shape}")
        if not 0 <= zero <= 255 or scale <= 0:
            raise ValueError("Activations must have a positive scale and U8 zero point")
        row = align(shape[3])
        value = Tensor(name, tuple(shape), scale, zero, self.tensor_next, row, row * shape[2])
        self.tensor_next += value.size
        self.tensors.append(value)
        self.quantized[name] = value
        return value

    def validate_input(self, tensor, scale, zero):
        if tensor.scale != scale or tensor.zero != zero:
            raise ValueError(f"Mismatched quantization metadata: {tensor.name}")

    def layer(self, op, source, target, kernel=(1, 1), stride=(1, 1), padding=(0, 0),
              second=None, weights=0, params=0, coefficients=(0, 0, 0), node=None):
        cin, cout = source.shape[1], target.shape[1]
        geometry = kernel[1] | kernel[0] << 4 | stride[1] << 8 | stride[0] << 12 | padding[1] << 16 | padding[0] << 20
        registers = [op, source.address, second.address if second else 0, target.address,
                     weights, params, source.shape[3] | source.shape[2] << 16,
                     cin | cout << 16, target.shape[3] | target.shape[2] << 16,
                     source.row | target.row << 16, source.plane, target.plane,
                     geometry, source.zero | target.zero << 8 | (second.zero if second else 0) << 16,
                     coefficients[0], coefficients[1], coefficients[2]]
        self.layers.append({"index": len(self.layers), "onnx_node": node.name if node else "",
                            "op": op, "source": source.name, "target": target.name,
                            "registers": registers, "output": target.record()})

    def conv(self, node):
        source = self.quantized[node.input[0]]
        self.validate_input(source, self.scalar(node.input[1]), self.scalar(node.input[2]))
        weight = self.constants[node.input[3]]
        if weight.dtype != np.int8 or np.any(self.constants[node.input[5]] != 0):
            raise ValueError("Weights must be symmetric signed INT8")
        attrs = self.attributes(node)
        group, cin, cout = attrs.get("group", 1), source.shape[1], weight.shape[0]
        depthwise = group == cin and weight.shape[1] == 1 and cout == cin
        if group != 1 and not depthwise:
            raise ValueError("Only ordinary and multiplier-one depthwise convolution are supported")
        kh, kw = weight.shape[2:]
        sy, sx = attrs.get("strides", [1, 1])
        py, px, bottom, right = attrs.get("pads", [0, 0, 0, 0])
        if attrs.get("dilations", [1, 1]) != [1, 1] or (py, px) != (bottom, right):
            raise ValueError("Dilated or asymmetric padding is not supported")
        if kh not in (1, 3, 7) or kw != kh or sy not in (1, 2) or sx != sy:
            raise ValueError("Unsupported convolution geometry")
        reduction = weight.shape[1] * kh * kw
        blocks = (cout + 15) // 16
        if cin > 512 or cout > 512 or reduction > 576 or blocks * reduction > 4096:
            raise ValueError("Convolution exceeds the tile memories")
        shape = (1, cout, (source.shape[2]+py+bottom-kh)//sy+1, (source.shape[3]+px+right-kw)//sx+1)
        output_scale, output_zero = self.scalar(node.input[6]), self.scalar(node.input[7])
        target = self.tensor(node.output[0], shape, output_scale, output_zero)
        flat = weight.reshape(cout, reduction)
        padded = np.pad(flat, ((0, blocks*16-cout), (0, 0)))
        packed = np.ascontiguousarray(padded.reshape(blocks, 16, reduction).transpose(0, 2, 1))
        weight_address = self.blob(packed.tobytes())
        scales = self.constants[node.input[4]].reshape(-1)
        if scales.size == 1:
            scales = np.repeat(scales, cout)
        bias = self.constants[node.input[8]].reshape(-1) if len(node.input) == 9 else np.zeros(cout, dtype=np.int32)
        parameters = bytearray()
        for channel in range(cout):
            if abs(int(bias[channel])) + reduction*255*128 >= 1 << 31:
                raise ValueError("Convolution cannot guarantee an INT32 accumulator")
            multiplier, shift = fixed_multiplier(source.scale * float(scales[channel]) / output_scale)
            parameters.extend(struct.pack("<iiBBBBI", int(bias[channel]), multiplier, shift, 0, 255, 0, 0))
        parameter_address = self.blob(parameters)
        self.layer(2 if depthwise else 1, source, target, (kh, kw), (sy, sx), (py, px),
                   weights=weight_address, params=parameter_address, node=node)

    def residual(self, node):
        first, second = self.quantized[node.input[0]], self.quantized[node.input[3]]
        self.validate_input(first, self.scalar(node.input[1]), self.scalar(node.input[2]))
        self.validate_input(second, self.scalar(node.input[4]), self.scalar(node.input[5]))
        if first.shape != second.shape:
            raise ValueError("Broadcast residual addition is not supported")
        target = self.tensor(node.output[0], first.shape, self.scalar(node.input[6]), self.scalar(node.input[7]))
        coefficients = add_multipliers(first.scale/target.scale, second.scale/target.scale)
        self.layer(3, first, target, second=second, coefficients=coefficients, node=node)

    def spatial(self, node):
        view = self.views[node.input[0]]
        if view["steps"]:
            raise ValueError("Spatial operators require the original NCHW layout")
        source = view["tensor"]
        attrs, quantizer = self.attributes(node), self.quantizers[node.output[0]]
        scale, zero = self.scalar(quantizer.input[1]), self.scalar(quantizer.input[2])
        if scale != source.scale or zero != source.zero:
            raise ValueError("Pool/Resize must preserve activation quantization")
        if node.op_type == "MaxPool":
            kh, kw = attrs["kernel_shape"]
            sy, sx = attrs["strides"]
            if (kh, kw, sy, sx) != (2, 2, 2, 2) or attrs.get("ceil_mode", 0) or any(attrs.get("pads", [0, 0, 0, 0])):
                raise ValueError("Only unpadded 2x2 stride-two MaxPool is supported")
            shape = (1, source.shape[1], source.shape[2]//2, source.shape[3]//2)
            geometry, op = ((kh, kw), (sy, sx)), 4
        else:
            factors = self.constants[node.input[2]].reshape(-1)
            if list(factors) != [1, 1, 2, 2] or attrs.get("mode") != b"nearest" or attrs.get("coordinate_transformation_mode") != b"asymmetric" or attrs.get("nearest_mode") != b"floor":
                raise ValueError("Only asymmetric nearest-neighbor 2x Resize is supported")
            shape = (1, source.shape[1], source.shape[2]*2, source.shape[3]*2)
            geometry, op = ((1, 1), (2, 2)), 5
        target = self.tensor(quantizer.output[0], shape, scale, zero)
        self.views[node.output[0]] = {"tensor": target, "steps": [], "shape": shape}
        self.layer(op, source, target, *geometry, node=node)

    def compile(self):
        for node in self.model.graph.node:
            if node.op_type == "QLinearConv":
                self.conv(node)
            elif node.op_type == "QLinearAdd":
                self.residual(node)
            elif node.op_type == "QuantizeLinear":
                if node.input[0] == self.model.graph.input[0].name:
                    self.input_tensor = self.tensor(node.output[0], self.input_shape,
                                                    self.scalar(node.input[1]), self.scalar(node.input[2]))
                else:
                    value = self.views[node.input[0]]["tensor"]
                    self.validate_input(value, self.scalar(node.input[1]), self.scalar(node.input[2]))
                    self.quantized[node.output[0]] = value
            elif node.op_type == "DequantizeLinear":
                value = self.quantized[node.input[0]]
                self.validate_input(value, self.scalar(node.input[1]), self.scalar(node.input[2]))
                self.views[node.output[0]] = {"tensor": value, "steps": [], "shape": value.shape}
            elif node.op_type in ("MaxPool", "Resize"):
                self.spatial(node)
            elif node.op_type == "Transpose":
                view = self.views[node.input[0]]
                perm = self.attributes(node)["perm"]
                self.views[node.output[0]] = {"tensor": view["tensor"], "shape": tuple(view["shape"][i] for i in perm),
                                             "steps": view["steps"] + [{"op": "transpose", "perm": perm}]}
            elif node.op_type == "Reshape":
                view = self.views[node.input[0]]
                shape = self.constants[node.input[1]].reshape(-1).tolist()
                dimensions = [view["shape"][i] if size == 0 else size for i, size in enumerate(shape)]
                if -1 in dimensions:
                    known = math.prod(size for size in dimensions if size != -1)
                    dimensions[dimensions.index(-1)] = math.prod(view["shape"]) // known
                if math.prod(dimensions) != math.prod(view["shape"]):
                    raise ValueError("Reshape changes tensor element count")
                self.views[node.output[0]] = {"tensor": view["tensor"], "shape": tuple(dimensions),
                                             "steps": view["steps"] + [{"op": "reshape", "shape": dimensions}]}
            elif node.op_type == "Sigmoid":
                view = self.views[node.input[0]]
                self.views[node.output[0]] = {**view, "steps": view["steps"] + [{"op": "sigmoid"}]}
            elif node.op_type == "Constant":
                self.constants[node.output[0]] = numpy_helper.to_array(self.attributes(node)["value"])
            else:
                raise ValueError(f"Unsupported quantized operator: {node.op_type}")
        outputs = []
        for index, value in enumerate(self.model.graph.output):
            view = self.views[value.name]
            tensor = view["tensor"]
            outputs.append({"name": value.name, "tensor": tensor.record(), "shape": list(view["shape"]), "steps": view["steps"]})
            scale_bits = struct.unpack("<I", struct.pack("<f", tensor.scale))[0]
            flags = int(any(step["op"] == "sigmoid" for step in view["steps"]))
            record = [tensor.address, tensor.shape[1], tensor.shape[2], tensor.shape[3],
                      tensor.row, tensor.plane, scale_bits, tensor.zero, flags, 0, 0, 0, 0, 0, 0, 0]
            struct.pack_into("<16I", self.image, 64+index*64, *record)
        descriptors = bytearray()
        for layer in self.layers:
            descriptor = layer["registers"] + [layer["output"]["bytes"], layer["index"]] + [0]*5
            descriptors.extend(struct.pack("<24I", *descriptor))
        descriptor_address = self.blob(descriptors)
        first = self.input_tensor
        header = [0x4e4e4350, 1, len(self.layers), descriptor_address, first.address,
                  first.shape[1], first.shape[2], first.shape[3], first.row, first.plane,
                  self.image_base+64, len(outputs), len(self.tensors), 0, 0, 0]
        struct.pack_into("<16I", self.image, 0, *header)
        if len(self.image) > 4*1024*1024 or self.tensor_next-first.address > 16*1024*1024:
            raise ValueError("Model exceeds its assigned DDR arena")
        return {"format": "PCNN-v1; 17 MMIO registers per 96-byte CPU descriptor", "model": self.path.name,
                "model_sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
                "image_address": self.image_base, "image_bytes": len(self.image),
                "image_sha256": hashlib.sha256(self.image).hexdigest(),
                "descriptor_address": descriptor_address, "layers": self.layers,
                "input": first.record(), "outputs": outputs,
                "tensors": [tensor.record() for tensor in self.tensors],
                "tensor_arena_bytes": self.tensor_next-first.address,
                "hardware_convolutions": sum(layer["op"] in (1, 2) for layer in self.layers),
                "postprocessing": "Dequantization, tensor views, sigmoid, detection decoding and embedding normalization follow the hardware tensors"}


def pack_input(tensor, values):
    value = np.clip(np.rint(values / tensor.scale) + tensor.zero, 0, 255).astype(np.uint8)
    packed = np.full((tensor.shape[1], tensor.shape[2], tensor.row), tensor.zero, np.uint8)
    packed[:, :, :tensor.shape[3]] = value[0]
    return packed.tobytes()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    calibration = np.load(args.models / "calibration_inputs.npz")
    for index, (name, input_name) in enumerate((("yunet", "yunet"), ("mobilefacenet", "mobilefacenet"))):
        exporter = Exporter(args.models / f"{name}_u8s8.onnx", 0x80000000+index*0x400000, 0x81000000+index*0x1000000)
        manifest = exporter.compile()
        (args.output / f"{name}_image.bin").write_bytes(exporter.image)
        inputs = calibration[input_name][0:1]
        packed = pack_input(exporter.input_tensor, inputs)
        (args.output / f"{name}_input.bin").write_bytes(packed)
        manifest["sample_input_sha256"] = hashlib.sha256(packed).hexdigest()
        manifest["sample_input_calibration_only"] = True
        if args.reference:
            inspected = onnx.load(exporter.path)
            for tensor in exporter.tensors:
                inspected.graph.output.append(onnx.helper.make_tensor_value_info(tensor.name, onnx.TensorProto.UINT8, tensor.shape))
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            session = ort.InferenceSession(inspected.SerializeToString(), options, providers=["CPUExecutionProvider"])
            values = session.run(None, {session.get_inputs()[0].name: inputs})
            names = [value.name for value in session.get_outputs()]
            np.savez_compressed(args.output / f"{name}_reference.npz", **dict(zip(names, values)))
            del session
        (args.output / f"{name}.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="ascii")
        print(f"{name}: {len(exporter.layers)} hardware layers, {manifest['hardware_convolutions']} convolutions; "
              f"image {len(exporter.image)} B, tensor arena {manifest['tensor_arena_bytes']} B", flush=True)


if __name__ == "__main__":
    main()
