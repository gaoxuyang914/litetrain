"""CPU YOLO training worker.

Launched by the scheduler (inside a Ray actor by default).  The worker is
intentionally small and independent of the web process:

* emits a periodic heartbeat and process resource samples to the control plane;
* forwards epoch-level YOLO metrics (losses, mAP, learning rates);
* always writes an atomic ``result.json`` on exit so the platform can recover
  the outcome even after an API restart.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import shutil
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RESULT_ENV = "LITETRAIN_RESULT_PATH"
TOKEN_ENV = "LITETRAIN_WORKER_TOKEN"
API_ENV = "LITETRAIN_WORKER_API_BASE"
INTERVAL_ENV = "LITETRAIN_HEARTBEAT_INTERVAL"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _write_result(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


class ProgressState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.progress = 0.0
        self.message = "worker starting"

    def update(self, progress: float, message: str | None = None) -> None:
        with self._lock:
            self.progress = max(0.0, min(100.0, float(progress)))
            if message:
                self.message = message

    def snapshot(self) -> tuple[float, str]:
        with self._lock:
            return self.progress, self.message


class ControlPlaneClient:
    def __init__(self, task_id: str, base_url: str, token: str) -> None:
        import requests

        self.task_id = task_id
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-Worker-Token": token,
                "User-Agent": "LiteTrain-Worker/1.0",
            }
        )
        self._inactive = threading.Event()

    def _url(self, suffix: str) -> str:
        return f"{self.base_url}/internal/tasks/{self.task_id}{suffix}"

    def heartbeat(self, payload: dict[str, Any]) -> None:
        response = self.session.post(self._url("/heartbeat"), json=payload, timeout=3.5)
        if response.status_code in (403, 404, 409):
            print(
                f"[platform] heartbeat rejected ({response.status_code}); "
                "task is no longer runnable, stopping worker",
                flush=True,
            )
            os._exit(1)
        response.raise_for_status()

    def metrics(self, payload: dict[str, Any]) -> None:
        response = self.session.post(self._url("/metrics"), json=payload, timeout=5.0)
        if response.status_code in (403, 404, 409):
            print(f"[platform] metric sink rejected ({response.status_code}); stopping", flush=True)
            os._exit(1)
        response.raise_for_status()

    def checkpoint(self, payload: dict[str, Any]) -> None:
        response = self.session.post(self._url("/checkpoints"), json=payload, timeout=8.0)
        if response.status_code in (403, 404, 409):
            print(
                f"[platform] checkpoint sink rejected ({response.status_code}); continuing",
                flush=True,
            )
            return
        response.raise_for_status()

    def close(self) -> None:
        self.session.close()


class HeartbeatReporter(threading.Thread):
    def __init__(
        self,
        client: ControlPlaneClient,
        state: ProgressState,
        interval: float,
    ) -> None:
        super().__init__(name="litetrain-heartbeat", daemon=True)
        self.client = client
        self.state = state
        self.interval = max(1.0, interval)
        self.stop_event = threading.Event()
        self._reported_http_error = False
        self._process = None
        try:
            import psutil

            self._psutil = psutil
            self._process = psutil.Process(os.getpid())
            self._process.cpu_percent(None)
        except Exception:
            self._psutil = None

    def _sample(self) -> tuple[float, float, int]:
        if self._psutil is None or self._process is None:
            return 0.0, 0.0, 1
        try:
            processes = [self._process]
            try:
                processes.extend(self._process.children(recursive=True))
            except Exception:
                pass
            cpu = 0.0
            memory = 0.0
            for process in processes:
                try:
                    cpu += process.cpu_percent(None)
                    memory += process.memory_info().rss / (1024 * 1024)
                except Exception:
                    continue
            return round(cpu, 2), round(memory, 2), max(1, len(processes))
        except Exception:
            return 0.0, 0.0, 1

    def run(self) -> None:
        while not self.stop_event.is_set():
            progress, message = self.state.snapshot()
            cpu, memory, count = self._sample()
            payload = {
                "pid": os.getpid(),
                "cpu_percent": cpu,
                "memory_mb": memory,
                "process_count": count,
                "progress": progress,
                "message": message,
            }
            try:
                self.client.heartbeat(payload)
                self._reported_http_error = False
            except Exception as exc:
                if not self._reported_http_error:
                    print(f"[platform] heartbeat failed: {type(exc).__name__}: {exc}", flush=True)
                    self._reported_http_error = True
            self.stop_event.wait(self.interval)

    def stop(self) -> None:
        self.stop_event.set()


def _collect_metrics(trainer: Any) -> dict[str, float]:
    values: dict[str, float] = {}
    try:
        loss_values = trainer.label_loss_items(trainer.tloss, prefix="train")
        for key, value in loss_values.items():
            numeric = float(value)
            if math.isfinite(numeric):
                values[str(key)] = numeric
    except Exception:
        pass
    for key, value in dict(getattr(trainer, "metrics", {}) or {}).items():
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            values[str(key)] = numeric
    for key, value in dict(getattr(trainer, "lr", {}) or {}).items():
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            values[str(key)] = numeric
    return values


def _ensure_ultralytics_font() -> None:
    """Avoid a network font fetch on first training run (Windows)."""
    config_dir = os.environ.get("YOLO_CONFIG_DIR")
    if not config_dir or os.name != "nt":
        return
    target = Path(config_dir) / "Ultralytics" / "Arial.ttf"
    if target.exists():
        return
    source = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.exists():
            target.write_bytes(source.read_bytes())
    except Exception:
        pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LiteTrain CPU YOLO worker")
    parser.add_argument("--kind", choices=("train", "preprocess", "evaluate"), default="train")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--data", default=None)
    parser.add_argument("--source-data", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--weights", default=None)
    parser.add_argument("--max-images", type=int, default=24)
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--dedupe", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--split", choices=("train", "val"), default="val")
    parser.add_argument("--conf", type=float, default=0.001)
    parser.add_argument("--iou", type=float, default=0.6)
    parser.add_argument("--max-det", type=int, default=300)
    parser.add_argument("--pipeline-id", default=None)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--imgsz", type=int, default=160)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--cpu-cores", type=int, default=4)
    parser.add_argument("--compute-threads", type=int, default=None)
    parser.add_argument("--cache", choices=("none", "disk", "ram"), default="none")
    parser.add_argument("--save-period", type=int, default=5)
    parser.add_argument("--patience", type=int, default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--resume", default=None)
    parser.add_argument("--resume-mode", choices=("full", "weights"), default=None)
    parser.add_argument("--project", default=None)
    parser.add_argument("--name", default=None)
    return parser.parse_args(argv)


def _resolve_dataset_root(data_path: Path, data: dict[str, Any]) -> Path:
    raw = data.get("path")
    candidates: list[Path] = []
    if raw:
        root_candidate = Path(str(raw))
        if root_candidate.is_absolute():
            candidates.append(root_candidate)
        else:
            candidates.extend([Path.cwd() / root_candidate, data_path.parent / root_candidate])
    candidates.append(data_path.parent)
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return data_path.parent.resolve()


def _split_images(root: Path, value: Any) -> list[Path]:
    images: list[Path] = []
    values = value if isinstance(value, list) else [value]
    for item in values:
        if not item:
            continue
        candidate = Path(str(item))
        if not candidate.is_absolute():
            candidate = (root / candidate).resolve()
        if candidate.is_file() and candidate.suffix.lower() == ".txt":
            for line in candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
                path = Path(line.strip())
                if path and path.is_file():
                    images.append(path)
        elif candidate.is_dir():
            images.extend(
                path
                for path in sorted(candidate.rglob("*"))
                if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
            )
        elif candidate.is_file() and candidate.suffix.lower() in IMAGE_SUFFIXES:
            images.append(candidate)
    return images


def _label_for_image(image: Path, root: Path) -> Path | None:
    parts = list(image.parts)
    if "images" in parts:
        idx = len(parts) - 1 - parts[::-1].index("images")
        parts[idx] = "labels"
        candidate = Path(*parts).with_suffix(".txt")
        if candidate.exists():
            return candidate
    # Fallback: sibling labels directory at the dataset root.
    relative = image.relative_to(root) if root in image.parents else Path(image.name)
    haystack = list(relative.parts)
    if "images" in haystack:
        haystack[haystack.index("images")] = "labels"
        candidate = (root / Path(*haystack)).with_suffix(".txt")
        if candidate.exists():
            return candidate
    return None


def _label_class_ids(label: Path, class_count: int) -> list[int] | None:
    """Return parsed class ids, or ``None`` for a malformed label file."""
    try:
        text = label.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    classes: list[int] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 5:
            return None
        try:
            class_id = int(parts[0])
            x, y, width, height = (float(item) for item in parts[1:])
        except (TypeError, ValueError):
            return None
        if class_id < 0 or (class_count > 0 and class_id >= class_count):
            return None
        if not all(math.isfinite(value) for value in (x, y, width, height)):
            return None
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 and 0 < height <= 1):
            return None
        classes.append(class_id)
    return classes


def run_preprocess(args: argparse.Namespace, state: ProgressState, client: ControlPlaneClient) -> dict[str, float]:
    import yaml

    if not args.source_data:
        raise ValueError("--source-data is required for preprocess")
    if not args.output_dir:
        raise ValueError("--output-dir is required for preprocess")
    source_yaml = Path(args.source_data)
    if not source_yaml.is_file():
        raise FileNotFoundError(f"source dataset yaml not found: {source_yaml}")
    with source_yaml.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("source dataset YAML must be a mapping")
    root = _resolve_dataset_root(source_yaml, data)
    names = data.get("names") or []
    class_count = len(names) if isinstance(names, (list, dict)) else 0
    val_ratio = min(0.5, max(0.05, float(args.val_ratio)))
    output = Path(args.output_dir)
    if output.exists():
        shutil.rmtree(output)
    for split in ("train", "val"):
        (output / "images" / split).mkdir(parents=True, exist_ok=True)
        (output / "labels" / split).mkdir(parents=True, exist_ok=True)

    state.update(5.0, "scanning source dataset")
    samples: list[dict[str, Any]] = []
    seen_hashes: dict[str, Path] = {}
    source_counts: dict[str, int] = {}
    dropped = {
        "duplicate_images": 0,
        "missing_labels": 0,
        "invalid_labels": 0,
        "unreadable_images": 0,
    }
    for split in ("train", "val"):
        source_split = data.get(split)
        if split == "val":
            source_split = source_split or data.get("validation")
        images = _split_images(root, source_split)
        source_counts[split] = len(images)
        for image in images:
            try:
                digest = hashlib.sha256(image.read_bytes()).hexdigest()
            except OSError:
                dropped["unreadable_images"] += 1
                continue
            if digest in seen_hashes:
                dropped["duplicate_images"] += 1
                if args.dedupe:
                    continue
            label = _label_for_image(image, root)
            if label is None:
                dropped["missing_labels"] += 1
                continue
            classes = _label_class_ids(label, class_count)
            if classes is None:
                dropped["invalid_labels"] += 1
                continue
            seen_hashes.setdefault(digest, image)
            samples.append(
                {
                    "image": image,
                    "label": label,
                    "classes": classes,
                    "source_split": split,
                    "sha256": digest,
                }
            )
            if len(samples) % 50 == 0:
                state.update(25.0, f"scanned {len(samples)} usable images")
    if not samples:
        raise ValueError("no usable images found after validation and de-duplication")

    random.Random(int(args.seed)).shuffle(samples)
    limit = max(1, int(args.max_images))
    selected = samples[:limit]
    val_count = 0
    if len(selected) >= 2:
        val_count = max(1, min(len(selected) - 1, round(len(selected) * val_ratio)))
    train_samples = selected[val_count:] if val_count else selected
    val_samples = selected[:val_count]
    if not train_samples:
        train_samples, val_samples = selected, []

    class_counts: dict[str, dict[str, Any]] = {}
    if isinstance(names, dict):
        name_items = [(int(key), str(value)) for key, value in names.items()]
    else:
        name_items = [(index, str(value)) for index, value in enumerate(names)]
    for class_id, class_name in name_items:
        class_counts[str(class_id)] = {"name": class_name, "train": 0, "val": 0}
    for sample in selected:
        split_key = "val" if val_samples and sample in val_samples else "train"
        for class_id in sample["classes"]:
            bucket = class_counts.setdefault(
                str(class_id), {"name": f"class {class_id}", "train": 0, "val": 0}
            )
            bucket[split_key] += 1

    state.update(50.0, "copying curated splits")
    copied_labels = 0
    for split, items in (("train", train_samples), ("val", val_samples)):
        for index, sample in enumerate(items):
            image: Path = sample["image"]
            destination = output / "images" / split / f"{index:05d}_{image.name}"
            shutil.copy2(image, destination)
            label_target = output / "labels" / split / destination.with_suffix(".txt").name
            shutil.copy2(sample["label"], label_target)
            copied_labels += 1
        print(f"[preprocess] {split}: copied {len(items)} images", flush=True)

    generated = {
        "path": output.resolve().as_posix(),
        "train": "images/train",
        "val": "images/val",
        "names": names,
    }
    yaml_path = output / "dataset.yaml"
    with yaml_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(generated, handle, allow_unicode=True, sort_keys=False)
    warnings: list[str] = []
    if not val_samples:
        warnings.append("验证集为空；评估会退化为训练集自检")
    if len(selected) < 2:
        warnings.append("可用样本不足 2 张，无法进行独立验证")
    if len(samples) > limit:
        warnings.append(f"从 {len(samples)} 张可用图片中按策略截取 {limit} 张")
    manifest = {
        "version": 2,
        "source_dataset": str(source_yaml),
        "dataset_yaml": str(yaml_path),
        "policy": {
            "max_images": limit,
            "val_ratio": val_ratio,
            "seed": int(args.seed),
            "dedupe": bool(args.dedupe),
        },
        "source": {"root": str(root), "splits": source_counts, "usable": len(samples)},
        "counts": {
            "input_images": sum(source_counts.values()),
            "kept_images": len(selected),
            "train_images": len(train_samples),
            "val_images": len(val_samples),
            "labels": copied_labels,
            **dropped,
        },
        "classes": len(name_items),
        "class_counts": class_counts,
        "warnings": warnings,
    }
    (output / "preprocess.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    metrics = {
        "data/input_images": float(manifest["counts"]["input_images"]),
        "data/images": float(len(selected)),
        "data/train_images": float(len(train_samples)),
        "data/val_images": float(len(val_samples)),
        "data/duplicates": float(dropped["duplicate_images"]),
        "data/invalid_labels": float(dropped["invalid_labels"]),
        "data/classes": float(len(name_items)),
    }
    try:
        client.metrics({"epoch": 1, "step": 1, "values": metrics, "progress": 100})
    except Exception:
        pass
    state.update(100.0, "preprocess complete")
    print(
        f"[preprocess] completed: kept={len(selected)} train={len(train_samples)} "
        f"val={len(val_samples)} dropped={dropped} -> {yaml_path}",
        flush=True,
    )
    return metrics


def run_evaluation(
    args: argparse.Namespace, state: ProgressState, client: ControlPlaneClient
) -> dict[str, float]:
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    os.environ.setdefault("OMP_NUM_THREADS", str(max(1, args.cpu_cores)))
    os.environ.setdefault("MKL_NUM_THREADS", str(max(1, args.cpu_cores)))
    _ensure_ultralytics_font()
    from ultralytics import YOLO
    import torch

    torch.set_num_threads(max(1, min(args.cpu_cores, os.cpu_count() or args.cpu_cores)))
    if not args.weights or not Path(args.weights).is_file():
        raise FileNotFoundError(f"weights not found: {args.weights}")
    if not args.data or not Path(args.data).is_file():
        raise FileNotFoundError(f"dataset yaml not found: {args.data}")
    state.update(5.0, "loading best weights")
    print(f"[evaluate] loading {args.weights}", flush=True)
    model = YOLO(args.weights, task="detect")
    state.update(15.0, f"evaluating {args.split} split")
    results = model.val(
        data=args.data,
        split=args.split,
        imgsz=args.imgsz,
        batch=args.batch,
        conf=args.conf,
        iou=args.iou,
        max_det=args.max_det,
        device="cpu",
        workers=0,
        plots=True,
        save_json=True,
        project=args.project,
        name=args.name or args.task_id,
        exist_ok=True,
        verbose=True,
    )
    raw = dict(getattr(results, "results_dict", {}) or {})
    numeric = {
        str(key): float(value)
        for key, value in raw.items()
        if isinstance(value, (int, float)) and math.isfinite(float(value))
    }

    def pick(*needles: str) -> float | None:
        for key, value in numeric.items():
            lowered = key.lower()
            if any(needle.lower() in lowered for needle in needles):
                return round(value, 6)
        return None

    metrics: dict[str, float] = {
        "eval/precision": pick("precision(b)", "precision"),
        "eval/recall": pick("recall(b)", "recall"),
        "eval/map50": pick("map50(b)", "map50"),
        "eval/map50_95": pick("map50-95(b)", "map50-95"),
    }
    metrics = {key: value for key, value in metrics.items() if value is not None}
    precision = metrics.get("eval/precision")
    recall = metrics.get("eval/recall")
    if precision is not None and recall is not None and precision + recall > 0:
        metrics["eval/f1"] = round(2 * precision * recall / (precision + recall), 6)

    per_class: list[dict[str, Any]] = []
    box = getattr(results, "box", None)
    ap_indices_raw = getattr(box, "ap_class_index", None)
    ap_values_raw = getattr(box, "maps", None)
    ap_indices = list(ap_indices_raw) if ap_indices_raw is not None else []
    ap_values = list(ap_values_raw) if ap_values_raw is not None else []
    names = getattr(results, "names", {}) or {}
    for class_id, ap in zip(ap_indices, ap_values):
        index = int(class_id)
        if isinstance(names, dict):
            class_name = str(names.get(index, names.get(str(index), f"class {index}")))
        else:
            class_name = str(names[index]) if index < len(names) else f"class {index}"
        per_class.append(
            {
                "class_id": index,
                "class": class_name,
                "ap50_95": round(float(ap), 4),
            }
        )
    speed: dict[str, float] = {}
    for key, value in (getattr(results, "speed", {}) or {}).items():
        try:
            numeric_speed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric_speed):
            speed[str(key)] = round(numeric_speed, 3)
    latency_ms = round(sum(speed.values()), 3)
    fps = round(1000 / latency_ms, 2) if latency_ms > 0 else None
    metrics["eval/latency_ms"] = latency_ms
    if fps is not None:
        metrics["eval/fps"] = fps

    run_dir = Path(args.project) / (args.name or args.task_id) if args.project else Path.cwd()
    report = {
        "version": 2,
        "config": {
            "weights": str(args.weights),
            "data": str(args.data),
            "split": args.split,
            "imgsz": args.imgsz,
            "batch": args.batch,
            "conf": args.conf,
            "iou": args.iou,
            "max_det": args.max_det,
            "cpu_cores": args.cpu_cores,
        },
        "overall": {key.removeprefix("eval/"): value for key, value in metrics.items()},
        "per_class": per_class,
        "speed_ms": speed,
        "latency_ms_per_image": latency_ms,
        "fps": fps,
        "source": "ultralytics-val",
    }
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "evaluation.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        print("[evaluate] warning: could not persist evaluation.json", flush=True)
    try:
        client.metrics({"epoch": 1, "step": 1, "values": metrics, "progress": 100})
    except Exception:
        pass
    state.update(100.0, "evaluation complete")
    print(
        "[evaluate] " + ", ".join(f"{key}={value:.4f}" for key, value in sorted(metrics.items())),
        flush=True,
    )
    return metrics



def _sync_resume_artifacts(source: Path, target: Path) -> None:
    """Ultralytics full-resume writes into the checkpoint's original run directory."""
    if not source.is_dir() or source.resolve() == target.resolve():
        return
    copied = []
    for relative in ("weights/best.pt", "weights/last.pt", "results.csv", "args.yaml"):
        origin = source / relative
        if not origin.is_file():
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, destination)
        copied.append(relative)
    for origin in sorted((source / "weights").glob("*.onnx")):
        destination = target / "weights" / origin.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, destination)
        copied.append(f"weights/{origin.name}")
    if copied:
        print(f"[platform] copied resume artifacts: {', '.join(copied)}", flush=True)


