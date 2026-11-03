"""YuNet detection and MobileFaceNet embeddings for the host reference."""

import hashlib
import json
import time
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

ROOT = Path(__file__).resolve().parent
ALIGNMENT_POINTS = np.array([
    [38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
    [41.5493, 92.3655], [70.7299, 92.2041],
], dtype=np.float32)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_image(path):
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Cannot read image: {path}")
    return image


def write_image(path, image):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(path.suffix, image)
    if not ok:
        raise ValueError(f"Cannot encode image: {path}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    encoded.tofile(str(temporary))
    temporary.replace(path)


def letterbox(image, size=(320, 192)):
    width, height = size
    ratio = min(width / image.shape[1], height / image.shape[0])
    resized_width = max(1, min(width, round(image.shape[1] * ratio)))
    resized_height = max(1, min(height, round(image.shape[0] * ratio)))
    scaled = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_AREA)
    padded = np.zeros((height, width, 3), dtype=np.uint8)
    padded[:resized_height, :resized_width] = scaled
    return padded, (resized_width / image.shape[1], resized_height / image.shape[0])


def normalize(feature):
    feature = np.asarray(feature, dtype=np.float32).reshape(-1)
    norm = np.linalg.norm(feature)
    if not np.all(np.isfinite(feature)) or norm < 1e-8:
        raise ValueError("Feature model returned an invalid embedding")
    return feature / norm


def quantize_embedding(feature):
    return np.clip(np.rint(normalize(feature) * 32767), -32767, 32767).astype("<i2")


class FacePipeline:
    def __init__(self, input_size=(320, 192), score_threshold=0.7, match_threshold=0.55):
        self.input_size = input_size
        self.match_threshold = match_threshold
        self.detector_path = ROOT / "models/face_detection_yunet_2023mar.onnx"
        self.embedding_path = ROOT / "models/MobileFaceNet.onnx"
        self.detector = cv2.FaceDetectorYN.create(
            str(self.detector_path), "", input_size, score_threshold, 0.3, 5000,
        )
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.embedding = ort.InferenceSession(
            str(self.embedding_path), sess_options=options, providers=["CPUExecutionProvider"],
        )
        self.embedding_input = self.embedding.get_inputs()[0].name
        self.gallery = {}

    def detect(self, image):
        padded, (scale_x, scale_y) = letterbox(image, self.input_size)
        _, faces = self.detector.detect(padded)
        if faces is None:
            return np.empty((0, 15), dtype=np.float32)
        faces = faces.copy()
        faces[:, [0, 2, 4, 6, 8, 10, 12]] /= scale_x
        faces[:, [1, 3, 5, 7, 9, 11, 13]] /= scale_y
        return faces

    def align(self, image, face):
        landmarks = np.asarray(face[4:14], dtype=np.float32).reshape(5, 2)
        matrix, _ = cv2.estimateAffinePartial2D(
            landmarks, ALIGNMENT_POINTS, method=cv2.LMEDS,
        )
        if matrix is None or not np.all(np.isfinite(matrix)):
            raise ValueError("Cannot align the detected face")
        return cv2.warpAffine(image, matrix, (112, 112), flags=cv2.INTER_LINEAR)

    def embed_aligned(self, aligned):
        # The upstream training/evaluation pipeline uses OpenCV BGR, not RGB.
        tensor = ((aligned.astype(np.float32) - 127.5) / 128.0).transpose(2, 0, 1)[None]
        feature = self.embedding.run(None, {self.embedding_input: tensor})[0]
        return normalize(feature)

    def enroll(self, name, image):
        faces = self.detect(image)
        if len(faces) != 1:
            raise ValueError(f"Enrollment requires one face; {name} has {len(faces)}")
        feature = self.embed_aligned(self.align(image, faces[0]))
        self.gallery[name] = feature
        return feature

    def match(self, feature):
        if not self.gallery:
            return "Unknown", None, {}
        scores = {name: float(np.dot(feature, reference)) for name, reference in self.gallery.items()}
        identity = max(scores, key=scores.get)
        return (identity if scores[identity] >= self.match_threshold else "Unknown"), scores[identity], scores

    def run(self, image):
        start = time.perf_counter()
        faces = self.detect(image)
        detection_ms = (time.perf_counter() - start) * 1000
        records = []
        for face in faces:
            begin = time.perf_counter()
            aligned = self.align(image, face)
            feature = self.embed_aligned(aligned)
            embedding_ms = (time.perf_counter() - begin) * 1000
            begin = time.perf_counter()
            identity, score, scores = self.match(feature)
            matching_ms = (time.perf_counter() - begin) * 1000
            records.append({
                "box": [float(x) for x in face[:4]],
                "landmarks": face[4:14].reshape(5, 2).tolist(),
                "confidence": float(face[14]), "identity": identity,
                "cosine": score, "gallery_scores": scores,
                "embedding_ms": embedding_ms, "matching_ms": matching_ms,
                "embedding": feature.tolist(), "embedding_q15": quantize_embedding(feature).tolist(),
            })
        return {
            "execution": "host CPU reference; no FPGA inference in this result",
            "input_width": self.input_size[0], "input_height": self.input_size[1],
            "image_width": image.shape[1], "image_height": image.shape[0],
            "match_threshold": self.match_threshold, "detection_ms": detection_ms,
            "total_ms": (time.perf_counter() - start) * 1000, "faces": records,
        }

    def save_gallery(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        records = []
        for index, (name, feature) in enumerate(self.gallery.items()):
            vector = quantize_embedding(feature)
            binary = path.parent / f"gallery_{index}.bin"
            vector.tofile(str(binary))
            records.append({"index": index, "name": name, "embedding": feature.tolist(),
                            "embedding_q15": vector.tolist(), "binary": str(binary), "sha256": sha256(binary)})
        path.write_text(json.dumps({"dimension": 128, "format": "signed Q15 LE", "identities": records}, indent=2) + "\n")


def annotate(image, result, title="Host Reference"):
    display, (scale_x, scale_y) = letterbox(image, (640, 384))
    for face in result["faces"]:
        x, y, width, height = face["box"]
        x0, y0 = max(0, round(x * scale_x)), max(0, round(y * scale_y))
        x1, y1 = min(639, round((x + width) * scale_x)), min(383, round((y + height) * scale_y))
        color = (65, 173, 75) if face["identity"] != "Unknown" else (25, 180, 230)
        cv2.rectangle(display, (x0, y0), (x1, y1), color, 2)
        label = face["identity"]
        if face["cosine"] is not None:
            label += f" {face['cosine']:.3f}"
        if face.get("track_id") is not None:
            label += f"  #{face['track_id']}"
        score = (face.get("liveness") or {}).get("score")
        if score is not None:
            label += f" live {score * 100:.0f}%"
        text_width, text_height = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)[0]
        label_x = min(x0, max(0, 638 - text_width))
        label_y = max(text_height + 5, y0 - 4)
        cv2.rectangle(display, (label_x, label_y - text_height - 4),
                      (label_x + text_width + 4, label_y + 3), color, -1)
        cv2.putText(display, label, (label_x + 2, label_y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (15, 15, 15), 1, cv2.LINE_AA)
        for point in face["landmarks"]:
            cv2.circle(display, (round(point[0] * scale_x), round(point[1] * scale_y)), 2, (255, 220, 30), -1)
    output = np.full((430, 640, 3), 247, dtype=np.uint8)
    output[34:418] = display
    label = f"{title} | {len(result['faces'])} faces | {result['total_ms']:.1f} ms"
    cv2.putText(output, label, (12, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (32, 35, 38), 1, cv2.LINE_AA)
    return output
