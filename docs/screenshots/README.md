# Screenshots

These screenshots are captured from the real running application (FastAPI + Ray +
Redis + Vite) with Playwright-driven Microsoft Edge at a 1440×1000 viewport.

| File | Page / state |
| --- | --- |
| `overview.png` | Cluster overview with one running task and one queued task |
| `tasks.png` | Task list with status filters and pagination |
| `submit.png` | YOLO CPU task form and live command preview |
| `detail.png` | Succeeded task: logs, metrics and lifecycle |
| `queue.png` | Pending task detail while a 6-CPU task occupies the cluster |
| `dark.png` | Dark mode detail or overview |

The screenshots are illustrative; the actual demo can be run locally with:

```powershell
.\scripts\start_ray.ps1
.\scripts\start_backend.ps1
.\scripts\start_frontend.ps1
```
