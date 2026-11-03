#!/usr/bin/env python3
"""Local board console with one UART owner and latest-frame browser streams."""
import argparse
import atexit
from collections import deque
import copy
import datetime
import json
import logging
import math
import os
from pathlib import Path
import queue
import threading
import time
import uuid

import cv2
from flask import Flask, Response, abort, jsonify, render_template, request, send_file
import numpy as np
from werkzeug.exceptions import HTTPException

from camera_stream import CAMERA_MAGIC
from face_pipeline import annotate, read_image
from hardware_face_demo import HardwareFacePipeline
from hardware_transport import Board
from live_features import FaceTracker, LatencyStats, liveness_hint
from measurement_evidence import current_reference_accuracy, matches_running_identity, read_measurement

ROOT = Path(__file__).resolve().parents[1]
CAMERA_STARTUP_SECONDS = 10
CAMERA_FRAME_TIMEOUT_SECONDS = 5
CAMERA_INPUT_MAX_AGE_SECONDS = 1
CAMERA_INPUT_TIMEOUT_SECONDS = 3
SAMPLES = {name: ROOT / "vision/test_images" / filename for name, filename in
           [("obama", "obama.jpg"), ("obama2", "obama2.jpg"), ("biden", "biden.jpg"), ("astronaut", "astronaut.png")]}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def encode_jpeg(image):
    success, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not success:
        raise ValueError("Cannot encode the image")
    return data.tobytes()


