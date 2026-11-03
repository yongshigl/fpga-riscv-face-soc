"""FPGA DDR model layout, independent integer reference and face output decoding."""
import hashlib
import json
import struct
import time
from pathlib import Path

import cv2
import numpy as np

from face_pipeline import ALIGNMENT_POINTS, letterbox


def round_even(values, shift):
    values = np.asarray(values, dtype=np.int64)
    magnitude = np.abs(values).astype(np.uint64)
    divisor = np.uint64(1 << int(shift))
    whole, fraction = magnitude // divisor, magnitude % divisor
    increment = ((fraction > divisor // 2) | ((fraction == divisor // 2) & ((whole & 1) != 0))) if shift else 0
    rounded = (whole + increment).astype(np.int64)
    return np.where(values < 0, -rounded, rounded)


def unpack_tensor(data, tensor):
    _, channels, height, width = tensor["shape"]
    if len(data) != tensor["bytes"]:
        raise ValueError("Tensor data length differs from the DDR descriptor")
    return np.frombuffer(data, dtype=np.uint8).reshape(1, channels, height, tensor["row_stride"])[..., :width].copy()


def pack_tensor(values, tensor, quantized=False):
    values = np.asarray(values)
    if tuple(values.shape) != tuple(tensor["shape"]) or not np.all(np.isfinite(values)):
        raise ValueError("Model input shape or values are invalid")
    if not quantized:
        values = np.clip(np.rint(values / tensor["scale"]) + tensor["zero"], 0, 255).astype(np.uint8)
    _, channels, height, width = tensor["shape"]
    packed = np.full((1, channels, height, tensor["row_stride"]), tensor["zero"], dtype=np.uint8)
    packed[..., :width] = values
    return packed.tobytes()


def output_view(quantized, output):
    tensor = output["tensor"]
    values = (quantized.astype(np.float32) - tensor["zero"]) * tensor["scale"]
    for step in output["steps"]:
        if step["op"] == "transpose":
            values = values.transpose(step["perm"])
        elif step["op"] == "reshape":
            values = values.reshape(step["shape"])
        elif step["op"] == "sigmoid":
            values = 1 / (1 + np.exp(-np.clip(values, -80, 80)))
        else:
            raise ValueError(f"Unknown model output view: {step['op']}")
    if tuple(values.shape) != tuple(output["shape"]) or not np.all(np.isfinite(values)):
        raise ValueError("Model output view is invalid")
    return values


class Model:
    def __init__(self, directory, name):
        self.directory, self.name = Path(directory), name
        self.manifest = json.loads((self.directory / f"{name}.json").read_text())
        self.image = (self.directory / f"{name}_image.bin").read_bytes()
        if len(self.image) != self.manifest["image_bytes"] or hashlib.sha256(self.image).hexdigest() != self.manifest["image_sha256"]:
            raise ValueError("Exported FPGA model image hash differs")
        self.tensors = {tensor["name"]: tensor for tensor in self.manifest["tensors"]}
        self._reference_constants = {}

    def upload(self, board, verify=True, progress=None):
        milliseconds = board.upload(self.manifest["image_address"], self.image, progress)
        if verify and board.download(self.manifest["image_address"], len(self.image)) != self.image:
            raise RuntimeError(f"{self.name}: model DDR readback differs from the exported image")
        return milliseconds

    def input_bytes(self, values):
        return pack_tensor(values, self.manifest["input"])

    def read_outputs(self, board):
        tensors, outputs = {}, {}
        self.last_output_read_stats = []
        for output in self.manifest["outputs"]:
            tensor = output["tensor"]
            if tensor["name"] not in tensors:
                if hasattr(board, "download_tensor"):
                    data = board.download_tensor(tensor)
                    tensors[tensor["name"]] = np.frombuffer(data, np.uint8).reshape(tensor["shape"]).copy()
                    self.last_output_read_stats.append({"tensor": tensor["name"], **board.last_download_stats})
                else:
                    tensors[tensor["name"]] = unpack_tensor(board.download(tensor["address"], tensor["bytes"]), tensor)
            outputs[output["name"]] = output_view(tensors[tensor["name"]], output)
        return outputs

    def reference(self, input_bytes, progress=None, source_overrides=None, force_float64=False):
        import torch
        import torch.nn.functional as functional
        torch.set_num_threads(2)
        tensors = {self.manifest["input"]["name"]: unpack_tensor(input_bytes, self.manifest["input"])}
        start = time.monotonic()
        for layer in self.manifest["layers"]:
            cfg = layer["registers"]
            source = source_overrides[layer["source"]] if source_overrides is not None else tensors[layer["source"]]
            op, cout = layer["op"], cfg[7] >> 16
            za, zy, zb = cfg[13] & 255, (cfg[13] >> 8) & 255, (cfg[13] >> 16) & 255
            if op in (1, 2):
                kernel, stride, padding = cfg[12] & 15, (cfg[12] >> 8) & 15, (cfg[12] >> 16) & 15
                key = (layer["index"], force_float64)
                if key not in self._reference_constants:
                    reduction = (1 if op == 2 else source.shape[1]) * kernel * kernel
                    blocks = (cout + 15) // 16
                    offset = cfg[4] - self.manifest["image_address"]
                    weights = np.frombuffer(self.image[offset:offset + blocks * reduction * 16], dtype=np.int8)
                    weights = weights.reshape(blocks, reduction, 16).transpose(0, 2, 1).reshape(-1, reduction)[:cout]
                    weights = weights.reshape(cout, 1 if op == 2 else source.shape[1], kernel, kernel)
                    offset = cfg[5] - self.manifest["image_address"]
                    params = [struct.unpack_from("<iiBBBBI", self.image, offset + channel * 16)[:5] for channel in range(cout)]
                    bound = np.sum(np.abs(weights.astype(np.int64)).reshape(cout, -1), axis=1) * max(za, 255 - za)
                    bound += np.abs(np.asarray([record[0] for record in params], dtype=np.int64))
                    # Every product and partial sum is an exactly representable
                    # integer below 2^24; larger bounds use float64 instead.
                    dtype = np.float32 if not force_float64 and np.max(bound) < (1 << 24) else np.float64
                    self._reference_constants[key] = (torch.from_numpy(weights.astype(dtype)),
                        torch.tensor([record[0] for record in params], dtype=torch.float32 if dtype == np.float32 else torch.float64),
                        params, dtype)
                weights, bias, params, dtype = self._reference_constants[key]
                sums = functional.conv2d(torch.from_numpy(source.astype(dtype) - za), weights, bias,
                                         stride=stride, padding=padding, groups=source.shape[1] if op == 2 else 1).numpy().astype(np.int64)
                target = np.empty(sums.shape, dtype=np.uint8)
                for channel, (_, multiplier, shift, low, high) in enumerate(params):
                    target[:, channel] = np.clip(round_even(sums[:, channel] * multiplier, shift) + zy, low, high).astype(np.uint8)
            elif op == 3:
                second_name = next(tensor["name"] for tensor in self.manifest["tensors"] if tensor["address"] == cfg[2])
                second = source_overrides[second_name] if source_overrides is not None else tensors[second_name]
                numerator = (source.astype(np.int64) - za) * cfg[14] + (second.astype(np.int64) - zb) * cfg[15]
                target = np.clip(round_even(numerator, cfg[16]) + zy, 0, 255).astype(np.uint8)
            elif op == 4:
                target = functional.max_pool2d(torch.from_numpy(source.astype(np.float32)), 2, 2).numpy().astype(np.uint8)
            elif op == 5:
                target = np.repeat(np.repeat(source, 2, axis=2), 2, axis=3)
            else:
                raise ValueError(f"Unsupported FPGA operation {op}")
            if tuple(target.shape) != tuple(layer["output"]["shape"]):
                raise RuntimeError("Independent reference produced an invalid layer shape")
            tensors[layer["target"]] = target
            if progress:
                progress(layer["index"], len(self.manifest["layers"]))
        outputs = {output["name"]: output_view(tensors[output["tensor"]["name"]], output)
                   for output in self.manifest["outputs"]}
        return tensors, outputs, (time.monotonic() - start) * 1000


def decode_yunet(outputs, image_shape, score_threshold=0.7, nms_threshold=0.3):
    height, width = image_shape[:2]
    scaled_width, scaled_height = max(1, min(320, round(width * min(320 / width, 192 / height)))), max(1, min(192, round(height * min(320 / width, 192 / height))))
    scale_x, scale_y = scaled_width / width, scaled_height / height
    candidates = []
    for stride in (8, 16, 32):
        columns, rows = 320 // stride, 192 // stride
        cls = outputs[f"cls_{stride}"].reshape(-1)
        obj = outputs[f"obj_{stride}"].reshape(-1)
        bbox = outputs[f"bbox_{stride}"].reshape(-1, 4)
        kps = outputs[f"kps_{stride}"].reshape(-1, 10)
        if cls.size != columns * rows or obj.size != cls.size:
            raise ValueError("YuNet detection grid size differs")
        scores = np.sqrt(np.clip(cls, 0, 1) * np.clip(obj, 0, 1))
        for index in np.flatnonzero(scores >= score_threshold):
            row, column = divmod(int(index), columns)
            center_x, center_y = (column + bbox[index, 0]) * stride, (row + bbox[index, 1]) * stride
            box_width, box_height = np.exp(np.clip(bbox[index, 2:4], -20, 20)) * stride
            points = kps[index].copy().reshape(5, 2)
            points[:, 0] = (points[:, 0] + column) * stride / scale_x
            points[:, 1] = (points[:, 1] + row) * stride / scale_y
            candidates.append([(center_x - box_width / 2) / scale_x, (center_y - box_height / 2) / scale_y,
                               box_width / scale_x, box_height / scale_y, *points.reshape(-1), scores[index]])
    if not candidates:
        return np.empty((0, 15), dtype=np.float32)
    faces = np.asarray(candidates, dtype=np.float32)
    keep = cv2.dnn.NMSBoxes(faces[:, :4].tolist(), faces[:, 14].tolist(), score_threshold, nms_threshold, top_k=5000)
    return faces[np.asarray(keep, dtype=np.int32).reshape(-1)]


def align_face(image, face):
    matrix, _ = cv2.estimateAffinePartial2D(np.asarray(face[4:14], dtype=np.float32).reshape(5, 2),
                                         ALIGNMENT_POINTS, method=cv2.LMEDS)
    if matrix is None or not np.all(np.isfinite(matrix)):
        raise ValueError("Cannot align the detected face")
    return cv2.warpAffine(image, matrix, (112, 112), flags=cv2.INTER_LINEAR)


def detector_input(image):
    padded, _ = letterbox(image, (320, 192))
    return np.ascontiguousarray(padded.transpose(2, 0, 1)[None], dtype=np.float32)


def embedding_input(aligned):
    return np.ascontiguousarray(((aligned.astype(np.float32) - 127.5) / 128.0).transpose(2, 0, 1)[None])
