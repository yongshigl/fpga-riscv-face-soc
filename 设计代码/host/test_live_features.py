"""Host-side tracking, liveness hint and latency window: decorative, never gating."""
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from demo_server import Console
from live_features import FaceTracker, LatencyStats, intersection_over_union, liveness_hint


class TrackingTests(unittest.TestCase):
    def test_overlapping_box_keeps_its_identifier(self):
        tracker = FaceTracker()
        self.assertEqual(tracker.update([[10, 10, 40, 40], [200, 10, 40, 40]]), [0, 1])
        self.assertEqual(tracker.update([[13, 12, 40, 40], [201, 11, 40, 40]]), [0, 1])

    def test_distant_box_gets_a_new_identifier(self):
        tracker = FaceTracker()
        tracker.update([[10, 10, 40, 40]])
        self.assertEqual(tracker.update([[400, 300, 40, 40]]), [1])

    def test_expired_track_is_forgotten(self):
        tracker = FaceTracker(max_age=2)
        tracker.update([[10, 10, 40, 40]])
        for _ in range(3):
            tracker.update([])
        self.assertEqual(tracker.tracks, {})

    def test_overlap_is_symmetric(self):
        self.assertAlmostEqual(intersection_over_union([0, 0, 10, 10], [5, 0, 10, 10]), 1 / 3)
        self.assertAlmostEqual(intersection_over_union([5, 0, 10, 10], [0, 0, 10, 10]), 1 / 3)


class LivenessTests(unittest.TestCase):
    def test_flat_patch_scores_below_textured_patch(self):
        flat = np.full((120, 160, 3), 128, dtype=np.uint8)
        texture = np.random.RandomState(7).randint(0, 255, (120, 160, 3), dtype=np.uint8)
        box = [20, 10, 80, 90]
        self.assertLess(liveness_hint(flat, box)["score"], liveness_hint(texture, box)["score"])

    def test_blown_out_highlights_lower_the_score(self):
        texture = np.random.RandomState(7).randint(0, 200, (120, 160, 3), dtype=np.uint8)
        glare = texture.copy()
        glare[10:100, 20:100] = 255
        box = [20, 10, 80, 90]
        self.assertLess(liveness_hint(glare, box)["score"], liveness_hint(texture, box)["score"])

    def test_motion_only_appears_with_a_previous_crop(self):
        image = np.random.RandomState(1).randint(0, 255, (120, 160, 3), dtype=np.uint8)
        first = liveness_hint(image, [20, 10, 80, 90])
        self.assertIsNone(first["motion"])
        second = liveness_hint(image, [20, 10, 80, 90], first["crop"])
        self.assertEqual(second["motion"], 0.0)

    def test_box_outside_the_frame_does_not_raise(self):
        image = np.zeros((16, 16, 3), dtype=np.uint8)
        self.assertIn("score", liveness_hint(image, [-40, -40, 10, 10]))


class LatencyTests(unittest.TestCase):
    def test_empty_window_reports_zero_samples(self):
        self.assertEqual(LatencyStats().summary(), {"samples": 0})

    def test_window_keeps_the_latest_samples_and_counts_detections(self):
        stats = LatencyStats(window=3)
        for value in (100.0, 200.0, 300.0, 400.0):
            stats.add(value, [1] if value in (200.0, 400.0) else [])
        summary = stats.summary()
        self.assertEqual(summary["samples"], 3)
        self.assertEqual(summary["min_ms"], 200.0)
        self.assertEqual(summary["max_ms"], 400.0)
        self.assertEqual(summary["detection_rate"], 0.5)


class ConsoleLiveFeatureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        with patch("demo_server.threading.Thread"):
            self.console = Console("COM19", "vision", Path("models"), Path(self.directory.name))

    def record(self, boxes, total_ms=250.0):
        return {"total_ms": total_ms,
                "faces": [{"box": list(box), "landmarks": [[0, 0]] * 5, "identity": "Unknown",
                           "confidence": 0.9, "cosine": 0.1} for box in boxes]}

    def test_faces_receive_identifiers_and_a_hint(self):
        image = np.random.RandomState(3).randint(0, 255, (720, 1280, 3), dtype=np.uint8)
        record = self.console.apply_live_features(self.record([[100, 100, 120, 120]]), image)
        self.assertEqual(record["faces"][0]["track_id"], 0)
        self.assertTrue(0.0 <= record["faces"][0]["liveness"]["score"] <= 1.0)
        self.assertEqual(self.console.latency.summary()["samples"], 1)

    def test_scale_maps_board_coordinates_onto_the_readback_image(self):
        image = np.random.RandomState(4).randint(0, 255, (180, 320, 3), dtype=np.uint8)
        record = self.console.apply_live_features(self.record([[640, 360, 240, 240]]), image, 320 / 1280)
        self.assertIsNotNone(record["faces"][0]["liveness"]["score"])

    def test_a_failing_tracker_never_breaks_the_record(self):
        image = np.zeros((64, 64, 3), dtype=np.uint8)
        with patch.object(self.console.tracker, "update", side_effect=RuntimeError("boom")):
            record = self.console.apply_live_features(self.record([[1, 1, 20, 20]]), image)
        self.assertEqual(record["tracking_error"], "boom")
        self.assertEqual(record["faces"][0]["identity"], "Unknown")

    def test_status_exposes_the_latency_window(self):
        self.console.latency.add(275.0, [1])
        self.assertEqual(self.console.status()["app_metrics"]["samples"], 1)


if __name__ == "__main__":
    unittest.main()
