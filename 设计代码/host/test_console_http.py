"""HTTP errors and dynamic metrics remain usable around invalid/unfinished input."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from demo_server import Console, create_app


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        with patch("demo_server.threading.Thread"):
            self.console = Console("COM19", "offline", self.root / "models", self.root / "runtime")
        self.app = create_app(self.console)
        self.client = self.app.test_client()

    def assert_api_error(self, response, status):
        self.assertEqual(response.status_code, status)
        self.assertTrue(response.is_json)
        self.assertIsInstance(response.get_json()["error"], str)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_jobs_non_object_json_is_a_json_400_without_queuing(self):
        for body in ("null", "[]", "true", "1", '"string"'):
            with self.subTest(body=body):
                self.assert_api_error(self.client.post("/api/jobs", data=body, content_type="application/json"), 400)
        self.assertTrue(self.console.pending.empty())
        self.assertFalse(self.console.jobs)

    def test_malformed_json_in_jobs_and_continuous_preserves_json_error(self):
        for endpoint in ("/api/jobs", "/api/continuous"):
            with self.subTest(endpoint=endpoint):
                self.assert_api_error(self.client.post(endpoint, data="{", content_type="application/json"), 400)

    def test_oversized_upload_is_json_413(self):
        self.app.config["MAX_CONTENT_LENGTH"] = 256
        reply = self.client.post("/api/jobs", data={"action": "infer", "image": (io.BytesIO(b"x" * 1024), "large.jpg")})
        self.assert_api_error(reply, 413)
        self.assertTrue(self.console.pending.empty())

    def test_invalid_image_and_task_field_types_are_json_400(self):
        self.console.connected = True
        reply = self.client.post("/api/jobs", data={"action": "infer", "image": (io.BytesIO(b"invalid"), "broken.jpg")})
        self.assert_api_error(reply, 400)
        for fields in ({"action": "infer", "source": []}, {"action": "enroll", "name": {}},
                       {"action": "remove_identity", "index": True}, {"action": "preview", "index": "0"},
                       {"action": "preview", "index": 2}):
            with self.subTest(fields=fields):
                self.assert_api_error(self.client.post("/api/jobs", json=fields), 400)
        self.assertTrue(self.console.pending.empty())

    def test_api_unknown_route_and_wrong_method_are_json_without_changing_normal_pages(self):
        self.assert_api_error(self.client.get("/api/missing"), 404)
        self.assert_api_error(self.client.get("/api/jobs"), 405)
        self.assertFalse(self.client.get("/missing-page").is_json)

    def metric_project(self, name, changes=None, manifest_changes=None):
        project = self.root / "projects" / name
        project.mkdir(parents=True)
        record = {"clock_hz": 148500000, "lut_post_route": 4280, "coremark_per_mhz": 2.4427716699065076,
                  "coremark_per_second": 362.75159298111635, "coremark_per_lut_post_route": 0.08475504508904587,
                  "seconds": 13.78353698989899, "bitstream_sha256": "b" * 64,
                  "crc_validated": True, "project": name}
        manifest = {"clock_hz": record["clock_hz"], "lut_post_route": record["lut_post_route"],
                    "bitstream_sha256": record["bitstream_sha256"], "timing_met": True,
                    "pipeline_stages": 5, "icache_bytes": 2048, "dcache_bytes": 1024}
        record.update(changes or {})
        manifest.update(manifest_changes or {})
        (project / "result.json").write_text(json.dumps(record))
        (project / "manifest.json").write_text(json.dumps(manifest))
        return project

    def test_metrics_and_export_skip_partial_corrupt_and_incomplete_projects(self):
        good = self.metric_project("riscv_cached_v25_fixture")
        bad = self.metric_project("riscv_partial_write")
        (bad / "result.json").write_text('{"clock_hz":')
        bad = self.metric_project("riscv_missing_manifest_fields")
        (bad / "manifest.json").write_text('{"timing_met":true,"bitstream_sha256":"' + "b"*64 + '"}')
        bad = self.metric_project("riscv_result_list")
        (bad / "result.json").write_text("[]")
        self.metric_project("riscv_crc_failure", {"crc_validated": False})
        self.metric_project("riscv_short_run", {"seconds": 9})
        self.metric_project("riscv_timing_failure", manifest_changes={"timing_met": False})
        self.metric_project("riscv_hash_mismatch", {"bitstream_sha256": "c"*64})
        self.metric_project("riscv_hash_wrong_type", {"bitstream_sha256": []})
        self.metric_project("riscv_score_wrong_type", {"coremark_per_second": "unfinished"})
        self.metric_project("riscv_lut_mismatch", {"lut_post_route": 999})
        self.metric_project("riscv_nan", {"coremark_per_lut_post_route": float("nan")})
        with patch("demo_server.ROOT", self.root / "repo"):
            client = create_app(self.console).test_client()
            metrics = client.get("/api/metrics")
            self.assertEqual(metrics.status_code, 200)
            self.assertEqual([item["project"] for item in metrics.get_json()["cpu"]], [good.name])
            report = client.get("/api/report")
            self.assertEqual(report.status_code, 200)
            self.assertEqual([item["project"] for item in report.get_json()["metrics"]["cpu"]], [good.name])

    def test_historical_board_accuracy_requires_the_running_identity_as_well_as_matching_artifacts(self):
        (self.root / "projects").mkdir()
        identity = {"firmware_version": 1, "accelerator_version": 2, "cpu_hz": 81000000,
                    "ai_hz": 63000000, "uart_baud": 921600}
        evidence = {"project": "historical", "bitstream_sha256": "b" * 64}
        hardware = self.root / "hardware.json"
        hardware.write_text(json.dumps({"complete": True, "board": identity,
                                        "project_evidence": evidence, "models": []}))
        accuracy = self.root / "repo/build/vision/lfw_board_devtest/report.json"
        accuracy.parent.mkdir(parents=True)
        accuracy.write_text(json.dumps({"complete": True, "dataset": "LFW DevTest",
            "execution": "Historical board evaluation", "unique_images": 1549,
            "project_evidence": evidence, "metrics": {"accuracy_all_pairs_with_failures_counted_wrong": 0.98,
                "pairs": 1000, "threshold": 0.3, "pairs_with_failed_face_processing": 0}}))
        with patch("demo_server.ROOT", self.root / "repo"):
            client = create_app(self.console, hardware).test_client()
            self.console.connected = True
            self.console.identity = dict(identity, ai_hz=72000000)
            value = client.get("/api/metrics").get_json()
            self.assertFalse(value["hardware_matches_running_identity"])
            self.assertFalse(value["board_accuracy"]["matches_hardware"])
            self.console.identity = identity
            value = client.get("/api/metrics").get_json()
            self.assertTrue(value["hardware_matches_running_identity"])
            self.assertTrue(value["board_accuracy"]["matches_hardware"])
            self.console.connected = False
            self.assertFalse(client.get("/api/metrics").get_json()["board_accuracy"]["matches_hardware"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
