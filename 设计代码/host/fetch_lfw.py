#!/usr/bin/env python3
"""Fetch the hash-pinned LFW development split used by scikit-learn."""
import argparse
import hashlib
import json
import shutil
import tarfile
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath

SOURCES = (
    ("lfw-funneled.tgz", "5976015", "b47c8422c8cded889dc5a13418c4bc2abbda121092b3533a83306f90d900100a"),
    ("pairsDevTrain.txt", "5976012", "1d454dada7dfeca0e7eab6f65dc4e97a6312d44cf142207be28d688be92aabfa"),
    ("pairsDevTest.txt", "5976009", "7cb06600ea8b2814ac26e946201cdb304296262aad67d046a16a7ec85d0ff87c"),
)


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_ranges(url, target, workers):
    request = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(request, timeout=45) as response:
        size = int(response.headers["Content-Length"])
        if response.headers.get("Accept-Ranges") != "bytes":
            raise RuntimeError("The dataset endpoint does not support byte ranges")
    chunk_size = 8 * 1024 * 1024
    parts = target.parent / (target.name + ".parts")
    parts.mkdir(exist_ok=True)
    ranges = [(start, min(size, start + chunk_size)) for start in range(0, size, chunk_size)]
    # Preserve complete blocks from a previous sequential download.
    if target.exists():
        with target.open("rb") as source:
            for start, end in ranges:
                if end > target.stat().st_size:
                    break
                part = parts / f"{start:012d}"
                block = source.read(end - start)
                if not part.exists():
                    part.write_bytes(block)

    def fetch(bounds):
        start, end = bounds
        part = parts / f"{start:012d}"
        if part.exists() and part.stat().st_size == end - start:
            return end - start
        for attempt in range(3):
            temporary = part.with_suffix(".partial")
            request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end-1}"})
            try:
                with urllib.request.urlopen(request, timeout=45) as response, temporary.open("wb") as stream:
                    if response.status != 206 or response.headers.get("Content-Range") != f"bytes {start}-{end-1}/{size}":
                        raise RuntimeError("Unexpected response to a dataset range request")
                    shutil.copyfileobj(response, stream, 64 * 1024)
                if temporary.stat().st_size != end - start:
                    raise IOError("Incomplete dataset range")
                temporary.replace(part)
                return end - start
            except (OSError, urllib.error.URLError):
                if attempt == 2:
                    raise
        raise RuntimeError("Dataset range download failed")

    received = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(fetch, bounds) for bounds in ranges]):
            received += future.result()
            print(f"  {target.name}: {received // (1024*1024)}/{size // (1024*1024)} MiB verified length", flush=True)
    with target.open("wb") as stream:
        for start, _ in ranges:
            with (parts / f"{start:012d}").open("rb") as source:
                shutil.copyfileobj(source, stream)
    return parts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "datasets/lfw")
    parser.add_argument("--download-workers", type=int, default=8, choices=range(1, 17))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    for name, remote_id, checksum in SOURCES:
        path = args.output / name
        object_name = "lfwfunneled.tgz" if name == "lfw-funneled.tgz" else name
        url = f"https://s3-eu-west-1.amazonaws.com/pfigshare-u-files/{remote_id}/{object_name}"
        if not path.exists() or file_hash(path) != checksum:
            temporary = path.with_suffix(path.suffix + ".partial")
            print(f"Downloading {name}", flush=True)
            endpoints = (url, f"https://ndownloader.figshare.com/files/{remote_id}?fpga_download={time.time_ns()}")
            parts = None
            if name == "lfw-funneled.tgz" and args.download_workers > 1:
                parts = download_ranges(url, temporary, args.download_workers)
                if file_hash(temporary) != checksum:
                    raise RuntimeError(f"Dataset hash mismatch: {name}")
                temporary.replace(path)
                shutil.rmtree(parts)
                records.append({"path": name, "url": url, "sha256": checksum, "bytes": path.stat().st_size})
                continue
            response = None
            for endpoint in endpoints:
                request = urllib.request.Request(endpoint,
                    headers={"User-Agent": "FPGA-RISC-V-model-evaluation/1.0"})
                try:
                    response = urllib.request.urlopen(request, timeout=60)
                    url = endpoint
                    break
                except urllib.error.URLError:
                    if endpoint == endpoints[-1]:
                        raise
            with response, temporary.open("wb") as stream:
                received = 0
                next_report = 16 * 1024 * 1024
                while block := response.read(1024 * 1024):
                    stream.write(block)
                    received += len(block)
                    if received >= next_report:
                        print(f"  {name}: {received // (1024 * 1024)} MiB", flush=True)
                        next_report += 16 * 1024 * 1024
            if file_hash(temporary) != checksum:
                raise RuntimeError(f"Dataset hash mismatch: {name}")
            temporary.replace(path)
        records.append({"path": name, "url": url, "sha256": checksum, "bytes": path.stat().st_size})
    marker = args.output / "extraction.json"
    if not marker.exists():
        count = 0
        ancillary_files = []
        with tarfile.open(args.output / SOURCES[0][0], "r:gz") as archive:
            for member in archive:
                relative = PurePosixPath(member.name)
                if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "lfw_funneled":
                    raise RuntimeError(f"Unexpected dataset member: {member.name}")
                if member.isdir():
                    (args.output / str(relative)).mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    destination = args.output / str(relative)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as source, destination.open("wb") as target:
                        shutil.copyfileobj(source, target)
                    if relative.suffix.lower() == ".jpg":
                        count += 1
                    else:
                        ancillary_files.append(str(relative))
                else:
                    raise RuntimeError("Dataset archive contains a non-regular member")
        if count != 13233:
            raise RuntimeError(f"Unexpected LFW image count: {count}")
        marker.write_text(json.dumps({"images": count, "ancillary_files": ancillary_files,
                                     "archive_sha256": SOURCES[0][2]}, indent=2) + "\n")
    manifest = {"dataset": "Labeled Faces in the Wild, funneled images",
                "metadata_source": "scikit-learn 1.7.2 sklearn/datasets/_lfw.py",
                "splits": "DevTrain for calibration; DevTest for evaluation. Exclude evaluation images from calibration.",
                "source": "http://vis-www.cs.umass.edu/lfw/", "files": records}
    (args.output / "sources.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="ascii")
    print("LFW download hashes and 13233 extracted images verified", flush=True)


if __name__ == "__main__":
    main()
