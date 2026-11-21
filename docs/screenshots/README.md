# Screenshots

These screenshots are captured from the real running application (FastAPI + Ray +
Redis + Vite) with Playwright-driven Microsoft Edge at a 1440×1000 viewport.

| File | Page / state |
| --- | --- |
| `overview.png` | Multi-node overview: two 4-CPU simulated nodes each running a 3-CPU task, one task queued |
| `tasks.png` | Task list with running / pending / succeeded tasks, filters and pagination |
| `submit.png` | YOLO CPU task form and live command preview |
| `detail.png` | Succeeded task: logs, metrics and lifecycle |
| `queue.png` | Pending task detail while both simulated nodes are occupied |
| `dark.png` | Dark mode cluster overview |
| `pipelines.png` | Serial preprocess → train → evaluate pipelines, both completed |
| `assets.png` | Dataset registry with built-in COCO128 and an uploaded 24-image dataset |
| `artifacts.png` | Model artifact registry with kind filter, SHA-256 and download |
| `webhooks.png` | Signed webhook endpoint, delivery outbox and HMAC header hint |
| `topology.png` | Multi-node topology editor |

The screenshots are illustrative; the actual demo can be run locally with:

```powershell
.\scripts\start_ray.ps1
.\scripts\start_backend.ps1
.\scripts\start_frontend.ps1
# optional webhook receiver for webhooks.png
.\.venv\Scripts\python scripts\webhook_receiver.py --port 9099
```
