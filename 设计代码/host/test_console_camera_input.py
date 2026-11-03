"""Camera jobs use fresh worker-selected frames without another UART owner."""
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from camera_stream import CAMERA_MAGIC, WirePacket
from demo_server import CAMERA_INPUT_TIMEOUT_SECONDS, Console, create_app


class CameraInputTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        with patch("demo_server.threading.Thread"):
            self.console = Console("COM19", "vision", Path("models"), self.output, baud=460800)
        self.console.connected = self.console.models_ready = True
        self.console.identity = {"firmware_version": 0x26100133}
        self.console.capabilities = {"camera_preview": True, "compressed_write": True}
        self.console.board = SimpleNamespace(
            read_camera=Mock(), preview=Mock(), bridge_startup_ms=15,
            decoder=SimpleNamespace(crc_errors=0, last_crc_error=None, framing_errors=0))
        self.console.pipeline = SimpleNamespace(run=Mock(return_value={"faces": [], "total_ms": 0}))
        self.now = [100.0]
        clock = patch("demo_server.time.monotonic", side_effect=lambda: self.now[0])
        clock.start()
        self.addCleanup(clock.stop)
        annotation = patch("demo_server.annotate", side_effect=lambda image, *_: image)
        annotation.start()
        self.addCleanup(annotation.stop)

    def frame(self, camera, value, sequence, received=None, transport_age_ms=200):
        received = self.now[0] if received is None else received
        image = np.full((8, 12, 3), value, dtype=np.uint8)
        self.console.frames[camera] = (image, b"jpeg", {
            "camera": camera, "sequence": sequence, "received_monotonic": received,
            "estimated_frame_age_ms": transport_age_ms,
        })
        self.console.frame_times[camera].append(received)
        return image

    def execute_one(self):
        identifier, action, image, name, index, source = self.console.pending.get_nowait()
        try:
            return self.console.execute(identifier, action, image, name, index, source)
        finally:
            self.console.pending.task_done()

    def test_http_submission_records_source_without_reading_or_copying_the_old_frame(self):
        self.frame(0, 10, 1)
        client = create_app(self.console).test_client()
        response = client.post("/api/jobs", json={"action": "infer", "source": "camera0"})
        self.assertEqual(response.status_code, 202)
        item = self.console.pending.get_nowait()
        self.console.pending.task_done()
        self.assertIsNone(item[2])
        self.assertEqual(item[-1], "camera0")
        self.console.board.read_camera.assert_not_called()
        self.console.board.preview.assert_not_called()

    def test_queued_job_uses_updated_frame_and_exports_queue_and_camera_timing(self):
        self.frame(0, 10, 1)
        identifier = self.console.enqueue("infer", "camera0")
        self.now[0] += 5
        expected = self.frame(0, 90, 27)
        self.execute_one()
        np.testing.assert_array_equal(self.console.pipeline.run.call_args.args[0], expected)
        job = self.console.jobs[identifier]
        self.assertEqual(job["queue_wait_ms"], 5000)
        self.assertEqual(job["camera_sequence"], 27)
        self.assertEqual(job["camera_host_age_ms"], 0)
        self.assertEqual(job["camera_estimated_age_at_selection_ms"], 200)
        self.console.board.read_camera.assert_not_called()
        self.assertEqual([call.args[0] for call in self.console.board.preview.call_args_list], [2, 1])
        result = json.loads((self.output / "latest_result.json").read_text())
        self.assertEqual(result["input_source"], "camera0")
        self.assertEqual(result["queue_wait_ms"], 5000)
        self.assertEqual(result["request_to_result_ms"], 5000)
        self.assertEqual(result["camera_input"]["camera_sequence"], 27)
        self.assertIn("excludes sensor exposure", result["camera_input"]["camera_age_timing_scope"])

    def test_stale_frame_is_refreshed_on_the_worker_before_preview_is_paused(self):
        self.frame(1, 10, 1)
        identifier = self.console.enqueue("infer", "camera1")
        self.now[0] += 2

        def deliver(timeout):
            self.assertLessEqual(timeout, 0.15)
            self.console.board.preview.assert_not_called()
            self.now[0] += timeout
            self.frame(1, 80, 2)

        self.console.board.read_camera.side_effect = deliver
        self.execute_one()
        self.console.board.read_camera.assert_called_once()
        self.assertEqual(self.console.jobs[identifier]["camera_sequence"], 2)
        self.assertAlmostEqual(self.console.jobs[identifier]["camera_acquire_ms"], 150)
        self.assertTrue(np.all(self.console.pipeline.run.call_args.args[0] == 80))

    def test_recent_packet_with_excessive_capture_age_is_not_selected(self):
        self.frame(0, 10, 1, transport_age_ms=1200)
        identifier = self.console.enqueue("infer", "camera0")

        def deliver(timeout):
            self.now[0] += timeout
            self.frame(0, 80, 2, transport_age_ms=180)

        self.console.board.read_camera.side_effect = deliver
        self.execute_one()
        self.assertEqual(self.console.jobs[identifier]["camera_sequence"], 2)
        self.assertEqual(self.console.jobs[identifier]["camera_estimated_age_at_selection_ms"], 180)

    def test_absent_camera_times_out_without_pausing_preview_or_running_inference(self):
        self.console.enqueue("infer", "camera0")
        started = self.now[0]
        self.console.board.read_camera.side_effect = lambda timeout: self.now.__setitem__(0, self.now[0] + timeout)
        with self.assertRaisesRegex(TimeoutError, "CAM0.*recent image"):
            self.execute_one()
        self.assertAlmostEqual(self.now[0] - started, CAMERA_INPUT_TIMEOUT_SECONDS)
        self.console.board.preview.assert_not_called()
        self.console.pipeline.run.assert_not_called()

    def test_other_camera_updates_do_not_satisfy_the_requested_camera(self):
        self.console.enqueue("infer", "camera1")

        def deliver(timeout):
            self.now[0] += timeout
            self.frame(0, 80, 2)

        self.console.board.read_camera.side_effect = deliver
        with self.assertRaisesRegex(TimeoutError, "CAM1.*recent image"):
            self.execute_one()
        self.console.pipeline.run.assert_not_called()

    def test_paused_camera_submission_fails_and_does_not_touch_uart(self):
        self.frame(0, 10, 1)
        self.console.preview_enabled = False
        with self.assertRaisesRegex(ValueError, "preview is paused"):
            self.console.enqueue("infer", "camera0")
        self.assertTrue(self.console.pending.empty())
        self.console.board.read_camera.assert_not_called()

    def test_a_pause_ahead_of_a_queued_camera_job_prevents_capturing_an_old_frame(self):
        self.frame(0, 10, 1)
        self.console.enqueue("infer", "camera0")
        self.console.preview_enabled = False
        with self.assertRaisesRegex(ValueError, "preview is paused"):
            self.execute_one()
        self.console.board.preview.assert_not_called()
        self.console.board.read_camera.assert_not_called()
        self.console.pipeline.run.assert_not_called()

    def test_upload_remains_a_submission_snapshot(self):
        original = np.full((8, 12, 3), 20, dtype=np.uint8)
        self.console.enqueue("infer", image=original)
        original[:] = 99
        self.execute_one()
        self.assertTrue(np.all(self.console.pipeline.run.call_args.args[0] == 20))
        self.assertIsNone(self.console.result["camera_input"])
        self.console.board.read_camera.assert_not_called()

    def test_sample_is_read_on_submission_and_not_reloaded_after_queueing(self):
        original = np.full((8, 12, 3), 20, dtype=np.uint8)
        with patch("demo_server.read_image", return_value=original) as read:
            self.console.enqueue("infer", "obama")
        with patch("demo_server.read_image", side_effect=AssertionError("Sample must remain its submission snapshot")):
            self.execute_one()
        read.assert_called_once()
        self.assertTrue(np.all(self.console.pipeline.run.call_args.args[0] == 20))
        self.console.board.read_camera.assert_not_called()

    def test_camera_job_cannot_cross_a_connection_generation(self):
        self.frame(0, 10, 1)
        self.console.enqueue("infer", "camera0")
        self.console.connection_generation += 1
        with self.assertRaisesRegex(ValueError, "connection changed"):
            self.execute_one()
        self.console.board.read_camera.assert_not_called()
        self.console.pipeline.run.assert_not_called()

    def test_pending_jobs_are_explicitly_failed_on_connection_exit(self):
        camera_job = self.console.enqueue("infer", "camera0")
        sample_job = self.console.enqueue("infer", "obama")
        self.console.cancel_pending_jobs()
        self.assertTrue(self.console.pending.empty())
        self.console.pending.join()
        for identifier in (camera_job, sample_job):
            self.assertEqual(self.console.jobs[identifier]["status"], "failed")
            self.assertIn("connection changed", self.console.jobs[identifier]["error"])
        self.console.pipeline.run.assert_not_called()

    def test_unloaded_models_fail_before_waiting_for_a_camera_frame(self):
        self.console.enqueue("infer", "camera0")
        self.console.models_ready = False
        with self.assertRaisesRegex(ValueError, "Load the FPGA models"):
            self.execute_one()
        self.console.board.read_camera.assert_not_called()
        self.console.board.preview.assert_not_called()

    def test_status_exposes_the_explicit_port_and_baud(self):
        status = self.console.status()
        self.assertEqual(status["port"], "COM19")
        self.assertEqual(status["baud"], 460800)

    def test_worker_opens_the_explicit_port_and_baud(self):
        def fail_open(*_, **__):
            self.console.stopped.set()
            raise OSError("Disconnected test device")

        with patch("demo_server.Board", side_effect=fail_open) as board, \
                patch("demo_server.logging.getLogger"), \
                patch.object(self.console.stopped, "wait", return_value=False):
            self.console.worker()
        self.assertEqual(board.call_args.args[0], "COM19")
        self.assertEqual(board.call_args.kwargs["baud"], 460800)
        self.assertEqual(self.console.connection_retries, 1)
        self.assertFalse(self.console.connected)

    def test_worker_refreshes_a_queued_camera_job_and_completes_it_with_auditable_timing(self):
        console = self.console
        pipeline = console.pipeline
        jobs = []
        preview_operations = []
        reads = []

        def deliver(callback, value, sequence):
            from demo_server import encode_jpeg
            pixels = np.full((8, 12, 3), value, dtype=np.uint8)
            payload = struct.pack("<HHII", 12, 8, 100, 200) + encode_jpeg(pixels)
            callback(WirePacket(CAMERA_MAGIC, 1, sequence, payload, camera=0,
                                format=5, received_monotonic=self.now[0]))

        class SimulatedBoard:
            def __init__(self, *_, camera_callback, **__):
                self.callback = camera_callback
                self.baud = 460800

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def ping(self, **kwargs):
                return {"firmware_version": 0x26100133}

            def capabilities(self):
                return {"camera_preview": True, "compressed_write": True}

            def preview(self, operation):
                preview_operations.append(operation)

            def read_camera(self, timeout):
                reads.append(timeout)
                if len(reads) == 1:
                    console.pipeline = pipeline
                    console.models_ready = True
                    deliver(self.callback, 10, 1)
                    jobs.append(console.enqueue("infer", "camera0"))
                    self_case.now[0] += 2
                elif len(reads) == 2:
                    self_case.now[0] += timeout
                    deliver(self.callback, 80, 2)
                else:
                    console.stopped.set()

        self_case = self
        with patch("demo_server.Board", SimulatedBoard):
            console.worker()
        self.assertEqual(len(jobs), 1)
        job = console.jobs[jobs[0]]
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["queue_wait_ms"], 2000)
        self.assertEqual(job["camera_sequence"], 2)
        self.assertAlmostEqual(job["camera_acquire_ms"], 150)
        self.assertAlmostEqual(job["request_to_done_ms"], 2150)
        # The worker explicitly stops the physical preview sender before the
        # simulated board is released, so the final operation is the stop.
        self.assertEqual(preview_operations, [1, 2, 1, 2])
        self.assertTrue(np.all(pipeline.run.call_args.args[0] == 80))
        self.assertEqual(console.pending.unfinished_tasks, 0)
        self.assertFalse(console.connected)

    def test_worker_reconnect_cancels_camera_jobs_queued_behind_it(self):
        console = self.console
        opened = []
        jobs = []

        class SimulatedBoard:
            def __init__(self, *_, **__):
                opened.append(self)
                if len(opened) == 2:
                    console.stopped.set()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def ping(self, **kwargs):
                return {"firmware_version": 0x26100133}

            def capabilities(self):
                return {"camera_preview": True, "compressed_write": True}

            def preview(self, operation):
                pass

            def read_camera(self, timeout):
                jobs.append(console.enqueue("reconnect"))
                jobs.append(console.enqueue("infer", "camera0"))

        with patch("demo_server.Board", SimulatedBoard), \
                patch.object(console.stopped, "wait", return_value=False):
            console.worker()
        self.assertEqual(len(opened), 2)
        self.assertEqual(console.jobs[jobs[0]]["status"], "done")
        self.assertEqual(console.jobs[jobs[1]]["status"], "failed")
        self.assertIn("connection changed", console.jobs[jobs[1]]["error"])
        self.assertEqual(console.pending.unfinished_tasks, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
