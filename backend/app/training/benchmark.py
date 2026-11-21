"""Short, isolated CPU training benchmark.

This script intentionally does not use the control-plane heartbeat path.  It is
launched by BenchmarkService for one configuration at a time, writes a single
JSON result, and exits.  Keeping it separate prevents a benchmark from
polluting the task queue, logs or run history.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--imgsz", type=int, default=160)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--cache", choices=("none", "disk", "ram"), default="none")
    parser.add_argument("--output", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--name", required=True)
    return parser.parse_args()


def _rss_mb() -> float:
    try:
        import psutil

        process = psutil.Process(os.getpid())
        total = process.memory_info().rss
        for child in process.children(recursive=True):
            try:
                total += child.memory_info().rss
            except Exception:
                pass
        return round(total / 1024 / 1024, 1)
    except Exception:
        return 0.0


def main() -> int:
    args = parse_args()
    result: dict[str, Any] = {
        "threads": args.threads,
        "workers": args.workers,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "epochs": args.epochs,
        "cache": args.cache,
        "status": "failed",
        "error": None,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    peak = 0.0
    try:
        threads = max(1, min(args.threads, os.cpu_count() or args.threads))
        os.environ["OMP_NUM_THREADS"] = str(threads)
        os.environ["MKL_NUM_THREADS"] = str(threads)
        os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
        from ultralytics import YOLO
        import torch

        torch.set_num_threads(threads)
        model = YOLO(args.model, task="detect")
        dataset_images = 0
        try:
            import yaml

            with Path(args.data).open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
            root = Path(str(data.get("path") or Path(args.data).parent))
            train = data.get("train")
            if isinstance(train, str):
                candidate = root / train
                if candidate.is_dir():
                    dataset_images = len(
                        [p for p in candidate.rglob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"}]
                    )
        except Exception:
            dataset_images = 0
        started = time.perf_counter()
        model.train(
            data=args.data,
            epochs=args.epochs,
            imgsz=args.imgsz,
            batch=args.batch,
            device="cpu",
            workers=args.workers,
            cache=False if args.cache == "none" else args.cache,
            project=args.project,
            name=args.name,
            exist_ok=True,
            plots=False,
            val=False,
            save=False,
            verbose=False,
            seed=0,
            deterministic=True,
            amp=False,
        )
        elapsed = max(0.001, time.perf_counter() - started)
        peak = max(peak, _rss_mb())
        result.update(
            {
                "status": "succeeded",
                "elapsed_seconds": round(elapsed, 3),
                "images": dataset_images,
                "images_per_second": round(dataset_images * args.epochs / elapsed, 2)
                if dataset_images
                else None,
                "peak_rss_mb": peak,
            }
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
