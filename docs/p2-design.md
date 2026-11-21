# P2 高阶能力：设计与实现

> 本项目的 P2 不是只写方案：多节点模拟、三阶段训练流水线、数据集/模型产物、状态 Webhook 都已编码实现，并在本机用真实 Ray 训练和真实 HTTP 回调跑通。本文先记录实现，再讨论生产化演进。

## 0. 总体边界

- 不引入 Kubeflow、Cube Studio、Airflow 等外部平台，也没有 iframe 嵌入。
- 仍然只使用 CPU，不使用 GPU / NCCL / DDP。
- 元数据仍使用 SQLite（WAL），API 仍为单进程演示；生产化演进在最后单独说明。
- P2 复用 P0/P1 的 Task 生命周期、CPU 配额、心跳、日志、指标、重试和自动回收。

```text
Vue 3
  ├─ /pipelines      流水线列表 / 创建 / 阶段进度 / 取消
  └─ /assets         数据集 / 产物 / Webhooks

FastAPI
  ├─ /api/v1/pipelines
  ├─ /api/v1/datasets
  ├─ /api/v1/artifacts
  ├─ /api/v1/webhooks
  └─ /api/v1/cluster/nodes/*

Scheduler tick
  ├─ watch running tasks
  ├─ PipelineService.advance()
  └─ schedule pending tasks

Webhook dispatcher (daemon thread)
  └─ outbox → signed HTTP POST → retry/backoff
```

---

## 1. 多节点集群模拟

### 1.1 已实现行为

- `Node` 表保存逻辑节点：`id/name/hostname/total_cpus/status/last_seen`。
- 默认启动一个节点；总览页可以把 `LITETRAIN_TOTAL_CPUS` 拆成 1–8 个逻辑节点。
- 拓扑约束：
  - 所有节点 CPU 之和不超过 `LITETRAIN_TOTAL_CPUS`；
  - 单节点 CPU 至少为 1；
  - 节点名不可重复；
  - 存在 active allocation 时禁止替换拓扑。
- 调度：
  - 只在 `online` 节点中寻找容量足够的节点；
  - 同时检查用户配额；
  - 在候选节点中选择当前利用率最低的节点，避免全部压到第一个节点；
  - `ResourceAllocation` 显式记录 `node_id`，释放时按任务回收。
- 任务必须能落在单个节点上；超过最大节点容量时提交阶段返回 422。
- 节点离线/上线：
  - 拥有 active allocation 的节点不能直接下线；
  - 重新上线会校验全局 CPU 预算，防止超过 Ray 实际 capacity。
- 拓扑变更会保留历史 allocation：有历史记录的旧节点标记 offline，无历史记录的旧节点删除。

### 1.2 为什么是逻辑节点，而不是直接起多个 Ray 节点

在 Windows 单机演示环境里，直接启动多个 Ray worker 会引入端口、对象存储、进程隔离和资源竞争等与题目无关的复杂度。本实现把“节点”放在控制面资源账本中：

- 调度决策、节点容量、负载均衡、排队、节点上下线都是真实的；
- Ray 仍是本机 8 CPU head，actor 的 `num_cpus` 由任务 CPU 申请量决定；
- UI 明确说明这是多节点**模拟**，不声称是跨物理机集群。

生产化替换点：让每台机器运行 `litetrain-agent`，注册节点和 Ray node id；scheduler 使用 `NodeAffinitySchedulingStrategy` 把 actor 固定到对应节点。

### 1.3 关键代码

- `backend/app/services/resources.py`：拓扑、节点选择、分配/释放、预算检查。
- `backend/app/services/scheduler.py`：调度时选择节点并创建 allocation。
- `backend/app/api/cluster.py`：`GET /cluster/nodes`、`POST /cluster/nodes/topology`、`PATCH /cluster/nodes/{id}`。
- `frontend/src/views/OverviewView.vue`：节点表、拓扑编辑器、上下线操作。

---

## 2. 预处理 → 训练 → 评估流水线

### 2.1 数据模型

```text
pipelines
  id, name, owner, status, config_json, failure_reason,
  submitted_at, started_at, finished_at

pipeline_stages
  id, pipeline_id, stage_key, sequence, status,
  task_id -> tasks.id, config_json, message,
  started_at, finished_at
```

