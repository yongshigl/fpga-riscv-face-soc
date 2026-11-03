"""Bounded continuous recognition and opt-in model restoration on the UART worker."""
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from demo_server import Console, create_app


class ContinuousTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        with patch("demo_server.threading.Thread"):
            self.console = Console("COM19", "vision", Path("models"), self.output)
        self.console.connected = self.console.models_ready = True
        self.console.identity = {"firmware_version": 0x26100133}
        self.console.capabilities = {"camera_preview": True, "compressed_write": True}
        self.console.board = SimpleNamespace(
            read_camera=Mock(), preview=Mock(), command=Mock(), enroll=Mock(), bridge_startup_ms=15,
            decoder=SimpleNamespace(crc_errors=0, last_crc_error=None, framing_errors=0))
        self.console.pipeline = SimpleNamespace(run=Mock(return_value={"faces": [], "total_ms": 0}))
        annotation = patch("demo_server.annotate", side_effect=lambda image, *_: image)
        annotation.start()
        self.addCleanup(annotation.stop)
        self.client = create_app(self.console).test_client()

    def frame(self, camera, value, sequence):
        import time
        received = time.monotonic()
        self.console.frames[camera] = (
            np.full((8, 12, 3), value, dtype=np.uint8), b"jpeg",
            {"camera": camera, "sequence": sequence, "received_monotonic": received,
             "estimated_frame_age_ms": 200})
        self.console.frame_times[camera].append(received)
        self.console.frame_count[camera] += 1

    def test_continuous_is_disabled_until_explicitly_started_and_uses_no_queue(self):
        self.assertFalse(self.console.status()["continuous"]["enabled"])
        self.assertIsNone(self.console.next_continuous_job())
        response = self.client.post("/api/continuous", json={"enabled": True, "source": "camera0"})
        self.assertEqual(response.status_code, 200)
        item = self.console.next_continuous_job()
        self.assertIsNotNone(item)
        self.assertIsNone(self.console.next_continuous_job())
        self.assertTrue(self.console.pending.empty())
        self.assertEqual(self.console.pending.unfinished_tasks, 0)

    def test_successful_auto_tasks_use_different_camera_frames(self):
        self.frame(0, 10, 1)
        self.console.set_continuous(True, "camera0")
        first = self.console.next_continuous_job()
        self.console.run_job(first, queued=False)
        self.assertIsNone(self.console.continuous_job)
        self.assertIsNone(self.console.next_continuous_job())
        self.frame(0, 80, 2)
        self.assertIsNone(self.console.next_continuous_job())
        self.frame(1, 40, 1)
        second = self.console.next_continuous_job()
        self.console.run_job(second, queued=False)
        self.assertEqual([self.console.jobs[item[0]]["camera_sequence"] for item in (first, second)], [1, 2])
        self.console.board.read_camera.assert_not_called()
        self.assertTrue(np.all(self.console.pipeline.run.call_args.args[0] == 80))
        self.assertTrue(self.console.continuous_enabled)
        self.assertEqual(self.console.pending.unfinished_tasks, 0)

    def test_automatic_preview_wait_keeps_manual_jobs_available(self):
        self.frame(0, 10, 1)
        self.console.set_continuous(True, "camera0")
        self.console.run_job(self.console.next_continuous_job(), queued=False)
        self.assertIsNone(self.console.claim_next_job()[0])
        identifier = self.console.enqueue("load_models")
        claimed, queued = self.console.claim_next_job()
        self.assertEqual(claimed[0], identifier)
        self.assertTrue(queued)
        self.assertFalse(self.console.continuous_enabled)

    def test_restarting_continuous_does_not_keep_an_obsolete_preview_wait(self):
        self.frame(0, 10, 1)
        self.console.set_continuous(True, "camera0")
        self.console.run_job(self.console.next_continuous_job(), queued=False)
        self.assertIsNone(self.console.next_continuous_job())
        self.console.set_continuous(False)
        self.console.set_continuous(True, "camera1")
        self.assertIsNotNone(self.console.next_continuous_job())

    def test_stop_blocks_new_auto_tasks_while_the_current_one_finishes(self):
        self.frame(0, 10, 1)
        self.console.set_continuous(True, "camera0")
        item = self.console.next_continuous_job()
        self.console.set_continuous(False)
        self.assertIsNone(self.console.next_continuous_job())
        self.console.run_job(item, queued=False)
        self.assertFalse(self.console.continuous_enabled)
        self.assertIsNone(self.console.continuous_job)
        self.assertIsNone(self.console.next_continuous_job())

    def test_manual_operation_stops_auto_and_receives_the_next_worker_turn(self):
        self.console.set_continuous(True, "camera0")
        manual = self.console.enqueue("load_models")
        self.assertFalse(self.console.continuous_enabled)
        self.assertIn("Manual board task", self.console.continuous_reason)
        self.assertIsNone(self.console.next_continuous_job())
        self.assertEqual(self.console.pending.get_nowait()[0], manual)
        self.console.pending.task_done()

    def test_pending_manual_work_prevents_auto_from_starting(self):
        self.console.enqueue("load_models")
        with self.assertRaisesRegex(ValueError, "current board task"):
            self.console.set_continuous(True, "camera0")
        self.assertFalse(self.console.continuous_enabled)
        self.assertIsNone(self.console.next_continuous_job())

    def test_failure_stops_auto_and_preserves_the_failed_task(self):
        self.frame(0, 10, 1)
        self.console.set_continuous(True, "camera0")
        self.console.pipeline.run.side_effect = RuntimeError("Inference readback mismatch")
        item = self.console.next_continuous_job()
        self.console.run_job(item, queued=False)
        self.assertFalse(self.console.continuous_enabled)
        self.assertIn("Inference readback mismatch", self.console.continuous_reason)
        self.assertEqual(self.console.jobs[item[0]]["status"], "failed")
        self.assertIsNone(self.console.next_continuous_job())

    def test_pause_and_disconnection_stop_future_auto_tasks(self):
        for change in ("preview_enabled", "connected", "models_ready"):
            with self.subTest(change=change):
                self.console.connected = self.console.models_ready = self.console.preview_enabled = True
                self.console.set_continuous(True, "camera1")
                setattr(self.console, change, False)
                self.assertIsNone(self.console.next_continuous_job())
                self.assertFalse(self.console.continuous_enabled)
                self.assertIsNotNone(self.console.continuous_reason)

    def test_api_rejects_non_camera_sources_and_invalid_enable_values(self):
        for source in ("uploaded", "obama", None):
            self.assertEqual(self.client.post("/api/continuous", json={"enabled": True, "source": source}).status_code, 400)
        self.assertEqual(self.client.post("/api/continuous", json={"enabled": "false", "source": "camera0"}).status_code, 400)
        self.assertEqual(self.client.post("/api/continuous", json=[]).status_code, 400)
        self.assertEqual(self.client.post("/api/continuous", data="true").status_code, 400)
        self.assertFalse(self.console.continuous_enabled)

    def test_source_change_stop_has_an_explicit_reason(self):
        self.console.set_continuous(True, "camera0")
        response = self.client.post("/api/continuous", json={"enabled": False, "reason": "source_changed"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Image source changed", response.get_json()["reason"])
        self.assertIsNone(self.console.next_continuous_job())

    def test_status_task_records_remain_stable_during_worker_updates(self):
        identifier = self.console.new_job("load_models")
        snapshot = self.console.status()
        self.console.jobs[identifier].update(status="running", started_monotonic=100)
        self.assertEqual(snapshot["jobs"][0]["status"], "queued")
        self.assertNotIn("started_monotonic", snapshot["jobs"][0])
        self.assertEqual(snapshot["pending_jobs"], 0)
        self.assertFalse(snapshot["model_restore"]["armed"])

    def test_reserved_auto_job_blocks_a_restart_with_a_different_camera(self):
        self.console.set_continuous(True, "camera0")
        item = self.console.next_continuous_job()
        self.assertIsNone(self.console.active_job)
        with self.assertRaisesRegex(ValueError, "current board task"):
            self.console.set_continuous(True, "camera1")
        self.assertEqual(self.console.continuous_source, "camera0")
        self.assertEqual(item[-1], "camera0")

    def test_repeated_start_of_the_same_source_preserves_frame_deduplication(self):
        self.console.set_continuous(True, "camera0")
        self.console.continuous_last_sequence = 125
        self.console.set_continuous(True, "camera0")
        self.assertEqual(self.console.continuous_last_sequence, 125)
        item = self.console.next_continuous_job()
        self.console.set_continuous(True, "camera0")
        self.assertEqual(self.console.continuous_job, item[0])
        self.assertEqual(self.console.continuous_last_sequence, 125)

    def test_manual_claim_publishes_busy_atomically_before_other_clients_can_start(self):
        identifier = self.console.enqueue("load_models")
        removed, release, attempted, completed = (threading.Event() for _ in range(4))
        original = self.console.pending.get_nowait
        outcomes = {}

        def dequeue():
            item = original()
            removed.set()
            if not release.wait(2):
                raise RuntimeError("Test did not release the queue claim")
            return item

        def claim():
            outcomes["claim"] = self.console.claim_next_job()

        def start():
            attempted.set()
            try:
                self.console.set_continuous(True, "camera1")
                outcomes["start"] = "accepted"
            except ValueError as error:
                outcomes["start"] = str(error)
            finally:
                completed.set()

        worker, client = threading.Thread(target=claim), threading.Thread(target=start)
        with patch.object(self.console.pending, "get_nowait", side_effect=dequeue):
            try:
                worker.start()
                self.assertTrue(removed.wait(2))
                client.start()
                self.assertTrue(attempted.wait(2))
                self.assertFalse(completed.wait(0.05), "Another client observed an empty queue without the claimed busy state")
            finally:
                release.set()
                worker.join(2)
                if client.ident is not None:
                    client.join(2)
        self.assertFalse(worker.is_alive() or client.is_alive())
        self.assertEqual(outcomes["claim"][0][0], identifier)
        self.assertTrue(outcomes["claim"][1])
        self.assertIn("current board task", outcomes["start"])
        self.assertEqual(self.console.active_job, identifier)
        self.assertEqual(self.console.jobs[identifier]["status"], "running")
        self.assertFalse(self.console.continuous_enabled)

    def model_fixture(self, fail_second=False):
        console = self.console
        snapshots = []
        uploads = []
        hashes = {"yunet": "detector-model", "mobilefacenet": "embedding-model"}

        class FakeModel:
            def __init__(self, name):
                self.name = name
                self.manifest = {"model_sha256": hashes[name]}

            def upload(self, board, verify=True, progress=None):
                uploads.append((self.name, verify))
                snapshots.append(console.status())
                progress(1, 1)
                if fail_second and self.name == "mobilefacenet":
                    raise RuntimeError("Model DDR readback differs")

        def pipeline(*args):
            return SimpleNamespace(detector=FakeModel("yunet"), embedding=FakeModel("mobilefacenet"), gallery={})

        threshold = {"devtest_labels_used_for_selection": False, "devtest_image_overlap": False,
                     "threshold_q15": 9612, "model_hashes": hashes}
        console.gallery = [{"id": "saved", "name": "Saved identity", "models": hashes, "embedding_q15": [100] * 128}]
        return snapshots, uploads, pipeline, threshold

    def test_restore_is_opt_in_and_never_uploads_on_first_connection(self):
        snapshots, uploads, pipeline, threshold = self.model_fixture()
        with patch("demo_server.HardwareFacePipeline", side_effect=pipeline), patch("demo_server.read_json", return_value=threshold):
            self.console.models_loaded_once = True
            self.console.restore_models_after_reconnect()
            self.assertFalse(uploads)
            self.console.restore_models_on_reconnect = True
            self.console.models_loaded_once = False
            self.console.restore_models_after_reconnect()
            self.assertFalse(uploads)
        self.assertFalse(self.console.jobs)

    def test_restore_runs_full_verified_upload_and_gallery_before_readiness(self):
        snapshots, uploads, pipeline, threshold = self.model_fixture()
        self.console.restore_models_on_reconnect = self.console.models_loaded_once = True
        self.console.models_ready = False
        with patch("demo_server.HardwareFacePipeline", side_effect=pipeline), patch("demo_server.read_json", return_value=threshold):
            self.console.restore_models_after_reconnect()
        self.assertEqual(uploads, [("yunet", True), ("mobilefacenet", True)])
        self.assertTrue(all(not row["models_ready"] and row["model_restore"]["state"] == "loading" for row in snapshots))
        self.assertTrue(all(row["active_job"] for row in snapshots))
        self.console.board.command.assert_called_once_with(8)
        self.console.board.enroll.assert_called_once_with(0, [100] * 128)
        self.assertEqual(self.console.pipeline.gallery, {0: "Saved identity"})
        self.assertTrue(self.console.models_ready)
        self.assertEqual(self.console.model_restore_state, "ready")
        job = next(iter(self.console.jobs.values()))
        self.assertEqual(job["trigger"], "reconnect")
        self.assertEqual(job["status"], "done")

    def test_restore_failure_keeps_models_unready_and_shows_the_readback_error(self):
        snapshots, uploads, pipeline, threshold = self.model_fixture(fail_second=True)
        self.console.restore_models_on_reconnect = self.console.models_loaded_once = True
        with patch("demo_server.HardwareFacePipeline", side_effect=pipeline), patch("demo_server.read_json", return_value=threshold):
            self.console.restore_models_after_reconnect()
        self.assertFalse(self.console.models_ready)
        self.assertEqual(self.console.model_restore_state, "failed")
        self.assertIn("DDR readback", self.console.model_restore_error)
        self.console.board.enroll.assert_not_called()
        self.assertEqual(next(iter(self.console.jobs.values()))["status"], "failed")

    def test_restore_keeps_a_user_paused_preview_paused(self):
        snapshots, uploads, pipeline, threshold = self.model_fixture()
        self.console.restore_models_on_reconnect = self.console.models_loaded_once = True
        self.console.preview_enabled = False
        with patch("demo_server.HardwareFacePipeline", side_effect=pipeline), patch("demo_server.read_json", return_value=threshold):
            self.console.restore_models_after_reconnect()
        self.assertFalse(self.console.preview_enabled)
        self.assertEqual([call.args[0] for call in self.console.board.preview.call_args_list], [2])
        self.assertTrue(self.console.models_ready)

    def test_only_a_fully_successful_manual_load_arms_restore(self):
        snapshots, uploads, pipeline, threshold = self.model_fixture(fail_second=True)
        self.console.models_loaded_once = False
        identifier = self.console.new_job("load_models")
        with patch("demo_server.HardwareFacePipeline", side_effect=pipeline), patch("demo_server.read_json", return_value=threshold):
            self.console.run_job((identifier, "load_models", None, None, None, None), queued=False)
        self.assertFalse(self.console.models_loaded_once)
        self.assertFalse(self.console.models_ready)

    def test_successful_manual_load_arms_the_subsequent_reconnection(self):
        snapshots, uploads, pipeline, threshold = self.model_fixture()
        self.console.models_loaded_once = False
        identifier = self.console.new_job("load_models")
        with patch("demo_server.HardwareFacePipeline", side_effect=pipeline), patch("demo_server.read_json", return_value=threshold):
            self.console.run_job((identifier, "load_models", None, None, None, None), queued=False)
        self.assertTrue(self.console.models_loaded_once)
        self.assertTrue(self.console.models_ready)
        self.assertEqual(uploads, [("yunet", True), ("mobilefacenet", True)])

    def test_worker_restores_only_after_manual_success_and_reconnect_with_pause_preserved(self):
        console = self.console
        snapshots, uploads, pipeline, threshold = self.model_fixture()
        console.restore_models_on_reconnect = True
        console.model_restore_state = "idle"
        console.models_loaded_once = False
        opened = []
        phase = [0]

        class SimulatedBoard:
            def __init__(self, *_, **__):
                self.preview_operations = []
                self.command, self.enroll = Mock(), Mock()
                self.decoder = SimpleNamespace(crc_errors=0, last_crc_error=None, framing_errors=0)
                self.bridge_startup_ms = 15
                opened.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def ping(self, **kwargs):
                return {"firmware_version": 0x26100133}

            def capabilities(self):
                return {"camera_preview": True, "compressed_write": True}

            def preview(self, operation):
                self.preview_operations.append(operation)

            def read_camera(self, timeout):
                if phase[0] == 0:
                    self_case.assertFalse(uploads, "Initial connection must not upload models")
                    console.enqueue("load_models")
                    phase[0] = 1
                elif phase[0] == 1:
                    console.enqueue("preview", index=0)
                    console.enqueue("reconnect")
                    phase[0] = 2
                else:
                    console.stopped.set()

        self_case = self
        with patch("demo_server.Board", SimulatedBoard), \
                patch("demo_server.HardwareFacePipeline", side_effect=pipeline), \
                patch("demo_server.read_json", return_value=threshold), \
                patch.object(console.stopped, "wait", return_value=False):
            console.worker()
        self.assertEqual(len(opened), 2)
        self.assertEqual(uploads, [("yunet", True), ("mobilefacenet", True)] * 2)
        self.assertEqual(opened[1].preview_operations, [2, 2])
        opened[1].enroll.assert_called_once_with(0, [100] * 128)
        self.assertEqual(console.model_restore_state, "ready")
        self.assertFalse(console.preview_enabled)
        restores = [job for job in console.jobs.values() if job["trigger"] == "reconnect"]
        self.assertEqual(len(restores), 1)
        self.assertEqual(restores[0]["status"], "done")
        self.assertEqual(console.connection_retries, 0)
        self.assertEqual(console.pending.unfinished_tasks, 0)

    def test_active_preview_is_quiesced_before_serial_owner_closes(self):
        console, operations, closed = self.console, [], []
        console.models_loaded_once = False
        self_case = self

        class SimulatedBoard:
            def __init__(self, *_, **__):
                self.decoder = SimpleNamespace(crc_errors=0, last_crc_error=None, framing_errors=0)
                self.bridge_startup_ms = 15

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self_case.assertEqual(operations[-1], 2, "Physical UART sender must stop before its reader closes")
                closed.append(True)

            def ping(self, **kwargs):
                return {"firmware_version": 0x26101301}

            def capabilities(self):
                return {"camera_preview": True, "compressed_write": True}

            def preview(self, operation):
                operations.append(operation)

            def read_camera(self, timeout):
                console.stopped.set()

        with patch("demo_server.Board", SimulatedBoard), patch.object(console.stopped, "wait", return_value=False):
            console.worker()
        self.assertEqual(operations, [1, 2])
        self.assertEqual(closed, [True])
        self.assertTrue(console.preview_enabled, "Shutdown must preserve the user's resume preference")
        self.assertEqual(console.connection_retries, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
