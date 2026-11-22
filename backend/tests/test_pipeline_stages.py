"""Preprocessing curation and report tests."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import yaml

from app.training.worker import run_preprocess


class _State:
    def update(self, progress: float, message: str) -> None:
        pass


class _Client:
    def metrics(self, payload: dict) -> None:
        pass


def test_preprocess_deduplicates_validates_and_splits():
    with tempfile.TemporaryDirectory() as directory:
        tmp_path = Path(directory)
        root = tmp_path / "dataset"
        for split in ("train", "val"):
            (root / "images" / split).mkdir(parents=True)
            (root / "labels" / split).mkdir(parents=True)
        (root / "images" / "train" / "a.jpg").write_bytes(b"image-a")
        (root / "labels" / "train" / "a.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
        (root / "images" / "train" / "b.jpg").write_bytes(b"image-b")
        (root / "labels" / "train" / "b.txt").write_text("0 0.4 0.4 0.1 0.1\n", encoding="utf-8")
        (root / "images" / "val" / "c.jpg").write_bytes(b"image-a")  # duplicate of a.jpg
        (root / "labels" / "val" / "c.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
        (root / "images" / "val" / "d.jpg").write_bytes(b"image-d")  # missing label
        (root / "images" / "val" / "e.jpg").write_bytes(b"image-e")
        (root / "labels" / "val" / "e.txt").write_text("99 0.5 0.5 0.2 0.2\n", encoding="utf-8")

        source_yaml = root / "source.yaml"
        source_yaml.write_text(
            yaml.safe_dump(
                {
                    "path": str(root),
                    "train": "images/train",
                    "val": "images/val",
                    "names": {0: "widget"},
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        output = tmp_path / "output"
        metrics = run_preprocess(
            argparse.Namespace(
                source_data=str(source_yaml),
                output_dir=str(output),
                max_images=10,
                val_ratio=0.5,
                seed=42,
                dedupe=True,
                cpu_cores=1,
            ),
            _State(),
            _Client(),
        )
        report = json.loads((output / "preprocess.json").read_text(encoding="utf-8"))
        counts = report["counts"]
        assert counts["input_images"] == 5
        assert counts["kept_images"] == 2
        assert counts["train_images"] == 1
        assert counts["val_images"] == 1
        assert counts["duplicate_images"] == 1
        assert counts["missing_labels"] == 1
        assert counts["invalid_labels"] == 1
        assert report["classes"] == 1
        assert report["class_counts"]["0"] == {"name": "widget", "train": 1, "val": 1}
        assert (output / "dataset.yaml").is_file()
        assert metrics["data/images"] == 2
        assert len(list((output / "images" / "train").glob("*.jpg"))) == 1
        assert len(list((output / "images" / "val").glob("*.jpg"))) == 1
