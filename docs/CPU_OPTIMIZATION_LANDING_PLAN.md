# LiteTrain CPU 训练平台优化落地方案

> 基于《YOLO CPU 训练平台优化方案 v1.0》与当前 LiteTrain **v2.3.0** 实现整理  
> 目标：把方向性建议收敛成可排期、可验收、可回滚的工程计划  
> 日期：2026-11-21

## 实施状态（v2.4.0）

第一轮 P0 已落地：

- **M0**：SQLite 可重复加列迁移；数据集内容 SHA-256；任务配置快照与 Run Manifest。
- **M1**：数据质量体检（标签结构/坐标/类别、图片解码、缺失/孤儿标签、完全重复、train/val 泄漏、类别分布、小目标）、质量报告、警告确认与训练/流水线准入。
- **M2**：短周期 CPU Benchmark（threads/workers，images/s、耗时、峰值 RSS）和推荐 profile API。
- **M3**：每轮 checkpoint 上报、任务详情展示、失败/取消后人工 resume；worker 支持 seed/save_period/patience/cache/compute_threads。
- **M4**：Run Manifest API、任务详情清单、2–4 个任务对比 API/UI。
- 验证：后端 33 个测试通过；前端 typecheck/build 通过；真实 1-epoch 任务产出 manifest 和 checkpoint；真实 Benchmark 返回推荐值。

P1/P2 尚未实施，仍按本文后续章节推进。下文保留原始规划口径，便于对照。

## 0. 一页结论

当前平台已经具备任务队列、CPU 配额、优先级调度、心跳/超时回收、Ray detached actor、日志 SSE、指标曲线、资源采样、流水线、数据集注册、产物 SHA-256 和 Webhook outbox。源方案里的“任务队列、资源限制、训练日志和指标展示”**不需要重做**。

真正应该先补的是四件事：

1. **数据准入**：上传后自动体检，严重标签错误或 train/val 泄漏直接阻断训练。
2. **CPU 实测调优**：把“线程数=CPU 配额”改为基于真实 Benchmark 的 profile；同时记录 I/O、内存和吞吐。
3. **可恢复**：周期 checkpoint，失败后从最近 checkpoint 恢复；P0 先做人工恢复，不自动改参重训。
4. **可复现与可比较**：每次运行生成 manifest（数据哈希、模型哈希、配置、seed、版本、Git、CPU），支持两次实验并排对比。

P0 不建议引入 PostgreSQL、MinIO、MLflow、FiftyOne、ClearML 或 Optuna；当前单机 SQLite + Redis + 本地文件系统仍然够用。先建立字段、ID、报告格式和迁移机制，等出现多用户/多 API 实例/大数据量时再替换存储。

本方案建议的 P0 总量约 **32–42 人日**，单人约 **6–8 周**（含测试和 UI）。不要用“CPU 优化一定带来数量级加速”作为目标；所有收益必须用同一数据集、同一模型、同一评测协议做对照。

## 1. 当前项目基线

### 1.1 运行环境（本机实测）

| 项 | 当前值 |
|---|---|
| CPU | AMD Ryzen 7 PRO 5875U，8 核 / 16 逻辑线程 |
| 内存 | 30.8 GB |
| 磁盘 | 512 GB NVMe SSD，当前约 69 GB 可用 |
| 系统 | Windows |
| Python | 3.13.5 |
| PyTorch | 2.14.0+cpu |
| Ultralytics | 8.4.216 |
| Ray | 2.60.0 |
| ONNX Runtime | 未安装 |
| 平台 CPU 预算 | `LITETRAIN_TOTAL_CPUS=8` |
| 平台用户配额 | `LITETRAIN_USER_CPU_QUOTA=8` |
| 执行模式 | Ray 优先，Ray 不可用时本地子进程降级 |

注意：宿主有 16 个逻辑线程，但平台只登记 8 个 CPU。这是当前配置的刻意预算，不应在优化中偷偷改成 16；是否调整需要单独确认。

### 1.2 已有能力与源方案的对应关系

| 源方案能力 | 当前实现 | 结论 |
|---|---|---|
| 任务队列、资源配额 | `Task` + `ResourceAllocation` + `Scheduler`，1 CPU 粒度，用户配额 | 已有，继续用 |
| 心跳、超时、异常退出回收 | worker 4s 心跳，scheduler watchdog | 已有 |
| 失败重试 | `max_retries`，失败自动重试；超时/取消不重试 | 已有，但重试从头开始 |
| CPU/GPU 执行隔离 | `ExecutionManager` 负责 Ray/local 启动；worker 固定 `device=cpu` | 只是“启动器”，还不是统一 TrainingExecutor |
| 日志与指标 | 每任务日志、SSE、ECharts、资源采样 | 已有 |
| 数据集注册 | ZIP 上传、YAML 校验、图片/标签计数、版本名 | 有“结构校验”，没有“质量校验” |
| 数据版本 | `datasets.name/version`，YAML 路径 | 没有内容哈希，没有不可变快照语义 |
| 流水线 | preprocess → train → evaluate | 已有串行编排；preprocess 只是取前 N 张复制 |
| 产物 | 扫描 `best.pt/last.pt/results.csv/args.yaml`、SHA-256、下载 | 已有；`.onnx` 有匹配规则但未生成 |
| Webhook | outbox + HMAC + 重试 | 已有，非本轮重点 |
| 实验追踪 | 任务配置 + 指标 + 事件 + 结果文件 | 有原始数据，没有统一 manifest/对比 |
| 断点恢复 | 无 checkpoint、无 resume | 明确缺口 |
| 数据质量、重复/泄漏 | 无 | 明确缺口 |
| CPU 自动 Benchmark | 无；threads 直接等于 `cpu_cores` | 明确缺口 |
| 错误分析 | `model.val(plots=False)`，只有标量指标 | 明确缺口 |
| ONNX/量化 | 无 | 明确缺口 |
| 自动异常诊断 | 无 | 明确缺口 |
| GPU Executor | 无，但字段保留 `device` | P2 再实现 |

