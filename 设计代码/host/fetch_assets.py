#!/usr/bin/env python3
"""Fetch reproducible model and image assets without executing remote code."""

import argparse
import base64
import hashlib
import http.client
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = [
    {
        "path": "test_images/astronaut.png",
        "url": "https://api.github.com/repos/scikit-image/scikit-image/contents/skimage/data/astronaut.png?ref=v0.22.0",
        "github_json": True,
        "source": "scikit-image v0.22.0; NASA portrait of Eileen Collins",
    },
    {
        "path": "test_images/obama.jpg",
        "url": "https://api.github.com/repos/ageitgey/face_recognition/contents/examples/obama.jpg",
        "github_json": True,
        "source": "face_recognition examples; official portrait of Barack Obama",
    },
    {
        "path": "test_images/obama2.jpg",
        "url": "https://api.github.com/repos/ageitgey/face_recognition/contents/examples/obama2.jpg",
        "github_json": True,
        "source": "face_recognition examples; second photograph for a positive pair",
    },
    {
        "path": "test_images/biden.jpg",
        "url": "https://api.github.com/repos/ageitgey/face_recognition/contents/examples/biden.jpg",
        "github_json": True,
        "source": "face_recognition examples; portrait of Joe Biden",
    },
    {
        "path": "models/LICENSE-MOBILEFACENET",
        "url": "https://api.github.com/repos/foamliu/MobileFaceNet/contents/LICENSE?ref=v1.0",
        "github_json": True,
        "source": "foamliu/MobileFaceNet v1.0; Apache-2.0",
    },
    {
        "path": "models/MobileFaceNet.onnx",
        "url": "https://api.github.com/repos/foamliu/MobileFaceNet/git/blobs/5d111882bdd6d3452884da4c3da428654cdfe3c2",
        "github_json": True,
        "git_blob": "5d111882bdd6d3452884da4c3da428654cdfe3c2",
        "source": "foamliu/MobileFaceNet trained ONNX model; immutable Git blob",
    },
    {
        "path": "models/MobileFaceNet.onnx.data",
        "url": "https://api.github.com/repos/foamliu/MobileFaceNet/git/blobs/7173db8cd015744356a30166814b30c72204f6c9",
        "github_json": True,
        "git_blob": "7173db8cd015744356a30166814b30c72204f6c9",
        "source": "foamliu/MobileFaceNet external ONNX weights; immutable Git blob",
    },
    {
        "path": "../third_party/jpeg_ls/RTL/jls_encoder.v",
        "url": "https://api.github.com/repos/WangXuan95/FPGA-JPEG-LS-encoder/git/blobs/cbfd9720db09b6dc8209a81c83ed581070a39b3c",
        "github_json": True,
        "git_blob": "cbfd9720db09b6dc8209a81c83ed581070a39b3c",
        "source": "WangXuan95/FPGA-JPEG-LS-encoder 6bce7c5c21bd7c17bc2a0796171c1ac28a09d959; GPL-3.0",
    },
    {
        "path": "../third_party/jpeg_ls/LICENSE",
        "url": "https://api.github.com/repos/WangXuan95/FPGA-JPEG-LS-encoder/git/blobs/871ce8e638ad6d763308e44411d2c4a2e658cf55",
        "github_json": True,
        "git_blob": "871ce8e638ad6d763308e44411d2c4a2e658cf55",
        "source": "WangXuan95/FPGA-JPEG-LS-encoder; GPL-3.0",
    },
    {
        "path": "../third_party/charls/charls.native.4.1.1.nupkg",
        "url": "https://api.nuget.org/v3-flatcontainer/charls.native/4.1.1/charls.native.4.1.1.nupkg",
        "github_json": False,
        "source": "CharLS.Native 4.1.1 NuGet package; BSD-3-Clause",
    },
]


def request_bytes(url):
    request = urllib.request.Request(url, headers={"User-Agent": "fpga-vision/1.0"})
    with urllib.request.urlopen(request, timeout=40) as response:
        return response.read()


def fetch(asset):
    failure = None
    for attempt in range(3):
        try:
            data = request_bytes(asset["url"])
            if asset["github_json"]:
                record = json.loads(data)
                if record.get("encoding") == "base64" and record.get("content"):
                    data = base64.b64decode(record["content"], validate=False)
                else:
                    data = request_bytes(record["download_url"])
            if len(data) < 100:
                raise ValueError("Downloaded asset is unexpectedly small")
            if asset.get("git_blob"):
                header = f"blob {len(data)}\0".encode()
                if hashlib.sha1(header + data).hexdigest() != asset["git_blob"]:
                    raise ValueError("Git blob checksum mismatch")
            return data
        except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException) as error:
            failure = error
            print(f"RETRY {asset['path']} {attempt + 1}: {error}", flush=True)
            time.sleep(1)
    raise RuntimeError(f"Cannot fetch {asset['path']}: {failure}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--images-only", action="store_true")
    args = parser.parse_args()
    manifest_path = ROOT / "assets.json"
    existing = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"assets": []}
    records = {entry["path"]: entry for entry in existing["assets"]}
    for asset in ASSETS:
        if args.images_only and not asset["path"].startswith("test_images/"):
            continue
        target = ROOT / asset["path"]
        if target.exists() and asset["path"] in records:
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != records[asset["path"]]["sha256"]:
                raise RuntimeError(f"Asset has changed: {target}")
            print(f"VERIFIED {asset['path']} {actual}", flush=True)
            continue
        data = fetch(asset)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(target)
        record = dict(asset, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
        records[asset["path"]] = record
        manifest_path.write_text(json.dumps({"assets": list(records.values())}, indent=2) + "\n")
        print(f"FETCHED {asset['path']} {len(data)} bytes {record['sha256']}", flush=True)


if __name__ == "__main__":
    main()
