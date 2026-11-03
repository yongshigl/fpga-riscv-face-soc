#!/usr/bin/env python3
"""Check the on-board YuNet decode/NMS port against the host reference.

The firmware decoder (sw/vision_yunet.c) is compiled for the host with a memory
shim, fed the same quantized tensors the board holds in DDR, and compared with
fpga_model.decode_yunet plus the alignment coefficients the board applies.
"""
import json
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from fpga_model import Model, decode_yunet, detector_input
from face_pipeline import ALIGNMENT_POINTS, read_image

ROOT = Path(__file__).resolve().parents[1]
MODELS = Path("/mnt/d/FPGA竞赛/上板闭环验收_20261024_1853/validation_models")
SOURCES = [ROOT / "sw/vision_yunet.c", ROOT / "vision/yunet_host_driver.c"]
FRAME = (720, 1280, 3)  # the frame size the console reports boxes in


def build_driver(directory, compiler="gcc"):
    binary = directory / "yunet_host_driver"
    subprocess.run([compiler, "-O2", "-Wall", "-Wextra", "-Werror", "-std=c99",
                    "-DVISION_YUNET_HOST_TEST", "-I", str(ROOT / "sw"),
                    "-o", str(binary), *map(str, SOURCES)], check=True)
    return binary


def ddr_heap(model, tensors):
    """Lay the weight image and the quantized outputs out the way DDR holds them."""
    base = model.manifest["image_address"]
    records = model.manifest["tensors"]
    end = max(record["address"] + record["bytes"] for record in records) - base
    end = max(end, len(model.image))
    heap = bytearray(end + 64)
    heap[:len(model.image)] = model.image
    for record in records:
        offset = record["address"] - base
        values = tensors.get(record["name"])
        if values is None:
            continue
        _, channels, height, width = record["shape"]
        values = np.asarray(values).reshape(1, channels, height, width)
        row_stride = record["row_stride"]
        plane_stride = record["plane_stride"]
        for channel in range(channels):
            for row in range(height):
                start = offset + channel * plane_stride + row * row_stride
                heap[start:start + width] = values[0, channel, row].astype(np.uint8).tobytes()
    return bytes(heap[:end]), base, end


def run_decoder(binary, heap, base, size, threshold=0.7, nms=0.3, directory=None):
    directory = directory or Path(tempfile.mkdtemp())
    heap_path = directory / "heap.bin"
    heap_path.write_bytes(heap)
    output = directory / "faces.json"
    subprocess.run([str(binary), str(heap_path), hex(base), str(size[1]), str(size[0]),
                    str(int(round(threshold * 65536))), str(int(round(nms * 65536))),
                    str(len(heap)), str(output)], check=True)
    return json.loads(output.read_text())


class OnboardDecodeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = Path(tempfile.mkdtemp())
        cls.binary = build_driver(cls.directory)
        cls.model = Model(MODELS, "yunet")
        cls.samples = {}
        for name in ("obama.jpg", "obama2.jpg", "biden.jpg", "astronaut.png"):
            image = read_image(ROOT / "vision/test_images" / name)
            cls.samples[name] = cls.model.input_bytes(detector_input(image))

    def reference(self, input_bytes):
        tensors, outputs, _ = self.model.reference(input_bytes)
        return tensors, decode_yunet(outputs, FRAME)

    def test_decode_matches_host(self):
        for name, input_bytes in self.samples.items():
            with self.subTest(image=name):
                tensors, faces = self.reference(input_bytes)
                heap, base, size = ddr_heap(self.model, tensors)
                result = run_decoder(self.binary, heap, base, FRAME[:2], directory=self.directory)
                self.assertEqual(result["dropped"], 0)
                self.assertEqual(result["count"], len(faces),
                                 f"{name}: board kept {result['count']} faces, host {len(faces)}")
                if not len(faces):
                    continue
                for board, host in zip(result["faces"], faces):
                    box = np.array([board["x"], board["y"], board["w"], board["h"]], dtype=np.float64) / 256
                    self.assertLessEqual(np.max(np.abs(box - host[:4])), 1.5, f"{name}: box {box} vs {host[:4]}")
                    points = np.array(board["keypoints"], dtype=np.float64).reshape(5, 2) / 256
                    self.assertLessEqual(np.max(np.abs(points - host[4:14].reshape(5, 2))), 1.5,
                                         f"{name}: keypoints")
                    self.assertLessEqual(abs(board["score"] / 65536 - host[14]), 1e-3, f"{name}: score")

    def test_alignment_matches_host(self):
        for name, input_bytes in self.samples.items():
            with self.subTest(image=name):
                tensors, faces = self.reference(input_bytes)
                if not len(faces):
                    continue
                heap, base, size = ddr_heap(self.model, tensors)
                result = run_decoder(self.binary, heap, base, FRAME[:2], directory=self.directory)
                image = read_image(ROOT / "vision/test_images" / name)
                for board, host in zip(result["faces"], faces):
                    matrix, _ = cv2.estimateAffinePartial2D(
                        host[4:14].reshape(5, 2).astype(np.float32), ALIGNMENT_POINTS, method=cv2.LMEDS)
                    self.assertIsNotNone(matrix)
                    host_inverse = cv2.invertAffineTransform(matrix).reshape(-1)
                    board_inverse = np.array(board["affine"], dtype=np.float64) / 65536
                    # The board fits the similarity by least squares, the host by
                    # LMEDS, so compare the coefficients relatively.
                    np.testing.assert_allclose(board_inverse, host_inverse, rtol=5e-3, atol=2.0,
                                               err_msg=f"{name}: affine coefficients differ")
                    # The warp itself must agree to a fraction of a pixel over 112x112.
                    grid = np.array([[x, y] for y in range(0, 112, 16) for x in range(0, 112, 16)],
                                    dtype=np.float32).reshape(-1, 1, 2)
                    host_points = cv2.transform(grid, host_inverse.reshape(2, 3)).reshape(-1, 2)
                    board_matrix = np.array([[board_inverse[0], board_inverse[1], board_inverse[2]],
                                             [board_inverse[3], board_inverse[4], board_inverse[5]]])
                    board_points = cv2.transform(grid, board_matrix.reshape(2, 3)).reshape(-1, 2)
                    self.assertLessEqual(np.max(np.abs(host_points - board_points)),
                                         max(1.5, 0.01 * np.max(np.abs(host_points))),
                                         f"{name}: warp deviates")

    def test_threshold_is_honoured(self):
        input_bytes = self.samples["obama2.jpg"]
        tensors, faces = self.reference(input_bytes)
        heap, base, size = ddr_heap(self.model, tensors)
        relaxed = run_decoder(self.binary, heap, base, FRAME[:2], threshold=0.5, directory=self.directory)
        self.assertGreaterEqual(relaxed["count"], len(faces))
        strict = run_decoder(self.binary, heap, base, FRAME[:2], threshold=0.99, directory=self.directory)
        self.assertEqual(strict["count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
