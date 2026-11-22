# LiteTrain 需求规格说明书（SDD）

| 项 | 内容 |
| --- | --- |
| 文档版本 | v1.0 |
| 日期 | 2026-11-22 |
| 对应实现 | LiteTrain v2.5.0 |
| 方法论 | SDD — Spec-Driven Development（规格驱动开发） |
| 文档定位 | 唯一需求事实源；设计、测试、实现、验收均从本文派生 |
| 状态 | 已实现基线回填（现有代码已交付本文全部 P0/P1 与 P2 需求） |

---

## 0. 文档约定与方法论

### 0.1 为什么用 SDD

本文采用 **SDD（规格驱动开发）** 而非「边写边补文档」，原因有三：

1. **规格先于实现**：任何功能先有唯一 ID、输入/输出、状态转换和验收准则，再进入设计与编码。本文是对已交付实现的规格回填，因此每条需求都能指到真实代码与测试。
2. **规格必须可验证**：P0 需求一律用 `Given / When / Then` 描述，禁止「体验良好」「响应快速」这类不可判定表述。性能指标给出具体阈值和测量口径。
3. **追溯链闭合**：`需求 FR → 验收 AC → 测试 TC → 实现文件` 四段可追踪。任何变更先评估受影响的下游节点，避免改了代码却漏改测试或验收脚本。

### 0.2 需求 ID 规则

| 前缀 | 含义 |
| --- | --- |
| `FR-TASK-xx` | 任务生命周期 |
| `FR-SCHED-xx` | 资源调度与配额 |
| `FR-RESI-xx` | 容错与恢复 |
| `FR-LOG-xx` | 日志 |
| `FR-METRIC-xx` | 指标与资源采样 |
| `FR-UI-xx` | 前端页面 |
| `FR-PIPE-xx` | 流水线 |
| `FR-ASSET-xx` | 数据集与产物 |
| `FR-HOOK-xx` | Webhook |
| `FR-AUTH-xx` | 认证与权限 |
| `FR-CFG-xx` | 运行配置 |
| `NFR-xx` | 非功能需求 |

优先级：`P0` 必做（不满足不合格）、`P1` 加分（答辩重点）、`P2` 高阶（口述设计，本项目已编码实现）。

### 0.3 术语表

| 术语 | 定义 |
| --- | --- |
| Task | 一次可独立调度、有唯一 ID 和日志文件的训练/预处理/评估执行单元 |
| Attempt | 一次 Task 的执行尝试，`attempt = retry_count + 1` |
| Worker | 实际执行 YOLO/预处理/评估的 Python 进程，由 Ray actor 或本地子进程拉起 |
| Allocation | 一条 active 状态的 CPU 租用记录；释放后置为 released，不物理删除 |
| Node | 控制面逻辑节点，承载 CPU 容量账本；默认 1 个，可模拟 1–8 个 |
| Heartbeat | Worker 每 4 秒向控制面上报存活与资源占用的回调 |
| Watchdog | Scheduler 内检查运行中任务的超时/僵死/进程消失的子流程 |
| Checkpoint | 每 epoch 保存的权重文件，用于重试恢复与高优先级抢占 |
| Artifact | `best.pt / last.pt / results.csv / args.yaml` 等训练产物登记记录 |
| Outbox | 与业务写操作同事务落库的待投递 Webhook 事件 |

---

## 1. 项目背景与目标

### 1.1 背景

面试命题要求独立开发一套前后端分离的训练平台，用于在普通笔记本上提交、排队、运行和观察 CPU 模式的 YOLO 训练任务。命题明确允许直接封装 `ray job submit`，但禁止嵌入 Kubeflow、Cube Studio、Airflow 等成品平台页面，也不允许使用 GPU / NCCL / DDP。

### 1.2 目标