### 1.3 当前实测样本

| 场景 | 配置 | 实测耗时 |
|---|---|---:|
| 50 epoch / 160px / batch 4 / workers 0 / 3 cores | `queue-holder-*` | 172.9–193.6 s |
| 2 epoch / 96px / batch 2 / 3 cores | `parallel-node-*` | 18.1–19.0 s |
| 1 epoch / 64px / batch 2 / pipeline train stage | P2 pipeline | 14.7 s |
| 当前数据集 | COCO128 128 张，mini 24 张 | 7.0 MB / 1.6 MB |
| 当前 `data/runs` | 154 个文件 | 约 807 MB |
| 当前指标点 | 7,387 个 | — |

这组数据说明：

- 现在适合做短周期 Benchmark，不适合用 50 epoch 的完整训练当调参试验。
- `data/runs` 已经接近 1 GB，checkpoint 和错误分析图片必须带保留策略，否则会快速膨胀。
- 当前 `workers=0`，不能假设开启多 worker 一定更快；Windows + 小数据集下反而可能因进程启动和数据复制变慢，必须实测。

### 1.4 当前代码中与本方案直接相关的位置

| 文件 | 当前职责 | 本次要改什么 |
|---|---|---|
| `backend/app/services/datasets.py` | ZIP 导入、YAML、图片/标签计数、SHA 工具 | 加内容哈希、质量报告、审计状态 |
| `backend/app/training/worker.py` | train/preprocess/evaluate；`torch.set_num_threads(cpu_cores)`；`seed=0`；无 save_period/resume | 加 checkpoint、resume、cache、perf 采样、导出、错误分析 |
| `backend/app/services/executors.py` | 生成命令、启动 Ray/local、环境变量 | 引入 `TrainingExecutor` 接口；增加 compute_threads/workers/cache 参数 |
| `backend/app/services/scheduler.py` | 调度、watchdog、重试、恢复 | 保留状态机；接 checkpoint 信息；不要自动改参重训 |
| `backend/app/models.py` | 任务、指标、数据集、流水线、产物 | 新增质量报告、checkpoint、manifest、benchmark 表 |
| `backend/app/database.py` | `create_all`，无迁移 | 引入 Alembic 或显式迁移；SQLite 加列不会自动生效 |
| `backend/app/api/assets.py` | 数据集/产物 API | 加 audit/quality/report 接口 |
| `backend/app/api/tasks.py` | 任务 CRUD、日志、指标 | 加 manifest/checkpoint/resume/compare 接口 |
| `frontend/src/views/AssetsView.vue` | 数据集列表 | 质量状态、报告抽屉、阻断提示 |
| `frontend/src/views/TaskSubmitView.vue` | 提交表单 | 质量准入、推荐 profile、resume 入口 |
| `frontend/src/views/TaskDetailView.vue` | 日志/指标/事件 | checkpoint、manifest、错误分析入口 |
| `frontend/src/views/TaskListView.vue` | 任务列表 | 选择两个任务进行对比 |

## 2. 差距地图：源方案逐项落地判断

| 源方案项 | 当前状态 | 建议动作 | 优先级 |
|---|---|---|---|
| 数据集自动体检 | 只有文件计数 | 规则体检 + 报告 + 训练准入 | P0 |
| 完全重复 / 近重复 / 泄漏 | 无 | SHA-256 + pHash/dHash，按 split 比较 | P0 |
| 数据缓存 | 无显式 `cache` 参数 | benchmark 测试 none/disk/ram，按命中率决定 | P0（先测量） |
| 合理切分与版本 | 有版本名，无内容哈希/分层切分 | 内容哈希；P1 分层/分组切分 | P0/P1 |
| 轻量模型/尺寸推荐 | 固定 YOLOv8n，尺寸手填 | 预设快速/标准/高精度；基于基准推荐 | P1 |
| CPU 线程/Worker Benchmark | `threads=cpu_cores` | 独立 `compute_threads`，矩阵实测 | P0 |
| 断点恢复 | 无 | checkpoint + 人工 resume；P0 不自动重训 | P0 |
| 实验追踪 | 有原始配置/指标，无 manifest | 完整 manifest + 对比 | P0 |
| 分阶段训练/Early Stopping | 无 patience | 加 `patience`、`save_period`、三档预设 | P1 |
| 自动异常诊断 | 无 | 规则引擎：NaN、低 CPU、过拟合迹象；只给证据 | P1 |
| 错误样本/类别分析 | 只有 mAP 等标量 | val plots + 按类指标 + FP/FN 样本 | P1 |
| ONNX Runtime Benchmark | 无 | 导出 ONNX，ORT CPU 延迟/吞吐对照 | P1 |
| 量化/交付包 | 无 | INT8 仅推理；交付目录 + manifest | P2 |
| 困难样本回流 | 无 | 低置信度/分歧/多样性候选 + 人工审核 | P2 |
| 统一 Executor | 启动器，不是训练接口 | `TrainingExecutor` + `CpuExecutor`；预留 `CudaExecutor` | P0 接口，P2 实现 GPU |

