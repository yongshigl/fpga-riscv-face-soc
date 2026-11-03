"""A silent serial stream must reconnect without interrupting deliberate pauses."""
from collections import deque
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from demo_server import Console


class CameraLivenessTests(unittest.TestCase):
    def console(self):
        with patch("demo_server.threading.Thread"):
            return Console("COM7", "vision", Path("models"), self.output)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)

    def test_startup_pause_and_resume_allow_missing_frames(self):
        console = self.console()
        console.preview_started = 100
        with patch("demo_server.time.monotonic", return_value=109):
            console.check_camera_liveness()
        console.preview_enabled = False
        with patch("demo_server.time.monotonic", return_value=1000):
            console.check_camera_liveness()
        console.preview_enabled = True
        console.preview_started = 1000
        with patch("demo_server.time.monotonic", return_value=1009):
            console.check_camera_liveness()

    def test_one_silent_camera_fails_even_when_the_other_is_fresh(self):
        console = self.console()
        console.preview_started = 100
        console.frame_times = [deque([120]), deque([114])]
        with patch("demo_server.time.monotonic", return_value=120):
            with self.assertRaisesRegex(TimeoutError, "CAM1"):
                console.check_camera_liveness()
        console.frame_times[1].append(115)
        with patch("demo_server.time.monotonic", return_value=120):
            console.check_camera_liveness()

    def test_worker_reopens_a_live_bridge_that_delivers_no_images(self):
        console = self.console()
        now = [100.0]
        opened = []

        class SilentBoard:
            def __init__(self, *args, **kwargs):
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
                now[0] += 6
                console.frame_times[0].append(now[0])

        with patch("demo_server.Board", SilentBoard), \
                patch("demo_server.time.monotonic", side_effect=lambda: now[0]), \
                patch.object(console.stopped, "wait", return_value=False), \
                patch("demo_server.logging.getLogger"):
            console.worker()
        self.assertEqual(len(opened), 2)
        self.assertEqual(console.connection_retries, 1)
        self.assertFalse(console.connected)
        self.assertEqual(console.frames, [None, None])

    def test_failed_reconnection_discards_stale_frames_and_sensor_status(self):
        for failure in ("open", "ping"):
            with self.subTest(failure=failure):
                console = self.console()
                console.connected = console.models_ready = True
                console.identity = {"firmware_version": 0x26100133}
                console.capabilities = {"camera_preview": True, "compressed_write": True}
                console.frames = [(None, b"jpeg", {"camera": index, "received_monotonic": 100})
                                  for index in range(2)]
                console.frame_times = [deque([100, 101]), deque([100, 101])]
                console.frame_count = [23, 24]
                console.sensors = [{"verified": True}, {"verified": True}]
                console.gallery = [{"id": "saved", "name": "Saved identity"}]
                attempts = []
                statuses = []

                class FailingBoard:
                    def __init__(self, *args, **kwargs):
                        attempts.append(self)
                        if len(attempts) == 2:
                            statuses.append(console.status())
                            console.stopped.set()
                        if failure == "open":
                            raise OSError("Serial port unavailable")

                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        return False

                    def ping(self, **kwargs):
                        raise TimeoutError("Board did not respond")

                with patch("demo_server.Board", FailingBoard), \
                        patch.object(console.stopped, "wait", return_value=False), \
                        patch("demo_server.logging.getLogger"):
                    console.worker()
                status = statuses[0]
                self.assertFalse(status["connected"])
                self.assertFalse(status["models_ready"])
                self.assertIsNone(status["board"])
                self.assertFalse(status["capabilities"]["camera_preview"])
                self.assertEqual([camera["frames"] for camera in status["cameras"]], [23, 24])
                for camera in status["cameras"]:
                    self.assertEqual(camera["fps"], 0)
                    self.assertIsNone(camera["stale_seconds"])
                    self.assertIsNone(camera["sensor"])
                    self.assertNotIn("received_monotonic", camera)
                self.assertEqual(status["gallery"], [{"id": "saved", "name": "Saved identity", "index": 0}])


if __name__ == "__main__":
    unittest.main()