| 编号 | 目标 | 衡量方式 |
| --- | --- | --- |
| G1 | 完整复现训练任务提交 → 排队 → 运行 → 终态闭环 | 黑盒脚本 `scripts/e2e_live.py` 通过 |
| G2 | CPU 资源账本准确，任何终态路径都不泄漏配额 | 自动测试断言 active allocation 归零 |
| G3 | 后端重启不丢状态、不中断已运行训练 | 重启实测：allocation 保持 4，任务继续到 succeeded |
| G4 | 日志、指标、资源采样可按任务检索并增量消费 | 字节 offset 语义 + ECharts 曲线 |
| G5 | 四个 P0 页面交互可用，状态与筛选正确 | Playwright 真实页面截图 24 张 |

### 1.3 非目标（明确不做）

- 不做 GPU / CUDA / NCCL / DDP 分布式训练；worker 强制 `device=cpu`。
- 不嵌入任何第三方训练平台的页面或 iframe。
- 不做通用 DAG 编排；流水线固定为 `preprocess → train → evaluate` 三阶段。
- 多节点是**控制面资源账本模拟**，不声称跨物理机执行；任务仍在本机 Ray head 上跑。
- 不引入 PostgreSQL / MinIO / MLflow 等外部存储（单机演示场景 SQLite 足够）。

---

## 2. 干系人与使用场景

| 角色 | 场景 | 关键诉求 |
| --- | --- | --- |
| 操作员（operator） | 提交训练、取消任务、管理数据集与产物 | 快速看到排队位置、日志和指标；能中止误提交任务 |
| 管理员（admin） | 管理账号、调整运行策略 | 超时上限、心跳阈值、CPU 配额可动态生效 |
| 只读成员（viewer） | 查看总览、任务、日志、产物 | 不允许任何写操作 |

主流程（操作员）：登录 → 集群总览确认空闲 CPU → 提交任务 → 任务列表观察状态 → 详情页看日志/指标 → 任务终态后查看产物。

---

## 3. 需求范围总览

| 范围 | 需求条目数 | 说明 |
| --- | --- | --- |
| P0 必做 | 22 | 任务 CRUD、调度、容错、日志、四个页面 |
| P1 加分 | 10 | 优先级/抢占、指标、重试、配额、资源采样、终端增强 |
| P2 高阶 | 4 | 多节点、流水线、数据集/产物、Webhook（已实现） |
| NFR | 7 | 性能、一致性、安全、兼容性、可观测性等 |

---

## 4. 功能需求

### 4.1 任务生命周期（FR-TASK）

#### FR-TASK-01 任务提交 — P0 — 已实现

系统必须提供 `POST /api/v1/tasks`，接受任务名、模型、数据集、epochs、imgsz、batch、workers、CPU 核数、优先级、最大运行时长、重试次数等参数；写入后状态为 `pending`，生成 24 位任务 ID、独立日志路径、结果文件路径和 worker token（不下发前端）。提交前必须校验：CPU 核数为正且不超过单节点容量、数据集已登记且通过质量准入、参数不超过后台运行设置上限。

**AC-TASK-01**
- **Given** 数据集 `coco128.yaml` 已登记且质量状态为 `passed`，请求 CPU 4 核、1 epoch
- **When** 操作员调用 `POST /api/v1/tasks`
- **Then** 返回 201 与 `status=pending` 的任务对象；SQLite 存在对应行；磁盘存在空日志文件；`GET /tasks/options` 返回的上限与表单默认值一致

#### FR-TASK-02 任务分页查询 — P0 — 已实现

`GET /api/v1/tasks` 支持 `page / page_size / status / q / sort_by / sort_order`；`q` 同时匹配任务 ID 和名称；返回总数、当前页数据和队列位置。状态筛选只允许五种合法枚举值。

**AC-TASK-02**
- **Given** 库中已有 12 个任务，其中 3 个 `failed`
- **When** 以 `status=failed&page=1&page_size=2` 查询
- **Then** 返回 `total=3`，当前页 2 条且均为 `failed`，分页元数据可驱动前端分页器

#### FR-TASK-03 任务详情 — P0 — 已实现

`GET /api/v1/tasks/{id}` 返回基础信息、资源分配、运行时长、当前进度、CPU/内存最新采样、队列位置和失败原因；不存在的 ID 返回 404。

#### FR-TASK-04 任务取消 — P0 — 已实现