- 固定 `stage_key`：`preprocess`、`train`、`evaluate`。
- 每个阶段关联一个普通 Task，因此直接继承调度、排队、心跳、日志、指标、重试和取消逻辑。
- 没有修改 `Task` 表结构；阶段信息通过 `pipeline_stages.task_id` 反查。

### 2.2 创建与推进

1. `POST /pipelines`：校验数据源、模型、CPU、用户配额和单节点容量。
2. 写入 pipeline 和三个 stage；只创建 preprocess Task。
3. Scheduler 的每个 tick 在 `_watch_running()` 和 `_schedule_pending()` 之间调用 `PipelineService.advance()`。
4. preprocess Task 成功后：
   - 确认 `datasets/pipelines/{pipeline_id}/dataset.yaml` 存在；
   - 创建 train Task，dataset 指向该 YAML；
   - train stage 从 `waiting` 变为 `pending`，继续走正常资源队列。
5. train Task 成功后：
   - 确认 `runs/{train_task_id}/weights/best.pt` 存在；
   - 创建 evaluate Task，权重绝对路径写入 stage config；
   - evaluate 使用同一个生成数据集和 `YOLO.val()`。
6. evaluate 成功后 pipeline `succeeded`；所有阶段 `finished_at` 写入。
7. 阶段失败/取消：pipeline 终止，后续 stage 标记 `cancelled`；用户取消 pipeline 时先取消当前 Task，再标记 pipeline。
8. API 重启：coordinator 从 DB 继续推进，不需要额外队列。

### 2.3 预处理 worker

`python -m app.training.worker --kind preprocess`：

- 读取数据源 YAML；
- 解析 `path/train/val/names`，同时支持相对路径和绝对路径；
- 默认复制最多 `max_images` 张训练图和验证图；
- 将 `images/...` 映射到 `labels/...`，复制标签；
- 生成新的绝对路径 `dataset.yaml` 和 `preprocess.json` manifest；
- 通过现有 metrics 接口记录图片、标签、类别数；
- 输出完整日志，仍由普通 Task 详情页展示。

### 2.4 评估 worker

`python -m app.training.worker --kind evaluate`：

- 加载 train stage 产出的 `best.pt`；
- 对验证集执行 `model.val(...)`；
- 将 `results_dict` 中的 mAP / precision / recall / loss 写入现有 metrics 表；
- 结果可在任务详情和 pipeline 产物中看到。

### 2.5 产物

train Task 的 run 目录：

```text
backend/data/runs/{task_id}/
  weights/best.pt
  weights/last.pt
  results.csv
  args.yaml
```

`ArtifactService` 扫描这些文件，写入 `artifacts` 表并计算 SHA-256。pipeline 详情的 `artifacts` 字段按 `pipeline_id` 聚合阶段 Task 的产物。

### 2.6 关键代码

- `backend/app/services/pipelines.py`：创建、coordinator、取消、stage schema。
- `backend/app/services/executors.py`：按 stage 生成 preprocess/evaluate/train 命令。
- `backend/app/training/worker.py`：三种 `--kind` 分支。
- `backend/app/api/pipelines.py`：流水线 API。
- `frontend/src/views/PipelinesView.vue`：列表、创建、阶段进度、详情抽屉。

---

## 3. 数据集与模型产物管理

### 3.1 数据集注册表

`datasets` 表记录：

```text
id, name, version, description, yaml_path, archive_path,
source_filename, image_count, label_count, class_count,
size_bytes, status, is_builtin, created_at, deleted_at
```

实现：

- 首次启动自动登记仓库内置 COCO128（不复制文件，直接引用 `coco128/coco128.yaml`）。
- `POST /datasets/upload`：
  - 仅接受 zip，最大 512 MB；
  - 流式写入临时文件，再安全解压；
  - 查找包含 `train` 和 `val/validation` 的 YAML；
  - 统计 images/labels/classes；
  - 将 YAML 的 `path` 规范化为解压目录绝对路径，并重命名为 `{slug}-{version}-{id}.yaml`，避免同名冲突；
  - 原始 zip 复制到 `datasets/_archives/`，记录归档路径；
  - 同 name+version 返回 409。
- `DELETE /datasets/{id}` 只做软删除，保留归档和历史任务引用；原始文件不物理删除。
- 内置数据集没有上传归档，下载接口返回 404；UI 对内置项隐藏下载按钮。

