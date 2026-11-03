#!/usr/bin/env python3
"""Offline checks for the hardware verifier's byte/provenance gates."""
import copy
import json
import unittest
from pathlib import Path

import numpy as np

from fpga_model import pack_tensor
from board_evidence import validate_model_report
from verify_board_models import compare_baseline, verify_layer

ROOT = Path(__file__).resolve().parents[1]


class DownloadBoard:
    def __init__(self, padded, logical):
        self.padded, self.logical = padded, logical
        self.last_download_stats = {"mode": "packed", "commands": 1}

    def download(self, address, length):
        return self.padded

    def download_tensor(self, tensor):
        return self.logical


class BoardValidationTests(unittest.TestCase):
    def setUp(self):
        self.tensor = {"name": "tail", "shape": [1, 2, 2, 33], "zero": 17,
                       "row_stride": 64, "plane_stride": 128, "bytes": 256, "address": 0x80000000}
        self.values = np.arange(132, dtype=np.uint8).reshape(1, 2, 2, 33)
        self.layer = {"index": 0, "op": 2, "onnx_node": "test_depthwise", "source": "source",
                      "target": "tail", "registers": [0] * 17, "output": self.tensor}
        self.padded = pack_tensor(self.values, self.tensor, quantized=True)
        self.logical = self.values.tobytes()

    def test_full_and_packed_hashes_have_separate_scopes(self):
        result = verify_layer(DownloadBoard(self.padded, self.logical), self.layer, self.values, True)
        self.assertTrue(result["full_padding_verified"])
        self.assertEqual(result["bytes"], 256)
        self.assertEqual(result["packed_readback"]["logical_bytes"], 132)
        self.assertNotEqual(result["actual_sha256"], result["packed_readback"]["actual_sha256"])

    def test_padding_corruption_fails_even_when_packed_elements_are_correct(self):
        corrupt = bytearray(self.padded)
        corrupt[63] ^= 1
        with self.assertRaisesRegex(RuntimeError, "exact reference"):
            verify_layer(DownloadBoard(bytes(corrupt), self.logical), self.layer, self.values, True)

    def test_packed_element_corruption_fails_when_full_read_is_correct(self):
        corrupt = bytearray(self.logical)
        corrupt[-1] ^= 1
        with self.assertRaisesRegex(RuntimeError, "packed readback differs"):
            verify_layer(DownloadBoard(self.padded, bytes(corrupt)), self.layer, self.values, True)

    def model_report(self):
        layer = verify_layer(DownloadBoard(self.padded, self.logical), self.layer, self.values, True)
        return {"complete": True, "all_full_padded_bytes_identical": True,
                "packed_readback_checked": True, "total_layers": 1,
                "models": [{"name": "fixture", "layers": [layer]}]}

    def test_completed_model_report_uses_its_actual_schema_without_passed_flag(self):
        report = self.model_report()
        self.assertNotIn("passed", report)
        validate_model_report(report, {"fixture": 1})

    def test_model_report_rejects_incomplete_or_missing_packed_evidence(self):
        for key, value in (("complete", False), ("packed_readback_checked", False),
                           ("all_full_padded_bytes_identical", False), ("total_layers", 0)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_model_report(dict(self.model_report(), **{key: value}), {"fixture": 1})
        for key, value in (("packed_readback", {}), ("mismatched_bytes", 1),
                           ("full_padding_verified", False), ("actual_sha256", "different")):
            report = self.model_report()
            report["models"][0]["layers"][0][key] = value
            with self.subTest(layer_key=key), self.assertRaises(ValueError):
                validate_model_report(report, {"fixture": 1})

    def test_model_report_rejects_a_summary_with_missing_or_duplicate_models(self):
        report = self.model_report()
        report["models"] = []
        with self.assertRaisesRegex(ValueError, "missing or duplicate"):
            validate_model_report(report, {"fixture": 1})
        report = self.model_report()
        report["total_layers"] = 2
        report["models"] *= 2
        with self.assertRaisesRegex(ValueError, "missing or duplicate"):
            validate_model_report(report, {"fixture": 1, "other": 1})

    def test_counter_comparison_distinguishes_frequency_from_cycle_savings(self):
        baseline = json.loads((ROOT / "build/vision/unified_board_validation/report.json").read_text())
        result = copy.deepcopy(baseline["models"][0])
        identity = dict(baseline["board"])
        identity["ai_hz"] *= 2
        comparison = compare_baseline(result, identity, baseline)
        self.assertEqual(comparison["reduction_fraction"]["accelerator_cycles"], 0)
        self.assertEqual(comparison["reduction_fraction"]["accelerator_ms"], 0.5)

    def test_different_input_or_output_rejects_performance_comparison(self):
        baseline = json.loads((ROOT / "build/vision/unified_board_validation/report.json").read_text())
        for key in ("input_sha256", "image_sha256", "model_sha256"):
            result = copy.deepcopy(baseline["models"][0])
            result[key] = "0" * 64
            with self.subTest(key=key), self.assertRaises(ValueError):
                compare_baseline(result, baseline["board"], baseline)
        result = copy.deepcopy(baseline["models"][0])
        result["layers"][0]["actual_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "layer bytes differ"):
            compare_baseline(result, baseline["board"], baseline)


if __name__ == "__main__":
    unittest.main(verbosity=2)