`POST /api/v1/tasks/{id}/cancel` 对 `pending` 任务直接置 `cancelled`；对 `running` 任务先终止 worker 进程树，再释放 allocation 并置 `cancelled`。对终态任务重复取消返回 409。

**AC-TASK-04**
- **Given** 任务 A 处于 `pending`，任务 B 处于 `running` 且占用 4 CPU
- **When** 分别取消 A、B
- **Then** A 无 allocation 且状态 `cancelled`；B 的 worker 停止、allocation 释放、状态 `cancelled`；两者均不可再次取消（409）

#### FR-TASK-05 状态机与终态判定 — P0 — 已实现

任务状态限定为 `pending / running / succeeded / failed / cancelled`。Worker 退出码 0 标记 `succeeded`，非 0 标记 `failed`。所有状态转换由 Scheduler 在 SQLite 写事务中完成，禁止旁路修改。

### 4.2 资源调度（FR-SCHED）

#### FR-SCHED-01 CPU 资源账本 — P0 — 已实现

平台维护 `LITETRAIN_TOTAL_CPUS`（默认 8）逻辑 CPU 池。唯一资源事实来源是 `nodes.total_cpus` 与 `resource_allocations(state='active')` 的求和。每次调度在写事务内同时校验节点空闲容量与单用户配额，避免超卖。

#### FR-SCHED-02 排队与派发 — P0 — 已实现

Scheduler 每秒 tick 一次，持 Redis 短锁（Redis 不可用时退化为进程内锁），按 `priority DESC, submitted_at ASC` 扫描 `pending`。资源足够时原子占用 CPU 并拉起 worker；资源不足保持 `pending`，资源释放后下一 tick 自动派发。

**AC-SCHED-02**
- **Given** 平台 8 CPU，任务 A 占用 8 CPU 正在运行，任务 B 申请 4 CPU
- **When** 提交 B 并等待两个 tick
- **Then** B 仍为 `pending` 且 `queue_position=1`；取消 A 后一秒内 B 变为 `running` 且分配 4 CPU

#### FR-SCHED-03 资源自动回收 — P0 — 已实现

`succeeded / failed / cancelled`、超时、心跳超时、worker 异常退出、API 重启对账等所有路径必须调用 `release_allocation`。资源总览页的已分配 CPU 必须与 active allocation 之和一致。

#### FR-SCHED-04 优先级调度 — P1 — 已实现

`priority` 取值 0–100；所有 pending 任务按 `priority DESC, submitted_at ASC` 排序。高优先级任务可影响排队位置，前端列表展示队列序号。

#### FR-SCHED-05 低优先级抢占 — P1 — 已实现

当高优先级任务因资源不足无法启动，且优先级差 ≥ 20 时，允许抢占同节点上的低优先级 `running` 任务。被抢占任务必须同时满足：非流水线阶段、`preempt_count < 2`、当前 attempt 存在有效 checkpoint。抢占流程：终止旧 worker → 释放 allocation → 旧任务 `cancelled / failure_reason=preempted` → 用 `resume(mode=full)` 创建低优先级子任务 → 下一 tick 高优先级任务获得 CPU。

**AC-SCHED-05**
- **Given** 低优先级任务 A（priority 10，4 CPU，已有 epoch 1 checkpoint）运行中，高优先级任务 C（priority 90，4 CPU）pending
- **When** Scheduler 执行抢占检查
- **Then** A 变为 `cancelled/preempted`，生成 `resume_from_task_id=A` 的子任务且 `preempt_count=1`，C 在下一 tick 变为 `running`

#### FR-SCHED-06 单用户 CPU 配额 — P1 — 已实现

每个 owner 并发占用 CPU 不得超过 `LITETRAIN_USER_CPU_QUOTA`（默认 8，且不得大于集群总量）。配额可被管理员在运行时修改并即时生效。

#### FR-SCHED-07 多节点模拟 — P2 — 已实现

可将总 CPU 拆为 1–8 个逻辑节点；节点总容量不超过平台预算；任务必须能落在单个 online 节点，否则提交返回 422。调度在候选节点中选择当前利用率最低者；带 active allocation 的节点禁止下线。

