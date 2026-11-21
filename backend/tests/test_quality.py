from __future__ import annotations

import io
import json
import zipfile

from PIL import Image

from tests.conftest import create_task


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (128, 128), (20, 120, 80)).save(buffer, format="PNG")
    return buffer.getvalue()


def _zip(files: dict[str, bytes | str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _upload(client, name: str, files: dict[str, bytes | str]):
    response = client.post(
        "/api/v1/datasets/upload",
        data={"name": name, "version": "v1"},
        files={"file": (f"{name}.zip", _zip(files), "application/zip")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _valid_files(label: str = "0 0.5 0.5 0.2 0.2\n") -> dict[str, bytes | str]:
    return {
        "dataset.yaml": "path: .\ntrain: images/train\nval: images/train\nnames:\n  0: widget\n",
        "images/train/a.png": _png(),
        "labels/train/a.txt": label,
    }


def test_quality_gate_blocks_invalid_labels(client, ctx):
    dataset = _upload(
        client,
        "bad-labels",
        _valid_files("0 1.2 0.5 0.2 0.2\n"),
    )
    quality = ctx.quality.audit_now(dataset["id"])
    assert quality.status == "blocked"
    assert any(issue.code == "invalid_coordinates" for issue in quality.issues)

    response = client.post(
        "/api/v1/tasks",
        json={
            "name": "must be blocked",
            "dataset": "coco128.yaml",
            "dataset_id": dataset["id"],
            "epochs": 1,
            "cpu_cores": 2,
        },
    )
    assert response.status_code == 422
    assert "quality" in response.json()["detail"]


def test_quality_gate_and_manifest_for_valid_dataset(client, ctx, payload):
    dataset = _upload(client, "valid-widgets", _valid_files())
    quality = ctx.quality.audit_now(dataset["id"])
    assert quality.status == "passed", quality.model_dump()

    task_payload = dict(payload)
    task_payload["dataset_id"] = dataset["id"]
    task_payload["dataset"] = "coco128.yaml"
    task = create_task(client, task_payload)
    assert task["dataset_id"] == dataset["id"]
    assert task["dataset_sha256"]

    manifest_response = client.get(f"/api/v1/tasks/{task['id']}/manifest")
    assert manifest_response.status_code == 200, manifest_response.text
    manifest = manifest_response.json()["manifest"]
    assert manifest["dataset"]["id"] == dataset["id"]
    assert manifest["dataset"]["content_sha256"]
    assert manifest["runtime"]["python"]
    assert manifest["config"]["epochs"] == task_payload["epochs"]


def test_quality_detects_split_leakage(client, ctx):
    files = {
        "dataset.yaml": "path: .\ntrain: images/train\nval: images/val\nnames:\n  0: widget\n",
        "images/train/a.png": _png(),
        "images/val/b.png": _png(),
        "labels/train/a.txt": "0 0.5 0.5 0.2 0.2\n",
        "labels/val/b.txt": "0 0.5 0.5 0.2 0.2\n",
    }
    dataset = _upload(client, "leaky-widgets", files)
    quality = ctx.quality.audit_now(dataset["id"])
    assert quality.status == "blocked"
    assert any(issue.code == "split_exact_leak" for issue in quality.issues)
