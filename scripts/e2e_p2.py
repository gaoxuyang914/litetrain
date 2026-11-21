"""Live P2 acceptance: webhook + serial pipeline + artifact download."""

from __future__ import annotations

import sys
import time

import requests

BASE = "http://127.0.0.1:8000/api/v1"


def main() -> int:
    hook = requests.post(
        f"{BASE}/webhooks",
        json={
            "name": "local receiver",
            "url": "http://127.0.0.1:9099/hook",
            "secret": "demo-secret",
            "events": ["task.succeeded", "task.failed", "pipeline.succeeded", "pipeline.failed"],
        },
        timeout=10,
    )
    hook.raise_for_status()
    print("webhook:", hook.json()["id"], hook.json()["url"])

    response = requests.post(
        f"{BASE}/pipelines",
        json={
            "name": "P2 E2E pipeline",
            "source_dataset": "coco128.yaml",
            "model": "yolov8n.pt",
            "epochs": 1,
            "imgsz": 64,
            "batch_size": 2,
            "workers": 0,
            "cpu_cores": 4,
            "priority": 60,
            "max_runtime_seconds": 600,
            "max_retries": 0,
            "max_images": 8,
        },
        timeout=15,
    )
    response.raise_for_status()
    pipeline = response.json()
    pipeline_id = pipeline["id"]
    print("pipeline:", pipeline_id, "first task:", pipeline["stages"][0]["task"]["id"])

    deadline = time.time() + 600
    previous = None
    last = None
    while time.time() < deadline:
        last = requests.get(f"{BASE}/pipelines/{pipeline_id}", timeout=15).json()
        snapshot = tuple(
            (
                stage["stage_key"],
                stage["status"],
                (stage["task"] or {}).get("status"),
                (stage["task"] or {}).get("progress"),
            )
            for stage in last["stages"]
        )
        if snapshot != previous:
            print("status:", last["status"], "progress:", last["progress"])
            for stage in last["stages"]:
                task = stage["task"] or {}
                print(
                    f"  {stage['stage_key']:10s} {stage['status']:10s} "
                    f"task={task.get('id')} task_status={task.get('status')} "
                    f"progress={task.get('progress')}"
                )
            previous = snapshot
        if last["status"] in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(2)

    print("final:", last["status"], last.get("failure_reason"))
    artifacts = requests.get(
        f"{BASE}/artifacts", params={"pipeline_id": pipeline_id}, timeout=30
    ).json()
    print("artifacts:", [(item["kind"], item["name"], item["size_bytes"]) for item in artifacts["items"]])
    for stage in last["stages"]:
        task = stage.get("task")
        if not task:
            continue
        detail = requests.get(f"{BASE}/tasks/{task['id']}", timeout=10).json()
        print(
            "stage task:",
            stage["stage_key"],
            detail["status"],
            detail.get("failure_reason"),
            "executor=",
            detail.get("executor"),
        )
        if detail["status"] in {"failed", "cancelled"}:
            logs = requests.get(f"{BASE}/tasks/{task['id']}/logs", timeout=10).json()
            print("\n".join(logs["content"].splitlines()[-30:]))
    if last["status"] == "succeeded" and artifacts["items"]:
        weight = next((item for item in artifacts["items"] if item["name"] == "best.pt"), None)
        if weight:
            download = requests.get(f"http://127.0.0.1:8000{weight['download_url']}", timeout=30)
            print("download:", download.status_code, len(download.content), "bytes")
            if download.status_code != 200 or len(download.content) < 1000:
                return 1
    time.sleep(3)
    deliveries = requests.get(f"{BASE}/webhooks/deliveries", timeout=10).json()
    print(
        "webhook deliveries:",
        [(item["event_type"], item["status"], item["response_code"]) for item in deliveries["items"][:10]],
    )
    print("PIPELINE E2E", "OK" if last["status"] == "succeeded" else "FAILED")
    return 0 if last["status"] == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
