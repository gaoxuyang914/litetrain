"""Asset resolution and command construction for YOLO workers."""

from __future__ import annotations

from pathlib import Path

from app.config import Settings


class TrainingAssetError(ValueError):
    """Raised when a requested model or dataset cannot be found."""


def _display_path(path: Path, settings: Settings) -> str:
    root = settings.data_dir.parent.parent
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def resolve_model_path(settings: Settings, model: str) -> Path:
    name = Path(model).name
    candidate = (settings.weights_dir / name).resolve()
    weights_root = settings.weights_dir.resolve()
    if weights_root not in candidate.parents or not candidate.is_file():
        available = sorted(p.name for p in settings.weights_dir.glob("*.pt"))
        raise TrainingAssetError(
            f"Model '{model}' not found. Available weights: {', '.join(available) or 'none'}"
        )
    return candidate


def resolve_dataset_path(settings: Settings, dataset: str) -> Path:
    query = dataset.replace("\\", "/")
    direct = (settings.datasets_dir / query).resolve()
    datasets_root = settings.datasets_dir.resolve()
    if datasets_root == direct or datasets_root in direct.parents:
        if direct.is_file() and direct.suffix == ".yaml":
            return direct
    # Allow a friendly dataset name such as "coco128.yaml".
    matches = [
        p
        for p in settings.datasets_dir.rglob(Path(query).name)
        if p.is_file() and p.suffix == ".yaml"
    ]
    if matches:
        return matches[0].resolve()
    raise TrainingAssetError(
        f"Dataset '{dataset}' not found under {_display_path(settings.datasets_dir, settings)}. "
        "Run scripts/download_assets.py or add a YOLO dataset YAML."
    )


def build_display_command(
    settings: Settings,
    *,
    task_id: str,
    model: str,
    dataset: str,
    epochs: int,
    imgsz: int,
    batch_size: int,
    workers: int,
    cpu_cores: int,
) -> str:
    model_path = resolve_model_path(settings, model)
    dataset_path = resolve_dataset_path(settings, dataset)
    return " ".join(
        [
            "python -m app.training.worker",
            f"--task-id {task_id}",
            f"--model {_display_path(model_path, settings)}",
            f"--data {_display_path(dataset_path, settings)}",
            f"--epochs {epochs}",
            f"--imgsz {imgsz}",
            f"--batch {batch_size}",
            "--device cpu",
            f"--workers {workers}",
            f"--cpu-cores {cpu_cores}",
        ]
    )


def build_equivalent_yolo_command(
    *,
    model: str,
    dataset: str,
    epochs: int,
    imgsz: int,
    batch_size: int,
    workers: int,
) -> str:
    return (
        f"yolo detect train model={Path(model).name} data={Path(dataset).name} "
        f"epochs={epochs} imgsz={imgsz} batch={batch_size} "
        f"device=cpu workers={workers}"
    )


def build_worker_command(
    settings: Settings,
    *,
    task_id: str,
    model: str,
    dataset: str,
    epochs: int,
    imgsz: int,
    batch_size: int,
    workers: int,
    cpu_cores: int,
    run_dir: Path,
    compute_threads: int | None = None,
    cache_mode: str = "none",
    save_period: int = 5,
    patience: int | None = None,
    seed: int = 0,
    resume: str | None = None,
    resume_mode: str | None = None,
) -> list[str]:
    model_path = resolve_model_path(settings, model)
    dataset_path = resolve_dataset_path(settings, dataset)
    command = [
        settings.resolved_worker_python,
        "-m",
        "app.training.worker",
        "--task-id",
        task_id,
        "--model",
        str(model_path),
        "--data",
        str(dataset_path),
        "--epochs",
        str(epochs),
        "--imgsz",
        str(imgsz),
        "--batch",
        str(batch_size),
        "--device",
        "cpu",
        "--workers",
        str(workers),
        "--cpu-cores",
        str(cpu_cores),
    ]
    if compute_threads is not None:
        command.extend(["--compute-threads", str(compute_threads)])
    if cache_mode and cache_mode != "none":
        command.extend(["--cache", cache_mode])
    if save_period is not None:
        command.extend(["--save-period", str(save_period)])
    if patience is not None:
        command.extend(["--patience", str(patience)])
    if seed:
        command.extend(["--seed", str(seed)])
    if resume:
        command.extend(["--resume", resume])
    if resume_mode:
        command.extend(["--resume-mode", resume_mode])
    command.extend(["--project", str(settings.runs_dir), "--name", task_id])
    return command


def list_models(settings: Settings) -> list[str]:
    return sorted(path.name for path in settings.weights_dir.glob("*.pt"))


def list_datasets(settings: Settings) -> list[str]:
    seen: set[str] = set()
    root = settings.datasets_dir
    for path in root.rglob("*.yaml"):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        # Pipeline-generated datasets are internal stage inputs, not user-selectable
        # base datasets.
        if relative.parts and relative.parts[0] == "pipelines":
            continue
        seen.add(path.name)
    return sorted(seen)