### 4.3 容错与恢复（FR-RESI）

#### FR-RESI-01 心跳与僵死检测 — P0 — 已实现

Worker 每 `LITETRAIN_HEARTBEAT_INTERVAL_SECONDS`（默认 4 秒）回调一次心跳，携带 pid、进度、进程树 CPU/RSS/进程数。Watchdog 以 `last_heartbeat_at or started_at` 为基准，超过 `heartbeat_timeout_seconds`（默认 25 秒）判定僵死，终止任务并标记 `failed / heartbeat_timeout`。

**AC-RESI-01**
- **Given** 任务运行中心跳停止，且距上次心跳已超过 25 秒
- **When** Watchdog 执行检查
- **Then** 任务状态 `failed`、`failure_reason=heartbeat_timeout`，allocation 释放，事件时间线出现对应记录

#### FR-RESI-02 任务超时 — P0 — 已实现

每个任务独立 `max_runtime_seconds`，实际阈值取 `min(task.max_runtime_seconds, settings.default_max_runtime_seconds)`。超时后终止 worker，标记 `failed / runtime_timeout`，并禁止自动重试。

#### FR-RESI-03 失败自动重试 — P1 — 已实现

`max_retries` 取值 0–5。worker 异常、非 0 退出、启动失败可自动重试；超时、心跳超时、手动取消不重试。重试时 `retry_count += 1`、回到 `pending`、删除旧结果文件与该 attempt 的指标点、保留日志并追加分隔线。

#### FR-RESI-04 重启恢复 — P0 — 已实现

后端重启时 `Scheduler.recover()` 必须：对有 result 文件的运行任务直接结算；对进程/actor 仍存活的任务重新附着并刷新心跳窗口；对 worker 丢失的任务标记失败并按规则重试；清理非 running 任务的孤儿 allocation。

**AC-RESI-04**
- **Given** 一个 8 epoch 任务已运行到 12.5%，API 进程被终止后重启
- **When** 新进程完成 `recover()`
- **Then** 任务仍为 `running`，allocation 仍为 4 CPU，心跳继续更新，最终 `succeeded`

### 4.4 日志（FR-LOG）

#### FR-LOG-01 独立日志持久化 — P0 — 已实现

每个任务对应 `data/logs/{task_id}.log`，UTF-8 编码；stdout/stderr 由 worker 重定向写入。日志不随 API 重启丢失。

#### FR-LOG-02 增量读取 — P0 — 已实现

`GET /api/v1/tasks/{id}/logs?offset=&limit=` 按**原始文件字节偏移**读取，返回 `content / start_offset / next_offset / file_size / eof`。展示层对 ANSI 转义和 `\r` 做清理，因此 `len(content)` 可以不等于 `next_offset - start_offset`，客户端必须用 `next_offset` 续读。`limit` 上限 1 MB。

**AC-LOG-02**
- **Given** 日志文件含带色 ANSI 序列，首次读取返回 `next_offset=100`
- **When** 以 `offset=100` 再次读取
- **Then** 只返回新增字节内容，不重复、不遗漏；`content` 中无 ANSI 控制字符

#### FR-LOG-03 SSE 实时日志 — P1 — 已实现

`GET /api/v1/tasks/{id}/logs/stream?offset=` 提供 Server-Sent Events 流；断线后前端自动退回 offset 轮询。

#### FR-LOG-04 终端交互 — P1 — 已实现

日志面板支持滚动、暂停自动滚动、关键词搜索高亮、复制、下载完整日志；采用固定行高虚拟滚动，最多保留最近 25,000 行。

### 4.5 指标与资源采样（FR-METRIC）

#### FR-METRIC-01 YOLO 训练指标 — P1 — 已实现

通过 Ultralytics `on_fit_epoch_end` 回调采集 `train/*_loss`、`val/*_loss`、`metrics/mAP50(B)`、`metrics/mAP50-95(B)`、`precision(B)`、`recall(B)`、`lr/pg*`，经内部接口幂等写入 `task_metrics`（唯一键 `task_id + name + epoch + step`）。