def run_training(args: argparse.Namespace, state: ProgressState, client: ControlPlaneClient) -> dict[str, float]:
    # These must be set before importing torch/ultralytics.
    compute_threads = max(
        1, min(args.compute_threads or args.cpu_cores, os.cpu_count() or args.cpu_cores)
    )
    os.environ["OMP_NUM_THREADS"] = str(compute_threads)
    os.environ["MKL_NUM_THREADS"] = str(compute_threads)
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

    _ensure_ultralytics_font()
    from ultralytics import YOLO
    import torch

    torch.set_num_threads(compute_threads)

    resume_path = Path(args.resume).resolve() if args.resume else None
    if resume_path is not None and not resume_path.is_file():
        raise FileNotFoundError(f"checkpoint not found: {resume_path}")
    if not args.model or not Path(args.model).is_file():
        raise FileNotFoundError(f"model checkpoint not found: {args.model}")
    if not args.data or not Path(args.data).is_file():
        raise FileNotFoundError(f"dataset yaml not found: {args.data}")

    print(
        f"[platform] starting YOLO CPU training: epochs={args.epochs} imgsz={args.imgsz} "
        f"batch={args.batch} cores={args.cpu_cores} threads={compute_threads} "
        f"workers={args.workers} cache={args.cache}",
        flush=True,
    )
    state.update(1.0, "loading model")
    model = YOLO(str(resume_path) if resume_path else args.model, task="detect")
    last_metrics: dict[str, float] = {}

    def on_train_epoch_start(trainer: Any) -> None:
        state.update(
            (max(0, int(getattr(trainer, "epoch", 0))) / max(1, args.epochs)) * 100,
            f"epoch {trainer.epoch + 1}/{args.epochs}",
        )

    def on_train_batch_end(trainer: Any) -> None:
        length = 1
        try:
            length = max(1, len(trainer.train_loader))
        except Exception:
            pass
        batch = int(getattr(trainer, "batch_i", 0)) + 1
        epoch = int(getattr(trainer, "epoch", 0))
        pct = min(99.5, ((epoch + min(1.0, batch / length)) / max(1, args.epochs)) * 100)
        state.update(pct, f"epoch {epoch + 1}/{args.epochs}")

    def on_fit_epoch_end(trainer: Any) -> None:
        nonlocal last_metrics
        epoch = int(getattr(trainer, "epoch", 0)) + 1
        values = _collect_metrics(trainer)
        last_metrics = values or last_metrics
        pct = min(99.8, (epoch / max(1, args.epochs)) * 100)
        state.update(pct, f"epoch {epoch}/{args.epochs} done")
        save_dir = Path(getattr(trainer, "save_dir", "") or "")
        candidate = save_dir / "weights" / f"epoch{epoch}.pt"
        if not candidate.is_file():
            candidate = save_dir / "weights" / "last.pt"
        if candidate.is_file():
            try:
                client.checkpoint(
                    {
                        "epoch": max(1, min(epoch, args.epochs)),
                        "path": str(candidate.resolve()),
                        "size_bytes": candidate.stat().st_size,
                        "metrics": values,
                        "is_best": False,
                    }
                )
                state.update(pct, f"epoch {epoch}/{args.epochs} checkpoint saved")
            except Exception as exc:
                print(f"[platform] checkpoint upload failed: {type(exc).__name__}: {exc}", flush=True)
        # Ultralytics runs a final validation with epoch temporarily bumped by
        # one; keep those values as a summary but do not create a duplicate
        # chart point beyond the configured epoch count.
        if epoch > args.epochs:
            return
        if values:
            try:
                client.metrics(
                    {
                        "epoch": epoch,
                        "step": epoch,
                        "values": values,
                        "progress": pct,
                    }
                )
                print(
                    f"[platform] metrics epoch {epoch}: "
                    + ", ".join(f"{k}={v:.4f}" for k, v in sorted(values.items())),
                    flush=True,
                )
            except SystemExit:
                raise
            except Exception as exc:
                print(f"[platform] metric upload failed: {type(exc).__name__}: {exc}", flush=True)

    model.add_callback("on_train_epoch_start", on_train_epoch_start)
    model.add_callback("on_train_batch_end", on_train_batch_end)
    model.add_callback("on_fit_epoch_end", on_fit_epoch_end)

    state.update(2.0, "training")
    train_kwargs: dict[str, Any] = {
        "data": args.data,
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "device": "cpu",
        "workers": args.workers,
        "project": args.project,
        "name": args.name,
        "exist_ok": True,
        "plots": False,
        "val": True,
        "save": True,
        "save_period": args.save_period,
        "cache": args.cache,
        "verbose": True,
        "seed": args.seed,
        "deterministic": True,
        "amp": False,
        "resume": bool(resume_path and args.resume_mode == "full"),
    }
    if args.patience is not None:
        train_kwargs["patience"] = args.patience
    model.train(**train_kwargs)
    if resume_path is not None and args.resume_mode == "full" and args.project:
        trainer = getattr(model, "trainer", None)
        actual_save_dir = getattr(trainer, "save_dir", None)
        target_dir = Path(args.project) / (args.name or args.task_id)
        if actual_save_dir:
            _sync_resume_artifacts(Path(actual_save_dir), target_dir)
    state.update(100.0, "finished")
    return last_metrics


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result_path_raw = os.environ.get(RESULT_ENV)
    token = os.environ.get(TOKEN_ENV)
    api_base = os.environ.get(API_ENV)
    if not result_path_raw or not token or not api_base:
        print(
            "missing worker environment: LITETRAIN_RESULT_PATH, "
            "LITETRAIN_WORKER_TOKEN and LITETRAIN_WORKER_API_BASE are required",
            file=sys.stderr,
            flush=True,
        )
        return 2
    result_path = Path(result_path_raw)
    interval = float(os.environ.get(INTERVAL_ENV, "4"))
    started = time.time()
    state = ProgressState()
    client = ControlPlaneClient(args.task_id, api_base, token)
    reporter = HeartbeatReporter(client, state, interval)
    reporter.start()

    print(
        f"[platform] worker ready pid={os.getpid()} python={sys.executable} "
        f"task={args.task_id} kind={args.kind}",
        flush=True,
    )
    exit_code = 0
    error: str | None = None
    metrics: dict[str, float] = {}
    runner = {
        "preprocess": run_preprocess,
        "evaluate": run_evaluation,
        "train": run_training,
    }[args.kind]
    try:
        metrics = runner(args, state, client)
    except BaseException as exc:  # noqa: BLE001 - write a durable failure record
        exit_code = 1
        error = f"{type(exc).__name__}: {exc}"
        print(f"[platform] {args.kind} failed", flush=True)
        traceback.print_exc()
    finally:
        reporter.stop()
        payload = {
            "task_id": args.task_id,
            "kind": args.kind,
            "exit_code": exit_code,
            "error": error,
            "started_at": datetime.fromtimestamp(started, tz=timezone.utc).isoformat(),
            "finished_at": _utc_iso(),
            "duration_seconds": round(time.time() - started, 2),
            "metrics": metrics,
            "python": sys.executable,
        }
        try:
            _write_result(result_path, payload)
            print(
                f"[platform] task finished exit_code={exit_code} "
                f"duration={payload['duration_seconds']:.1f}s",
                flush=True,
            )
        except Exception:
            traceback.print_exc()
            exit_code = exit_code or 1
        client.close()
        sys.stdout.flush()
        sys.stderr.flush()
        # Bypass interpreter teardown on Windows; PyTorch/OpenMP can otherwise
        # change the process exit status after all work has completed.
        os._exit(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
