"""Dataset registry, model weights and model artifact APIs."""

from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.api.deps import get_ctx
from app.context import AppContext
from app.schemas import (
    ArtifactListResponse,
    DatasetListResponse,
    DatasetOut,
    DatasetQualityAck,
    DatasetQualityOut,
    ModelWeightListResponse,
    ModelWeightOut,
    QualityIssueOut,
)
from app.services.datasets import DatasetError, MAX_UPLOAD_BYTES
from app.services.models import (
    MAX_MODEL_BYTES,
    ModelWeightError,
    checkpoint_prefix_valid,
    commit_upload,
    list_weights,
    target_path,
    validate_upload_name,
)
from app.services.quality import QualityError

datasets_router = APIRouter(prefix="/datasets", tags=["datasets"])
artifacts_router = APIRouter(prefix="/artifacts", tags=["artifacts"])
models_router = APIRouter(prefix="/models", tags=["models"])


@models_router.get("", response_model=ModelWeightListResponse, summary="Base model weights")
def list_model_weights(ctx: AppContext = Depends(get_ctx)) -> ModelWeightListResponse:
    return list_weights(ctx.settings)


@models_router.post(
    "/upload",
    response_model=ModelWeightOut,
    status_code=201,
    summary="Import a .pt model weight file",
)
async def upload_model_weight(
    file: UploadFile = File(...),
    ctx: AppContext = Depends(get_ctx),
) -> ModelWeightOut:
    try:
        name = validate_upload_name(file.filename)
        target_path(ctx.settings, name)
    except ModelWeightError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    temporary = ctx.settings.tmp_dir / f"model-{uuid.uuid4().hex}.pt"
    digest = hashlib.sha256()
    prefix = b""
    total = 0
    try:
        with temporary.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_MODEL_BYTES:
                    raise HTTPException(status_code=413, detail="模型权重不能超过 512 MB")
                if not prefix:
                    prefix = chunk[:4]
                digest.update(chunk)
                handle.write(chunk)
        if total == 0:
            raise HTTPException(status_code=422, detail="上传的模型权重为空")
        if not checkpoint_prefix_valid(prefix):
            raise HTTPException(
                status_code=422,
                detail="文件不是可识别的 PyTorch 权重（需为 .pt checkpoint）",
            )
        return commit_upload(ctx.settings, name, temporary, digest.hexdigest())
    except ModelWeightError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


@datasets_router.get("", response_model=DatasetListResponse, summary="Managed datasets")
def list_datasets(ctx: AppContext = Depends(get_ctx)) -> DatasetListResponse:
    items = ctx.datasets.list()
    return DatasetListResponse(items=items, total=len(items))


@datasets_router.post(
    "/upload",
    response_model=DatasetOut,
    status_code=201,
    summary="Upload a YOLO dataset zip archive",
)
async def upload_dataset(
    file: UploadFile = File(...),
    name: str = Form(...),
    version: str = Form("v1"),
    description: str | None = Form(None),
    ctx: AppContext = Depends(get_ctx),
) -> DatasetOut:
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=422, detail="dataset archive must be a .zip")
    name = (name or "").strip()
    version = (version or "v1").strip()
    if not name or len(name) > 120:
        raise HTTPException(status_code=422, detail="dataset name is required (max 120 chars)")
    if not version or len(version) > 40:
        raise HTTPException(status_code=422, detail="dataset version is required (max 40 chars)")
    temporary = ctx.settings.tmp_dir / f"dataset-{uuid.uuid4().hex}.zip"
    total = 0
    try:
        with temporary.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="dataset archive exceeds 512 MB")
                handle.write(chunk)
        if total == 0:
            raise HTTPException(status_code=422, detail="uploaded archive is empty")
        result = ctx.datasets.import_archive(
            name=name,
            version=version,
            description=(description or "").strip() or None,
            filename=file.filename,
            source_path=temporary,
        )
        ctx.quality.start(result.id)
        return ctx.datasets.get(result.id)
    except DatasetError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