#### FR-METRIC-02 资源采样 — P1 — 已实现

心跳线程随每次上报采集进程树 CPU%、RSS 与进程数，写入 `resource_samples`，与任务绑定形成时间序列。

#### FR-METRIC-03 指标可视化 — P1 — 已实现

任务详情页用 ECharts 折线图展示 loss、mAP/precision/recall 和 CPU/内存曲线，按 epoch 对齐。

### 4.6 前端页面（FR-UI）

| ID | 页面 | 优先级 | 需求要点 |
| --- | --- | --- | --- |
| FR-UI-01 | 集群总览 | P0 | CPU 统计卡片；节点 CPU 占用表格与进度条；队列/配额/服务状态；多节点拓扑入口 |
| FR-UI-02 | 任务列表 | P0 | 任务表格；状态筛选、任务 ID/名称搜索、分页；详情/日志/取消操作 |
| FR-UI-03 | 任务提交 | P0 | 表单录入；内置 YOLO CPU 默认命令预览；质量准入提示；提交后跳转列表 |
| FR-UI-04 | 任务详情 & 日志 | P0 | 基础信息；终端风格日志面板（滚动/暂停/复制/高亮）；指标与事件时间线 |
| FR-UI-05 | 训练流水线 | P2 | 三阶段进度、创建、取消、详情抽屉 |
| FR-UI-06 | 数据与产物 | P2 | 数据集质量、产物下载、Webhook 管理三个 tab |
| FR-UI-07 | 后台管理 | P1 | 账号 CRUD、角色分配、运行设置 |
| FR-UI-08 | 深色模式 | P1 | Element Plus dark + 主题变量，持久化到 localStorage |

**AC-UI-01**
- **Given** 浏览器中已登录操作员账号，平台 8 CPU 中有 4 CPU 被占用
- **When** 打开集群总览页
- **Then** 卡片显示已分配 4/8；节点表显示对应 CPU 占用进度条；队列数量与实际 pending 任务数一致

### 4.7 流水线、资产与 Webhook（P2，已实现）

#### FR-PIPE-01 三阶段流水线

创建时只生成 `preprocess` Task；Scheduler 每 tick 调用 `PipelineService.advance()`，前一阶段成功后自动创建下一阶段；失败/取消则终止并标记后续阶段 `cancelled`；API 重启后可续跑。每阶段都是普通 Task，继承全部调度与日志能力。

#### FR-ASSET-01 数据集管理

内置 COCO128 自动登记；支持 ZIP 上传、YAML 校验、图片/标签统计、内容 SHA-256、质量体检（标签结构、坐标/类别、图片解码、缺失/孤儿标签、完全重复、train/val 泄漏、类别分布、小目标）、警告确认与软删除。严重问题阻断训练并返回 422。

#### FR-ASSET-02 模型产物管理

扫描 `runs/{task_id}` 下的 `best.pt / last.pt / results.csv / args.yaml`，登记大小与 SHA-256，支持按任务/流水线查询和安全下载（防路径穿越）。

#### FR-HOOK-01 状态 Webhook

支持 endpoint 注册与事件订阅；事件与 TaskEvent 在同一 SQLite 事务内入队（transactional outbox）；HMAC-SHA256 签名头 `X-LiteTrain-Signature`；非 2xx 按指数退避重试，最大尝试次数可配置；提供投递记录与手动测试接口。

### 4.8 认证与配置（FR-AUTH / FR-CFG）

#### FR-AUTH-01 登录与会话 — P1 — 已实现

`POST /auth/login` 密码使用 PBKDF2-SHA256 校验，返回 Bearer token 并设置 `litetrain_session` cookie；session 存 SQLite 且可注销；`auth_enabled=false` 时可关闭鉴权用于演示。

#### FR-AUTH-02 角色权限 — P1 — 已实现

三角色：`admin`（全部）、`operator`（提交/取消/资产）、`viewer`（只读）。中间件按路径与方法映射所需权限，越权返回 403。

