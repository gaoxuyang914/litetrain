# P2 高阶方案：多节点、流水线、产物与 Webhook

> 按题目要求，P2 只做设计说明，不要求编码。以下方案保持与当前实现的兼容边界：当前数据模型中的 `Node`、`ResourceAllocation`、`TaskEvent`、`result_path` 都已为扩展留出位置。

## 1. 多节点集群模拟

### 目标

让控制面管理多台 CPU 机器，资源仍以“逻辑 CPU 核”为单位分配；任务可以在指定节点上执行，节点离线时自动失败/重排。

### 设计

```text
              ┌──────────────┐
   Web/API ──▶│  Postgres    │◀── Scheduler (1 leader)
              │  nodes       │
              │  allocations │
              └──────┬───────┘
                     │
              ┌──────▼───────┐
              │    Redis     │  ◀── node heartbeat lease
              │  leader lock │      task event pub/sub
              └──────┬───────┘
        ┌────────────┼─────────────┐
        │            │             │
   ┌────▼────┐  ┌────▼────┐   ┌────▼────┐
   │ Ray     │  │ Ray     │   │ Ray     │
   │ node A  │  │ node B  │   │ node C  │
   └─────────┘  └─────────┘   └─────────┘
```

1. **节点注册**：每台机器运行 `litetrain-agent`，启动 Ray worker 并注册 `hostname / physical_cores / memory / labels / ray_node_id`。Redis lease（30s）+ DB `nodes.last_seen` 双重心跳；失去 lease 的节点标记 offline。
2. **节点调度**：
   - 调度器 SELECT pending 并按优先级取任务；遍历候选节点，检查节点 active allocation 与用户配额。
   - 选定节点后，Ray actor 的 `scheduling_strategy=NodeAffinitySchedulingStrategy(node_id=..., soft=False)`，保证 actor 落入对应节点；同时保留 `num_cpus=cpu_cores` 让 Ray 做二次资源保护。
   - 每个节点启动 Ray 时带 custom resource `node:<uuid>: 1`，用于无 node_id 时的兼容亲和。
3. **离线处理**：
   - 节点失联后，其 active allocations 进入 `orphaned`；运行任务标记失败并进入重试策略（若允许）。
   - 运维可手动 drain 节点：停止接收新任务，等待当前任务结束或强制迁移。
4. **状态与一致性**：
   - 单 leader：Redis `SET NX PX` 短锁 + fencing token 写入 DB，避免旧 leader 恢复后重复派发。
   - 元数据迁移到 Postgres，使用 `SELECT ... FOR UPDATE SKIP LOCKED` 领取任务。
   - API 无状态，可水平扩展；SQLite 退化为本地开发存储。
5. **UI**：节点列表展示每节点 CPU 分配、主机负载、最近心跳、角色（head/worker）、drain 状态；任务详情显示 node_id。

### 验收模拟

Docker Compose 启动 Redis、Postgres、2 个 Ray worker 容器 + 1 个 API；提交 3 个 4 CPU 任务，观察分配到两个节点并在节点停机时失败/重排。

---

## 2. 训练流水线编排（预处理 → 训练 → 评估）

### 数据模型

```text
pipelines(id, name, status, graph_json, created_at)
pipeline_stages(id, pipeline_id, stage_key, task_id, depends_on_json, status)
tasks(id, pipeline_id, stage_key, ...)
```

### 调度

- DAG 存为 stage 依赖；每个 stage 复用现有 Task 生命周期。
- 新增 `PipelineCoordinator`：当依赖满足时创建下一 stage 的 Task（状态 pending）；任何 stage 失败时按策略 `fail_fast` / `continue`。
- 预处理 stage 产出标准 YOLO 数据集目录，产物 URI 写入 DB；训练 stage 读取该 URI；评估 stage 读取训练 weights。
- 每个 stage 拥有独立的 CPU、超时、重试和心跳；资源不足时 stage 排队。
- 断点恢复：协调器状态持久化，API 重启不会重复创建 stage；Task 的幂等键为 `(pipeline_id, stage_key, attempt)`。

### 命令约束

每个 stage 的命令由服务端模板生成，只允许：

- `preprocess`：内置数据集校验/裁剪脚本；
- `train`：worker + YOLO 参数；
- `eval`：worker 的评估模式。

不允许任意 shell 命令，降低失控风险。

---

## 3. 数据集与模型权重管理

### 数据集

- 上传 zip/tar 或登记外部 URI，后台 job 校验 YOLO YAML、图像/标签配对、类别数与图片可读性。
- 记录 `sha256`、大小、版本、创建人；多个训练任务可引用同一不可变 dataset version。
- 对象存储使用 MinIO/S3；任务启动前通过预签名 URL 拉取至节点本地缓存。
- 删除策略：软删除 + 引用计数；训练任务仍引用时禁止物理删除。

### 模型与产物

- 训练 worker 的 `project/name` 指向 run 目录；完成后扫描 `weights/best.pt`、`last.pt`、`results.csv` 等。
- 产物 manifest 写入 DB：`run_id / task_id / kind / sha256 / size / storage_uri`。
- Web 端提供预签名下载与模型版本列表；可加模型卡（mAP、数据版本、命令、commit）。
- 保留策略：按数量/天数/指标阈值清理；best.pt 可标记 immutable。

---

## 4. 任务状态变更 Webhook

### 投递模型

```text
task_events / event_outbox(id, event_type, payload_json, status, attempts, next_retry_at)
webhook_endpoints(id, url, secret, event_types, active, created_at)
webhook_deliveries(id, endpoint_id, outbox_id, status, response_code, attempts)
```

1. 状态事务和 outbox 写入同一 DB 事务（transactional outbox）。
2. 后台 dispatcher 按 `FOR UPDATE SKIP LOCKED` 拉取可投递事件。
3. HTTP POST JSON，携带：
   - `X-LiteTrain-Event`；
   - `X-LiteTrain-Delivery`（幂等键）；
   - `X-LiteTrain-Signature: sha256=HMAC(secret, timestamp + "." + body)`；
   - `X-LiteTrain-Timestamp`。
4. 2xx 成功；429/5xx/超时按指数退避（1s, 5s, 30s, 2m, 10m...），超过上限进入 dead letter。
5. 接收端按 `delivery_id` 做幂等；支持手动重置 dead letter。
6. 安全：只允许 http(s)，阻止内网/回环地址（SSRF），secret 加密存储，可选 mTLS。

当前实现已有 `TaskEvent` 和 Redis `task.*` 事件发布，可直接扩展为 outbox。

---

## 5. 上线前还需要补齐

- OIDC/JWT 鉴权、RBAC、审计日志；
- Postgres 迁移（Alembic）+ 备份；
- 指标监控（Prometheus/OpenTelemetry）、结构化日志、trace；
- 资源超卖/节点隔离策略；
- 对象存储生命周期与配额；
- 端到端测试矩阵（多节点、断网、磁盘满、Redis 抖动）。