## 3. 目标架构

```text
Vue UI
  ├─ 数据集质量报告 / 训练准入
  ├─ CPU Profile 推荐
  ├─ 任务 checkpoint / manifest / compare
  └─ 错误分析 / ONNX 报告
          ↓ REST + SSE
FastAPI
  ├─ Task / Pipeline / Scheduler          （复用）
  ├─ DatasetQualityService                （新增，后台审计线程）
  ├─ BenchmarkService                     （新增，独立短进程）
  ├─ RunManifestService                   （新增）
  ├─ CheckpointService                    （新增）
  └─ EvaluationService                    （P1）
          ↓
SQLite(WAL) + Redis + 本地文件系统
          ↓
ExecutionManager
  └─ TrainingExecutor (protocol)
       ├─ CpuExecutor  ← 现在
       └─ CudaExecutor ← 未来
          ↓
app.training.worker
  ├─ train/preprocess/evaluate
  ├─ benchmark
  ├─ export
  └─ analysis
```

### 3.1 明确的架构约束

- **不重写调度器**：继续使用现有 `Task`/`Scheduler`/`ResourceAllocation`；新能力挂在服务层。
- **数据集审计独立于训练 CPU 队列**：审计是短时 I/O/CPU 任务，用单独后台线程 + 单并发；不要占训练配额，也不要伪装成训练任务。
- **Benchmark 独立于训练队列**：基准测试使用独立短进程和全局 benchmark 锁；训练运行期间拒绝 benchmark，避免测量污染。
- **P0 不扩展任务状态机**：保留 `pending/running/succeeded/failed/cancelled`。审计用 `quality_status`，准备阶段用事件/进度表达。P1 若确实需要人工暂停，再引入 `paused`。
- **P0 只人工 resume**：失败后显示“从最近 checkpoint 恢复”，创建新 Task 并记录 `resume_from_task_id`；不自动使用 checkpoint 重试，避免掩盖数据/配置错误。
- **本地存储优先**：继续 `data/datasets`、`data/runs`、`data/benchmarks`、`data/quality`；不引入 MinIO。
- **数据库迁移**：当前 `create_all` 不能给已有 SQLite 表加列；本轮已加入可重复执行的加列迁移和备份流程。若后续进入多实例/频繁演进，再升级到 Alembic。
- **GPU 预留**：只定义接口和配置字段，不在本轮写 CUDA 代码。

### 3.2 新增存储目录

```text
data/
├── datasets/                 # 现有
├── quality/
│   └── {dataset_id}/
│       ├── report.json
│       ├── report.html
│       ├── issues.jsonl
│       └── samples/          # 最多 200 张问题缩略图
├── benchmarks/
│   └── {benchmark_id}/
│       ├── config.json
│       ├── results.json
│       └── run.log
├── runs/                     # 现有
│   └── {task_id}/
│       ├── weights/          # best.pt/last.pt/epochN.pt
│       ├── checkpoint.json
│       ├── manifest.json
│       └── analysis/         # P1
└── releases/
    └── {task_id}/            # P2
```

## 4. P0 工作包

### P0-1 数据集自动体检与训练准入

**目标**：上传后自动出报告；严重标签错误、图片不可解码、train/val 完全重复或泄漏直接阻断；其他问题给 warning 和人工确认。

**执行方式**

- 上传接口只做快速结构校验（ZIP 安全、YAML、至少一张 train 图），返回 `status=auditing`。
- `DatasetQualityService` 用单后台线程执行深度审计；启动时把中断的 `auditing` 重置为 `pending` 后重跑。
- 深度审计结果写入 `data/quality/{dataset_id}`，数据库只保存摘要和状态。
- 训练创建时读取数据集的 `quality_status`：`blocked` 拒绝；`auditing` 拒绝；`warning` 需要显式 `quality_ack=true`。

**检查项与准入级别**