@datasets_router.get("/{dataset_id}", response_model=DatasetOut, summary="Dataset detail")
def get_dataset(dataset_id: str, ctx: AppContext = Depends(get_ctx)) -> DatasetOut:
    try:
        return ctx.datasets.get(dataset_id)
    except DatasetError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@datasets_router.post(
    "/{dataset_id}/audit",
    response_model=DatasetQualityOut,
    summary="Start or refresh dataset quality inspection",
)
def audit_dataset(
    dataset_id: str,
    force: bool = Query(default=False),
    ctx: AppContext = Depends(get_ctx),
) -> DatasetQualityOut:
    try:
        return ctx.quality.start(dataset_id, force=force)
    except QualityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@datasets_router.get(
    "/{dataset_id}/quality",
    response_model=DatasetQualityOut,
    summary="Dataset quality summary and issues",
)
def dataset_quality(dataset_id: str, ctx: AppContext = Depends(get_ctx)) -> DatasetQualityOut:
    try:
        return ctx.quality.get_quality(dataset_id)
    except QualityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@datasets_router.get(
    "/{dataset_id}/quality/issues",
    response_model=list[QualityIssueOut],
    summary="Dataset quality issues",
)
def dataset_quality_issues(
    dataset_id: str,
    ctx: AppContext = Depends(get_ctx),
) -> list[QualityIssueOut]:
    try:
        return ctx.quality.get_quality(dataset_id).issues
    except QualityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@datasets_router.post(
    "/{dataset_id}/quality/ack",
    response_model=DatasetQualityOut,
    summary="Acknowledge a warning dataset",
)
def acknowledge_quality(
    dataset_id: str,
    payload: DatasetQualityAck,
    ctx: AppContext = Depends(get_ctx),
) -> DatasetQualityOut:
    try:
        return ctx.quality.ack(dataset_id, payload.note)
    except QualityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@datasets_router.get(
    "/{dataset_id}/quality/report",
    summary="Download the HTML quality report",
)
def download_quality_report(dataset_id: str, ctx: AppContext = Depends(get_ctx)) -> FileResponse:
    try:
        path = ctx.quality.report_path(dataset_id)
    except QualityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return FileResponse(path, media_type="text/html", filename=f"{dataset_id}-quality.html")


@datasets_router.delete("/{dataset_id}", status_code=204, summary="Soft-delete a dataset")
def delete_dataset(dataset_id: str, ctx: AppContext = Depends(get_ctx)) -> None:
    try:
        ctx.datasets.delete(dataset_id)
    except DatasetError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@datasets_router.get("/{dataset_id}/download", summary="Download the original dataset archive")
def download_dataset(dataset_id: str, ctx: AppContext = Depends(get_ctx)) -> FileResponse:
    try:
        path = ctx.datasets.download_archive(dataset_id)
    except DatasetError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return FileResponse(path, media_type="application/zip", filename=path.name)


@artifacts_router.get("", response_model=ArtifactListResponse, summary="Model/result artifacts")
def list_artifacts(
    task_id: str | None = Query(default=None),
    pipeline_id: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    ctx: AppContext = Depends(get_ctx),
) -> ArtifactListResponse:
    items = ctx.artifacts.list(task_id=task_id, pipeline_id=pipeline_id, kind=kind)
    return ArtifactListResponse(items=items, total=len(items))


@artifacts_router.get("/{artifact_id}/download", summary="Download a model weight or result file")
def download_artifact(artifact_id: str, ctx: AppContext = Depends(get_ctx)) -> FileResponse:
    try:
        path, filename = ctx.artifacts.get_file(artifact_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc
    media_type = "application/octet-stream" if path.suffix != ".csv" else "text/csv"
    return FileResponse(path, media_type=media_type, filename=filename)
