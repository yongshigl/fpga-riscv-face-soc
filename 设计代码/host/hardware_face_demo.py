#!/usr/bin/env python3
"""Face detection/embedding on FPGA, with scheduling and matching on RISC-V."""
import argparse
import datetime
import hashlib
import json
import struct
import time
from pathlib import Path

import cv2
import numpy as np

from face_pipeline import ALIGNMENT_POINTS, annotate, read_image, write_image
from fpga_model import Model, align_face, decode_yunet, detector_input, embedding_input
from hardware_transport import Board, BoardError
from board_evidence import validate_project

ROOT = Path(__file__).resolve().parents[1]


class HardwareFacePipeline:
    def __init__(self, board, models, identity=None, threshold_q15=None):
        self.board = board
        self.identity = identity or board.ping()
        self.detector = Model(models, "yunet")
        self.embedding = Model(models, "mobilefacenet")
        self.gallery = {}
        if self.identity["firmware_version"] >= 0x26100131:
            self.threshold_q15 = board.threshold(threshold_q15)
        elif threshold_q15 is None or threshold_q15 == 18022:
            self.threshold_q15 = 18022
        else:
            raise ValueError("The board firmware must be updated to configure its matching threshold")

    def read_camera_frame(self):
        if not self.board.capabilities().get("camera_coherent_capture"):
            raise ValueError("Reading a frozen camera frame requires coherent capture firmware")
        before = struct.unpack("<8I", self.board.command(17))
        if not before[7] & 1:
            raise RuntimeError("Camera source frame is not locked")
        data = bytearray()
        for offset in range(0, 320 * 180 * 2, 4092):
            count = min(4092, 320 * 180 * 2 - offset)
            block = self.board.command(20, struct.pack("<II", offset, count))
            if len(block) != count:
                raise RuntimeError("Camera frame readback has an incorrect length")
            data.extend(block)
        after = struct.unpack("<8I", self.board.command(17))
        if after != before:
            raise RuntimeError("Camera source frame changed during readback")
        pixels = np.frombuffer(data, ">u2").reshape(180, 320)
        red, green, blue = (pixels >> 11) & 31, (pixels >> 5) & 63, pixels & 31
        return np.stack([(blue << 3) | (blue >> 2), (green << 2) | (green >> 4),
                         (red << 3) | (red >> 2)], axis=2).astype(np.uint8)

    def detect(self, image):
        start = time.monotonic()
        payload = self.detector.input_bytes(detector_input(image))
        upload_ms = self.board.upload(self.detector.manifest["input"]["address"], payload)
        upload = dict(self.board.last_upload_stats)
        profile = self.board.run(self.detector.manifest)
        begin = time.monotonic()
        outputs = self.detector.read_outputs(self.board)
        download_ms = (time.monotonic() - begin) * 1000
        begin = time.monotonic()
        faces = decode_yunet(outputs, image.shape)
        return faces, {"upload_ms": upload_ms, "upload": upload,
                       "input_sha256": hashlib.sha256(payload).hexdigest(),
                       "download_ms": download_ms, "profile": profile,
                       "output_readback": self.detector.last_output_read_stats,
                       "host_decode_ms": (time.monotonic() - begin) * 1000,
                       "accelerator_ms": sum(item["cycles"] for item in profile["layers"]) * 1000 / self.identity["ai_hz"],
                       "cpu_schedule_ms": profile["cpu_cycles"] * 1000 / self.identity["cpu_hz"],
                       "wall_ms": (time.monotonic() - start) * 1000}

    def embed_and_match(self, image, face):
        start = time.monotonic()
        aligned = align_face(image, face)
        align_ms = (time.monotonic() - start) * 1000
        payload = self.embedding.input_bytes(embedding_input(aligned))
        upload_ms = self.board.upload(self.embedding.manifest["input"]["address"], payload)
        upload = dict(self.board.last_upload_stats)
        profile = self.board.run(self.embedding.manifest)
        match = self.board.match(self.embedding.manifest)
        identity = self.gallery.get(match["identity_index"], "Unknown")
        return {"box": [float(value) for value in face[:4]], "landmarks": face[4:14].reshape(5, 2).tolist(),
                "confidence": float(face[14]), "identity": identity, "cosine": match["cosine"],
                "gallery_scores": {name: match["gallery_cosines"][index] for index, name in self.gallery.items()},
                "embedding_q15": match["embedding_q15"], "cpu_match": match,
                "host_alignment_ms": align_ms, "upload_ms": upload_ms, "upload": upload,
                "input_sha256": hashlib.sha256(payload).hexdigest(), "profile": profile,
                "accelerator_ms": sum(item["cycles"] for item in profile["layers"]) * 1000 / self.identity["ai_hz"],
                "cpu_schedule_ms": profile["cpu_cycles"] * 1000 / self.identity["cpu_hz"],
                "cpu_matching_ms": match["cpu_cycles"] * 1000 / self.identity["cpu_hz"],
                "embedding_ms": (time.monotonic() - start) * 1000}

    def run_camera_onboard(self):
        """Use the OV5640 -> DDR tensor/frame -> accelerator path without host image input."""
        capabilities = self.board.capabilities()
        if not capabilities.get("camera_tensor"):
            raise ValueError("This bitstream does not provide the on-board camera tensor path")
        # The on-board decoder is the deliverable, but the console must not lose a
        # demo if it ever misbehaves: fall back to the proven host decode instead.
        fallback = None
        if capabilities.get("camera_face_decode"):
            try:
                return self.run_faces_onboard(capabilities)
            except (BoardError, RuntimeError, struct.error) as error:
                fallback = f"{type(error).__name__}: {error}"
        start = time.monotonic()
        layers = len(self.detector.manifest["layers"])
        begin = time.monotonic()
        reply = self.board.command(
            16, struct.pack("<III", self.detector.manifest["image_address"], 0, layers), timeout=60)
        count, detect_cycles = struct.unpack_from("<IQ", reply)
        if count != layers:
            raise RuntimeError("Board returned an incorrect YuNet layer count")
        detect_ms = (time.monotonic() - begin) * 1000
        outputs = self.detector.read_outputs(self.board)
        faces = decode_yunet(outputs, (720, 1280, 3))
        status = struct.unpack("<8I", self.board.command(17, b""))
        if not status[7] & 1:
            raise RuntimeError("Camera detection did not retain its source frame lock")
        if len(faces) > 1 and not capabilities.get("camera_coherent_capture"):
            raise RuntimeError("Multiple-face alignment requires coherent camera capture firmware")
        bank = (status[7] >> 1) & 1
        records = []
        for face in faces:
            landmarks = np.asarray(face[4:14], dtype=np.float32).reshape(5, 2)
            matrix, _ = cv2.estimateAffinePartial2D(
                landmarks, ALIGNMENT_POINTS, method=cv2.LMEDS)
            if matrix is None or not np.isfinite(matrix).all():
                raise RuntimeError("Face landmarks do not define a valid alignment transform")
            inverse = cv2.invertAffineTransform(matrix)
            coefficients = [int(round(value * 65536)) & 0xFFFFFFFF
                            for value in inverse.reshape(-1)]
            begin = time.monotonic()
            reply = self.board.command(
                18, struct.pack("<7I", *coefficients, (0x83000000, 0x83400000)[bank]),
                timeout=60)
            completed, align_cycles = struct.unpack("<II", reply)
            if not completed:
                raise RuntimeError("Board affine engine did not complete")
            align_ms = (time.monotonic() - begin) * 1000
            profile = self.board.run(self.embedding.manifest)
            match = self.board.match(self.embedding.manifest)
            identity = self.gallery.get(match["identity_index"], "Unknown")
            records.append({
                "box": [float(value) for value in face[:4]],
                "landmarks": face[4:14].reshape(5, 2).tolist(),
                "confidence": float(face[14]),
                "identity": identity, "cosine": match["cosine"],
                "gallery_scores": {name: match["gallery_cosines"][index]
                                   for index, name in self.gallery.items()},
                "embedding_q15": match["embedding_q15"], "cpu_match": match,
                "affine_ms": align_ms, "affine_cycles": align_cycles,
                "profile": profile,
                "accelerator_ms": sum(item["cycles"] for item in profile["layers"]) * 1000 / self.identity["ai_hz"],
                "cpu_schedule_ms": profile["cpu_cycles"] * 1000 / self.identity["cpu_hz"],
                "cpu_matching_ms": match["cpu_cycles"] * 1000 / self.identity["cpu_hz"],
            })
        record = {
            "execution": "OV5640 RGB565 -> DDR frame/tensor -> FPGA YuNet -> HW affine -> FPGA MobileFaceNet -> RISC-V match",
            "host_work": "YuNet output decode/NMS and 5-point affine coefficient calculation",
            "timing_scope": "Board capture wait, detection, output readback, alignment, embedding and matching; model upload excluded",
            "match_threshold": self.threshold_q15 / 32768,
            "match_threshold_q15": self.threshold_q15,
            "board": self.identity, "faces": records,
            "image_width": 1280, "image_height": 720,
            "frame_sequence": status[6], "frame_flags": status[7], "frame_bank": bank,
            "detect_ms": detect_ms, "detect_cpu_cycles": detect_cycles,
            "total_ms": (time.monotonic() - start) * 1000,
        }
        if fallback:
            record["face_decode_fallback"] = fallback
        return record

    # The decoder thresholds the board applies, matching decode_yunet defaults.
    SCORE_THRESHOLD_Q16 = int(round(0.7 * 65536))
    NMS_THRESHOLD_Q16 = int(round(0.3 * 65536))

    def board_faces(self):
        """Runs capture, detection, decode, NMS and alignment fits on the RISC-V core."""
        layers = len(self.detector.manifest["layers"])
        payload = struct.pack("<IIIII", self.detector.manifest["image_address"], 0, layers,
                              self.SCORE_THRESHOLD_Q16, self.NMS_THRESHOLD_Q16)
        reply = self.board.command(21, payload, timeout=180)
        values = struct.unpack_from(f"<{len(reply) // 4}I", reply)
        if len(values) < 8 or values[0] != layers:
            raise RuntimeError("Board returned an incorrect face-decode response")
        found, candidates, dropped = values[1], values[2], values[3]
        detect_cycles = values[4] | values[5] << 32
        decode_cycles = values[6] | values[7] << 32
        if len(reply) != 32 + found * 84:
            raise RuntimeError("Malformed on-board face list")
        faces = []
        for index in range(found):
            record = struct.unpack_from("<21i", reply, 32 + index * 84)
            points = record[4:14]
            faces.append({
                "box": [value / 256 for value in record[0:4]],
                "landmarks": [[points[point * 2] / 256, points[point * 2 + 1] / 256] for point in range(5)],
                "confidence": record[14] / 65536,
                "affine_q16": list(record[15:21]),
            })
        return {"faces": faces, "candidates": candidates, "dropped": dropped,
                "detect_cycles": detect_cycles, "decode_cycles": decode_cycles}

    def run_faces_onboard(self, capabilities):
        """Full on-board chain: the RISC-V core returns faces, the host only relays."""
        start = time.monotonic()
        begin = time.monotonic()
        decoded = self.board_faces()
        decode_ms = (time.monotonic() - begin) * 1000
        status = struct.unpack("<8I", self.board.command(17, b""))
        if not status[7] & 1:
            raise RuntimeError("Camera detection did not retain its source frame lock")
        if len(decoded["faces"]) > 1 and not capabilities.get("camera_coherent_capture"):
            raise RuntimeError("Multiple-face alignment requires coherent camera capture firmware")
        bank = (status[7] >> 1) & 1
        records = []
        for face in decoded["faces"]:
            coefficients = [value & 0xFFFFFFFF for value in face["affine_q16"]]
            reply = self.board.command(
                18, struct.pack("<7I", *coefficients, (0x83000000, 0x83400000)[bank]), timeout=60)
            completed, align_cycles = struct.unpack("<II", reply)
            if not completed:
                raise RuntimeError("Board affine engine did not complete")
            profile = self.board.run(self.embedding.manifest)
            match = self.board.match(self.embedding.manifest)
            identity = self.gallery.get(match["identity_index"], "Unknown")
            records.append({
                "box": face["box"], "landmarks": face["landmarks"],
                "confidence": face["confidence"], "affine_q16": face["affine_q16"],
                "identity": identity, "cosine": match["cosine"],
                "gallery_scores": {name: match["gallery_cosines"][index]
                                   for index, name in self.gallery.items()},
                "embedding_q15": match["embedding_q15"], "cpu_match": match,
                "affine_cycles": align_cycles, "affine_ms": align_cycles * 1000 / self.identity["cpu_hz"],
                "profile": profile,
                "accelerator_ms": sum(item["cycles"] for item in profile["layers"]) * 1000 / self.identity["ai_hz"],
                "cpu_schedule_ms": profile["cpu_cycles"] * 1000 / self.identity["cpu_hz"],
                "cpu_matching_ms": match["cpu_cycles"] * 1000 / self.identity["cpu_hz"],
            })
        return {
            "execution": "OV5640 RGB565 -> DDR frame/tensor -> FPGA YuNet -> RISC-V decode/NMS/landmarks/affine -> HW affine -> FPGA MobileFaceNet -> RISC-V match",
            "host_work": "none: the RISC-V core decodes the detector, suppresses overlaps, fits the alignment and matches",
            "timing_scope": "Board capture wait, detection, on-board decode and NMS, alignment, embedding and matching; model upload excluded",
            "match_threshold": self.threshold_q15 / 32768,
            "match_threshold_q15": self.threshold_q15,
            "board": self.identity, "faces": records,
            "image_width": 1280, "image_height": 720,
            "frame_sequence": status[6], "frame_flags": status[7], "frame_bank": bank,
            "detect_ms": decode_ms, "detect_cpu_cycles": decoded["detect_cycles"],
            "decode_cpu_cycles": decoded["decode_cycles"],
            "decode_ms": decoded["decode_cycles"] * 1000 / self.identity["cpu_hz"],
            "candidates": decoded["candidates"], "dropped_candidates": decoded["dropped"],
            "total_ms": (time.monotonic() - start) * 1000,
        }

    def enroll_camera_onboard(self, name):
        result = self.run_camera_onboard()
        if len(result["faces"]) != 1:
            raise ValueError(f"Enrollment requires one face; {name} has {len(result['faces'])}")
        index = len(self.gallery)
        self.board.enroll(index)
        self.gallery[index] = name
        return result["faces"][0]

    def enroll(self, name, image):
        faces, _ = self.detect(image)
        if len(faces) != 1:
            raise ValueError(f"Enrollment requires one face; {name} has {len(faces)}")
        record = self.embed_and_match(image, faces[0])
        index = len(self.gallery)
        self.board.enroll(index)
        self.gallery[index] = name
        return record

    def run(self, image):
        start = time.monotonic()
        faces, detection = self.detect(image)
        records = [self.embed_and_match(image, face) for face in faces]
        uploads = [detection["upload"], *[record["upload"] for record in records]]
        upload = {key: sum(item[key] for item in uploads) for key in
                  ("input_bytes", "wire_bytes", "compressed_blocks", "raw_blocks", "milliseconds")}
        return {"execution": "FPGA INT8 YuNet/MobileFaceNet; RISC-V scheduling, normalization and gallery matching",
                "host_work": "Image resize/quantization, YuNet output decode/NMS and landmark alignment",
                "timing_scope": "Image processing includes host work and UART transfers; CPU schedule time includes accelerator waiting; camera exposure/capture is excluded",
                "match_threshold": self.threshold_q15 / 32768, "match_threshold_q15": self.threshold_q15,
                "board": self.identity,
                "image_width": image.shape[1], "image_height": image.shape[0],
                "input_width": 320, "input_height": 192, "faces": records,
                "upload": upload,
                "detection_ms": detection["wall_ms"], "detection": detection,
                "total_ms": (time.monotonic() - start) * 1000}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", type=Path, default=ROOT / "build/vision/fpga")
    parser.add_argument("--output", type=Path, default=ROOT / "build/vision/hardware_demo")
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--skip-model-upload", action="store_true")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--enroll", action="append", default=[], help="Name=path to one enrollment portrait")
    parser.add_argument("--threshold", type=Path, help="Independent DevTrain threshold artifact")
    parser.add_argument("--project", type=Path, help="Frozen PDS project with its successful JTAG program record")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    threshold = json.loads(args.threshold.read_text()) if args.threshold else None
    if threshold and (threshold["devtest_labels_used_for_selection"] or threshold["devtest_image_overlap"]):
        raise ValueError("Threshold selection used the test set")
    with Board(args.port, bridge_log=args.output / "bridge.log") as board:
        pipeline = HardwareFacePipeline(board, args.models, threshold_q15=threshold["threshold_q15"] if threshold else None)
        project = validate_project(args.project, pipeline.identity)
        if threshold and any(threshold["model_hashes"][model.name] != model.manifest["model_sha256"]
                             for model in (pipeline.detector, pipeline.embedding)):
            raise ValueError("Threshold was trained with different model files")
        print("Board:", json.dumps(pipeline.identity), flush=True)
        if not args.skip_model_upload:
            for model in (pipeline.detector, pipeline.embedding):
                print(f"Uploading/checking {model.name}", flush=True)
                model.upload(board, progress=lambda done, total: print(f"  {done}/{total}", flush=True))
        board.command(8)
        enrollment = args.enroll or [f"Obama={ROOT / 'vision/test_images/obama.jpg'}", f"Biden={ROOT / 'vision/test_images/biden.jpg'}"]
        enrolled = []
        for specification in enrollment:
            name, path = specification.split("=", 1)
            print(f"Enrolling {name} on RISC-V", flush=True)
            record = pipeline.enroll(name, read_image(path))
            enrolled.append({"name": name, "path": str(path), "image_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                             "embedding_q15": record["embedding_q15"]})
        images = [args.image] if args.image else [ROOT / "vision/test_images" / name for name in ("obama.jpg", "obama2.jpg", "biden.jpg", "astronaut.png")]
        records = []
        expected = {"obama.jpg": "Obama", "obama2.jpg": "Obama", "biden.jpg": "Biden", "astronaut.png": "Unknown"}
        for path in images:
            print(f"FPGA inference: {path.name}", flush=True)
            image = read_image(path)
            record = pipeline.run(image)
            record["path"] = str(path)
            record["image_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            write_image(args.output / f"{path.stem}_fpga.png", annotate(image, record, "FPGA + RISC-V"))
            (args.output / f"{path.stem}.json").write_text(json.dumps(record, indent=2) + "\n", encoding="ascii")
            print(json.dumps({"image": path.name, "total_ms": record["total_ms"],
                              "faces": [{"identity": face["identity"], "cosine": face["cosine"]} for face in record["faces"]]}), flush=True)
            if not args.image and not args.enroll:
                if len(record["faces"]) != 1 or record["faces"][0]["identity"] != expected[path.name]:
                    raise RuntimeError(f"FPGA demonstration identity differs: {path.name}")
            records.append(record)
        counters = board.counters()
        if counters["rx_errors"]:
            raise RuntimeError("UART receive errors during inference")
    report = {"measured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "board": pipeline.identity,
              "project_evidence": project,
              "gallery": enrolled, "images": records, "counters": counters,
              "scope": "Demonstration images; separate from recognition dataset accuracy",
              "models": {model.name: {"model_sha256": model.manifest["model_sha256"], "image_sha256": model.manifest["image_sha256"]}
                         for model in (pipeline.detector, pipeline.embedding)},
              "threshold_training_sha256": hashlib.sha256(args.threshold.read_bytes()).hexdigest() if threshold else None}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")
    print("PASS: FPGA face detection/embedding and RISC-V gallery matching", flush=True)


if __name__ == "__main__":
    main()