| 检查 | 实现 | 级别 |
|---|---|---|
| ZIP 路径穿越 | 现有检查保留 | 阻断 |
| 图片可解码、宽高 > 0 | Pillow 解码，限制像素数 | 阻断 |
| 标签 5 列、类别 ID < nc | 解析 txt | 阻断 |
| 坐标均在 `[0,1]`，w/h > 0 | 数值校验 | 阻断 |
| 图片无对应标签 | 按 YOLO 结构检查 | 阻断（可配置策略） |
| 标签无对应图片 | 扫描 orphan | 阻断 |
| train/val 完全重复 | SHA-256 分组 | 阻断 |
| train/val 近重复 | dHash/pHash 汉明距离 ≤ 6 | warning，不自动阻断 |
| 同 split 近重复 | 同上 | warning |
| 类别分布、长尾、空标签、小目标 | 统计 | warning |
| 分辨率异常 | 分位数统计 | warning |
| 疑似错标 | P1 预训练模型辅助 | P1 |

**报告内容**

- 总数、可解码数、标签数、类别数、split 统计。
- 类别分布、每图目标数、目标面积分位、分辨率分位。
- 重复组、泄漏组、错误样本缩略图（最多 200）。
- 阻断项、警告项、`quality_score`（只做辅助，不参与准入）。
- 报告版本号，便于后续规则升级后重算。

**数据模型**

`datasets` 增加：

- `content_sha256`：内容哈希（排序后的 relative path + file SHA-256）。
- `quality_status`：`pending / auditing / passed / warning / blocked / failed`。
- `quality_score`：0–100。
- `quality_report_path`、`quality_issue_path`。
- `quality_engine_version`、`quality_checked_at`。

**API**

- `POST /api/v1/datasets/{id}/audit`
- `GET /api/v1/datasets/{id}/quality`
- `GET /api/v1/datasets/{id}/quality/report?format=json|html`
- `GET /api/v1/datasets/{id}/quality/issues?severity=blocking|warning`
- `GET /api/v1/datasets/{id}/quality/samples/{name}`
- `POST /api/v1/datasets/{id}/quality/ack`（只对 warning）

**任务创建契约**

- `TaskCreate` 增加可选 `dataset_id` 和 `quality_ack`；保留现有 `dataset` 字段作为兼容路径。
- 有 `dataset_id` 时由后端解析 YAML、写入 `dataset_sha256`，不再信任前端传的文件名。
- `quality_status=auditing/blocked` 返回 422；`warning` 必须带 `quality_ack=true`；内置数据集在 seed 时预审计。

**UI**

- 数据集行显示质量徽标与进度；点击打开报告抽屉。
- 提交任务的数据集下拉显示质量状态；阻断时禁用提交并给原因。
- 报告里能按类别/问题类型筛选，显示样本缩略图和 split 泄漏提示。

**验收**

- 固定测试夹具：损坏图片、越界框、类别越界、train/val 同图、近重复、空标签。
- 坏数据 100% 被阻断；完全重复/泄漏 100% 被识别。
- 1,000 张、512 MB 以内数据集审计 < 60 秒（本机 SSD；不含缩略图生成可放宽）。
- 审计中断后重启可重跑，不产生半成品记录。

**工作量**：6–8 人日（含测试夹具和报告 UI）。

---

### P0-2 CPU 线程 / DataLoader 自动 Benchmark

**目标**：用短周期实测决定 `compute_threads`、`workers`、`batch`、`imgsz`，而不是继续把 `cpu_cores` 直接赋给 `torch.set_num_threads`。

**当前问题**

- worker 里 `torch.set_num_threads(cpu_cores)`，同时设置 `OMP_NUM_THREADS`/`MKL_NUM_THREADS`；`cpu_cores` 是调度预留，不一定是计算最优线程数。
- Windows 上 `workers>0` 受 spawn、数据复制和磁盘影响，不能假设更快。
- 没有记录数据加载耗时、RSS 峰值、swap，无法判断瓶颈。
- 当前 `workers=0` 的示例训练 50 epoch 约 3 分钟；完整训练不适合反复试参。

**Benchmark 协议**

统一在 `data/datasets` 的固定子集上跑（优先 COCO128 mini / 24–128 张）；配置：

| 维度 | 取值 |
|---|---|
| compute_threads | 2 / 4 / 6 / 8 |
| DataLoader workers | 0 / 2 |
| batch | 4 / 8 |
| imgsz | 160 / 320 |
| cache | none（首轮） |
| epochs | 1，先 warmup 1 轮或固定 batch 预热 |
| 重复 | 2–3 次，取中位数 |

先做最多 8–12 个组合，每个组合硬上限 90 秒；若 RSS > 系统内存 70% 或 swap 增长，立即终止该组合。  
每个组合记录：images/s、epoch 秒、数据等待占比、CPU%、RSS 峰值、是否 OOM/swap、错误。  
Benchmark 运行前后检查没有训练任务在跑；使用全局 benchmark 锁。

**Profile 选择**

- 先排除 OOM/swap/错误的组合。
- 在剩余组合中按 images/s 排序；吞吐相同则选较低 threads/workers，减少资源占用。
- 生成的 profile 绑定 host fingerprint + dataset hash + model hash，避免换机器/换数据后误用。
- 表单允许手动覆盖；覆盖必须显示“与推荐 profile 不同”。

