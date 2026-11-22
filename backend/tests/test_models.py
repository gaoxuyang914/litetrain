"""Model weight import tests."""

from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient


def _checkpoint_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("archive/data.pkl", b"placeholder")
    return buffer.getvalue()


def _remove(weights_dir: Path, name: str) -> None:
    (weights_dir / name).unlink(missing_ok=True)


def test_list_and_import_model_weight(client: TestClient, ctx):
    name = f"custom-yolo-{uuid.uuid4().hex[:8]}.pt"
    initial = client.get("/api/v1/models")
    assert initial.status_code == 200
    assert "yolov8n.pt" in {item["name"] for item in initial.json()["items"]}

    try:
        upload = client.post(
            "/api/v1/models/upload",
            files={"file": (name, _checkpoint_bytes(), "application/octet-stream")},
        )
        assert upload.status_code == 201, upload.text
        body = upload.json()
        assert body["name"] == name
        assert body["size_bytes"] > 0
        assert len(body["sha256"]) == 64
        assert (ctx.settings.weights_dir / name).is_file()

        models = client.get("/api/v1/models").json()
        assert name in {item["name"] for item in models["items"]}
        options = client.get("/api/v1/tasks/options").json()
        assert name in options["models"]
    finally:
        _remove(ctx.settings.weights_dir, name)


def test_model_import_validation(client: TestClient):
    wrong_suffix = client.post(
        "/api/v1/models/upload",
        files={"file": ("weights.bin", b"not a checkpoint", "application/octet-stream")},
    )
    assert wrong_suffix.status_code == 422
    assert ".pt" in wrong_suffix.json()["detail"]

    invalid_bytes = client.post(
        "/api/v1/models/upload",
        files={"file": ("fake.pt", b"not a checkpoint", "application/octet-stream")},
    )
    assert invalid_bytes.status_code == 422
    assert "PyTorch" in invalid_bytes.json()["detail"]

    empty = client.post(
        "/api/v1/models/upload",
        files={"file": ("empty.pt", b"", "application/octet-stream")},
    )
    assert empty.status_code == 422
    assert "为空" in empty.json()["detail"]

    traversal = client.post(
        "/api/v1/models/upload",
        files={"file": ("../escape.pt", _checkpoint_bytes(), "application/octet-stream")},
    )
    assert traversal.status_code == 422
    assert "路径" in traversal.json()["detail"]


def test_model_import_rejects_duplicate(client: TestClient, ctx):
    name = f"duplicate-{uuid.uuid4().hex[:8]}.pt"
    try:
        first = client.post(
            "/api/v1/models/upload",
            files={"file": (name, _checkpoint_bytes(), "application/octet-stream")},
        )
        assert first.status_code == 201, first.text
        second = client.post(
            "/api/v1/models/upload",
            files={"file": (name, _checkpoint_bytes(), "application/octet-stream")},
        )
        assert second.status_code == 409
        assert "已存在" in second.json()["detail"]
    finally:
        _remove(ctx.settings.weights_dir, name)
