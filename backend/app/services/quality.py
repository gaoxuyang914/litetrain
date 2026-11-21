"""Dataset quality inspection and training admission."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import math
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from PIL import Image
from sqlalchemy import select

from app.config import Settings
from app.database import Database, utcnow
from app.models import DatasetQualityReport, DatasetRecord
from app.schemas import DatasetQualityOut, QualityIssueOut
from app.services.datasets import DatasetError, content_hash

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
ENGINE_VERSION = "1"
SMALL_AREA_RATIO = 0.0004
NEAR_DUPLICATE_HAMMING = 6
MAX_NEAR_DUPLICATE_IMAGES = 3000


class QualityError(ValueError):
    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except Exception as exc:
        raise QualityError(f"invalid dataset YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise QualityError("dataset YAML must be a mapping")
    return data


def _dataset_root(yaml_path: Path, data: dict[str, Any]) -> Path:
    raw = data.get("path")
    candidates: list[Path] = []
    if raw:
        candidate = Path(str(raw))
        if candidate.is_absolute():
            candidates.append(candidate)
        else:
            candidates.extend([Path.cwd() / candidate, yaml_path.parent / candidate])
    candidates.append(yaml_path.parent)
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return yaml_path.parent.resolve()


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
                if not path.is_absolute():
                    path = (root / path).resolve()
                if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
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
    try:
        relative = image.relative_to(root)
    except ValueError:
        relative = Path(image.name)
    haystack = list(relative.parts)
    if "images" in haystack:
        haystack[haystack.index("images")] = "labels"
        candidate = (root / Path(*haystack)).with_suffix(".txt")
        if candidate.exists():
            return candidate
    return None


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _dhash(image: Image.Image) -> int:
    gray = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    getter = getattr(gray, "get_flattened_data", gray.getdata)
    pixels = list(getter())
    value = 0
    for row in range(8):
        for col in range(8):
            left = pixels[row * 9 + col]
            right = pixels[row * 9 + col + 1]
            value = (value << 1) | (1 if left > right else 0)
    return value


def _hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _add_issue(
    issues: list[dict[str, Any]],
    *,
    code: str,
    severity: str,
    message: str,
    count: int,
    examples: list[str] | None = None,
) -> None:
    issues.append(
        {
            "code": code,
            "severity": severity,
            "message": message,
            "count": int(count),
            "examples": (examples or [])[:20],
        }
    )


def _issue_list(issues: list[dict[str, Any]]) -> list[QualityIssueOut]:
    return [QualityIssueOut(**issue) for issue in issues]


class DatasetQualityService:
    def __init__(self, settings: Settings, db: Database, datasets: Any) -> None:
        self.settings = settings
        self.db = db
        self.datasets = datasets
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dataset-audit")
        self._lock = threading.RLock()
        self._running: set[str] = set()

    # --------------------------------------------------------------- lifecycle
    def recover(self) -> None:
        with self.db.session() as session:
            interrupted = session.scalars(
                select(DatasetRecord).where(DatasetRecord.quality_status == "auditing")
            ).all()
            for record in interrupted:
                record.quality_status = "pending"
            pending = session.scalars(
                select(DatasetRecord).where(
                    DatasetRecord.quality_status == "pending",
                    DatasetRecord.status != "deleted",
                )
            ).all()
            pending_ids = [record.id for record in pending if not record.is_builtin]
            session.flush()
        for dataset_id in pending_ids:
            self.start(dataset_id)

    def stop(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------- public
    def start(self, dataset_id: str, *, force: bool = False) -> DatasetQualityOut:
        with self.db.session() as session:
            record = session.get(DatasetRecord, dataset_id)
            if record is None or record.status == "deleted":
                raise QualityError("dataset not found", 404)
            if record.is_builtin:
                return self.get_quality(dataset_id)
            if record.quality_status == "passed" and not force:
                return self.get_quality(dataset_id)
            record.quality_status = "auditing"
            record.quality_acknowledged = False
            record.quality_score = None
            session.flush()
        self._submit(dataset_id)
        return self.get_quality(dataset_id)

    def audit_now(self, dataset_id: str) -> DatasetQualityOut:
        """Synchronous audit used by tests and explicit operator actions."""
        self._audit(dataset_id)
        return self.get_quality(dataset_id)

    def ack(self, dataset_id: str, note: str | None = None) -> DatasetQualityOut:
        with self.db.session() as session:
            record = session.get(DatasetRecord, dataset_id)
            if record is None or record.status == "deleted":
                raise QualityError("dataset not found", 404)
            if record.quality_status != "warning":
                raise QualityError("only warning datasets can be acknowledged", 409)
            record.quality_acknowledged = True
            if note:
                record.description = (record.description or "").strip()
            session.flush()
        return self.get_quality(dataset_id)

    def get_quality(self, dataset_id: str) -> DatasetQualityOut:
        with self.db.session() as session:
            record = session.get(DatasetRecord, dataset_id)
            if record is None or record.status == "deleted":
                raise QualityError("dataset not found", 404)
            report = session.scalar(
                select(DatasetQualityReport)
                .where(DatasetQualityReport.dataset_id == dataset_id)
                .order_by(DatasetQualityReport.created_at.desc())
            )
            summary = {}
            issues: list[dict[str, Any]] = []
            if report is not None:
                try:
                    summary = json.loads(report.summary_json or "{}")
                except json.JSONDecodeError:
                    summary = {}
                if report.issue_path:
                    issue_file = self.settings.data_dir / report.issue_path
                    if issue_file.is_file():
                        issues = [
                            json.loads(line)
                            for line in issue_file.read_text(encoding="utf-8").splitlines()
                            if line.strip()
                        ]
            return DatasetQualityOut(
                dataset_id=record.id,
                status=record.quality_status,
                score=record.quality_score,
                engine_version=record.quality_engine_version,
                checked_at=record.quality_checked_at,
                summary=summary,
                issues=_issue_list(issues),
                report_url=(
                    f"/api/v1/datasets/{record.id}/quality/report"
                    if record.quality_report_path
                    else None
                ),
            )

    def report_path(self, dataset_id: str) -> Path:
        with self.db.session() as session:
            record = session.get(DatasetRecord, dataset_id)
            if record is None or not record.quality_report_path:
                raise QualityError("quality report not found", 404)
            path = (self.settings.data_dir / record.quality_report_path).resolve()
            root = self.settings.data_dir.resolve()
            if root not in path.parents or not path.is_file():
                raise QualityError("quality report not found", 404)
            return path

    # ------------------------------------------------------------------ workers
    def _submit(self, dataset_id: str) -> None:
        with self._lock:
            if dataset_id in self._running:
                return
            self._running.add(dataset_id)
        try:
            self._executor.submit(self._run, dataset_id)
        except RuntimeError:
            with self._lock:
                self._running.discard(dataset_id)

    def _run(self, dataset_id: str) -> None:
        try:
            self._audit(dataset_id)
        except Exception:
            logger.exception("dataset quality audit failed for %s", dataset_id)
        finally:
            with self._lock:
                self._running.discard(dataset_id)

    def _audit(self, dataset_id: str) -> None:
        started = utcnow()
        with self.db.session() as session:
            record = session.get(DatasetRecord, dataset_id)
            if record is None or record.status == "deleted":
                return
            yaml_path = self.settings.datasets_dir / record.yaml_path
            quality_dir = self.settings.quality_dir / dataset_id
            quality_dir.mkdir(parents=True, exist_ok=True)

        try:
            result = self._inspect(yaml_path, quality_dir)
        except Exception as exc:
            logger.exception("dataset quality inspection failed for %s", dataset_id)
            with self.db.session() as session:
                record = session.get(DatasetRecord, dataset_id)
                if record is not None:
                    record.quality_status = "failed"
                    record.quality_engine_version = ENGINE_VERSION
                    record.quality_checked_at = started
                session.add(
                    DatasetQualityReport(
                        dataset_id=dataset_id,
                        status="failed",
                        summary_json="{}",
                        engine_version=ENGINE_VERSION,
                        error=f"{type(exc).__name__}: {exc}",
                        created_at=started,
                        finished_at=utcnow(),
                    )
                )
                session.flush()
            return

        summary = result["summary"]
        issues = result["issues"]
        blocking = [item for item in issues if item["severity"] == "blocking"]
        warnings = [item for item in issues if item["severity"] == "warning"]
        status = "blocked" if blocking else ("warning" if warnings else "passed")
        score = max(
            0,
            min(
                100,
                round(100 - 25 * len(blocking) - 3 * len(warnings), 1),
            ),
        )
        report_path = quality_dir / "report.json"
        issue_path = quality_dir / "issues.jsonl"
        html_path = quality_dir / "report.html"
        report_path.write_text(
            json.dumps(
                {
                    "dataset_id": dataset_id,
                    "status": status,
                    "score": score,
                    "summary": summary,
                    "issues": issues,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        issue_path.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in issues) + ("\n" if issues else ""),
            encoding="utf-8",
        )
        self._write_html(html_path, dataset_id, status, score, summary, issues)

        with self.db.session() as session:
            record = session.get(DatasetRecord, dataset_id)
            if record is None:
                return
            record.content_sha256 = result["content_sha256"]
            record.image_count = result["image_count"]
            record.label_count = result["label_count"]
            record.class_count = result["class_count"]
            record.quality_status = status
            record.quality_score = score
            record.quality_report_path = html_path.relative_to(self.settings.data_dir).as_posix()
            record.quality_issue_path = issue_path.relative_to(self.settings.data_dir).as_posix()
            record.quality_engine_version = ENGINE_VERSION
            record.quality_checked_at = utcnow()
            record.quality_acknowledged = False
            session.add(
                DatasetQualityReport(
                    dataset_id=dataset_id,
                    status=status,
                    score=score,
                    summary_json=json.dumps(summary, ensure_ascii=False),
                    report_path=record.quality_report_path,
                    issue_path=record.quality_issue_path,
                    engine_version=ENGINE_VERSION,
                    created_at=started,
                    finished_at=utcnow(),
                )
            )
            session.flush()
        logger.info("dataset %s audited: %s (%s)", dataset_id, status, score)

    # ------------------------------------------------------------------ inspection
    def _inspect(self, yaml_path: Path, quality_dir: Path) -> dict[str, Any]:
        data = _read_yaml(yaml_path)
        root = _dataset_root(yaml_path, data)
        names = data.get("names") or []
        if isinstance(names, dict):
            class_count = len(names)
        elif isinstance(names, list):
            class_count = len(names)
        else:
            nc = data.get("nc")
            class_count = int(nc) if isinstance(nc, int) else 0

        image_to_split: dict[Path, str] = {}
        split_images: dict[str, list[Path]] = {}
        for split, key in (("train", "train"), ("val", "val"), ("val", "validation")):
            value = data.get(key)
            if value is None:
                continue
            paths = _split_images(root, value)
            for path in paths:
                resolved = path.resolve()
                image_to_split.setdefault(resolved, split)
                split_images.setdefault(split, []).append(resolved)

        images = sorted(image_to_split)
        labels_seen: set[Path] = set()
        exact_hashes: dict[str, list[Path]] = defaultdict(list)
        near_hashes: list[tuple[Path, int]] = []
        dimensions: list[tuple[int, int]] = []
        image_dimensions: dict[Path, tuple[int, int]] = {}
        classes: Counter[int] = Counter()
        invalid_images: list[str] = []
        invalid_rows: list[str] = []
        bad_classes: list[str] = []
        bad_coords: list[str] = []
        bad_boxes: list[str] = []
        missing_labels: list[str] = []
        empty_labels: list[str] = []
        small_objects = 0
        object_rows = 0

        for image_path in images:
            relative = _relative(image_path, root)
            try:
                with Image.open(image_path) as image:
                    image.load()
                    width, height = image.size
                    dimensions.append((width, height))
                    image_dimensions[image_path] = (width, height)
                    near_hashes.append((image_path, _dhash(image)))
                exact_hashes[_sha256(image_path)].append(image_path)
            except Exception as exc:
                invalid_images.append(f"{relative}: {type(exc).__name__}")
                continue

            label_path = _label_for_image(image_path, root)
            if label_path is None:
                missing_labels.append(relative)
                continue
            labels_seen.add(label_path.resolve())
            try:
                lines = label_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as exc:
                invalid_rows.append(f"{_relative(label_path, root)}: {type(exc).__name__}")
                continue
            if not any(line.strip() for line in lines):
                empty_labels.append(_relative(label_path, root))
            for line_number, line in enumerate(lines, 1):
                if not line.strip():
                    continue
                object_rows += 1
                parts = line.split()
                if len(parts) != 5:
                    invalid_rows.append(f"{_relative(label_path, root)}:{line_number}")
                    continue
                try:
                    class_id = int(float(parts[0]))
                    x, y, w, h = (float(value) for value in parts[1:])
                except ValueError:
                    invalid_rows.append(f"{_relative(label_path, root)}:{line_number}")
                    continue
                classes[class_id] += 1
                if class_id < 0 or class_id >= class_count:
                    bad_classes.append(f"{_relative(label_path, root)}:{line_number}")
                    continue
                if any(not math.isfinite(value) for value in (x, y, w, h)):
                    bad_coords.append(f"{_relative(label_path, root)}:{line_number}")
                    continue
                if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1):
                    bad_coords.append(f"{_relative(label_path, root)}:{line_number}")
                    continue
                if x - w / 2 < -1e-6 or x + w / 2 > 1 + 1e-6 or y - h / 2 < -1e-6 or y + h / 2 > 1 + 1e-6:
                    bad_boxes.append(f"{_relative(label_path, root)}:{line_number}")
                if w * h < SMALL_AREA_RATIO:
                    small_objects += 1

        expected_labels = set()
        for image_path in images:
            label = _label_for_image(image_path, root)
            if label is not None:
                expected_labels.add(label.resolve())
        orphan_labels = sorted(
            _relative(path, root)
            for path in (root.rglob("*.txt"))
            if path.is_file() and path.resolve() not in expected_labels and "labels" in path.parts
        )

        exact_groups = [
            values for values in exact_hashes.values() if len(values) > 1
        ]
        exact_leaks: list[tuple[Path, Path]] = []
        for group in exact_groups:
            splits = {image_to_split.get(path, "unknown") for path in group}
            if len(splits) > 1:
                exact_leaks.append((group[0], group[1]))

        near_pairs: list[tuple[Path, Path]] = []
        split_leak_pairs: list[tuple[Path, Path]] = []
        if len(near_hashes) <= MAX_NEAR_DUPLICATE_IMAGES:
            for index, (left_path, left_hash) in enumerate(near_hashes):
                for right_path, right_hash in near_hashes[index + 1:]:
                    if _hamming(left_hash, right_hash) <= NEAR_DUPLICATE_HAMMING:
                        near_pairs.append((left_path, right_path))
                        if image_to_split.get(left_path) != image_to_split.get(right_path):
                            split_leak_pairs.append((left_path, right_path))
        else:
            logger.warning("skipping near-duplicate scan: %s images", len(near_hashes))

        issues: list[dict[str, Any]] = []
        if not images:
            _add_issue(
                issues,
                code="no_images",
                severity="blocking",
                message="数据集中没有可训练的图片",
                count=1,
            )
        if invalid_images:
            _add_issue(
                issues,
                code="invalid_images",
                severity="blocking",
                message="存在无法解码的图片",
                count=len(invalid_images),
                examples=invalid_images,
            )
        if invalid_rows:
            _add_issue(
                issues,
                code="invalid_label_rows",
                severity="blocking",
                message="标签不是 5 列 YOLO 格式",
                count=len(invalid_rows),
                examples=invalid_rows,
            )
        if bad_classes:
            _add_issue(
                issues,
                code="class_id_out_of_range",
                severity="blocking",
                message="标签类别 ID 超出数据集 names/nc",
                count=len(bad_classes),
                examples=bad_classes,
            )
        if bad_coords:
            _add_issue(
                issues,
                code="invalid_coordinates",
                severity="blocking",
                message="标签坐标不在 0–1 或宽高无效",
                count=len(bad_coords),
                examples=bad_coords,
            )
        if bad_boxes:
            _add_issue(
                issues,
                code="box_out_of_bounds",
                severity="blocking",
                message="目标框越出图像边界",
                count=len(bad_boxes),
                examples=bad_boxes,
            )
        if exact_leaks:
            examples = [
                f"{_relative(left, root)} ↔ {_relative(right, root)}"
                for left, right in exact_leaks
            ]
            _add_issue(
                issues,
                code="split_exact_leak",
                severity="blocking",
                message="train/val 存在完全相同的图片",
                count=len(exact_leaks),
                examples=examples,
            )
        if split_leak_pairs:
            examples = [
                f"{_relative(left, root)} ↔ {_relative(right, root)}"
                for left, right in split_leak_pairs
            ]
            _add_issue(
                issues,
                code="split_near_leak",
                severity="warning",
                message="train/val 存在近重复图片，请人工确认",
                count=len(split_leak_pairs),
                examples=examples,
            )
        if missing_labels:
            _add_issue(
                issues,
                code="missing_labels",
                severity="warning",
                message="部分图片没有对应标签文件；如为纯背景图请确认",
                count=len(missing_labels),
                examples=missing_labels,
            )
        if empty_labels:
            _add_issue(
                issues,
                code="empty_labels",
                severity="warning",
                message="存在空标签文件",
                count=len(empty_labels),
                examples=empty_labels,
            )
        if orphan_labels:
            _add_issue(
                issues,
                code="orphan_labels",
                severity="blocking",
                message="存在没有对应图片的标签文件",
                count=len(orphan_labels),
                examples=orphan_labels,
            )
        if classes and min(classes.values()) > 0 and max(classes.values()) / min(classes.values()) > 10:
            _add_issue(
                issues,
                code="class_imbalance",
                severity="warning",
                message="类别分布不均衡（最大/最小 > 10）",
                count=len(classes),
            )
        if small_objects:
            _add_issue(
                issues,
                code="small_objects",
                severity="warning",
                message=f"小目标（面积 < {SMALL_AREA_RATIO:.2%}）较多",
                count=small_objects,
            )
        low_resolution = [
            path
            for path, (width, height) in image_dimensions.items()
            if min(width, height) < 64
        ]
        if low_resolution:
            _add_issue(
                issues,
                code="low_resolution",
                severity="warning",
                message="存在短边小于 64px 的图片",
                count=len(low_resolution),
                examples=[_relative(path, root) for path in low_resolution[:20]],
            )

        summary = {
            "images": len(images),
            "decoded_images": len(dimensions),
            "invalid_images": len(invalid_images),
            "labels": len(expected_labels),
            "objects": object_rows,
            "classes": class_count,
            "observed_classes": len(classes),
            "split_counts": {key: len(value) for key, value in split_images.items()},
            "exact_duplicate_groups": len(exact_groups),
            "near_duplicate_pairs": len(near_pairs),
            "split_exact_leaks": len(exact_leaks),
            "split_near_leaks": len(split_leak_pairs),
            "missing_labels": len(missing_labels),
            "empty_labels": len(empty_labels),
            "orphan_labels": len(orphan_labels),
            "small_objects": small_objects,
            "blocking_issues": len([item for item in issues if item["severity"] == "blocking"]),
            "warning_issues": len([item for item in issues if item["severity"] == "warning"]),
            "class_counts": {str(key): value for key, value in sorted(classes.items())},
        }
        return {
            "summary": summary,
            "issues": issues,
            "content_sha256": content_hash(root),
            "image_count": len(images),
            "label_count": len(expected_labels),
            "class_count": class_count,
        }

    @staticmethod
    def _write_html(
        path: Path,
        dataset_id: str,
        status: str,
        score: float,
        summary: dict[str, Any],
        issues: list[dict[str, Any]],
    ) -> None:
        rows = "\n".join(
            "<tr>"
            f"<td>{html.escape(item['severity'])}</td>"
            f"<td>{html.escape(item['code'])}</td>"
            f"<td>{html.escape(item['message'])}</td>"
            f"<td>{item['count']}</td>"
            "</tr>"
            for item in issues
        ) or "<tr><td colspan='4'>未发现问题</td></tr>"
        metrics = "\n".join(
            f"<li><strong>{html.escape(str(key))}</strong>: {html.escape(str(value))}</li>"
            for key, value in summary.items()
            if not isinstance(value, dict)
        )
        document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>数据质量报告 · {html.escape(dataset_id)}</title>
<style>
body{{font-family:"PingFang SC","Microsoft YaHei",sans-serif;max-width:920px;margin:40px auto;padding:0 20px;color:#14251b;line-height:1.7}}
h1{{font-size:28px;margin-bottom:4px}} .meta{{color:#5a6c61;margin-bottom:24px}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;background:#eef2ed}}
table{{width:100%;border-collapse:collapse;margin-top:20px}}th,td{{border-bottom:1px solid #dce5de;padding:8px;text-align:left;vertical-align:top}}
th{{font-size:13px;color:#5a6c61}}code{{font-size:12px}}
</style></head><body>
<h1>数据质量报告</h1>
<div class="meta">{html.escape(dataset_id)} · <span class="badge">{html.escape(status)}</span> · {score} 分</div>
<h2>摘要</h2><ul>{metrics}</ul>
<h2>问题</h2><table><thead><tr><th>级别</th><th>代码</th><th>说明</th><th>数量</th></tr></thead><tbody>{rows}</tbody></table>
<p style="margin-top:32px;color:#5a6c61;font-size:12px">LiteTrain dataset quality engine v{ENGINE_VERSION}</p>
</body></html>"""
        path.write_text(document, encoding="utf-8")