**缓存策略**

- worker 增加 `--cache none|disk|ram`；默认 `none`，不默认打开。
- Benchmark 至少比较 `none` 与 `disk`；只有 data wait 占比高且磁盘有空间时才推荐 `disk`。
- disk cache 目录按 `dataset content_sha256 + imgsz` 命名；数据集重新审计/哈希变化时自动失效。
- RAM cache 只在可用内存 > 50% 且数据集小于内存 25% 时开放；需要显示预计内存占用。
- cache 失败不能阻断训练：自动降级 `none` 并写入事件。
- 给出缓存清理命令和容量上限，避免当前 `data/runs` 已接近 1 GB 的情况继续膨胀。

**数据模型**

`benchmark_runs`：

- `id`、`status`、`host_json`、`matrix_json`、`selected_json`、`log_path`、`created_at`、`finished_at`。
- 可选 `cpu_profile` 缓存表：按 host/dataset/model/imgsz 查最近一次结果。

**API**

- `POST /api/v1/cluster/benchmarks`
- `GET /api/v1/cluster/benchmarks/{id}`
- `GET /api/v1/cluster/benchmarks/latest`
- `POST /api/v1/cluster/benchmarks/{id}/apply`（只写推荐值，不自动改运行中任务）

**UI**

- 总览或提交页增加“CPU 自动调优”抽屉：显示矩阵表、选中项、RSS/吞吐、应用按钮。
- 任务提交页显示“推荐：threads=6, workers=0, batch=4, imgsz=160”，可展开理由。

**验收**

- 同一数据集/模型重复 3 次，选择结果的 images/s 波动 ≤ 10%（本机无其他负载时）。
- 与当前默认组合对比，至少有可解释的收益或无回归；达不到 10% 收益时记录“无显著差异”，不能伪造加速。
- 不出现 OOM/swap；benchmark 期间不并发训练。
- profile 能持久化并在下次提交时复用。

**工作量**：7–9 人日（含 benchmark worker、表格 UI、锁和测试）。

---

### P0-3 Checkpoint 与任务恢复

**目标**：长训练中断后不丢全部进度；P0 做人工恢复，P1 再评估自动恢复策略。

**当前问题**

- `model.train(save=True)` 只保证常规 `last.pt`/`best.pt`，没有 `save_period` 和业务侧 checkpoint 记录。
- 失败自动重试从 epoch 0 重新开始。
- 平台重启能重接 Ray actor，但 worker 崩溃则整次作废。
- 任务没有 `resume_from` 关系，UI 无法显示“第几次尝试从哪个 checkpoint 恢复”。

**实现**

- worker 新增参数：
  - `--save-period N`（默认 5，快速实验 1）。
  - `--patience N`（P1 早停；P0 先透传）。
  - `--resume path`。
  - `--resume-mode full|weights`。
  - `--seed`、`--deterministic`。
- `model.train` 显式传：
  - `save=True`
  - `save_period=args.save_period`
  - `patience=args.patience`
  - `resume=bool(args.resume and args.resume_mode == "full")`
  - `seed=args.seed`
  - `deterministic=True`
  - `amp=False`
- 每轮 `on_fit_epoch_end` 检查 `trainer.save_dir/weights/last.pt`，写入 `checkpoint.json` 和 `task_checkpoints`。
- 保留策略：最近 2 个 epoch checkpoint + `best.pt` + `last.pt`；删除更早 epoch 文件，避免磁盘膨胀（当前 runs 已 807 MB）。
- 任务失败后 UI 出现“从最近 checkpoint 恢复”；点击创建新 Task：
  - 继承原配置快照；
  - `resume_from_task_id` 指向原任务；
  - `resume_mode=full` 默认；也允许 `weights`（从已有权重开始新实验，不恢复优化器）。
- 新任务权限、CPU 配额、日志、指标、产物按普通任务走；不改调度器主流程。
- 配置不一致时（模型/数据/imgsz/batch/epochs 与原任务不同）禁止 `full`，只允许 `weights`，并在 UI 明示。

**数据模型**

- `task_checkpoints`：`task_id`、`epoch`、`path`、`metrics_json`、`is_best`、`created_at`。
- `tasks` 增加：`resume_from_task_id`、`resume_mode`、`checkpoint_epoch`、`stop_reason`、`config_json`（完整配置快照）。
- `task result.json` 增加：`checkpoint_path`、`last_epoch`、`best_epoch`、`stop_reason`（早停/完成/失败）。

**API**

- `GET /api/v1/tasks/{id}/checkpoints`
- `POST /api/v1/tasks/{id}/resume`（body：`mode=full|weights`）
- 恢复任务与原任务互相跳转。

**UI**

- 详情页增加“Checkpoint”区：epoch、文件大小、存在时间、是否 best、从该点恢复。
- 失败状态的操作区增加“从最近 checkpoint 恢复”。
- 列表/详情显示“第 2 次尝试，恢复自 txxx · full”。

**验收**