#### FR-CFG-01 运行设置 — P1 — 已实现

管理员通过 `GET/PATCH /settings` 修改任务超时上限、心跳阈值、重试上限、默认 CPU、单用户配额；写入 `runtime_settings` 并即时生效。提交表单的超时/重试上限与后台一致。

---

## 5. 非功能需求

| ID | 类别 | 需求 | 验证口径 |
| --- | --- | --- | --- |
| NFR-01 | 性能 | 调度 tick 周期 1 秒；日志单次读取上限 1 MB；前端初始日志加载 128 KB | 配置项 + 接口响应 |
| NFR-02 | 一致性 | 状态转换、CPU 占用/释放必须在 SQLite 写事务内完成；多 API 实例用 Redis 短锁互斥 | 自动测试 + 代码审查 |
| NFR-03 | 可靠性 | 任何终态路径 active allocation 归零；API 重启后 running 任务可恢复 | 42 项自动测试 + 重启实测 |
| NFR-04 | 安全 | 密码哈希存储；worker 回调使用 `X-Worker-Token` + hmac 比较；下载路径防穿越；Webhook 仅允许 http(s) | 自动测试 |
| NFR-05 | 兼容性 | Python 3.11–3.13；Node ≥ 20.19；Windows / Linux / macOS；Redis 5 使用 protocol=2 | CI 与本机实测 |
| NFR-06 | 可观测性 | 任务事件时间线、集群健康接口、SSE 日志、资源采样曲线 | 接口与 UI |
| NFR-07 | 技术约束 | 仅 CPU；不引入 GPU/NCCL/DDP；不嵌入第三方训练平台页面 | worker 固定 `device=cpu`；无 iframe |

---

## 6. 约束与假设

1. 单机演示环境：宿主 CPU 16 逻辑线程，平台刻意只登记 8 CPU，不随宿主变化。
2. SQLite 采用 WAL；适合单 API 进程，不适合多实例共享。多实例演进见 `docs/p2-design.md`。
3. Redis 与 Ray 均为可选依赖：不可用时分别退化为进程内锁和本地子进程执行。
4. 抢占粒度是 checkpoint 边界恢复，不是进程挂起；当前 epoch 的进度会回退到最近 checkpoint。
5. 数据集只做软删除，历史任务与产物保留。
6. 默认管理员 `admin/admin123`；生产部署必须通过 `LITETRAIN_BOOTSTRAP_ADMIN_PASSWORD` 覆盖。

---

## 7. 验收标准（按命题验收基准逐条对齐）

| 编号 | 命题验收基准 | Given / When / Then | 自动验证 |
| --- | --- | --- | --- |
| ACC-1 | 正常提交 YOLO CPU 训练，日志输出，完成变更状态并释放资源 | Given coco128 已登记；When 提交 1 epoch 任务并等待终态；Then 日志持续增长、状态 `succeeded`、active allocation 为 0 | `test_scheduler.py::test_successful_result_marks_succeeded` + `e2e_live.py` |
| ACC-2 | 多任务并发，CPU 不足排队，释放后自动调度 | Given 8 CPU 被占满；When 提交 4 CPU 任务并取消占用者；Then 新任务先 `pending` 后 `running` | `test_scheduler.py::test_capacity_and_priority_queue` |
| ACC-3 | 取消排队与运行中任务，资源回收 | 见 AC-TASK-04 | `test_api.py::test_cancel_pending_and_conflict`、`test_scheduler.py::test_cancel_running_task_releases_resources` |
| ACC-4 | 超时、僵死任务自动识别并终止 | 见 AC-RESI-01；超时场景设置 `max_runtime_seconds` 后等待 watchdog | `test_scheduler.py::test_runtime_timeout_fails_and_releases`、`test_heartbeat_timeout_fails_and_releases` |
| ACC-5 | 前端交互、状态标签、分页筛选可用 | 见 AC-UI-01；AC-TASK-02 | Playwright 截图 + `test_api.py` 列表/筛选用例 |
| ACC-P1-1 | 优先级与抢占 | 见 AC-SCHED-05 | `test_scheduler.py::test_preemption_requires_checkpoint`、`test_preemption_finalises_original_and_resumes_child` |
| ACC-P1-2 | 指标折线图 | Given 任务完成多个 epoch；When 打开详情页；Then 图表显示 loss / mAP 曲线 | `test_api.py::test_heartbeat_and_metrics_ingest` + 截图 |
| ACC-P1-3 | 失败自动重试 | Given `max_retries=1`；When 首次非 0 退出；Then 状态回 `pending`，`retry_count=1`，二次失败后终态 `failed` | `test_scheduler.py::test_nonzero_exit_can_auto_retry`、`test_retry_then_terminal_failure` |
| ACC-P1-4 | 单用户配额 | Given 配额 4；When 已用 4 再提交；Then 新任务保持 `pending` | `test_scheduler.py::test_capacity_and_priority_queue` |
| ACC-P2-1 | 多节点 | 两个 4 CPU 节点各跑一个 3 CPU 任务 | `test_p2.py::test_two_node_topology_and_balanced_allocation` |
| ACC-P2-2 | 三阶段流水线 | preprocess→train→evaluate 全部 succeeded | `test_p2.py::test_pipeline_*` + `e2e_p2.py` |

