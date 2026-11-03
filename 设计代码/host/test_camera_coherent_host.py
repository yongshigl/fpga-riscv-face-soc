from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from demo_server import Console
from hardware_face_demo import HardwareFacePipeline
from hardware_transport import BoardError


class CoherentHostTests(unittest.TestCase):
    def test_exact_locked_frame_readback_and_changed_frame_rejection(self):
        pixels = (np.arange(180 * 320, dtype=np.uint32) * 179).astype(np.uint16).reshape(180, 320)
        raw = pixels.astype(">u2").tobytes()
        status = [7, 4, 7, 921600, 720, 180, 7, 3]
        calls = []
        statuses = []

        def command(operation, payload=b""):
            calls.append((operation, payload))
            if operation == 17:
                statuses.append(operation)
                return struct.pack("<8I", *status)
            self.assertEqual(operation, 20)
            offset, count = struct.unpack("<II", payload)
            self.assertEqual(offset % 2, 0)
            self.assertLessEqual(count, 4092)
            return raw[offset:offset + count]

        pipeline = object.__new__(HardwareFacePipeline)
        pipeline.board = SimpleNamespace(capabilities=lambda: {"camera_coherent_capture": True}, command=command)
        image = pipeline.read_camera_frame()
        red, green, blue = (pixels >> 11) & 31, (pixels >> 5) & 63, pixels & 31
        expected = np.stack([(blue << 3) | (blue >> 2), (green << 2) | (green >> 4),
                             (red << 3) | (red >> 2)], axis=2).astype(np.uint8)
        np.testing.assert_array_equal(image, expected)
        self.assertEqual(len(statuses), 2)
        self.assertEqual(sum(struct.unpack("<II", payload)[1] for operation, payload in calls if operation == 20), len(raw))
        original = command
        statuses.clear()

        def changed(operation, payload=b""):
            if operation == 17 and statuses:
                status[6] += 1
            return original(operation, payload)

        pipeline.board.command = changed
        with self.assertRaisesRegex(RuntimeError, "changed during readback"):
            pipeline.read_camera_frame()

    def test_multiple_faces_use_locked_bank_and_keep_separate_alignments(self):
        pipeline = object.__new__(HardwareFacePipeline)
        pipeline.identity = {"cpu_hz": 144000000, "ai_hz": 72000000}
        pipeline.threshold_q15, pipeline.gallery = 18022, {}
        pipeline.detector = SimpleNamespace(manifest={"layers": [{}], "image_address": 0x80080000},
                                            read_outputs=Mock(return_value={}))
        pipeline.embedding = SimpleNamespace(manifest={})
        calls = []

        def command(operation, payload=b"", **kwargs):
            calls.append((operation, payload))
            if operation == 16:
                return struct.pack("<IQ", 1, 400)
            if operation == 17:
                return struct.pack("<8I", 1, 4, 1, 0, 0, 0, 1, 3)
            self.assertEqual(operation, 18)
            self.assertEqual(struct.unpack("<7I", payload)[-1], 0x83400000)
            return struct.pack("<II", 1, 100)

        pipeline.board = SimpleNamespace(
            capabilities=lambda: {"camera_tensor": True, "camera_coherent_capture": True}, command=command,
            run=Mock(return_value={"layers": [{"cycles": 100}], "cpu_cycles": 200}),
            match=Mock(return_value={"identity_index": None, "cosine": 0, "gallery_cosines": [],
                                    "embedding_q15": [0] * 128, "cpu_cycles": 30}))
        faces = np.zeros((2, 15), dtype=np.float32)
        faces[:, :4] = [10, 20, 30, 40]
        faces[:, 14] = 0.9
        with patch("hardware_face_demo.decode_yunet", return_value=faces), \
                patch("hardware_face_demo.cv2.estimateAffinePartial2D", return_value=(np.eye(2, 3), None)):
            record = pipeline.run_camera_onboard()
        self.assertEqual(record["frame_bank"], 1)
        self.assertEqual(len(record["faces"]), 2)
        self.assertEqual([operation for operation, _ in calls], [16, 17, 18, 18])

    def test_onboard_decode_returns_faces_and_feeds_board_affine(self):
        pipeline = object.__new__(HardwareFacePipeline)
        pipeline.identity = {"cpu_hz": 100000000, "ai_hz": 72000000}
        pipeline.threshold_q15, pipeline.gallery = 18022, {0: "Alice"}
        pipeline.detector = SimpleNamespace(manifest={"layers": [{}], "image_address": 0x80080000})
        pipeline.embedding = SimpleNamespace(manifest={})
        calls = []
        affine = [65536, 0, -1024, 0, 65536, 512]
        record_bytes = struct.pack("<21i", 64, 128, 320, 320,
                                   *range(10), 58982, *affine)

        def command(operation, payload=b"", **kwargs):
            calls.append((operation, payload))
            if operation == 21:
                self.assertEqual(struct.unpack("<IIIII", payload),
                                 (0x80080000, 0, 1, HardwareFacePipeline.SCORE_THRESHOLD_Q16,
                                  HardwareFacePipeline.NMS_THRESHOLD_Q16))
                return struct.pack("<8I", 1, 1, 7, 2, 400, 0, 900, 0) + record_bytes
            if operation == 17:
                return struct.pack("<8I", 1, 4, 1, 0, 0, 0, 5, 3)
            self.assertEqual(operation, 18)
            self.assertEqual(struct.unpack("<7I", payload),
                             (*[value & 0xFFFFFFFF for value in affine], 0x83400000))
            return struct.pack("<II", 1, 250)

        pipeline.board = SimpleNamespace(
            capabilities=lambda: {"camera_tensor": True, "camera_coherent_capture": True,
                                  "camera_face_decode": True}, command=command,
            run=Mock(return_value={"layers": [{"cycles": 100}], "cpu_cycles": 200}),
            match=Mock(return_value={"identity_index": 0, "cosine": 0.9, "gallery_cosines": [0.9],
                                    "embedding_q15": [0] * 128, "cpu_cycles": 30}))
        record = pipeline.run_camera_onboard()
        self.assertEqual([operation for operation, _ in calls], [21, 17, 18])
        self.assertEqual((record["candidates"], record["dropped_candidates"]), (7, 2))
        self.assertEqual(record["decode_cpu_cycles"], 900)
        self.assertEqual(len(record["faces"]), 1)
        face = record["faces"][0]
        self.assertEqual(face["box"], [0.25, 0.5, 1.25, 1.25])
        self.assertEqual(face["landmarks"][4], [8 / 256, 9 / 256])
        self.assertAlmostEqual(face["confidence"], 0.9, places=4)
        self.assertEqual(face["affine_q16"], affine)
        self.assertEqual(face["identity"], "Alice")
        self.assertIn("none", record["host_work"])

    def test_onboard_decode_failure_falls_back_to_host_decode(self):
        pipeline = object.__new__(HardwareFacePipeline)
        pipeline.identity = {"cpu_hz": 100000000, "ai_hz": 72000000}
        pipeline.threshold_q15, pipeline.gallery = 18022, {}
        pipeline.detector = SimpleNamespace(manifest={"layers": [{}], "image_address": 0x80080000},
                                            read_outputs=Mock(return_value={}))
        pipeline.embedding = SimpleNamespace(manifest={})

        def command(operation, payload=b"", **kwargs):
            if operation == 21:
                raise BoardError(21, 4, b"")
            if operation == 16:
                return struct.pack("<IQ", 1, 400)
            if operation == 17:
                return struct.pack("<8I", 1, 4, 1, 0, 0, 0, 1, 3)
            self.assertEqual(operation, 18)
            return struct.pack("<II", 1, 100)

        pipeline.board = SimpleNamespace(
            capabilities=lambda: {"camera_tensor": True, "camera_coherent_capture": True,
                                  "camera_face_decode": True}, command=command,
            run=Mock(return_value={"layers": [{"cycles": 100}], "cpu_cycles": 200}),
            match=Mock(return_value={"identity_index": None, "cosine": 0, "gallery_cosines": [],
                                    "embedding_q15": [0] * 128, "cpu_cycles": 30}))
        faces = np.zeros((1, 15), dtype=np.float32)
        faces[:, :4] = [10, 20, 30, 40]
        faces[:, 14] = 0.9
        with patch("hardware_face_demo.decode_yunet", return_value=faces), \
                patch("hardware_face_demo.cv2.estimateAffinePartial2D", return_value=(np.eye(2, 3), None)):
            record = pipeline.run_camera_onboard()
        self.assertIn("BoardError", record["face_decode_fallback"])
        self.assertTrue(record["host_work"].startswith("YuNet"))
        self.assertEqual(len(record["faces"]), 1)

    def console(self, output):
        with patch("demo_server.threading.Thread"):
            console = Console("COM7", "vision", Path("models"), Path(output))
        console.connected = console.models_ready = True
        console.capabilities = {"camera_preview": True, "camera_tensor": True, "camera_coherent_capture": True}
        return console

    def test_primary_and_continuous_camera0_jobs_use_onboard_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            console = self.console(directory)
            identifier = console.enqueue("infer", "camera0")
            item = console.pending.get_nowait()
            console.pending.task_done()
            self.assertEqual(item[:2], (identifier, "infer_onboard"))
            console.set_continuous(True, "camera0")
            self.assertEqual(console.next_continuous_job()[1], "infer_onboard")

    def test_raw_preview_allows_wire_time_without_accepting_old_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            console = self.console(directory)
            console.board = SimpleNamespace(read_camera=Mock())
            image = np.zeros((180, 320, 3), np.uint8)
            info = {"camera": 1, "sequence": 1, "received_monotonic": 100,
                    "estimated_frame_age_ms": 1300, "format": 3, "encoded_bytes": 115200}
            console.frames[1] = (image, b"", info)
            with patch("demo_server.time.monotonic", return_value=100):
                selected, metadata = console.acquire_camera_input("camera1")
            np.testing.assert_array_equal(selected, image)
            self.assertEqual(metadata["camera_sequence"], 1)
            info["estimated_frame_age_ms"] = 4000
            ticks = iter([100, 100, 100, 104, 104])
            with patch("demo_server.time.monotonic", side_effect=lambda: next(ticks)):
                with self.assertRaises(TimeoutError):
                    console.acquire_camera_input("camera1")


if __name__ == "__main__":
    unittest.main()