- 在 epoch 3/10 左右强制杀 worker，API 将任务标记失败并保留 checkpoint。
- 恢复后从接近中断点继续，重复 epoch ≤ 1；新任务成功，CPU 释放正确。
- 同时只能有一个 resume 任务引用同一 checkpoint；重复点击返回 409。
- 删除旧 checkpoint 后不会破坏 `best.pt`/`last.pt` 下载。

**工作量**：8–10 人日（含故障注入测试）。

---

### P0-4 实验 Manifest 与对比

**目标**：任意一次训练都能回答“用了哪个数据版本、哪个权重、什么配置、什么环境、seed 是多少”，并能与另一次实验并排比较。

**Manifest 必含字段**

```json
{
  "task_id": "t...",
  "dataset": {
    "id": "ds-...",
    "name": "coco128-mini",
    "version": "v1",
    "content_sha256": "...",
    "quality_status": "passed"
  },
  "model": {
    "name": "yolov8n.pt",
    "sha256": "..."
  },
  "config": {
    "epochs": 50,
    "imgsz": 160,
    "batch": 4,
    "workers": 0,
    "cpu_cores": 3,
    "compute_threads": 6,
    "seed": 0,
    "cache": "none"
  },
  "runtime": {
    "python": "3.13.5",
    "torch": "2.14.0+cpu",
    "ultralytics": "8.4.216",
    "ray": "2.60.0",
    "cpu": "AMD Ryzen 7 PRO 5875U",
    "logical_cores": 16,
    "memory_gb": 30.8,
    "git_commit": "...",
    "git_dirty": false
  },
  "created_at": "..."
}
```

- 数据集内容哈希在导入/审计时生成；模型文件复用当前 artifact SHA-256 逻辑。
- Git commit 通过 `git rev-parse HEAD` 获取；无 Git 时写 `unknown` 并标 `git_dirty=true`。
- 配置快照必须存 Task；不能只依赖前端表单或当前默认值。
- 记录 `data.yaml` 快照和 `args.yaml`（Ultralytics 自动生成）的 artifacts。

**API/UI**

- `GET /api/v1/tasks/{id}/manifest`
- `GET /api/v1/tasks/compare?ids=id1,id2`（注意路由顺序要放在 `/{task_id}` 之前，或使用 `/compare` 独立路由）
- 任务列表多选两个任务 → “对比”抽屉：
  - 配置 diff；
  - 数据集名称/哈希/质量；
  - final metrics 差值（mAP50、mAP50-95、P、R、训练时长、峰值 RSS）；
  - 产物哈希/大小。
- 对比只允许相同评估协议（同数据集哈希）；不同数据集时提示“不可直接比较”。

**验收**

- 新任务 100% 生成 manifest。
- 任意两次成功任务可对比；配置 diff 正确；数据集不同时阻止误导性排名。
- Manifest 可用于复现：按其中的数据集/模型/配置重建任务，至少 seed 和版本一致。

**工作量**：5–7 人日（含对比 UI）。

## 5. P1 与 P2

### P1（P0 稳定后）

| 项 | 做法 | 验收 |
|---|---|---|
| Early Stopping / 分阶段训练 | worker 透传 `patience`；表单三档预设：快速验证 3–5 epoch / 标准 / 高精度 | 验证集指标触发早停；停止原因写入 manifest |
| 数据切分与版本不可变 | 分层/按采集批次分组切分；dataset version 记录 content hash 和变更说明 | 同一版本重复使用得到同一哈希；切分报告可下载 |
| 自动异常诊断 | 规则引擎：NaN、验证指标下降、CPU 低、数据等待高、RSS 接近上限 | 每条诊断有证据字段，不自动改参 |
| 错误分析 | `val(plots=True)` 生成混淆矩阵/PR；按类 AP/Recall；FP/FN 样本图 | 每个模型有按类报告和最多 N 张失败样本 |
| ONNX Runtime Benchmark | `export` worker + `onnxruntime` CPU EP；P50/P95、吞吐、内存、精度一致性 | 有 ONNX 产物和对照报告；精度下降可见 |
| 统一 Executor | `TrainingExecutor` 协议，`CpuExecutor` 实现 `validate/prepare/train/resume/evaluate/export` | 不改调度器即可替换 backend |

### P2（暂不做）

- CPU INT8 量化：只针对推理，必须在目标设备重测精度和延迟。
- 交付包：weights/configs/reports/examples/model_card/manifest 打 zip。
- 困难样本回流：低置信度、模型分歧、Embedding 多样性 + 人工审核。
- GPU Executor、LLM 诊断、自动超参搜索。

### 不建议现在做

- 不引入 PostgreSQL/MinIO：单机 SQLite + 本地文件仍满足当前规模；触发条件见第 10 节。
- 不接入 MLflow/FiftyOne/ClearML/Optuna 的完整 UI：先把 manifest、报告、对比做进现有页面；未来需要再按 API 适配。
- 不把完整数据集分析放进同步上传请求：容易超时，必须异步。
- 不把近重复检测当作自动删除/自动阻断：pHash 有误报，应先 warning + 人工确认。
- 不把量化当训练加速：量化主要影响推理。
- 不在 P0 自动重训/自动改参：失败后先保留证据，人工 resume 或调整。

