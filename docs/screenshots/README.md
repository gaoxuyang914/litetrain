# Screenshots

These screenshots are captured from the real running application (FastAPI + Ray +
Redis + Vite) with Playwright-driven Microsoft Edge at a 1440×1000 viewport.

The current set is **v2.5 Ultralytics-style workspace**: a light rail with task
search, blue square mark, hairline cards, black primary actions, a light cluster
topology, quality-gated datasets and reproducible run manifests. The interface was
checked at 375 / 768 / 1280 widths (no horizontal overflow) and against the
`deepseek-frontend-design` self-check: 16px body copy, 12px minimum UI labels,
2px visible focus, and reduced-motion fallbacks. Main text/background token pairs
measure 4.5:1–19.0:1.

| File | Page / state |
| --- | --- |
| `overview.png` | Light multi-node overview: CPU cards, topology, node resource table and recent tasks |
| `login.png` | Platform sign-in page on port 5173 |
| `admin.png` | Dedicated admin console on port 5174 with independent session |
| `tasks.png` | Task list with running task, status filters, pagination and row selection |
| `submit.png` | YOLO CPU task form, quality gate, compute threads/cache and live preview |
| `detail.png` | Succeeded task with logs, metrics, run manifest and checkpoint |
| `queue.png` | Pending task detail while both simulated nodes are occupied |
| `dark.png` | Dark mode cluster overview |
| `pipelines.png` | Serial preprocess → train → evaluate pipelines, both completed |
| `pipeline-v2.png` | Pipeline v2 stage report with dedupe/split and evaluation metrics |
| `pipeline-create-v2.png` | Preprocess and evaluation policy fields in the pipeline form |
| `preprocess-stage.png` | Preprocess task detail with data report, not training copy |
| `evaluate-stage.png` | Evaluate task detail with mAP/latency report, not training copy |
| `assets.png` | Dataset registry with quality status and a warning dataset |
| `artifacts.png` | Model artifact registry with kind filter, SHA-256 and download |
| `model-import.png` | Base `.pt` weight import and train-link action |
| `webhooks.png` | Signed webhook endpoint, delivery outbox and HMAC header hint |
| `topology.png` | Multi-node topology editor |
| `sidebar-collapsed.png` | Desktop navigation rail collapsed to icons |
| `quality-report.png` | Dataset quality report with warning issues |
| `settings.png` | Runtime settings for timeout, heartbeat, retries and CPU quota |
| `task-limits.png` | Submit form warning when a value exceeds the admin runtime cap |
| `preemption.png` | Cancelled preempted task with checkpoint resume hint |
| `rerun-inplace.png` | Same task rerun with attempt history preserved |
| `benchmark.png` | CPU benchmark result and selected thread/worker profile |
| `compare.png` | Two-run comparison with dataset compatibility warning |
| `distribution.png` | Expanded task status distribution with 5m–180d range selector |
| `rerun.png` | Finished task with one-click rerun action |
| `resume.png` | Checkpoint selection and full/weights resume dialog |
| `resume-no-checkpoint.png` | Blocked resume when the task stopped before its first epoch |

The screenshots are illustrative; the actual demo can be run locally with:

```powershell
.\scripts\start_ray.ps1
.\scripts\start_backend.ps1
.\scripts\start_frontend.ps1
# optional webhook receiver for webhooks.png
.\.venv\Scripts\python scripts\webhook_receiver.py --port 9099
```
