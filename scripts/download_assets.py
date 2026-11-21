"""Download the demo assets (YOLOv8n weights + coco128) through reachable mirrors.

The repository ships the assets for offline demos, so this is idempotent and
best-effort. Run it after setting up the Python environment:

    .venv/Scripts/python scripts/download_assets.py
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "backend" / "data" / "assets"
DATASETS = ROOT / "backend" / "data" / "datasets"
WEIGHTS = ROOT / "backend" / "data" / "weights"
for path in (ASSETS, DATASETS, WEIGHTS):
    path.mkdir(parents=True, exist_ok=True)

WEIGHT_URLS = [
    "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8n.pt",
    "https://ghproxy.net/https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8n.pt",
    "https://hf-mirror.com/Ultralytics/YOLOv8/resolve/main/yolov8n.pt",
    "https://ghfast.top/https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8n.pt",
]
DATASET_URLS = [
    "https://ultralytics.com/assets/coco128.zip",
    "https://ghproxy.net/https://github.com/ultralytics/assets/releases/download/v0.0.0/coco128.zip",
    "https://gh-proxy.com/https://github.com/ultralytics/assets/releases/download/v0.0.0/coco128.zip",
    "https://ghfast.top/https://github.com/ultralytics/assets/releases/download/v0.0.0/coco128.zip",
]


def download(urls: list[str], target: Path, label: str) -> None:
    if target.exists() and target.stat().st_size > 1024:
        print(f"{label}: already present ({target.stat().st_size / 1e6:.1f} MB)")
        return
    for url in urls:
        try:
            print(f"{label}: trying {url}")
            with requests.get(url, stream=True, timeout=40, allow_redirects=True) as response:
                if response.status_code != 200:
                    print(f"  HTTP {response.status_code}")
                    continue
                total = int(response.headers.get("Content-Length") or 0)
                temp = target.with_suffix(target.suffix + ".part")
                written = 0
                with temp.open("wb") as handle:
                    for chunk in response.iter_content(512 * 1024):
                        handle.write(chunk)
                        written += len(chunk)
                        if total and written % (5 * 1024 * 1024) < 512 * 1024:
                            print(f"  {written / 1e6:.1f}/{total / 1e6:.1f} MB", flush=True)
                if total and written < total:
                    raise IOError(f"incomplete download ({written}/{total} bytes)")
                temp.replace(target)
                print(f"{label}: downloaded {written / 1e6:.1f} MB")
                return
        except Exception as exc:  # noqa: BLE001 - try the next mirror
            print(f"  failed: {type(exc).__name__}: {str(exc)[:180]}")
    raise SystemExit(
        f"Could not download {label}. Download it manually into {target.parent} and retry."
    )


def safe_extract(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            target = (destination / member.filename).resolve()
            if root not in target.parents and target != root:
                raise SystemExit(f"unsafe archive member: {member.filename}")
        zf.extractall(destination)


def main() -> int:
    weight = WEIGHTS / "yolov8n.pt"
    dataset_root = DATASETS / "coco128"
    images = dataset_root / "images" / "train2017"
    labels = dataset_root / "labels" / "train2017"
    yaml_file = dataset_root / "coco128.yaml"

    download(WEIGHT_URLS, weight, "yolov8n.pt")

    if images.is_dir() and labels.is_dir():
        print("coco128: already extracted")
    else:
        archive = ASSETS / "coco128.zip"
        download(DATASET_URLS, archive, "coco128.zip")
        print("coco128: extracting...")
        safe_extract(archive, DATASETS)
        if not images.is_dir() or not labels.is_dir():
            raise SystemExit("coco128 archive layout was not recognised")

    if not yaml_file.is_file():
        print(
            f"warning: {yaml_file} is missing. Restore the copied coco128.yaml from the repo.",
            file=sys.stderr,
        )
    else:
        print(f"coco128: ready ({len(list(images.glob('*')))} images)")
    print("assets are ready under backend/data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