## 6. 数据模型与 API 汇总

### 6.1 新表

| 表 | 关键字段 | 用途 |
|---|---|---|
| `dataset_quality_reports` | `dataset_id`、`status`、`score`、`summary_json`、`report_path`、`issue_path`、`engine_version`、`created_at` | 质量报告索引 |
| `benchmark_runs` | `id`、`status`、`host_json`、`matrix_json`、`selected_json`、`log_path` | Benchmark 结果 |
| `task_checkpoints` | `task_id`、`epoch`、`path`、`metrics_json`、`is_best` | checkpoint 索引 |
| `run_manifests` | `task_id`、`manifest_json`、`dataset_hash`、`model_hash`、`git_commit` | 可复现快照 |

### 6.2 现有表加列

| 表 | 列 |
|---|---|
| `datasets` | `content_sha256`、`quality_status`、`quality_score`、`quality_report_path`、`quality_issue_path`、`quality_engine_version`、`quality_checked_at` |
| `tasks` | `dataset_id`、`dataset_sha256`、`config_json`、`resume_from_task_id`、`resume_mode`、`checkpoint_epoch`、`stop_reason` |
| `artifacts` | 可选：`kind` 增加 `analysis`、`report`、`onnx`、`release` |

### 6.3 API 增量

```text
# 数据集质量
POST /api/v1/datasets/{id}/audit
GET  /api/v1/datasets/{id}/quality
GET  /api/v1/datasets/{id}/quality/report
GET  /api/v1/datasets/{id}/quality/issues
POST /api/v1/datasets/{id}/quality/ack

# CPU profile
POST /api/v1/cluster/benchmarks
GET  /api/v1/cluster/benchmarks/{id}
GET  /api/v1/cluster/benchmarks/latest

# 运行可复现/恢复
GET  /api/v1/tasks/{id}/manifest
GET  /api/v1/tasks/{id}/checkpoints
POST /api/v1/tasks/{id}/resume
GET  /api/v1/tasks/compare?ids=...

# P1
POST /api/v1/tasks/{id}/analyze
POST /api/v1/tasks/{id}/export?format=onnx
```

### 6.4 配置增量

```yaml
training:
  model: yolov8n.pt
  dataset_id: ds-...
  epochs: 50
  imgsz: 160
  batch: 4
  workers: 0
  compute_threads: 6
  cache: none
  seed: 0
  patience: 15
  save_period: 5
  resume:
    from_task_id: null
    mode: full
resources:
  accelerator: cpu
  cpu_cores: 8
  memory_limit_mb: 12288
```

## 7. 里程碑与排期

### 7.1 推荐顺序

```text
M0 迁移/埋点/夹具（3–4 天）
  ↓
M1 数据质量准入（P0-1）
  ↓
M2 CPU Benchmark（P0-2）
  ↓
M3 Checkpoint/Resume（P0-3）
  ↓
M4 Manifest/Compare（P0-4）
  ↓
M5 回归、文档、演示（2–3 天）
```

先做 M1 的原因：它直接阻止无效训练，风险最低，且为 manifest 提供数据集哈希。  
先不做 M3 的原因：checkpoint 需要稳定的配置快照和数据集版本；否则恢复出的实验不可复现。

### 7.2 估算

| 阶段 | 人日 |
|---|---:|
| M0 schema/migration/内容哈希/测试夹具/perf 埋点 | 3–4 |
| P0-1 数据质量 | 6–8 |
| P0-2 CPU Benchmark | 7–9 |
| P0-3 Checkpoint/Resume | 8–10 |
| P0-4 Manifest/Compare | 5–7 |
| 回归/文档/演示 | 3–4 |
| **P0 合计** | **32–42 人日** |

单人专注开发约 **6–8 周**；若前后端并行、使用 AI 辅助编码，可压缩到 **4–5 周**。不要把估算当成承诺，先以两周里程碑验证。

### 7.3 第一个两周 Sprint（可直接开工）

**Week 1**

- 加 Alembic baseline；备份现有 SQLite；新增表/列。
- `datasets.content_sha256` 计算；现有两个数据集回填。
- 质量审计服务骨架 + JSON 报告格式；测试夹具（坏图、越界框、泄漏、近重复）。
- worker 埋点：data wait / train time / images per second / RSS 峰值；manifest 字段采集。

**Week 2**

- 数据集审计后台任务 + 状态 API + 报告抽屉；提交训练接入 gate。
- Benchmark 服务 v1：3 个组合起步，结果表 + 人工应用。
- 回归现有 28 个测试；新增质量与 benchmark 测试。
- 演示：坏数据被阻断；COCO128 mini 审计报告；当前 CPU 基线表。

**Sprint 出口**

- 训练提交不会绕过坏数据；
- 有一个可复现的 CPU 基线报告；
- 所有新任务具备数据集哈希和基础 manifest。

## 8. 测试与验收

### 8.1 测试矩阵