class Console:
    def __init__(self, port, mode, models, output, baud=921600, restore_models_on_reconnect=False):
        if baud <= 0:
            raise ValueError("Serial baud rate must be positive")
        self.port, self.mode, self.models, self.output = port, mode, Path(models), Path(output)
        self.baud = baud
        self.output.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.stopped = threading.Event()
        self.pending = queue.Queue(maxsize=4)
        self.jobs = {}
        self.frames = [None, None]
        self.frame_times = [deque(maxlen=16), deque(maxlen=16)]
        self.sensors = [None, None]
        self.gallery = read_json(self.output / "gallery.json") if (self.output / "gallery.json").exists() else []
        self.result_image = self.result = None
        self.frame_count = [0, 0]
        self.pipeline = self.board = None
        self.connected = self.models_ready = False
        self.preview_enabled = True
        self.preview_started = None
        self.capabilities = {"camera_preview": False, "compressed_write": False}
        self.identity = None
        self.connection_mode = "offline" if mode == "offline" else "connecting"
        self.error = None
        self.active_job = None
        self.tracker = FaceTracker()
        self.latency = LatencyStats()
        self.track_crops = {}
        self.decode_errors = 0
        self.connection_retries = 0
        self.connection_generation = 0
        self.continuous_enabled = False
        self.continuous_source = None
        self.continuous_reason = None
        self.continuous_job = None
        self.continuous_last_sequence = None
        self.continuous_preview_after = None
        self.restore_models_on_reconnect = restore_models_on_reconnect
        self.models_loaded_once = False
        self.model_restore_state = "idle" if restore_models_on_reconnect else "disabled"
        self.model_restore_error = None
        self.started = time.monotonic()
        self.thread = threading.Thread(target=self.worker, name="fpga-uart", daemon=True)
        self.thread.start()

    def status(self):
        with self.lock:
            cameras = []
            for index in range(2):
                value = copy.deepcopy(self.frames[index][2]) if self.frames[index] else {"camera": index}
                times = self.frame_times[index]
                value.update(frames=self.frame_count[index], sensor=self.sensors[index],
                             fps=(len(times) - 1) / (times[-1] - times[0]) if len(times) > 1 else 0,
                             stale_seconds=time.monotonic() - times[-1] if times else None)
                cameras.append(value)
            return {"connected": self.connected, "mode": self.connection_mode, "port": self.port, "baud": self.baud,
                    "board": self.identity, "capabilities": self.capabilities, "models_ready": self.models_ready,
                    "preview_enabled": self.preview_enabled, "error": self.error, "active_job": self.active_job,
                    "cameras": cameras, "decode_errors": self.decode_errors,
                    "connection_retries": self.connection_retries,
                    "connection_generation": self.connection_generation, "pending_jobs": self.pending.qsize(),
                    "continuous": {"enabled": self.continuous_enabled, "source": self.continuous_source,
                                   "active_job": self.continuous_job, "reason": self.continuous_reason},
                    "model_restore": {"enabled": self.restore_models_on_reconnect,
                                      "armed": self.models_loaded_once,
                                      "state": self.model_restore_state, "error": self.model_restore_error},
                    "bridge_startup_ms": self.board.bridge_startup_ms if self.board else None,
                    "crc_errors": self.board.decoder.crc_errors if self.board else 0,
                    "last_crc_error": self.board.decoder.last_crc_error if self.board else None,
                    "framing_errors": self.board.decoder.framing_errors if self.board else 0,
                    "gallery": [{"index": index, "name": entry["name"], "id": entry["id"]}
                                for index, entry in enumerate(self.gallery)],
                    "app_metrics": self.latency.summary(),
                    "jobs": copy.deepcopy(list(self.jobs.values())[-8:]), "result": self.result,
                    "uptime_seconds": time.monotonic() - self.started}

    def accept_camera(self, packet):
        try:
            if packet.command == 2:
                with self.lock:
                    self.sensors[packet.camera] = packet.sensor()
                return
            image, info = packet.image(baud=self.board.baud)
            jpeg = packet.payload[12:] if packet.format == 5 else encode_jpeg(image)
            with self.changed:
                self.frames[packet.camera] = (image, jpeg, info)
                self.frame_count[packet.camera] += 1
                self.frame_times[packet.camera].append(packet.received_monotonic)
                self.changed.notify_all()
        except (ValueError, cv2.error, RuntimeError) as error:
            with self.lock:
                self.decode_errors += 1
                self.error = f"Camera decode: {error}"

    def check_camera_liveness(self):
        with self.lock:
            now = time.monotonic()
            if (not self.preview_enabled or self.preview_started is None
                    or now - self.preview_started < CAMERA_STARTUP_SECONDS):
                return
            stalled = [f"CAM{index}" for index, times in enumerate(self.frame_times)
                       if not times or now - times[-1] > CAMERA_FRAME_TIMEOUT_SECONDS]
        if stalled:
            raise TimeoutError(f"Camera preview stalled on {', '.join(stalled)}")

    def source(self, specification):
        if specification in ("camera0", "camera1"):
            index = int(specification[-1])
            with self.lock:
                if self.frames[index] is None or time.monotonic() - self.frame_times[index][-1] > 8:
                    raise ValueError("Camera has no recent image")
                return self.frames[index][0].copy()
        if specification in SAMPLES:
            return read_image(SAMPLES[specification])
        raise ValueError("Unknown image source")

    def enqueue(self, action, source=None, image=None, name=None, index=None):
        if source is not None and not isinstance(source, str):
            raise ValueError("Image source must be a string")
        if name is not None and not isinstance(name, str):
            raise ValueError("Identity name must be a string")
        if index is not None and type(index) is not int:
            raise ValueError("Board task index must be an integer")
        if action == "preview" and index not in (0, 1):
            raise ValueError("Preview index must be 0 (pause) or 1 (resume)")
        if action not in ("load_models", "infer", "infer_onboard", "enroll",
                          "clear_gallery", "remove_identity", "preview", "reconnect"):
            raise ValueError("Unknown action")
        if not self.connected:
            raise ValueError("Board is disconnected")
        if (action == "infer" and image is None and source == "camera0" and
                self.capabilities.get("camera_coherent_capture")):
            action = "infer_onboard"
        camera_source = action in ("infer", "enroll") and image is None and source in ("camera0", "camera1")
        if action in ("infer", "enroll") and image is None and not camera_source:
            image = self.source(source)
        elif image is not None:
            image = image.copy()
        if action == "infer_onboard" and source != "camera0":
            raise ValueError("The on-board RGB path is wired to camera 0")
        if action == "enroll":
            name = str(name or "").strip()
            if not name or len(name) > 32:
                raise ValueError("Identity name must contain 1 to 32 characters")
        with self.lock:
            if not self.connected:
                raise ValueError("Board is disconnected")
            if camera_source and not self.preview_enabled:
                raise ValueError("Camera preview is paused; resume it before requesting a camera image")
            if camera_source and not self.capabilities["camera_preview"]:
                raise ValueError("This bitstream does not provide camera preview")
            identifier = self.new_job(action, source, name)
            try:
                self.pending.put_nowait((identifier, action, image, name, index, source))
            except queue.Full:
                del self.jobs[identifier]
                raise ValueError("Board queue is full")
            if self.continuous_enabled:
                self.stop_continuous("Manual board task requested; continuous recognition stopped")
        return identifier

    def new_job(self, action, source=None, name=None, automatic=False, trigger=None):
        """Create a task record while holding the console lock."""
        identifier = uuid.uuid4().hex
        if len(self.jobs) >= 32:
            old = next((key for key, value in self.jobs.items() if value["status"] in ("done", "failed")), None)
            if old:
                del self.jobs[old]
        self.jobs[identifier] = {
            "id": identifier, "action": action, "status": "queued", "progress": 0,
            "source": source, "name": name, "submitted_monotonic": time.monotonic(),
            "connection_generation": self.connection_generation, "queue_wait_ms": None,
            "automatic": automatic, "trigger": trigger,
        }
        return identifier

    def stop_continuous(self, reason):
        with self.lock:
            self.continuous_enabled = False
            self.continuous_reason = reason

    def set_continuous(self, enabled, source=None, reason=None):
        if not isinstance(enabled, bool):
            raise ValueError("Continuous recognition enabled must be true or false")
        with self.lock:
            if not enabled:
                self.stop_continuous(reason or "Continuous recognition stopped")
                return
            if source not in ("camera0", "camera1"):
                raise ValueError("Continuous recognition requires CAM0 or CAM1")
            if not self.connected or not self.models_ready:
                raise ValueError("Connect the board and load its models before continuous recognition")
            if not self.preview_enabled or not self.capabilities["camera_preview"]:
                raise ValueError("Resume camera preview before continuous recognition")
            if self.continuous_enabled and self.continuous_source == source:
                return
            if self.active_job or self.continuous_job or not self.pending.empty():
                raise ValueError("Wait for the current board task before continuous recognition")
            self.continuous_enabled = True
            self.continuous_source = source
            self.continuous_reason = None
            self.continuous_last_sequence = None
            self.continuous_preview_after = None
            self.error = None

    def next_continuous_job(self):
        """The UART worker schedules one automatic task only when manual work is absent."""
        with self.lock:
            if not self.continuous_enabled or self.continuous_job or self.active_job or not self.pending.empty():
                return None
            if not self.connected or not self.models_ready:
                self.stop_continuous("Board or models are unavailable; continuous recognition stopped")
                return None
            if not self.preview_enabled:
                self.stop_continuous("Camera preview is paused; continuous recognition stopped")
                return None
            if self.continuous_preview_after is not None and any(
                    count <= previous for count, previous in zip(self.frame_count, self.continuous_preview_after)):
                return None
            action = ("infer_onboard" if self.continuous_source == "camera0" and
                      self.capabilities.get("camera_coherent_capture") else "infer")
            identifier = self.new_job(action, self.continuous_source, automatic=True, trigger="continuous")
            self.continuous_job = identifier
            return (identifier, action, None, None, None, self.continuous_source)

    def claim_next_job(self):
        """Remove work and publish its busy state in the same console transaction."""
        with self.lock:
            try:
                item = self.pending.get_nowait()
                queued = True
            except queue.Empty:
                item = self.next_continuous_job()
                queued = False
            if item is not None:
                self.active_job = item[0]
                self.jobs[item[0]]["status"] = "running"
            return item, queued

    def acquire_camera_input(self, specification, after_sequence=None):
        """Select a recent frame; only the UART worker may read while waiting for it."""
        camera = int(specification[-1])
        started = time.monotonic()
        deadline = started + CAMERA_INPUT_TIMEOUT_SECONDS
        while not self.stopped.is_set():
            with self.lock:
                if not self.preview_enabled:
                    raise ValueError("Camera preview is paused; resume it before requesting a camera image")
                if not self.connected or self.board is None:
                    raise ValueError("Board disconnected while waiting for a camera image")
                now = time.monotonic()
                frame = self.frames[camera]
                if frame is not None:
                    info = frame[2]
                    host_age_ms = max(0, now - info["received_monotonic"]) * 1000
                    transport_age_ms = info.get("estimated_frame_age_ms")
                    estimated_age_ms = host_age_ms + transport_age_ms if transport_age_ms is not None else None
                    known_age_ms = host_age_ms if estimated_age_ms is None else estimated_age_ms
                    maximum_age_ms = CAMERA_INPUT_MAX_AGE_SECONDS * 1000
                    if info.get("format") == 3 and transport_age_ms is not None:
                        maximum_age_ms += (info.get("encoded_bytes", 0) + 32) * 10000 / self.baud
                    if known_age_ms <= maximum_age_ms and info["sequence"] != after_sequence:
                        return frame[0].copy(), {
                            "camera": camera, "camera_sequence": info["sequence"],
                            "camera_received_monotonic": info["received_monotonic"],
                            "camera_host_age_ms": host_age_ms,
                            "camera_transport_estimated_age_ms": transport_age_ms,
                            "camera_estimated_age_at_selection_ms": estimated_age_ms,
                            "camera_acquire_ms": (now - started) * 1000,
                            "camera_age_timing_scope": "Capture-complete to UART-complete estimate plus time since host packet decode; excludes sensor exposure and unmeasured USB/bridge backlog",
                        }
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"CAM{camera} did not deliver a recent image within {CAMERA_INPUT_TIMEOUT_SECONDS} seconds")
            self.board.read_camera(min(0.15, remaining))
        raise RuntimeError("Console stopped while waiting for a camera image")

    def cancel_pending_jobs(self):
        """A queued action belongs to its original connection and must be resubmitted."""
        while True:
            try:
                identifier, *_ = self.pending.get_nowait()
            except queue.Empty:
                return
            with self.lock:
                self.jobs[identifier].update(status="failed", error="Board connection changed; submit the task again")
            self.pending.task_done()

    def progress(self, identifier, fraction, stage):
        with self.lock:
            self.jobs[identifier].update(progress=round(fraction * 100, 1), stage=stage)

    def save_gallery(self):
        temporary = self.output / "gallery.json.tmp"
        temporary.write_text(json.dumps(self.gallery, indent=2) + "\n", encoding="ascii")
        temporary.replace(self.output / "gallery.json")

    def restore_gallery(self):
        self.board.command(8)
        self.pipeline.gallery.clear()
        for index, entry in enumerate(self.gallery):
            if entry["models"] != self.model_hashes():
                raise ValueError("Saved gallery uses different models")
            embedding = entry["embedding_q15"]
            if len(embedding) != 128 or any(not -32768 <= value <= 32767 for value in embedding):
                raise ValueError("Invalid saved embedding")
            self.board.enroll(index, embedding)
            self.pipeline.gallery[index] = entry["name"]

    def model_hashes(self):
        return {model.name: model.manifest["model_sha256"] for model in (self.pipeline.detector, self.pipeline.embedding)}

    def load_models(self, identifier):
        with self.lock:
            self.models_ready = False
        threshold_path = ROOT / "build/vision/threshold_training/threshold.json"
        threshold = read_json(threshold_path)
        if threshold["devtest_labels_used_for_selection"] or threshold["devtest_image_overlap"]:
            raise ValueError("Threshold training overlaps the test set")
        self.pipeline = HardwareFacePipeline(self.board, self.models, self.identity, threshold["threshold_q15"])
        if threshold["model_hashes"] != self.model_hashes():
            raise ValueError("Threshold and model hashes differ")
        for index, model in enumerate((self.pipeline.detector, self.pipeline.embedding)):
            self.progress(identifier, index / 2, model.name)
            model.upload(self.board, progress=lambda done, total, index=index, model=model:
                         self.progress(identifier, (index + done / total) / 2, model.name))
        self.restore_gallery()
        with self.lock:
            self.models_ready = True
            self.models_loaded_once = True

    def apply_live_features(self, record, image, scale=1.0):
        """Annotates each face with a track number and an advisory liveness hint and
        feeds the rolling latency window.

        Everything here is host-side decoration: if any of it fails the recognition
        record is left exactly as the pipeline produced it.
        """
        faces = record.get("faces") or []
        scaled = [[face["box"][0] * scale, face["box"][1] * scale,
                   face["box"][2] * scale, face["box"][3] * scale] for face in faces]
        try:
            identifiers = self.tracker.update(scaled)
        except Exception as error:
            identifiers = [None] * len(faces)
            record["tracking_error"] = str(error)
        for face, track_id, box in zip(faces, identifiers, scaled):
            face["track_id"] = track_id
            if track_id is None:
                continue
            try:
                hint = liveness_hint(image, box, self.track_crops.get(track_id))
                self.track_crops[track_id] = hint.pop("crop", None)
                face["liveness"] = hint
            except Exception as error:
                face["liveness"] = {"score": None, "error": str(error)}
        for track_id in list(self.track_crops):
            if track_id not in self.tracker.tracks:
                del self.track_crops[track_id]
        with self.lock:
            self.latency.add(record.get("total_ms"), faces)
        return record

    def execute(self, identifier, action, image, name, index, source=None):
        with self.lock:
            job = self.jobs[identifier]
            started = time.monotonic()
            job.update(started_monotonic=started,
                       queue_wait_ms=max(0, started - job["submitted_monotonic"]) * 1000)
            if job["connection_generation"] != self.connection_generation:
                raise ValueError("Board connection changed; submit the task again")
        if action == "reconnect":
            return "reconnect"
        if not self.identity:
            raise ValueError("This bitstream provides camera preview only")
        camera_input = None
        onboard_enrollment = (action == "enroll" and image is None and source == "camera0" and
                              self.capabilities.get("camera_coherent_capture"))
        if action in ("infer", "enroll", "infer_onboard"):
            if not self.models_ready:
                raise ValueError("Load the FPGA models first")
            if action != "infer_onboard" and not onboard_enrollment and image is None and source in ("camera0", "camera1"):
                after_sequence = self.continuous_last_sequence if job["trigger"] == "continuous" else None
                image, camera_input = self.acquire_camera_input(source, after_sequence=after_sequence)
                with self.lock:
                    self.jobs[identifier].update(camera_input)
                    if job["trigger"] == "continuous":
                        self.continuous_last_sequence = camera_input["camera_sequence"]
        restart_preview = self.capabilities["camera_preview"] and self.preview_enabled
        if self.capabilities["camera_preview"]:
            self.board.preview(2)
        try:
            if action == "preview":
                self.preview_enabled = bool(index)
                restart_preview = self.preview_enabled
            elif action == "load_models":
                self.load_models(identifier)
            else:
                if not self.models_ready:
                    raise ValueError("Load the FPGA models first")
                if action == "infer":
                    record = self.pipeline.run(image)
                    self.apply_live_features(record, image)
                    record.update(input_source=source, queue_wait_ms=job["queue_wait_ms"],
                                  camera_input=camera_input,
                                  request_to_result_ms=(time.monotonic() - job["submitted_monotonic"]) * 1000)
                    record["measured_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    jpeg = encode_jpeg(annotate(image, record, "FPGA + RISC-V"))
                    with self.lock:
                        self.result, self.result_image = record, jpeg
                    (self.output / "latest_result.json").write_text(json.dumps(record, indent=2) + "\n", encoding="ascii")
                    (self.output / "latest_result.jpg").write_bytes(jpeg)
                elif action == "infer_onboard":
                    record = self.pipeline.run_camera_onboard()
                    with self.lock:
                        self.jobs[identifier].update(camera=0, camera_sequence=record["frame_sequence"],
                                                     camera_sequence_scope="locked board capture")
                        if job["trigger"] == "continuous":
                            self.continuous_last_sequence = record["frame_sequence"]
                    record.update(input_source="camera0", queue_wait_ms=job["queue_wait_ms"],
                                  request_to_result_ms=(time.monotonic() - job["submitted_monotonic"]) * 1000)
                    record["measured_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    with self.lock:
                        self.result = record
                        self.result_image = None
                    if self.capabilities.get("camera_coherent_capture"):
                        visualization_started = time.monotonic()
                        captured_image = self.pipeline.read_camera_frame()
                        self.apply_live_features(record, captured_image,
                                                 captured_image.shape[1] / 1280.0)
                        preview = cv2.resize(captured_image, (640, 360), interpolation=cv2.INTER_AREA)
                        display = copy.deepcopy(record)
                        for face in display["faces"]:
                            face["box"] = [value * 0.5 for value in face["box"]]
                            face["landmarks"] = [[x * 0.5, y * 0.5] for x, y in face["landmarks"]]
                        jpeg = encode_jpeg(annotate(preview, display, "On-board RGB path"))
                        with self.lock:
                            self.result_image = jpeg
                        record["visualization_readback_ms"] = (time.monotonic() - visualization_started) * 1000
                        record["result_image_frame_sequence"] = record["frame_sequence"]
                    else:
                        with self.lock:
                            self.latency.add(record.get("total_ms"), record.get("faces"))
                    record["request_to_result_ms"] = (time.monotonic() - job["submitted_monotonic"]) * 1000
                    (self.output / "latest_result.json").write_text(json.dumps(record, indent=2) + "\n", encoding="ascii")
                    if self.result_image is not None:
                        (self.output / "latest_result.jpg").write_bytes(self.result_image)
                elif action == "enroll":
                    if len(self.gallery) >= 16 or any(entry["name"] == name for entry in self.gallery):
                        raise ValueError("Gallery is full or the identity name already exists")
                    if onboard_enrollment:
                        record = self.pipeline.enroll_camera_onboard(name)
                        image = self.pipeline.read_camera_frame()
                    else:
                        record = self.pipeline.enroll(name, image)
                    entry = {"id": uuid.uuid4().hex, "name": name, "embedding_q15": record["embedding_q15"],
                             "models": self.model_hashes()}
                    x, y, width, height = record["box"]
                    if onboard_enrollment:
                        x, width = x * image.shape[1] / 1280, width * image.shape[1] / 1280
                        y, height = y * image.shape[0] / 720, height * image.shape[0] / 720
                    left, top = max(0, int(x - width * 0.2)), max(0, int(y - height * 0.2))
                    right = min(image.shape[1], int(x + width * 1.2))
                    bottom = min(image.shape[0], int(y + height * 1.2))
                    thumbnail = image[top:bottom, left:right] if right > left and bottom > top else image
                    (self.output / (entry["id"] + ".jpg")).write_bytes(encode_jpeg(thumbnail))
                    with self.lock:
                        self.gallery.append(entry)
                    self.save_gallery()
                elif action == "clear_gallery":
                    self.board.command(8)
                    self.pipeline.gallery.clear()
                    with self.lock:
                        self.gallery.clear()
                    self.save_gallery()
                elif action == "remove_identity":
                    if not isinstance(index, int) or not 0 <= index < len(self.gallery):
                        raise ValueError("Unknown gallery index")
                    with self.lock:
                        self.gallery.pop(index)
                    self.restore_gallery()
                    self.save_gallery()
        finally:
            if restart_preview:
                self.board.preview(1)
                with self.lock:
                    self.preview_started = time.monotonic()
                    if job["trigger"] == "continuous":
                        self.continuous_preview_after = tuple(self.frame_count)

    def run_job(self, item, queued=True):
        identifier, action, image, name, index, source = item
        with self.lock:
            self.active_job = identifier
            self.jobs[identifier]["status"] = "running"
        reconnect = False
        try:
            reconnect = self.execute(identifier, action, image, name, index, source) == "reconnect"
            with self.lock:
                self.jobs[identifier].update(status="done", progress=100)
                self.error = None
        except Exception as error:
            with self.lock:
                self.jobs[identifier].update(status="failed", error=str(error))
                self.error = str(error)
                if self.jobs[identifier]["trigger"] == "continuous":
                    self.stop_continuous(f"Recognition failed: {error}")
        finally:
            with self.lock:
                job = self.jobs[identifier]
                started = job.get("started_monotonic")
                if started is not None:
                    finished = time.monotonic()
                    job.update(processing_ms=(finished - started) * 1000,
                               request_to_done_ms=(finished - job["submitted_monotonic"]) * 1000)
                self.active_job = None
                if self.continuous_job == identifier:
                    self.continuous_job = None
            if queued:
                self.pending.task_done()
        return reconnect

    def restore_models_after_reconnect(self):
        with self.lock:
            if not self.restore_models_on_reconnect or not self.models_loaded_once or not self.identity:
                return
            self.model_restore_state = "loading"
            self.model_restore_error = None
            identifier = self.new_job("load_models", automatic=True, trigger="reconnect")
        self.run_job((identifier, "load_models", None, None, None, None), queued=False)
        with self.lock:
            passed = self.jobs[identifier]["status"] == "done" and self.models_ready
            self.model_restore_state = "ready" if passed else "failed"
            self.model_restore_error = None if passed else self.jobs[identifier].get("error", "Model restoration failed")

    def worker(self):
        if self.mode == "offline":
            return
        while not self.stopped.is_set():
            try:
                with Board(self.port, baud=self.baud, bridge_log=self.output / "bridge.log", camera_callback=self.accept_camera) as board:
                    self.board = board
                    self.identity = None
                    self.models_ready = False
                    self.pipeline = None
                    if self.mode != "camera":
                        try:
                            # Board construction waits for the Windows COM-port readiness handshake.
                            self.identity = board.ping(timeout=10 if self.mode == "vision" else 2)
                        except TimeoutError:
                            if self.mode == "vision":
                                raise
                    self.capabilities = board.capabilities() if self.identity else {"camera_preview": False, "compressed_write": False}
                    with self.lock:
                        self.frames = [None, None]
                        self.frame_times = [deque(maxlen=16), deque(maxlen=16)]
                        self.sensors = [None, None]
                    if self.capabilities["camera_preview"]:
                        board.preview(1 if self.preview_enabled else 2)
                    self.preview_started = time.monotonic()
                    with self.lock:
                        self.connection_mode = "vision" if self.identity else "camera"
                        self.connection_generation += 1
                        self.connected = True
                        self.error = None
                    self.restore_models_after_reconnect()
                    reconnect = False
                    while not self.stopped.is_set() and not reconnect:
                        item, queued = self.claim_next_job()
                        if item is None:
                            if self.connection_mode == "camera" or self.capabilities["camera_preview"]:
                                board.read_camera(0.15)
                                self.check_camera_liveness()
                            else:
                                self.stopped.wait(0.05)
                            continue
                        reconnect = self.run_job(item, queued=queued)
                    # Stop the physical sender before releasing the COM port.
                    # The user's preview preference stays available for the
                    # next connection, which explicitly resumes it above.
                    if self.capabilities["camera_preview"] and self.preview_enabled:
                        board.preview(2)
            except Exception as error:
                logging.getLogger(__name__).exception("FPGA UART connection failed; retrying")
                with self.lock:
                    self.connection_retries += 1
                    self.error = str(error)
            finally:
                with self.changed:
                    if self.continuous_enabled or self.continuous_job:
                        self.stop_continuous("Board disconnected; continuous recognition stopped")
                    self.connected = self.models_ready = False
                    self.board = self.pipeline = self.identity = None
                    self.capabilities = {"camera_preview": False, "compressed_write": False}
                    self.frames = [None, None]
                    self.frame_times = [deque(maxlen=16), deque(maxlen=16)]
                    self.sensors = [None, None]
                    self.preview_started = None
                    self.changed.notify_all()
                self.cancel_pending_jobs()
            self.stopped.wait(2)

    def close(self):
        self.stop_continuous("Console stopped")
        self.stopped.set()
        with self.changed:
            self.changed.notify_all()
        self.thread.join(timeout=3)

    def stream(self, camera):
        previous = None
        while not self.stopped.is_set():
            with self.changed:
                frame = self.frames[camera]
                if frame is None or previous == frame[2]["received_monotonic"]:
                    self.changed.wait(timeout=1)
                    continue
                previous = frame[2]["received_monotonic"]
                jpeg = frame[1]
            yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n"


def create_app(console, hardware_report=None):
    web = ROOT / "vision/web"
    app = Flask(__name__, template_folder=str(web), static_folder=str(web / "static"))
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

    @app.errorhandler(HTTPException)
    def http_error(error):
        if request.path.startswith("/api/"):
            response = error.get_response()
            response.data = app.json.dumps({"error": error.description})
            response.content_type = "application/json"
            return response
        return error

    @app.after_request
    def no_cache(response):
        if request.path.startswith(("/api/", "/stream/", "/image/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def home():
        return render_template("index.html")

    @app.get("/api/status")
    def status():
        return jsonify(console.status())

    @app.get("/stream/<int:camera>")
    def stream(camera):
        if camera not in (0, 1):
            abort(404)
        return Response(console.stream(camera), mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.get("/image/<source>")
    def image(source):
        if source == "result":
            with console.lock:
                jpeg = console.result_image
            if jpeg is None:
                abort(404)
        elif source in SAMPLES:
            return send_file(SAMPLES[source])
        elif source.startswith("gallery-"):
            identifier = source.removeprefix("gallery-")
            if not any(entry["id"] == identifier for entry in console.gallery):
                abort(404)
            return send_file(console.output / (identifier + ".jpg"))
        else:
            try:
                jpeg = encode_jpeg(console.source(source))
            except ValueError:
                abort(404)
        return Response(jpeg, mimetype="image/jpeg")

    @app.post("/api/jobs")
    def enqueue():
        try:
            if request.is_json:
                data = request.get_json()
                if not isinstance(data, dict):
                    raise ValueError("Board tasks require a JSON object")
                image = None
            else:
                data = request.form
                uploaded = request.files.get("image")
                image = cv2.imdecode(np.frombuffer(uploaded.read(), np.uint8), cv2.IMREAD_COLOR) if uploaded else None
                if uploaded and (image is None or max(image.shape[:2]) > 6000):
                    raise ValueError("Invalid image or dimensions exceed 6000 pixels")
            identifier = console.enqueue(data.get("action"), data.get("source"), image,
                                         data.get("name"), data.get("index"))
            return jsonify(id=identifier), 202
        except (ValueError, cv2.error) as error:
            return jsonify(error=str(error)), 400

    @app.post("/api/continuous")
    def continuous():
        try:
            data = request.get_json() if request.is_json else None
            if not isinstance(data, dict):
                raise ValueError("Continuous recognition requires a JSON object")
            reason = "Image source changed; continuous recognition stopped" if data.get("reason") == "source_changed" else None
            console.set_continuous(data.get("enabled"), data.get("source"), reason)
            return jsonify(console.status()["continuous"])
        except ValueError as error:
            return jsonify(error=str(error)), 400

    @app.get("/api/metrics")
    def metrics():
        cpu = []
        project_root = Path(os.environ.get("FPGA_PROJECTS", str(ROOT.parent / "projects")))
        required_metrics = {"clock_hz", "lut_post_route", "coremark_per_mhz", "coremark_per_second",
                            "coremark_per_lut_post_route", "seconds", "bitstream_sha256", "crc_validated"}
        for project in sorted(project_root.glob("riscv_*")):
            if not (project / "result.json").is_file() or not (project / "manifest.json").is_file():
                continue
            try:
                result = read_json(project / "result.json")
                manifest = read_json(project / "manifest.json")
                if (not isinstance(result, dict) or not isinstance(manifest, dict)
                        or not required_metrics.issubset(result)
                        or result["crc_validated"] is not True or manifest.get("timing_met") is not True
                        or not isinstance(result["bitstream_sha256"], str)
                        or not isinstance(manifest.get("bitstream_sha256"), str)):
                    continue
                numeric = ("clock_hz", "lut_post_route", "coremark_per_mhz", "coremark_per_second",
                           "coremark_per_lut_post_route", "seconds")
                if (any(type(result[key]) not in (int, float) or not math.isfinite(result[key])
                        or result[key] <= 0 for key in numeric)
                        or result["seconds"] < 10
                        or result["bitstream_sha256"].lower() != manifest["bitstream_sha256"].lower()
                        or result["clock_hz"] != manifest["clock_hz"]
                        or result["lut_post_route"] != manifest["lut_post_route"]):
                    continue
            except (OSError, UnicodeError, ValueError, TypeError, KeyError):
                # A build/measurement can still be writing its result; other
                # completed projects remain available in the metrics/export.
                continue
            result["timing_met"] = True
            result["pipeline_stages"] = manifest.get("pipeline_stages", 5)
            result["icache_bytes"] = manifest.get("icache_bytes", 0)
            result["dcache_bytes"] = manifest.get("dcache_bytes", 0)
            cpu.append(result)
        hardware_path = Path(hardware_report) if hardware_report else ROOT / "build/vision/unified_board_validation/report.json"
        if not hardware_report and not hardware_path.exists():
            hardware_path = ROOT / "build/vision/board_validation/report.json"
        hardware = read_measurement(hardware_path)
        running = console.status()
        identity = running["board"] if running["connected"] else None
        hardware_matches_identity = matches_running_identity(hardware, identity)
        accuracy_path = ROOT / "build/vision/lfw_integer_devtest/report.json"
        accuracy = current_reference_accuracy(accuracy_path, console.models, ROOT / "vision/fpga_model.py",
                                             ROOT / "build/vision/threshold_training/threshold.json",
                                             ROOT / "vision/datasets/lfw/pairsDevTest.txt")
        board_accuracy_path = ROOT / "build/vision/lfw_board_devtest/report.json"
        board_accuracy = None
        full = read_measurement(board_accuracy_path)
        if full:
            if full.get("complete"):
                selected = full["metrics"]
                board_accuracy = {"dataset": full["dataset"], "execution": full["execution"],
                                  "accuracy": selected["accuracy_all_pairs_with_failures_counted_wrong"],
                                  "pairs": selected["pairs"], "unique_images": full["unique_images"],
                                  "threshold": selected["threshold"],
                                  "failed_pairs": selected["pairs_with_failed_face_processing"],
                                  "project": full["project_evidence"]["project"],
                                  "bitstream_sha256": full["project_evidence"]["bitstream_sha256"],
                                  "matches_hardware": bool(hardware_matches_identity and
                                      full["project_evidence"]["bitstream_sha256"].lower() ==
                                      hardware["project_evidence"]["bitstream_sha256"].lower())}
        return jsonify(cpu=cpu, hardware=hardware,
                       hardware_matches_running_identity=hardware_matches_identity,
                       host_accuracy=accuracy, board_accuracy=board_accuracy)

    @app.get("/api/report")
    def report():
        exported = console.status()
        exported["metrics"] = metrics().get_json()
        exported["exported_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return Response(json.dumps(exported, indent=2) + "\n", mimetype="application/json",
                        headers={"Content-Disposition": "attachment; filename=fpga-vision-report.json"})

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--restore-models-on-reconnect", action="store_true",
                        help="After an earlier successful load, fully reload and verify models/gallery on reconnection")
    parser.add_argument("--mode", choices=("auto", "camera", "vision", "offline"), default="auto")
    parser.add_argument("--models", type=Path, default=ROOT / "build/vision/fpga")
    parser.add_argument("--output", type=Path, default=ROOT / "build/vision/console")
    parser.add_argument("--http-port", type=int, default=8086)
    parser.add_argument("--hardware-report", type=Path)
    args = parser.parse_args()
    if args.baud <= 0:
        parser.error("Serial baud rate must be positive")
    console = Console(args.port, args.mode, args.models, args.output, baud=args.baud,
                      restore_models_on_reconnect=args.restore_models_on_reconnect)
    atexit.register(console.close)
    print(f"FPGA_CONSOLE_URL http://localhost:{args.http_port}", flush=True)
    create_app(console, args.hardware_report).run(host="127.0.0.1", port=args.http_port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
