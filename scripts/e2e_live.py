"""Live end-to-end checks against a running LiteTrain API.

This is intentionally a black-box script: it exercises the same endpoints the
Vue UI uses and prints a compact evidence trail for queueing, cancellation and
runtime watchdog behaviour.
"""

from __future__ import annotations

import json
import sys
import time

import requests

BASE = "http://127.0.0.1:8000/api/v1"


def submit(name: str, *, cpu: int, epochs: int = 1, imgsz: int = 96, priority: int = 50, timeout: int = 300):
    payload = {
        "name": name,
        "model": "yolov8n.pt",
        "dataset": "coco128.yaml",
        "epochs": epochs,
        "imgsz": imgsz,
        "batch_size": 4,
        "workers": 0,
        "cpu_cores": cpu,
        "priority": priority,
        "max_runtime_seconds": timeout,
        "max_retries": 0,
    }
    response = requests.post(f"{BASE}/tasks", json=payload, timeout=10)
    response.raise_for_status()
    task = response.json()
    print(f"submitted {task['id']} name={name!r} cpu={cpu} priority={priority} status={task['status']}")
    return task


def get(task_id: str):
    response = requests.get(f"{BASE}/tasks/{task_id}", timeout=10)
    response.raise_for_status()
    return response.json()


def wait_for(task_id: str, statuses: set[str], timeout: float = 60):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        task = get(task_id)
        if task["status"] != last:
            print(
                f"  {task_id}: status={task['status']} progress={task['progress']} "
                f"cpu={task['latest_cpu_percent']} mem={task['latest_memory_mb']}"
            )
            last = task["status"]
        if task["status"] in statuses:
            return task
        time.sleep(1.5)
    raise TimeoutError(f"{task_id} did not reach {statuses}; last={last}")


def overview():
    response = requests.get(f"{BASE}/cluster/overview", timeout=10)
    response.raise_for_status()
    return response.json()


def read_sse(task_id: str, max_events: int = 3) -> None:
    print("SSE probe:")
    with requests.get(
        f"{BASE}/tasks/{task_id}/logs/stream?offset=0",
        stream=True,
        timeout=(5, 8),
    ) as response:
        response.raise_for_status()
        events = 0
        for raw in response.iter_lines(decode_unicode=True):
            if not raw:
                continue
            line = raw.strip()
            if line.startswith("event:"):
                print(" ", line)
            if line.startswith("data:") and events < max_events:
                payload = line.removeprefix("data:").strip()
                try:
                    parsed = json.loads(payload)
                    fragment = parsed.get("content", "")
                    if fragment:
                        print("   log:", fragment[:100].replace("\n", " | "))
                except Exception:
                    pass
                events += 1
                if events >= max_events:
                    break


def main() -> int:
    health = requests.get("http://127.0.0.1:8000/healthz", timeout=5)
    health.raise_for_status()
    print("health", health.json())

    holder = submit("Queue holder - 6 cores", cpu=6, epochs=6, imgsz=160, priority=10, timeout=600)
    wait_for(holder["id"], {"running"}, timeout=40)
    pending = submit("High priority - waiting", cpu=4, epochs=1, imgsz=96, priority=95, timeout=300)
    time.sleep(4)
    pending_now = get(pending["id"])
    cluster = overview()
    print(
        "queue evidence:",
        {
            "holder": get(holder["id"])["status"],
            "pending": pending_now["status"],
            "queue_position": pending_now["queue_position"],
            "allocated": cluster["cpu"]["allocated"],
            "available": cluster["cpu"]["available"],
            "status_counts": cluster["tasks"],
        },
    )
    assert pending_now["status"] == "pending", pending_now
    assert pending_now["queue_position"] == 1, pending_now
    assert cluster["cpu"]["allocated"] == 6, cluster["cpu"]
    read_sse(holder["id"], max_events=3)

    print("cancelling holder to free 6 CPUs...")
    response = requests.post(f"{BASE}/tasks/{holder['id']}/cancel", timeout=20)
    response.raise_for_status()
    assert response.json()["task"]["status"] == "cancelled"
    wait_for(pending["id"], {"running"}, timeout=45)
    cluster = overview()
    print(
        "after cancel:",
        {
            "cancelled": get(holder["id"])["status"],
            "runner": get(pending["id"])["status"],
            "allocated": cluster["cpu"]["allocated"],
        },
    )
    assert cluster["cpu"]["allocated"] == 4

    timeout_task = submit(
        "Timeout watchdog - 15s", cpu=2, epochs=5, imgsz=160, priority=40, timeout=15
    )
    time.sleep(2)
    wait_for(timeout_task["id"], {"failed"}, timeout=60)
    final = get(timeout_task["id"])
    cluster = overview()
    print(
        "timeout evidence:",
        {
            "status": final["status"],
            "reason": final["failure_reason"],
            "runtime": final["runtime_seconds"],
            "allocated_after": cluster["cpu"]["allocated"],
        },
    )
    assert final["failure_reason"] == "runtime_timeout", final
    assert cluster["cpu"]["allocated"] == 4, cluster["cpu"]

    # Let the short runner finish naturally.
    wait_for(pending["id"], {"succeeded", "failed"}, timeout=90)
    final_runner = get(pending["id"])
    print("runner final:", {"status": final_runner["status"], "runtime": final_runner["runtime_seconds"]})
    metrics = requests.get(f"{BASE}/tasks/{pending['id']}/metrics", timeout=10).json()
    print("metric series:", [s["name"] for s in metrics["series"]])
    print("E2E OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