| 类型 | 用例 |
|---|---|
| 数据质量 | 损坏图片、非 5 列标签、类别越界、坐标越界、宽高 0、图片/标签孤儿、完全重复、近重复、split 泄漏、空标签 |
| 质量状态 | pending→auditing→passed/warning/blocked；中断重启重跑；warning 需 ack；blocked 不可提交 |
| Benchmark | 矩阵执行、超时、内存保护、无训练并发、重复结果、profile 复用 |
| Checkpoint | 每轮保存、保留策略、杀 worker、resume full/weights、配置不匹配拒绝、重复 resume 409 |
| Manifest | 必填字段、git 缺失、数据集不同不可比较、配置 diff |
| 回归 | 现有 28 个测试全部通过；Ray/local 两种后端；取消/超时/心跳回收 |
| 性能 | 50ep 基线、benchmark 重复、长时间运行 RSS/磁盘增长 |
| 安全 | ZIP 路径、报告 HTML 转义、模型路径、日志下载路径 |

### 8.2 验收指标

- 质量：坏数据阻断率 100%；完全重复/泄漏检出 100%；1,000 张内报告 <60s。
- CPU：同一配置 3 次中位数波动 ≤10%；有收益或无回归的结论必须附数据；不出现 OOM/swap。
- 恢复：强制中断后恢复成功 ≥95%；重复 epoch ≤1；CPU 分配释放正确。
- 可复现：新任务 manifest 覆盖率 100%；任意两次同数据集任务可对比。
- 回归：后端测试全部通过；前端 typecheck/build 通过；现有任务/流水线/Webhook 不回归。

## 9. 风险与应对

| 风险 | 应对 |
|---|---|
| Benchmark 噪声大 | warmup、固定子集、重复取中位数、记录主机负载、benchmark 锁 |
| 线程数与调度 CPU 不一致 | 引入 `compute_threads`，与 `cpu_cores` 分离；文档明确二者含义 |
| Windows DataLoader 反而更慢 | 默认 workers=0；benchmark 再决定；不预设收益 |
| checkpoint 占满磁盘 | 保留最近 2 个 epoch + best/last；磁盘水位检查；清理策略 |
| SQLite 迁移失败 | Alembic 版本化；升级前备份；测试库先跑 |
| 审计阻塞上传 | 异步状态；大文件限制；报告生成与训练 gate 解耦 |
| pHash 误报 | 近重复只 warning；完全重复才阻断；人工确认入口 |
| resume 语义混乱 | full/weights 两档；配置哈希匹配；新 Task 记录 lineage |
| 数据集/模型切换导致对比失真 | 对比前校验 dataset hash；不同数据集只展示不排名 |
| 依赖增加 | Pillow 已随 ultralytics；imagehash/onnxruntime 作为可选 extra，先审批 |

## 10. 待确认事项

1. 平台 CPU 预算是 8 个逻辑核，还是应使用宿主 16 个逻辑线程？当前配置为 8，建议暂不修改。
2. 数据集上限是否保持 512 MB？未来是否会超过 10 GB / 多用户并发？
3. 未来 6 个月是否有 GPU？若有，GPU Executor 的优先级是否仍低于 P0 数据闭环？
4. 是否允许新增依赖：`alembic`、`imagehash`（或自实现 dHash）、`onnx`、`onnxruntime`、`Pillow`（已间接存在）？
5. 近重复 train/val 是“阻断”还是“warning + 人工确认”？建议完全重复阻断，近重复 warning。
6. 是否保留当前固定 owner=demo 的单用户模式？多用户会影响审计队列、配额和报告权限。
7. 模型是否从 YOLOv8n 切到 YOLO11n？当前资产是 YOLOv8n；切换前必须重新 benchmark，不能只改默认值。
8. 任务最长运行时长/epoch 上限是否保持 50？如果有长训练，checkpoint 保留策略需要单独配置。

## 11. 存储与架构升级触发条件

| 触发条件 | 建议 |
|---|---|
| 单 API 进程 + <10 并发用户 + 数据集 <10 GB | 继续 SQLite + 本地文件 |
| 多 API 实例 / 多用户并发写 / 任务量 > 10 万 | PostgreSQL；Redis 保留 |
| 数据集 > 10 GB / 多机共享 | MinIO/S3；否则本地磁盘 |
| 实验对比/报告需求超出内置能力 | 再考虑 MLflow/FiftyOne 适配，不替换现有 UI |
| 需要多机 GPU 调度 | 独立 GPU Executor + 资源队列；不要直接在 Ray actor 里硬编码 |
| 需要自动超参搜索 | Optuna 作为独立 runner，先有 benchmark 和 manifest |

## 12. 总体验收结论

这套方案把源文档的“大而全清单”收敛为当前平台真正缺的闭环：

```text
数据质量准入
   → 可复现的训练配置
   → CPU 实测 profile
   → checkpoint 恢复
   → 实验对比
   → 错误分析/ONNX（P1）
```

先完成 P0，平台就能从“能跑训练”升级为“不会浪费算力、失败可恢复、结果可解释、优化有对照”的 CPU 训练控制台。GPU 接入留到执行器接口稳定之后，不在当前阶段重写架构。