### 3.2 产物

`artifacts` 表记录：

```text
id, task_id, pipeline_id, kind, name, relative_path,
size_bytes, sha256, created_at
```

- 扫描 `runs/{task_id}` 下的 `weights/*.pt`、`results.csv`、`args.yaml`；
- `kind`：`model / metrics / config`；
- 按 `(task_id, relative_path)` 去重；
- 下载前校验真实路径位于 `runs_dir`，防止 `..` 路径穿越；
- `GET /artifacts` 支持 task/pipeline/kind 过滤，前端分页展示。

### 3.3 生产化替换

- 数据集归档和模型文件迁移到 S3/MinIO；
- 用内容哈希做不可变版本；预签名下载；
- 增加引用计数和保留策略；
- 模型卡（mAP、数据版本、命令、git commit）；
- 数据库迁移到 Postgres，上传任务异步化（病毒扫描、图片可读性校验）。

---

## 4. 任务状态 Webhook

### 4.1 数据模型

```text
webhook_endpoints
  id, name, url, secret, events_json, active, created_at

webhook_deliveries
  id, endpoint_id, event_type, task_id, pipeline_id,
  payload_json, status, attempts, max_attempts,
  next_attempt_at, response_code, response_excerpt,
  created_at, updated_at
```

### 4.2 Transactional outbox

- TaskEvent 写入和 WebhookDelivery 入队在**同一个 SQLite 事务**中完成，避免“任务状态已提交但事件丢失”。
- 任务事件映射：

```text
submitted       -> task.submitted
scheduled       -> task.running
recovered       -> task.running
succeeded       -> task.succeeded
failed          -> task.failed
cancelled       -> task.cancelled
retry_scheduled -> task.retry_scheduled
```

- 流水线事件：`pipeline.created / running / succeeded / failed / cancelled`。
- 按 endpoint 的 events 列表匹配，支持 `*`。
- 只有 active endpoint 会生成 delivery。

### 4.3 Dispatcher

独立 daemon thread（后端启动时开启，测试中可手动 `process_once()`）：

1. 从 outbox 取下一条 `pending` 且 `next_attempt_at <= now` 的投递；
2. 生成 `timestamp` 和签名：

```text
signature = hex(HMAC-SHA256(secret, timestamp + "." + raw_body))
```

3. POST JSON，headers：

```text
X-LiteTrain-Event
X-LiteTrain-Delivery
X-LiteTrain-Timestamp
X-LiteTrain-Signature
```

4. 2xx → `success`；否则 attempts+1，按 `base * 2^(attempts-1)` 退避（上限 300s），超过 `max_attempts` → `failed`。
5. 每次投递的结果写回 outbox，前端显示成功/失败/待投递数量和最近记录。

### 4.4 测试与演示

- `POST /webhooks/{id}/test` 入队 `webhook.test`。
- `scripts/webhook_receiver.py` 是零依赖本地接收器，打印签名头和 JSON body；返回 204。
- 单元测试覆盖：事务入队、HMAC 签名、成功、500 失败重试、成功转终态。
- 本机实测：pipeline 完成时收到 4 条成功投递，`pipeline.succeeded` 和三条 `task.succeeded` 均为 204。

### 4.5 生产化

- S3/Postgres outbox + `FOR UPDATE SKIP LOCKED`；
- dead letter 管理 UI、手动重放；
- SSRF 防护（禁止内网/回环/云元数据地址）；
- secret 加密存储、密钥轮换、mTLS；
- 接收端按 delivery id 做幂等；
- 更细的过滤（按 owner、任务名、pipeline）；
- Prometheus 指标（outbox lag、投递成功率、重试次数）。

---

## 5. 生产化演进清单

- OIDC/JWT 鉴权、RBAC、审计；
- Postgres + Alembic，多 API 副本；
- 真正的多机 Ray 节点注册和 `NodeAffinitySchedulingStrategy`；
- 对象存储与异步资产流水线；
- Webhook dead letter、签名轮换、SSRF allowlist；
- 资源隔离（cgroup）和真实内存配额；
- Prometheus / OpenTelemetry / 结构化日志；
- E2E 矩阵：API 重启、Redis 抖动、节点失联、磁盘满、Webhook 接收端宕机。
