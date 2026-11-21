# Screenshots

These screenshots are captured from the real running application (FastAPI + Ray +
Redis + Vite) with Playwright-driven Microsoft Edge at a 1440×1000 viewport.

The current set is **v2.4 Operations Ledger**: a flat dark navigation rail, paper
workspace, the live cluster topology, quality-gated datasets and reproducible run
manifests. The interface was checked at 375 / 768 / 1280 widths (no horizontal
overflow) and against the `deepseek-frontend-design` self-check: 16px body copy,
12px minimum UI labels, 2px visible focus, and reduced-motion fallbacks. Main
text/background token pairs measure 5.18:1–16.82:1.

| File | Page / state |
| --- | --- |
| `overview.png` | Multi-node overview with two online nodes, CPU/queue/host cards and live topology |
| `tasks.png` | Task list with status filters, pagination and run selection |
| `submit.png` | YOLO CPU task form, dataset quality gate and live command preview |
| `detail.png` | Succeeded task with run manifest and checkpoint |
| `queue.png` | Pending task detail while both simulated nodes are occupied |
| `dark.png` | Dark mode cluster overview |
| `pipelines.png` | Serial preprocess → train → evaluate pipelines, both completed |
| `assets.png` | Dataset registry with quality status and a warning dataset |
| `artifacts.png` | Model artifact registry with kind filter, SHA-256 and download |
| `webhooks.png` | Signed webhook endpoint, delivery outbox and HMAC header hint |
| `topology.png` | Multi-node topology editor |
| `sidebar-collapsed.png` | Desktop navigation rail collapsed to icons |
| `quality-report.png` | Dataset quality report with blocking/warning issues |
| `benchmark.png` | CPU benchmark result and selected thread/worker profile |
| `compare.png` | Two-run comparison with dataset compatibility warning |

The screenshots are illustrative; the actual demo can be run locally with:

```powershell
.\scripts\start_ray.ps1
.\scripts\start_backend.ps1
.\scripts\start_frontend.ps1
# optional webhook receiver for webhooks.png
.\.venv\Scripts\python scripts\webhook_receiver.py --port 9099
```
