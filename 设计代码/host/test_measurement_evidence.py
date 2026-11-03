import copy
import json
from pathlib import Path
import tempfile
import unittest

from board_evidence import file_hash
from measurement_evidence import current_reference_accuracy, matches_running_identity, read_measurement


class MeasurementEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.reference = self.root / "reference.py"
        self.reference.write_text("reference v2")
        self.pairs = self.root / "pairs.txt"
        self.pairs.write_text("independent test split")
        self.threshold = self.root / "threshold.json"
        hashes = {}
        images = {}
        for name in ("yunet", "mobilefacenet"):
            image = self.root / (name + "_image.bin")
            image.write_bytes(name.encode())
            hashes[name] = name + "-model"
            images[name] = file_hash(image)
            (self.root / (name + ".json")).write_text(json.dumps(
                {"model_sha256": hashes[name], "image_sha256": images[name]}))
        self.threshold.write_text(json.dumps({"threshold": 0.3, "model_hashes": hashes,
                                             "devtest_labels_used_for_selection": False,
                                             "devtest_image_overlap": 0}))
        self.report = self.root / "report.json"
        self.value = {"dataset": "LFW DevTest", "execution": "Host exact integer reference",
                      "evaluated_utc": "2026-10-25T00:00:00Z",
                      "provenance": dict(images, reference_sha256=file_hash(self.reference),
                                         pairs_sha256=file_hash(self.pairs)),
                      "threshold_training_file_sha256": file_hash(self.threshold),
                      "metrics": {"devtrain_selected": {"pairs": 1000, "correct_pairs": 981,
                          "threshold": 0.3, "accuracy_all_pairs_with_failures_counted_wrong": 0.981}}}
        self.save()

    def save(self):
        self.report.write_text(json.dumps(self.value))

    def accuracy(self):
        return current_reference_accuracy(self.report, self.root, self.reference, self.threshold, self.pairs)

    def test_current_exact_reference_preserves_its_distinct_scope_and_provenance(self):
        result = self.accuracy()
        self.assertEqual(result["accuracy"], 0.981)
        self.assertEqual(result["execution"], "Host exact integer reference")
        self.assertEqual(result["report_sha256"], file_hash(self.report))

    def test_source_dataset_model_and_training_changes_invalidate_old_accuracy(self):
        for path in (self.reference, self.pairs, self.root / "yunet_image.bin", self.threshold):
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(original + b"changed")
                self.assertIsNone(self.accuracy())
                path.write_bytes(original)

    def test_inconsistent_counts_or_threshold_cannot_be_displayed_as_current_accuracy(self):
        original = copy.deepcopy(self.value)
        for key, value in (("correct_pairs", 1001), ("pairs", 999), ("threshold", 0.4),
                           ("accuracy_all_pairs_with_failures_counted_wrong", 0.999)):
            with self.subTest(key=key):
                self.value = copy.deepcopy(original)
                self.value["metrics"]["devtrain_selected"][key] = value
                self.save()
                self.assertIsNone(self.accuracy())

    def test_absent_partial_and_non_object_reports_are_ignored(self):
        for value in ("{", "[]", "null"):
            self.report.write_text(value)
            self.assertIsNone(read_measurement(self.report))
            self.assertIsNone(self.accuracy())
        self.report.unlink()
        self.assertIsNone(read_measurement(self.report))

    def test_matching_historical_artifacts_cannot_substitute_for_a_different_running_identity(self):
        identity = {"firmware_version": 1, "accelerator_version": 2,
                    "cpu_hz": 81000000, "ai_hz": 63000000, "uart_baud": 921600}
        report = {"complete": True, "board": identity}
        self.assertTrue(matches_running_identity(report, identity))
        self.assertFalse(matches_running_identity(report, dict(identity, ai_hz=72000000)))
        self.assertFalse(matches_running_identity(report, None))
        self.assertFalse(matches_running_identity(dict(report, complete=False), identity))


if __name__ == "__main__":
    unittest.main(verbosity=2)