---

## 8. 需求追溯矩阵

| 需求 | 验收 | 自动测试 | 实现位置 |
| --- | --- | --- | --- |
| FR-TASK-01/02/03 | AC-TASK-01/02 | `tests/test_api.py` | `app/api/tasks.py`、`app/services/tasks.py` |
| FR-TASK-04/05 | AC-TASK-04 | `tests/test_api.py`、`tests/test_scheduler.py` | `app/services/scheduler.py` |
| FR-SCHED-01/02/03 | ACC-1/2/3 | `tests/test_scheduler.py` | `app/services/resources.py`、`scheduler.py` |
| FR-SCHED-04/05 | AC-SCHED-05 | `tests/test_scheduler.py` | `app/services/scheduler.py` |
| FR-SCHED-06 | ACC-P1-4 | `tests/test_scheduler.py` | `app/services/resources.py` |
| FR-SCHED-07 | ACC-P2-1 | `tests/test_p2.py` | `app/services/resources.py`、`app/api/cluster.py` |
| FR-RESI-01/02 | AC-RESI-01、ACC-4 | `tests/test_scheduler.py` | `app/services/scheduler.py`、`app/training/worker.py` |
| FR-RESI-03 | ACC-P1-3 | `tests/test_scheduler.py` | `app/services/scheduler.py` |
| FR-RESI-04 | AC-RESI-04 | 重启实测脚本 | `app/services/scheduler.py::recover` |
| FR-LOG-01/02/03 | AC-LOG-02 | `tests/test_logs.py`、`tests/test_api.py` | `app/services/logs.py`、`app/api/tasks.py` |
| FR-LOG-04 | ACC-5 | 截图 | `frontend/src/components/LogTerminal.vue` |
| FR-METRIC-01/02/03 | ACC-P1-2 | `tests/test_api.py` | `app/training/worker.py`、`frontend/src/components/MetricsPanel.vue` |
| FR-UI-01–04 | ACC-5 | 截图 + `e2e_live.py` | `frontend/src/views/*View.vue` |
| FR-PIPE-01 | ACC-P2-2 | `tests/test_p2.py` | `app/services/pipelines.py` |
| FR-ASSET-01/02 | ACC-P2-2 | `tests/test_p2.py`、`tests/test_quality.py` | `app/services/datasets.py`、`artifacts.py` |
| FR-HOOK-01 | ACC-P2-2 | `tests/test_p2.py` | `app/services/webhooks.py` |
| FR-AUTH-01/02 | — | `tests/test_auth.py` | `app/main.py`、`app/services/auth.py` |
| FR-CFG-01 | — | `tests/test_settings.py` | `app/api/settings.py`、`runtime_settings` 表 |

---

## 9. 变更记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| v1.0 | 2026-11-22 | 基于 v2.5.0 已实现功能回填完整规格，建立 SDD 追溯链 |
