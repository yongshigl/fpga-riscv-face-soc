"""Host-side extras that do not change the bitstream: face tracking, a liveness
hint and rolling latency statistics.

Everything here runs on the PC. Nothing in this module is required by the
on-board recognition path, so a failure here can never break a recognition job.
"""
import collections

import cv2
import numpy as np

DETAIL_HEIGHT = 96        # detail is measured on a crop resized to this height
DETAIL_REFERENCE = 600.0  # Laplacian variance a live face reaches at 96 px
SPECULAR_REFERENCE = 0.12  # blown-out share treated as fully suspicious
MOTION_REFERENCE = 6.0     # grey-level difference that counts as clearly moving


def intersection_over_union(first, second):
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    left, top = max(ax, bx), max(ay, by)
    right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if right <= left or bottom <= top:
        return 0.0
    overlap = (right - left) * (bottom - top)
    union = aw * ah + bw * bh - overlap
    return overlap / union if union > 0 else 0.0


class FaceTracker:
    """Assigns stable track numbers by greedy IoU matching across jobs."""

    def __init__(self, iou_threshold=0.25, max_age=8):
        self.iou_threshold = iou_threshold
        self.max_age = max_age
        self.next_id = 0
        self.tracks = {}

    def update(self, boxes):
        pairs = []
        for index, box in enumerate(boxes):
            for track_id, track in self.tracks.items():
                pairs.append((intersection_over_union(box, track["box"]), index, track_id))
        pairs.sort(reverse=True)
        assigned_faces, assigned_tracks = {}, set()
        for overlap, index, track_id in pairs:
            if overlap < self.iou_threshold or index in assigned_faces or track_id in assigned_tracks:
                continue
            assigned_faces[index] = track_id
            assigned_tracks.add(track_id)
        identifiers = []
        for index, box in enumerate(boxes):
            track_id = assigned_faces.get(index)
            if track_id is None:
                track_id = self.next_id
                self.next_id += 1
            self.tracks[track_id] = {"box": box, "age": 0}
            identifiers.append(track_id)
        for track_id in list(self.tracks):
            if track_id not in assigned_tracks and track_id not in identifiers:
                self.tracks[track_id]["age"] += 1
                if self.tracks[track_id]["age"] > self.max_age:
                    del self.tracks[track_id]
        return identifiers


def liveness_hint(image, box, previous=None):
    """Advisory anti-spoofing hint computed from one face crop (and optionally the
    previous crop of the same track).

    Signals, all cheap and all explainable:
      detail     - Laplacian variance of the crop normalised to a fixed 96 px
                   height, so the number means the same thing whether the frame
                   is a 320 px DDR readback or a full-resolution upload. A
                   printed photo re-photographed at close range loses texture; a
                   live face keeps pores and edges.
      specular   - share of near-saturated pixels inside the crop. Screens and
                   glossy prints reflect the room light in flat blobs.
      motion     - mean absolute difference against the previous crop of the same
                   track. A live person is never pixel-identical twice; a photo
                   held still is close to identical.

    `moire` is reported but deliberately not scored: photographing a screen
    produces periodic patterns, yet so does fine hair, and the separation has
    not been measured.

    Returns a dict with the raw signals plus a score in [0, 1]. It is a hint, not
    a gate: recognition results are never suppressed by it.
    """
    height, width = image.shape[:2]
    x, y, w, h = (int(round(value)) for value in box)
    left, top = max(0, min(width - 1, x)), max(0, min(height - 1, y))
    right, bottom = max(left + 8, min(width, x + w)), max(top + 8, min(height, y + h))
    crop = image[top:bottom, left:right]
    if crop.size == 0:
        return {"score": None, "detail": None, "specular": None, "motion": None}
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    scale = DETAIL_HEIGHT / gray.shape[0]
    normalised = cv2.resize(gray, (max(8, int(round(gray.shape[1] * scale))), DETAIL_HEIGHT),
                            interpolation=cv2.INTER_AREA)
    detail = float(cv2.Laplacian(normalised, cv2.CV_64F).var())
    specular = float(np.count_nonzero(gray >= 245) / gray.size)
    motion = None
    if previous is not None and previous.shape == gray.shape:
        motion = float(np.mean(cv2.absdiff(gray, previous)))
    small = cv2.resize(gray, (64, 64)).astype(np.float32)
    spectrum = np.abs(np.fft.fftshift(np.fft.fft2(small)))
    centre = spectrum[24:40, 24:40].sum()
    ring = spectrum.sum() - centre
    moire = float(ring / (centre + 1e-6))
    score, weight = 0.0, 0.0
    score += 0.40 * min(1.0, detail / DETAIL_REFERENCE)
    weight += 0.40
    score += 0.25 * max(0.0, 1.0 - min(1.0, specular / SPECULAR_REFERENCE))
    weight += 0.25
    if motion is not None:
        score += 0.35 * min(1.0, motion / MOTION_REFERENCE)
        weight += 0.35
    return {"score": round(score / weight, 3) if weight else None,
            "detail": round(detail, 1), "specular": round(specular, 4),
            "motion": None if motion is None else round(motion, 2),
            "moire": round(moire, 2), "crop": gray}


class LatencyStats:
    """Rolling window statistics for the metrics panel."""

    def __init__(self, window=40):
        self.samples = collections.deque(maxlen=window)
        self.detections = 0
        self.jobs = 0

    def add(self, milliseconds, faces):
        self.jobs += 1
        if faces:
            self.detections += 1
        if milliseconds is not None:
            self.samples.append(float(milliseconds))

    def summary(self):
        if not self.samples:
            return {"samples": 0}
        values = sorted(self.samples)
        return {"samples": len(values),
                "min_ms": round(values[0], 1),
                "median_ms": round(values[len(values) // 2], 1),
                "max_ms": round(values[-1], 1),
                "avg_ms": round(sum(values) / len(values), 1),
                "detection_rate": round(self.detections / self.jobs, 3) if self.jobs else None}
