"""Final evidence gates tested against simulated HTTP transitions, never a board."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from console_integrity import begin_observation, check_connection, check_reconnect_transition
import verify_console_continuous_hardware as continuous
import verify_console_stability as stability


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def state():
    return {"connected": True, "models_ready": True, "preview_enabled": True, "mode": "vision",
            "error": None, "active_job": None, "pending_jobs": 0, "board": {},
            "connection_generation": 4, "connection_retries": 2, "decode_errors": 3,
            "crc_errors": 0, "framing_errors": 0, "gallery": [], "jobs": [], "result": None,
            "capabilities": {"camera_preview": True},
            "continuous": {"enabled": False, "source": None, "active_job": None, "reason": None},
            "model_restore": {"enabled": True, "armed": True, "state": "ready", "error": None},
            "cameras": [{"frames": 10, "sensor": {"verified": True}, "fps": 2, "stale_seconds": 0.1} for _ in range(2)]}


class FakeContinuousHttp:
    def __init__(self, clock, fault=None):
        self.clock, self.fault = clock, fault
        self.value = state()
        self.starts = self.automatic = self.restore_step = 0
        self.phase = "idle"

    def add_job(self, identifier, action, **fields):
        job = {"id": identifier, "action": action, "status": "done", "trigger": None,
               "submitted_monotonic": self.clock.now, "connection_generation": self.value["connection_generation"]}
        job.update(fields)
        self.value["jobs"].append(job)
        return job

    def __call__(self, operation, **_):
        self.clock.now += 0.01
        path = operation if isinstance(operation, str) else operation.full_url
        payload = None if isinstance(operation, str) or operation.data is None else json.loads(operation.data)
        if path.endswith("/api/continuous"):
            if payload["enabled"]:
                self.starts += 1
                self.phase = "frames" if self.starts == 1 else "running"
                self.value["continuous"].update(enabled=True, source=payload["source"])
            else:
                self.phase = "quiet"
                self.value["continuous"].update(enabled=False, active_job=None, reason="Continuous recognition stopped")
                self.value["active_job"] = None
                for job in self.value["jobs"]:
                    job["status"] = "done"
            result = self.value["continuous"]
        elif path.endswith("/api/jobs"):
            action = payload["action"]
            identifier = f"manual-{len(self.value['jobs'])}"
            self.add_job(identifier, action)
            if action == "infer":
                self.phase = "quiet"
                self.value["continuous"].update(enabled=False, reason="Manual board task requested")
                self.value["result"] = {"input_source": payload["source"]}
            elif action == "preview":
                self.value["preview_enabled"] = bool(payload["index"])
                self.phase = "resumed" if payload["index"] else "paused"
            elif action == "reconnect":
                self.phase = "restoring"
            result = {"id": identifier}
        else:
            if self.phase == "frames":
                self.automatic += 1
                for index, camera in enumerate(self.value["cameras"]):
                    if not (self.fault == "starve_camera1" and index == 1):
                        camera["frames"] += 1
                self.add_job(f"auto-{self.automatic}", "infer", trigger="continuous", source="camera0",
                             camera=0, camera_sequence=self.automatic)
            elif self.phase == "running":
                job = self.add_job("auto-stop", "infer", trigger="continuous", status="running")
                self.value["active_job"] = self.value["continuous"]["active_job"] = job["id"]
            elif self.phase == "restoring":
                if self.restore_step == 0:
                    self.value.update(connected=False, models_ready=False)
                elif self.restore_step == 1:
                    self.value.update(connected=True, connection_generation=5)
                    self.value["model_restore"]["state"] = "loading"
                    if self.fault == "extra_generation":
                        self.value["connection_generation"] = 6
                    elif self.fault:
                        self.value[self.fault] += 1
                else:
                    self.value["models_ready"] = True
                    self.value["model_restore"]["state"] = "ready"
                    self.add_job("verified-model-restore", "load_models", trigger="reconnect")
                    self.phase = "restored"
                self.restore_step += 1
            elif self.phase == "resumed":
                for camera in self.value["cameras"]:
                    camera["frames"] += 2
            result = self.value
        return io.BytesIO(json.dumps(result).encode())


class VerifierTests(unittest.TestCase):
    def test_lifetime_history_is_kept_separate_from_clean_current_connection(self):
        value = state()
        baseline = begin_observation(value)
        self.assertEqual(baseline["connection_retries"], 2)
        self.assertEqual(baseline["decode_errors"], 3)
        self.assertEqual(check_connection(value, baseline), baseline)
        for key in ("crc_errors", "framing_errors", "connection_retries", "decode_errors"):
            with self.subTest(key=key):
                changed = dict(value, **{key: value[key] + 1})
                with self.assertRaises(RuntimeError):
                    check_connection(changed, baseline)

    def test_exactly_one_intentional_generation_change_accepts_disconnect_then_clean_restore(self):
        value = state()
        baseline = begin_observation(value)
        self.assertFalse(check_reconnect_transition(dict(value, connected=False), baseline))
        self.assertFalse(check_reconnect_transition(value, baseline))
        self.assertTrue(check_reconnect_transition(dict(value, connection_generation=5), baseline))
        for changes in ({"connection_generation": 6}, {"connection_generation": 3},
                        {"connection_generation": 5, "crc_errors": 1},
                        {"connected": False, "connection_retries": 3},
                        {"connection_generation": 5, "decode_errors": 4}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                check_reconnect_transition(dict(value, **changes), baseline)

    def test_initial_crc_or_framing_errors_cannot_be_hidden_in_a_baseline(self):
        for key in ("crc_errors", "framing_errors"):
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                begin_observation(dict(state(), **{key: 1}))

    def run_stability(self, directory, second_changes=None, initial_changes=None):
        initial = state()
        initial.update(initial_changes or {})
        second = copy.deepcopy(initial)
        second.update(second_changes or {})
        for camera in second["cameras"]:
            camera["frames"] = 13
        clock = Clock()
        responses = [io.BytesIO(json.dumps(value).encode()) for value in (initial, second)]
        arguments = ["--project", str(directory / "project"), "--runtime", str(directory / "runtime"),
                     "--output", str(directory / "result"), "--frames-per-camera", "2",
                     "--min-seconds", "1", "--timeout", "20"]
        with patch.object(stability, "validate_project", return_value={"project": "fixture"}), \
             patch.object(stability.urllib.request, "urlopen", side_effect=responses), \
             patch.object(stability.time, "monotonic", side_effect=clock.monotonic), \
             patch.object(stability.time, "sleep", side_effect=clock.sleep), \
             contextlib.redirect_stdout(io.StringIO()):
            stability.main(arguments)

    def test_stability_full_http_loop_accepts_unchanged_lifetime_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.run_stability(directory)
            report = json.loads((directory / "result/report.json").read_text())
            self.assertTrue(report["passed"])
            self.assertEqual(report["frames_received"], [3, 3])
            self.assertEqual(report["initial_integrity"], report["final_integrity"])
            self.assertEqual(report["samples"][1]["integrity"]["connection_generation"], 4)

    def test_stability_rejects_silent_generation_change_or_new_error_and_preserves_status(self):
        for changes in ({"connection_generation": 5}, {"crc_errors": 1}, {"framing_errors": 1},
                        {"connection_retries": 3}, {"decode_errors": 4}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                with self.assertRaises(RuntimeError):
                    self.run_stability(directory, changes)
                report = json.loads((directory / "result/report.json").read_text())
                self.assertFalse(report["passed"])
                for key, value in changes.items():
                    self.assertEqual(report["final_status"][key], value)
                    self.assertEqual(report["final_integrity"][key], value)

    def test_raw_preview_accepts_the_dual_camera_wire_interval_and_rejects_a_stall(self):
        for age, passed in ((2.6, True), (4.0, False)):
            with self.subTest(age=age), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                cameras = [dict(camera, format=3, encoded_bytes=115200, stale_seconds=age)
                           for camera in state()["cameras"]]
                initial = {"baud": 921600, "cameras": cameras}
                if passed:
                    self.run_stability(directory, initial_changes=initial)
                else:
                    with self.assertRaisesRegex(RuntimeError, "camera stopped"):
                        self.run_stability(directory, initial_changes=initial)
                report = json.loads((directory / "result/report.json").read_text())
                self.assertEqual(report["passed"], passed)
                if passed:
                    self.assertGreater(report["samples"][0]["maximum_stale_seconds"][0], age)

    def test_jpeg_preview_keeps_its_two_second_stall_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            cameras = [dict(camera, format=5, stale_seconds=2.1) for camera in state()["cameras"]]
            with self.assertRaisesRegex(RuntimeError, "camera stopped"):
                self.run_stability(directory, initial_changes={"cameras": cameras})

    def run_continuous(self, directory, fault=None, min_seconds=0):
        clock = Clock()
        server = FakeContinuousHttp(clock, fault)
        arguments = ["--project", str(directory / "project"), "--output", str(directory / "result"),
                     "--frames", "2", "--timeout", "20", "--min-seconds", str(min_seconds),
                     "--check-reconnect-restore"]
        with patch.object(continuous, "validate_project", return_value={"project": "fixture"}), \
             patch.object(continuous.urllib.request, "urlopen", side_effect=server), \
             patch.object(continuous.time, "monotonic", side_effect=clock.monotonic), \
             patch.object(continuous.time, "sleep", side_effect=clock.sleep), \
             contextlib.redirect_stdout(io.StringIO()):
            continuous.main(arguments)

    def test_continuous_endurance_keeps_running_after_the_frame_target_and_preserves_raw_final_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.run_continuous(directory, min_seconds=1)
            report = json.loads((directory / "result/report.json").read_text())
            phase = report["phases"][0]
            self.assertTrue(report["passed"])
            self.assertGreaterEqual(phase["elapsed_seconds"], 1)
            self.assertGreater(len(phase["jobs"]), 2)
            self.assertGreaterEqual(min(phase["preview_frames_received"]), len(phase["jobs"]) - 1)
            self.assertTrue(report["compact_observations"])
            self.assertIn("model_restore", report["last_observed_status"])

    def test_continuous_endurance_rejects_a_starved_second_preview(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, "Both camera previews"):
                self.run_continuous(directory, fault="starve_camera1", min_seconds=1)
            report = json.loads((directory / "result/report.json").read_text())
            self.assertFalse(report["passed"])
            self.assertEqual(report["last_observed_status"]["cameras"][1]["frames"], 10)

    def test_complete_continuous_restore_keeps_lifetime_history_and_records_one_new_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.run_continuous(directory)
            report = json.loads((directory / "result/report.json").read_text())
            self.assertTrue(report["passed"])
            self.assertEqual(report["initial_integrity"]["connection_generation"], 4)
            self.assertEqual(report["final_integrity"]["connection_generation"], 5)
            self.assertEqual(report["final_integrity"]["connection_retries"], 2)
            self.assertEqual(report["final_integrity"]["decode_errors"], 3)
            self.assertEqual(report["requested_reconnect"]["expected_generation"], 5)
            self.assertTrue(any(not row["status"]["connected"] for row in report["observed_statuses"]))

    def test_continuous_reconnect_rejects_new_current_errors_retry_decode_or_extra_generation(self):
        for fault in ("crc_errors", "framing_errors", "connection_retries", "decode_errors", "extra_generation"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                with self.assertRaises(RuntimeError):
                    self.run_continuous(directory, fault)
                report = json.loads((directory / "result/report.json").read_text())
                self.assertFalse(report["passed"])
                self.assertTrue(report["observed_statuses"])
                self.assertIsNotNone(report["last_observed_integrity"])
                if fault != "extra_generation":
                    self.assertGreater(report["last_observed_integrity"][fault], report["initial_integrity"][fault])


if __name__ == "__main__":
    unittest.main(verbosity=2)
