<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  ArrowRight,
  Cpu,
  DataLine,
  Monitor,
  Odometer,
  Plus,
  Refresh,
  Share,
  Timer,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

import { api } from '@/api/client'
import StatusBadge from '@/components/StatusBadge.vue'
import type { BenchmarkRun, NodeTopologyItem } from '@/types/p2'
import type { ClusterOverview, Task } from '@/types/task'
import {
  formatDuration,
  formatPercent,
  parseDate,
  timeAgo,
} from '@/utils/format'

const router = useRouter()
const overview = ref<ClusterOverview | null>(null)
const recentTasks = ref<Task[]>([])
const loading = ref(true)
const refreshing = ref(false)
const error = ref<string | null>(null)
const topologyOpen = ref(false)
const topologyBusy = ref(false)
const topologyDraft = ref<NodeTopologyItem[]>([])
const nodeActionBusy = ref<string | null>(null)
const benchmarkOpen = ref(false)
const benchmarkBusy = ref(false)
const benchmark = ref<BenchmarkRun | null>(null)
const benchmarkForm = ref({ threads: '4,8', workers: '0', imgsz: 160, batch: 4 })
let benchmarkTimer: number | undefined
let timer: number | undefined

const cpu = computed(() => overview.value?.cpu)
const quota = computed(() => overview.value?.quota)
const services = computed(() => overview.value?.services)
const memoryGB = computed(() => ({
  total: ((overview.value?.memory.total_mb ?? 0) / 1024).toFixed(1),
  used: ((overview.value?.memory.used_mb ?? 0) / 1024).toFixed(1),
  available: ((overview.value?.memory.available_mb ?? 0) / 1024).toFixed(1),
}))

const statusRows = computed(() => {
  const tasks = overview.value?.tasks
  if (!tasks) return []
  return [
    { label: '运行中', value: tasks.running, color: 'var(--accent)', key: 'running' },
    { label: '等待调度', value: tasks.pending, color: 'var(--amber)', key: 'pending' },
    { label: '成功', value: tasks.succeeded, color: '#16865f', key: 'succeeded' },
    { label: '失败', value: tasks.failed, color: 'var(--red)', key: 'failed' },
    { label: '已取消', value: tasks.cancelled, color: 'var(--text-muted)', key: 'cancelled' },
  ]
})

const capacitySlots = computed(() => {
  const total = quota.value?.limit_cpus ?? 0
  if (!total || total > 24) return []
  const used = quota.value?.used_cpus ?? 0
  return Array.from({ length: total }, (_, index) => index < used)
})

const maxTaskCount = computed(() => {
  const values = statusRows.value.map((item) => item.value)
  return Math.max(1, ...values)
})

const cpuRingBackground = computed(() => {
  const pct = Math.max(1.5, Math.min(100, cpu.value?.utilization_percent ?? 0))
  return `conic-gradient(var(--accent-bright) 0 ${pct}%, color-mix(in srgb, var(--border) 36%, transparent) ${pct}% 100%)`
})

const nodeStatusTone = (status: string) => (status === 'online' ? 'online' : 'offline')

function hostBarHeight(index: number) {
  return 9 + index * 2
}

const lastSync = computed(() => {
  const parsed = parseDate(overview.value?.updated_at)
  return parsed ? parsed.toLocaleTimeString('zh-CN', { hour12: false }) : '—'
})

const onlineNodes = computed(
  () => overview.value?.nodes.filter((node) => node.status === 'online') ?? [],
)
const cpuBudget = computed(() => overview.value?.cpu.budget ?? 8)
const topologyTotal = computed(() =>
  topologyDraft.value.reduce((sum, node) => sum + Number(node.total_cpus || 0), 0),
)

function openTopologyDialog() {
  const existing = onlineNodes.value
  if (existing.length) {
    topologyDraft.value = existing.map((node) => ({ name: node.name, total_cpus: node.total_cpus }))
  } else {
    const half = Math.max(1, Math.floor(cpuBudget.value / 2))
    topologyDraft.value = [
      { name: 'sim-node-1', total_cpus: half },
      { name: 'sim-node-2', total_cpus: Math.max(1, cpuBudget.value - half) },
    ]
  }
  topologyOpen.value = true
}

async function openBenchmark() {
  benchmarkOpen.value = true
  try {
    benchmark.value = await api.latestBenchmark()
    if (benchmark.value && ['queued', 'running'].includes(benchmark.value.status)) {
      pollBenchmark()
    }
  } catch {
    benchmark.value = null
  }
}

function pollBenchmark() {
  if (benchmarkTimer) window.clearTimeout(benchmarkTimer)
  benchmarkTimer = window.setTimeout(async () => {
    if (!benchmark.value) return
    try {
      benchmark.value = await api.getBenchmark(benchmark.value.id)
      if (['queued', 'running'].includes(benchmark.value.status)) pollBenchmark()
    } catch {
      // Keep the last snapshot.
    }
  }, 1800)
}

async function startBenchmark() {
  const threads = benchmarkForm.value.threads
    .split(',')
    .map((value) => Number(value.trim()))
    .filter((value) => Number.isFinite(value) && value > 0)
  const workers = benchmarkForm.value.workers
    .split(',')
    .map((value) => Number(value.trim()))
    .filter((value) => Number.isFinite(value) && value >= 0)
  if (!threads.length) {
    ElMessage.warning('至少填写一个计算线程数')
    return
  }
  benchmarkBusy.value = true
  try {
    benchmark.value = await api.startBenchmark({
      dataset: 'coco128.yaml',
      model: 'yolov8n.pt',
      imgsz: benchmarkForm.value.imgsz,
      batch: benchmarkForm.value.batch,
      threads,
      workers,
      epochs: 1,
    })
    pollBenchmark()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '启动 Benchmark 失败')
  } finally {
    benchmarkBusy.value = false
  }
}

const benchmarkResults = computed(() => benchmark.value?.matrix.results ?? [])
const benchmarkSelected = computed(() => benchmark.value?.selected ?? null)

function benchValue(row: Record<string, unknown>, key: string): string {
  const value = row[key]
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2)
  if (typeof value === 'string') return value
  return '—'
}

function addTopologyRow() {
  topologyDraft.value.push({
    name: `sim-node-${topologyDraft.value.length + 1}`,
    total_cpus: 1,
  })
}

function removeTopologyRow(index: number) {
  if (topologyDraft.value.length <= 1) return
  topologyDraft.value.splice(index, 1)
}

async function applyTopology() {
  if (!topologyDraft.value.length) return
  if (topologyTotal.value > cpuBudget.value) {
    ElMessage.warning(`模拟总容量不能超过平台预算 ${cpuBudget.value} CPU`)
    return
  }
  if (topologyDraft.value.some((node) => !node.name.trim() || node.total_cpus < 1)) {
    ElMessage.warning('每个节点都需要名称和至少 1 个 CPU 核')
    return
  }
  topologyBusy.value = true
  try {
    await api.applyNodeTopology(
      topologyDraft.value.map((node) => ({ name: node.name.trim(), total_cpus: node.total_cpus })),
    )
    ElMessage.success('模拟拓扑已应用，调度器将按节点 CPU 分配任务')
    topologyOpen.value = false
    await load(true)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '应用拓扑失败')
  } finally {
    topologyBusy.value = false
  }
}

async function toggleNode(node: any) {
  nodeActionBusy.value = node.id
  try {
    await api.setNodeStatus(node.id, node.status === 'online' ? 'offline' : 'online')
    ElMessage.success(`节点 ${node.name} 状态已更新`)
    await load(true)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '更新节点状态失败')
  } finally {
    nodeActionBusy.value = null
  }
}

async function load(silent = false) {
  if (!silent) refreshing.value = true
  try {
    const [cluster, tasks] = await Promise.all([
      api.getOverview(),
      api.listTasks({ page: 1, page_size: 6, sort_by: 'submitted_at', sort_order: 'desc' }),
    ])
    overview.value = cluster
    recentTasks.value = tasks.items
    error.value = null
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载集群数据失败'
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

function openTask(task: { id: string }) {
  router.push({ name: 'task-detail', params: { id: task.id } })
}

onMounted(async () => {
  await load()
  timer = window.setInterval(() => load(true), 3000)
})

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
  if (benchmarkTimer) window.clearTimeout(benchmarkTimer)
})
</script>

<template>
  <div class="page overview-page">
    <div class="page-head">
      <div class="page-heading">
        <span class="eyebrow">集群运行视图</span>
        <div class="title-row">
          <h1 class="page-title">集群总览</h1>
          <span class="title-live"><i></i>实时</span>
        </div>
        <p class="page-subtitle">
          查看逻辑 CPU 配额、队列压力与 Ray 执行器状态。资源释放后，等待中的任务会自动被调度。
        </p>
        <div class="page-signals">
          <span><i class="signal-dot"></i>{{ onlineNodes.length }} 个节点在线</span>
          <span>Ray {{ services?.ray_cpus ?? '—' }} 核</span>
          <span>{{ overview?.tasks.pending ?? 0 }} 个排队</span>
          <span class="mono">已运行 {{ formatDuration(overview?.uptime_seconds ?? 0) }}</span>
        </div>
      </div>
      <div class="page-actions">
        <el-button :icon="Cpu" @click="openBenchmark">CPU 自动调优</el-button>
        <button class="sync-chip" type="button" :class="{ spinning: refreshing }" @click="load(true)">
          <el-icon><Refresh /></el-icon>
          <span>已同步 {{ lastSync }}</span>
        </button>
        <el-button :icon="Plus" type="primary" @click="router.push({ name: 'task-submit' })">
          提交训练任务
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      :closable="false"
      class="cluster-alert"
    >
      <template #default>
        <span class="alert-copy">请检查 FastAPI 后端是否运行在 127.0.0.1:8000。</span>
        <el-button size="small" text @click="load()">重新连接</el-button>
      </template>
    </el-alert>

    <template v-if="loading">
      <div class="grid cols-4">
        <div v-for="idx in 4" :key="idx" class="panel skeleton-card">
          <el-skeleton :rows="3" animated />
        </div>
      </div>
      <div class="grid overview-main">
        <div class="panel skeleton-panel"><el-skeleton :rows="7" animated /></div>
        <div class="panel skeleton-panel"><el-skeleton :rows="7" animated /></div>
      </div>
    </template>

    <template v-else-if="overview">
      <section class="metric-deck">
        <article class="metric metric-cpu">
          <div class="metric-kicker">
            <span class="metric-icon"><el-icon><Cpu /></el-icon></span>
            CPU 配额
            <span class="metric-tag">实时</span>
          </div>
          <div class="metric-cpu-main">
            <div class="cpu-ring" :style="{ background: cpuRingBackground }">
              <div class="cpu-ring-inner">
                <strong>{{ cpu?.allocated ?? 0 }}</strong>
                <span>/{{ cpu?.total ?? 0 }}</span>
              </div>
            </div>
            <div class="cpu-ring-copy">
              <strong>{{ formatPercent(cpu?.utilization_percent) }}</strong>
              <span>集群利用率</span>
              <em>{{ cpu?.available ?? 0 }} 核可用</em>
            </div>
          </div>
          <div class="metric-meter"><span :style="{ width: `${cpu?.utilization_percent ?? 0}%` }"></span></div>
          <div class="metric-foot">
            <span>{{ onlineNodes.length }} 个节点，{{ overview.tasks.pending }} 个排队</span>
            <strong>{{ overview.tasks.running }} 个任务运行中</strong>
          </div>
        </article>

        <article class="metric metric-queue">
          <div class="metric-kicker">
            <span class="metric-icon amber"><el-icon><Odometer /></el-icon></span>
            任务队列
            <span class="metric-tag">实时</span>
          </div>
          <div class="queue-main">
            <strong>{{ overview.tasks.running }}</strong>
            <span>运行中</span>
            <em>+{{ overview.tasks.pending }}</em>
            <span class="queue-waiting">等待中</span>
          </div>
          <div class="queue-strip">
            <span
              v-for="row in statusRows"
              :key="row.key"
              class="queue-strip-seg"
              :style="{ flexGrow: Math.max(0.14, row.value), background: row.color }"
            ></span>
          </div>
          <div class="metric-foot">
            <span>累计 {{ overview.tasks.total }} 个任务</span>
            <span>{{ overview.tasks.succeeded }} 个成功</span>
          </div>
        </article>

        <article class="metric metric-host">
          <div class="metric-kicker">
            <span class="metric-icon copper"><el-icon><Monitor /></el-icon></span>
            主机负载
            <span class="metric-tag">{{ cpu?.host_logical_cores ?? '—' }} 线程</span>
          </div>
          <div class="host-main">
            <strong>{{ formatPercent(cpu?.host_percent) }}</strong>
            <span>主机 CPU</span>
          </div>
          <div class="host-bars" aria-hidden="true">
            <i
              v-for="idx in 12"
              :key="idx"
              :class="{ on: (cpu?.host_percent ?? 0) >= idx * 8.3 }"
              :style="{ height: `${hostBarHeight(idx-1)}px` }"
            ></i>
          </div>
          <div class="host-scale">
            <span>{{ formatPercent(cpu?.process_percent) }} 进程</span>
            <span>{{ formatPercent(overview.memory.percent, 0) }} 内存</span>
          </div>
          <div class="metric-foot">
            <span>{{ memoryGB.used }} / {{ memoryGB.total }} GB</span>
            <span class="copper-copy">{{ memoryGB.available }} GB 可用</span>
          </div>
        </article>
      </section>

      <section class="grid overview-main">
        <div class="panel topology-panel">
          <div class="panel-head">
            <div>
              <h2>集群拓扑</h2>
              <p class="panel-caption">调度器、节点容量与正在运行的训练任务</p>
            </div>
            <div class="panel-head-actions">
              <span class="tag-soft accent"><span class="dot"></span>{{ onlineNodes.length }} 个在线</span>
              <el-button size="small" :icon="Share" @click="openTopologyDialog">多节点模拟</el-button>
            </div>
          </div>

          <div class="topology-stage">
            <div class="topology-hub">
              <span class="hub-label"><i></i>调度器 · 优先级队列</span>
              <strong>Ray Head</strong>
              <small>{{ services?.ray_address ?? '127.0.0.1:6380' }} · {{ services?.ray_cpus ?? '—' }} 核</small>
              <span class="hub-version">v{{ services?.ray_version ?? '—' }}</span>
            </div>
            <div class="topology-bus"></div>
            <div class="topology-nodes">
              <article
                v-for="node in overview.nodes"
                :key="node.id"
                class="topology-node"
                :class="nodeStatusTone(node.status)"
              >
                <div class="topology-node-head">
                  <span class="topology-node-glyph"><el-icon><Cpu /></el-icon></span>
                  <div>
                    <strong>{{ node.name }}</strong>
                    <span>{{ node.hostname }}</span>
                  </div>
                  <span class="topology-status" :class="nodeStatusTone(node.status)">
                    <i></i>{{ node.status === 'online' ? '在线' : '离线' }}
                  </span>
                </div>
                <div class="topology-node-meter">
                  <div>
                    <span>{{ node.allocated_cpus }} / {{ node.total_cpus }} 核</span>
                    <b>{{ formatPercent(node.cpu_utilization_percent, 0) }}</b>
                  </div>
                  <div class="meter compact"><span :style="{ width: `${node.cpu_utilization_percent}%` }"></span></div>
                </div>
                <div class="topology-tasks">
                  <button
                    v-for="task in node.active_tasks.slice(0, 3)"
                    :key="task.id"
                    type="button"
                    class="topology-task"
                    @click="openTask(task)"
                  >
                    <i></i>
                    <span>{{ task.name }}</span>
                    <b>{{ Math.round(task.progress) }}%</b>
                  </button>
                  <span v-if="node.active_tasks.length > 3" class="topology-task-more">
                    +{{ node.active_tasks.length - 3 }} 个运行中
                  </span>
                  <span v-if="!node.active_tasks.length" class="topology-idle">
                    {{ node.status === 'online' ? '空闲，等待任务' : '待机，无分配' }}
                  </span>
                </div>
                <div class="topology-node-foot">
                  <span>主机 {{ formatPercent(node.host_percent) }}</span>
                  <button
                    type="button"
                    class="topology-node-action"
                    :disabled="nodeActionBusy === node.id"
                    @click.stop="toggleNode(node)"
                  >
                    {{ node.status === 'online' ? '下线' : '上线' }}
                  </button>
                </div>
              </article>
            </div>
          </div>

          <div class="node-policy">
            <span><el-icon><Timer /></el-icon> 心跳超时自动回收</span>
            <span>调度粒度为 1 个 CPU 核</span>
            <span>Ray 逻辑资源 {{ services?.ray_cpus ?? '—' }} 核</span>
            <span class="node-policy-legend"><i></i>运行中 <i class="idle"></i>空闲</span>
          </div>
        </div>

        <div class="side-stack">
          <div class="panel services-panel">
            <div class="panel-head">
              <h2>服务与配额</h2>
              <span class="tag-soft" :class="{ accent: services?.ray_connected && services?.redis_connected }">
                <span class="dot"></span>控制平面
              </span>
            </div>
            <div class="panel-body service-body">
              <div class="service-row">
                <div class="service-symbol ray"><span>R</span></div>
                <div class="service-copy">
                  <strong>Ray 执行器</strong>
                  <span>{{ services?.ray_address }} · v{{ services?.ray_version ?? '—' }}</span>
                </div>
                <span class="service-state" :class="{ ok: services?.ray_connected }">
                  {{ services?.ray_connected ? '已连接' : '本地回退' }}
                </span>
              </div>
              <div class="service-row">
                <div class="service-symbol redis"><span>R</span></div>
                <div class="service-copy">
                  <strong>Redis 协调</strong>
                  <span>调度锁、事件订阅 · v{{ services?.redis_version ?? '—' }}</span>
                </div>
                <span class="service-state" :class="{ ok: services?.redis_connected }">
                  {{ services?.redis_connected ? '已连接' : '已降级' }}
                </span>
              </div>

              <div class="quota-box">
                <div class="row between">
                  <span class="soft-label">单用户配额</span>
                  <span class="mono quota-num">
                    {{ quota?.used_cpus ?? 0 }} / {{ quota?.limit_cpus ?? 0 }} 核
                  </span>
                </div>
                <div class="capacity-slots" v-if="capacitySlots.length">
                  <span
                    v-for="(used, idx) in capacitySlots"
                    :key="idx"
                    :class="{ used }"
                    :title="`core ${idx + 1}`"
                  ></span>
                </div>
                <div v-else class="meter">
                  <span
                    :style="{
                      width: `${quota?.limit_cpus ? ((quota.used_cpus / quota.limit_cpus) * 100) : 0}%`,
                    }"
                  ></span>
                </div>
                <div class="quota-foot">
                  <span>用户 <code class="mono">demo</code> 的并发 CPU 上限</span>
                  <strong>{{ quota?.available_cpus ?? 0 }} 核空闲</strong>
                </div>
              </div>

              <div class="service-foot-row">
                <span><el-icon><DataLine /></el-icon> 调度器 {{ services?.execution_mode === 'auto' ? '自动' : services?.execution_mode }}</span>
                <span><el-icon><Timer /></el-icon> 服务运行 {{ formatDuration(overview.uptime_seconds) }}</span>
              </div>
            </div>
          </div>

          <div class="panel queue-panel">
            <div class="panel-head">
              <h2>状态分布</h2>
              <span class="panel-caption">全部时间</span>
            </div>
            <div class="panel-body status-distribution">
              <div v-for="row in statusRows" :key="row.key" class="dist-row">
                <span class="dist-label"><i :style="{ background: row.color }"></i>{{ row.label }}</span>
                <div class="dist-bar">
                  <span
                    :style="{
                      width: `${(row.value / maxTaskCount) * 100}%`,
                      background: row.color,
                    }"
                  ></span>
                </div>
                <strong class="mono">{{ row.value }}</strong>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section class="panel recent-panel">
        <div class="panel-head">
          <div>
            <h2>最近任务</h2>
            <p class="panel-caption">最近 6 个提交的任务，点击行查看日志与指标</p>
          </div>
          <el-button text :icon="ArrowRight" @click="router.push({ name: 'tasks' })">全部任务</el-button>
        </div>
        <el-table :data="recentTasks" row-key="id" class="recent-table" @row-click="openTask">
          <el-table-column label="任务" min-width="230">
            <template #default="{ row }">
              <div class="recent-task">
                <span class="recent-glyph"><el-icon><DataLine /></el-icon></span>
                <div>
                  <strong>{{ row.name }}</strong>
                  <span class="mono">{{ row.id }}</span>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="120">
            <template #default="{ row }">
              <StatusBadge :status="row.status" />
            </template>
          </el-table-column>
          <el-table-column label="配置" min-width="160">
            <template #default="{ row }">
              <span class="mono muted small">{{ row.epochs }} 轮，{{ row.imgsz }} px，批 {{ row.batch_size }}</span>
            </template>
          </el-table-column>
          <el-table-column label="CPU" width="76" align="center">
            <template #default="{ row }">
              <span class="task-count-pill">{{ row.cpu_cores }}</span>
            </template>
          </el-table-column>
          <el-table-column label="提交时间" min-width="120">
            <template #default="{ row }">
              <span class="muted small">{{ timeAgo(row.submitted_at) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="" width="48" align="right">
            <template #default>
              <el-icon class="row-arrow"><ArrowRight /></el-icon>
            </template>
          </el-table-column>
          <template #empty>
            <div class="empty-state">
              <span class="empty-icon"><el-icon><DataLine /></el-icon></span>
              <strong>还没有训练任务</strong>
              <span>从提交页启动一次 YOLO CPU 训练，队列和资源变化会立即出现在这里。</span>
            </div>
          </template>
        </el-table>
      </section>

      <el-dialog v-model="topologyOpen" title="多节点集群模拟" width="560px">
        <div class="topology-intro">
          <p>
            把平台的 <strong>{{ cpuBudget }} 个 CPU 核</strong> 预算拆成多个逻辑节点，调度器会按节点空闲容量与负载分配任务。
          </p>
          <p>任务仍在本机 Ray 集群执行；该功能用于演示多节点资源账本、节点亲和与容量碎片化。</p>
        </div>
        <div class="topology-rows">
          <div v-for="(node, index) in topologyDraft" :key="index" class="topology-row">
            <el-input v-model="node.name" placeholder="节点名称" maxlength="60" />
            <el-input-number v-model="node.total_cpus" :min="1" :max="cpuBudget" controls-position="right" />
            <span class="topology-unit">核</span>
            <el-button text :disabled="topologyDraft.length <= 1" @click="removeTopologyRow(index)">
              移除
            </el-button>
          </div>
        </div>
        <el-button text :icon="Plus" @click="addTopologyRow">添加节点</el-button>
        <div class="topology-summary">
          <span>模拟总容量</span>
          <strong class="mono" :class="{ overloaded: topologyTotal > cpuBudget }">
            {{ topologyTotal }} / {{ cpuBudget }} 核
          </strong>
        </div>
        <template #footer>
          <el-button @click="topologyOpen = false">取消</el-button>
          <el-button type="primary" :loading="topologyBusy" @click="applyTopology">
            应用拓扑
          </el-button>
        </template>
      </el-dialog>

      <el-dialog v-model="benchmarkOpen" title="CPU 自动调优" width="760px">
        <p class="benchmark-intro">
          Benchmark 用 1 个 epoch 测量吞吐、峰值内存和耗时，不会占用训练队列。训练进行中时不可启动。
        </p>
        <div class="benchmark-form">
          <el-input v-model="benchmarkForm.threads" placeholder="线程，如 4,8" />
          <el-input v-model="benchmarkForm.workers" placeholder="Workers，如 0" />
          <el-input-number v-model="benchmarkForm.imgsz" :min="64" :max="640" :step="32" />
          <el-input-number v-model="benchmarkForm.batch" :min="1" :max="64" />
          <el-button type="primary" :loading="benchmarkBusy" @click="startBenchmark">开始测试</el-button>
        </div>

        <div v-if="benchmark" class="benchmark-status">
          <span>状态：<strong>{{ benchmark.status }}</strong></span>
          <span v-if="benchmark.error" class="benchmark-error">{{ benchmark.error }}</span>
        </div>

        <div v-if="benchmarkSelected" class="benchmark-selected">
          推荐：threads {{ benchValue(benchmarkSelected, 'threads') }} / workers
          {{ benchValue(benchmarkSelected, 'workers') }} /
          {{ benchValue(benchmarkSelected, 'images_per_second') }} images/s
        </div>

        <el-table v-if="benchmarkResults.length" :data="benchmarkResults" class="benchmark-table">
          <el-table-column label="线程" width="70">
            <template #default="{ row }">{{ benchValue(row, 'threads') }}</template>
          </el-table-column>
          <el-table-column label="Workers" width="80">
            <template #default="{ row }">{{ benchValue(row, 'workers') }}</template>
          </el-table-column>
          <el-table-column label="Images/s" width="100" align="right">
            <template #default="{ row }">{{ benchValue(row, 'images_per_second') }}</template>
          </el-table-column>
          <el-table-column label="耗时" width="90" align="right">
            <template #default="{ row }">{{ benchValue(row, 'elapsed_seconds') }}s</template>
          </el-table-column>
          <el-table-column label="峰值内存" width="110" align="right">
            <template #default="{ row }">{{ benchValue(row, 'peak_rss_mb') }} MB</template>
          </el-table-column>
          <el-table-column label="结果" min-width="120">
            <template #default="{ row }">
              <span :class="{ 'benchmark-failed': row.status !== 'succeeded' }">
                {{ row.status === 'succeeded' ? '成功' : benchValue(row, 'error') }}
              </span>
            </template>
          </el-table-column>
        </el-table>
        <p class="benchmark-intro">
          推荐值需在提交任务页手动应用；Benchmark 不会自动修改运行中的任务。
        </p>
      </el-dialog>
    </template>
  </div>
</template>

<style scoped>
.overview-page {
  gap: 18px;
}

.cluster-alert {
  border-radius: 12px;
}

.alert-copy {
  margin-right: 10px;
  font-size: 12px;
}

.sync-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--panel);
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 9.5px;
  cursor: default;
}

.sync-chip .el-icon {
  font-size: 13px;
  transition: transform 0.4s ease;
}

.sync-chip.spinning .el-icon {
  animation: spin 0.8s linear infinite;
}

.skeleton-card {
  min-height: 152px;
  padding: 20px;
}

.skeleton-panel {
  min-height: 420px;
  padding: 20px;
}

.stat-grid {
  gap: 14px;
}

.stat-card {
  position: relative;
  min-width: 0;
  padding: 17px 18px 15px;
  overflow: hidden;
  background:
    linear-gradient(150deg, color-mix(in srgb, var(--panel) 92%, transparent), var(--panel)),
    var(--panel);
}

.stat-card::after {
  content: '';
  position: absolute;
  top: -50px;
  right: -45px;
  width: 130px;
  height: 130px;
  border-radius: 50%;
  background: var(--accent-soft);
  opacity: 0.45;
  pointer-events: none;
}

.accent-card {
  border-color: var(--accent-line);
}

.stat-top {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.stat-icon {
  display: grid;
  place-items: center;
  width: 27px;
  height: 27px;
  flex: none;
  border-radius: 8px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 14px;
}

.stat-icon.amber {
  background: var(--amber-soft);
  color: var(--amber);
}

.stat-icon.violet {
  background: var(--violet-soft);
  color: var(--violet);
}

.stat-icon.blue {
  background: rgba(58, 124, 209, 0.11);
  color: #4f8dd6;
}

.stat-kicker {
  color: var(--text-secondary);
  font-family: var(--mono);
  font-size: 9.5px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.stat-trend {
  margin-left: auto;
  padding: 3px 6px;
  border-radius: 5px;
  background: var(--accent-soft);
  color: var(--accent);
  font-family: var(--mono);
  font-size: 9px;
  font-weight: 700;
}

.stat-trend.neutral {
  background: var(--panel-soft);
  color: var(--text-muted);
  font-weight: 500;
}

.stat-value-row {
  display: flex;
  align-items: baseline;
  gap: 4px;
  min-width: 0;
}

.stat-value-row .metric-value {
  font-size: 32px;
  line-height: 1;
}

.stat-denom {
  display: flex;
  align-items: baseline;
  gap: 3px;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 13px;
  font-weight: 600;
}

.stat-denom small {
  font-family: var(--font);
  font-size: 10px;
  font-weight: 500;
}

.queue-count {
  margin-left: 8px;
  font-size: 24px;
  color: var(--amber);
}

.stat-separator {
  color: var(--border-strong);
}

.meter {
  height: 6px;
  overflow: hidden;
  border-radius: 7px;
  background: var(--panel-soft);
  box-shadow: inset 0 0 0 1px var(--border);
}

.meter span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, var(--accent), #43d09a);
  transition: width 0.55s ease;
}

.meter.accent {
  margin: 13px 0 10px;
}

.meter.violet span {
  background: var(--copper);
}

.meter.blue span {
  background: var(--blue);
}

.meter.compact {
  height: 4px;
  margin-top: 6px;
}

.stat-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 9px;
  color: var(--text-muted);
  font-size: 10px;
}

.stat-inline {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--text-secondary);
  font-family: var(--mono);
  font-size: 9px;
}

.stat-inline i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--accent);
}

.stat-foot .stat-inline {
  margin: 0;
}

.queue-strip {
  display: flex;
  gap: 3px;
  height: 6px;
  margin: 13px 0 10px;
  overflow: hidden;
  border-radius: 7px;
  background: var(--panel-soft);
}

.queue-strip-seg {
  min-width: 4px;
  height: 100%;
  opacity: 0.78;
  transition: flex-grow 0.4s ease;
}

.host-load-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.host-load-row .metric-value {
  font-size: 32px;
  line-height: 1;
}

.host-bars {
  display: flex;
  align-items: flex-end;
  gap: 2px;
  height: 28px;
  margin-left: auto;
}

.host-bars i {
  width: 4px;
  height: 10px;
  border-radius: 2px;
  background: var(--border);
  transition: height 0.4s ease, background 0.4s ease;
}

.host-bars i:nth-child(3n) {
  height: 16px;
}

.host-bars i:nth-child(4n) {
  height: 22px;
}

.host-bars i:nth-child(5n) {
  height: 26px;
}

.host-bars i.on {
  background: var(--violet);
  opacity: 0.66;
}

.host-load-row + .meter {
  margin-top: 13px;
}

.overview-main {
  grid-template-columns: minmax(0, 1.75fr) minmax(300px, 0.9fr);
  align-items: start;
}

.nodes-panel {
  overflow: hidden;
}

.head-index {
  margin-right: 7px;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 9px;
  font-weight: 500;
}

.panel-caption {
  margin-top: 3px;
  font-size: 10.5px;
}

.node-name {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
}

.node-glyph {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  flex: none;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel-soft);
  color: var(--accent);
  font-size: 14px;
}

.node-name div {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.node-name strong {
  overflow: hidden;
  color: var(--text);
  font-family: var(--mono);
  font-size: 11.5px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.node-name span:not(.node-glyph) {
  overflow: hidden;
  color: var(--text-muted);
  font-size: 9.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.node-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--text-muted);
  font-size: 10px;
}

.node-status i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}

.node-status.online {
  color: var(--accent);
}

.node-meter-cell {
  max-width: 180px;
}

.node-meter-top {
  gap: 8px;
  font-size: 10.5px;
}

.node-meter-top strong {
  color: var(--text);
  font-family: var(--mono);
  font-size: 10px;
  font-weight: 600;
}

.task-count-pill {
  display: inline-grid;
  place-items: center;
  min-width: 24px;
  height: 20px;
  padding: 0 6px;
  border-radius: 6px;
  background: var(--panel-soft);
  color: var(--text-secondary);
  font-family: var(--mono);
  font-size: 10px;
  font-weight: 600;
}

.node-policy {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
  padding: 11px 20px;
  border-top: 1px solid var(--border);
  background: var(--panel-soft);
  color: var(--text-muted);
  font-size: 9.5px;
}

.panel-head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.topology-intro {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 14px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--panel-soft);
  color: var(--text-muted);
  font-size: 10px;
  line-height: 1.65;
}

.topology-intro strong {
  color: var(--accent);
  font-family: var(--mono);
}

.topology-rows {
  display: flex;
  flex-direction: column;
  gap: 7px;
  margin-bottom: 8px;
}

.topology-row {
  display: grid;
  grid-template-columns: 24px minmax(120px, 1fr) 120px 34px 48px;
  align-items: center;
  gap: 7px;
}

.topology-index {
  color: var(--text-muted);
  font-size: 8px;
}

.topology-unit {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 8px;
}

.topology-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed var(--border);
  color: var(--text-secondary);
  font-size: 10px;
}

.topology-summary strong {
  color: var(--accent);
  font-size: 11px;
}

.topology-summary strong.overloaded {
  color: var(--red);
}

.node-policy span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.node-policy .el-icon {
  font-size: 12px;
  color: var(--accent);
}

.side-stack {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.service-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 16px 18px;
}

.service-row {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 8px 0;
  border-bottom: 1px dashed var(--border);
}

.service-symbol {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  flex: none;
  border-radius: 8px;
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 700;
}

.service-symbol.ray {
  background: var(--violet-soft);
  color: var(--violet);
}

.service-symbol.redis {
  background: var(--red-soft);
  color: #cf5555;
}

.service-copy {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
  flex: 1;
}

.service-copy strong {
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 600;
}

.service-copy span {
  overflow: hidden;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 8.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.service-state {
  flex: none;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 7.5px;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.service-state.ok {
  color: var(--accent);
}

.quota-box {
  margin-top: 13px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 11px;
  background: var(--panel-soft);
}

.quota-num {
  color: var(--text);
  font-size: 10.5px;
  font-weight: 600;
}

.capacity-slots {
  display: flex;
  gap: 3px;
  height: 17px;
  margin: 11px 0 9px;
}

.capacity-slots span {
  flex: 1;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--panel);
}

.capacity-slots span.used {
  border-color: var(--accent);
  background: linear-gradient(135deg, var(--accent), #39c58f);
  box-shadow: 0 0 10px var(--accent-soft);
}

.quota-box .meter {
  margin: 12px 0 9px;
}

.quota-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 8px;
  color: var(--text-muted);
  font-size: 9px;
}

.quota-foot code {
  color: var(--text-secondary);
  font-size: 9px;
}

.quota-foot strong {
  color: var(--accent);
  font-family: var(--mono);
  font-size: 9px;
  font-weight: 600;
}

.service-foot-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 11px;
  color: var(--text-muted);
  font-size: 8.5px;
}

.service-foot-row span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.status-distribution {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 15px 18px 17px;
}

.dist-row {
  display: flex;
  align-items: center;
  gap: 9px;
}

.dist-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  width: 66px;
  flex: none;
  color: var(--text-secondary);
  font-size: 10px;
}

.dist-label i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
}

.dist-bar {
  flex: 1;
  height: 5px;
  overflow: hidden;
  border-radius: 4px;
  background: var(--panel-soft);
}

.dist-bar span {
  display: block;
  height: 100%;
  border-radius: inherit;
  opacity: 0.7;
  transition: width 0.45s ease;
}

.dist-row strong {
  width: 22px;
  color: var(--text-secondary);
  font-family: var(--mono);
  font-size: 9.5px;
  text-align: right;
}

.recent-panel {
  overflow: hidden;
}

.recent-task {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
}

.recent-glyph {
  display: grid;
  place-items: center;
  width: 27px;
  height: 27px;
  flex: none;
  border-radius: 8px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 13px;
}

.recent-task div {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.recent-task strong {
  overflow: hidden;
  color: var(--text);
  font-size: 11.5px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recent-task span {
  color: var(--text-muted);
  font-size: 9px;
}

.small {
  font-size: 10px;
}

.recent-table :deep(.el-table__row) {
  cursor: pointer;
}

.row-arrow {
  color: var(--text-muted);
  font-size: 12px;
}

@media (max-width: 1180px) {
  .overview-main {
    grid-template-columns: minmax(0, 1.4fr) minmax(280px, 0.85fr);
  }
}

@media (max-width: 1000px) {
  .overview-main {
    grid-template-columns: minmax(0, 1fr);
  }

  .side-stack {
    display: grid;
    grid-template-columns: minmax(0, 1.3fr) minmax(260px, 0.7fr);
  }
}

@media (max-width: 720px) {
  .side-stack {
    grid-template-columns: minmax(0, 1fr);
  }

  .node-policy {
    gap: 8px 14px;
  }

  .sync-chip {
    display: none;
  }

  .stat-grid {
    gap: 10px;
  }

  .stat-card {
    padding: 15px;
  }

  .stat-value-row .metric-value {
    font-size: 28px;
  }
}
</style>

<style scoped>
/* Cluster cockpit refinements */
.title-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.title-live {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 7px;
  border: 1px solid var(--accent-line);
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-family: var(--mono);
  font-size: 7px;
  font-weight: 700;
  letter-spacing: 0.1em;
  box-shadow: 0 0 22px -14px var(--accent);
}

.title-live i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: currentColor;
  animation: cockpit-pulse 2s ease-out infinite;
}

.page-signals {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  margin-top: 2px;
}

.page-signals span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  border: 1px solid color-mix(in srgb, var(--border) 78%, transparent);
  border-radius: 999px;
  background: color-mix(in srgb, var(--panel) 42%, transparent);
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 7.5px;
  letter-spacing: 0.025em;
}

.signal-dot {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.stat-grid {
  gap: 15px;
}

.stat-card {
  min-height: 147px;
  padding: 18px 19px 15px;
  border-radius: 19px;
  transition:
    transform 0.22s ease,
    box-shadow 0.22s ease,
    border-color 0.22s ease;
}

.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-hover);
}

.stat-top {
  margin-bottom: 13px;
}

.stat-icon {
  width: 30px;
  height: 30px;
  border: 1px solid color-mix(in srgb, currentColor 15%, transparent);
  border-radius: 9px;
}

.stat-kicker {
  color: var(--text-muted);
  font-size: 8px;
  font-weight: 700;
  letter-spacing: 0.11em;
}

.stat-trend {
  padding: 3px 7px;
  border-radius: 6px;
  font-size: 8px;
}

.stat-value-row .metric-value {
  font-size: 36px;
  line-height: 0.96;
}

.stat-denom {
  font-size: 12px;
}

.stat-foot {
  margin-top: 10px;
  font-size: 9px;
}

.accent-card {
  border-color: rgba(76, 240, 179, 0.25);
  background:
    radial-gradient(320px 180px at 100% 0%, rgba(89, 255, 193, 0.2), transparent 68%),
    radial-gradient(220px 160px at 5% 110%, rgba(198, 243, 106, 0.09), transparent 70%),
    linear-gradient(145deg, #0a2b1f 0%, #061b12 55%, #04140d 100%);
  box-shadow:
    0 1px 0 rgba(255, 255, 255, 0.08) inset,
    0 26px 50px -32px rgba(4, 28, 18, 0.55);
}

html.dark .accent-card {
  border-color: rgba(76, 240, 179, 0.25);
  background:
    radial-gradient(320px 180px at 100% 0%, rgba(89, 255, 193, 0.2), transparent 68%),
    radial-gradient(220px 160px at 5% 110%, rgba(198, 243, 106, 0.08), transparent 70%),
    linear-gradient(145deg, #0d3023 0%, #071c13 55%, #04120c 100%);
}

.accent-card::after {
  top: -62px;
  right: -36px;
  width: 190px;
  height: 190px;
  background: radial-gradient(circle, rgba(77, 255, 188, 0.22), transparent 68%);
  filter: blur(12px);
  opacity: 1;
}

.accent-card .stat-icon {
  border-color: rgba(106, 255, 202, 0.18);
  background: rgba(79, 240, 181, 0.1);
  color: #8bffd6;
  box-shadow: 0 0 22px -10px rgba(83, 255, 191, 0.8);
}

.accent-card .stat-kicker {
  color: #98b9aa;
}

.accent-card .stat-trend {
  background: rgba(94, 243, 185, 0.11);
  color: #9dffda;
}

.accent-card .stat-value-row .metric-value {
  color: #f2fff9;
  text-shadow: 0 0 32px rgba(99, 255, 199, 0.18);
}

.accent-card .stat-denom {
  color: #9ebeb0;
}

.accent-card .meter {
  height: 4px;
  background: rgba(255, 255, 255, 0.09);
  box-shadow: none;
}

.accent-card .meter span {
  background: linear-gradient(90deg, #2ed69c, #c6f36a);
  box-shadow: 0 0 16px rgba(92, 255, 193, 0.48);
}

.accent-card .stat-foot {
  color: #8eae9f;
}

.accent-card .stat-inline {
  color: #d6ffd6;
}

.accent-card .stat-inline i {
  background: #8bffd6;
}

.stat-card:not(.accent-card) .stat-icon {
  background:
    radial-gradient(circle at 30% 20%, rgba(255, 255, 255, 0.6), transparent 50%),
    var(--panel-soft);
}

.nodes-panel,
.services-panel,
.queue-panel,
.recent-panel {
  border-radius: 20px;
}

.nodes-panel .panel-head {
  background:
    linear-gradient(105deg, color-mix(in srgb, var(--accent-soft) 48%, transparent), transparent 62%);
}

.node-table :deep(.el-table__row) {
  transition: background 0.2s ease;
}

.node-table :deep(.el-table__row:hover) {
  background: color-mix(in srgb, var(--accent-soft) 38%, var(--panel-soft));
}

.node-glyph,
.recent-glyph {
  border: 1px solid color-mix(in srgb, var(--accent) 14%, var(--border));
  background:
    radial-gradient(circle at 35% 20%, var(--accent-soft), transparent 70%),
    var(--panel-soft);
  box-shadow: 0 0 24px -18px var(--accent);
}

.service-row {
  border-radius: 12px;
}

.quota-box {
  border-radius: 13px;
  background:
    linear-gradient(150deg, color-mix(in srgb, var(--panel-soft) 82%, transparent), var(--panel));
}

.recent-panel .panel-head {
  background:
    linear-gradient(105deg, color-mix(in srgb, var(--violet-soft) 38%, transparent), transparent 65%);
}

.recent-task strong {
  font-weight: 600;
}

.row-arrow {
  transition: transform 0.2s ease, color 0.2s ease;
}

.recent-table :deep(.el-table__row):hover .row-arrow {
  color: var(--accent);
  transform: translateX(3px);
}

.sync-chip {
  border-radius: 999px;
  background: color-mix(in srgb, var(--panel) 54%, transparent);
  box-shadow: 0 1px 0 var(--panel-highlight) inset;
}

@keyframes cockpit-pulse {
  0% {
    box-shadow: 0 0 0 0 currentColor;
  }
  70% {
    box-shadow: 0 0 0 4px transparent;
  }
  100% {
    box-shadow: 0 0 0 0 transparent;
  }
}

@media (max-width: 1180px) {
  .overview-main {
    grid-template-columns: minmax(0, 1fr);
  }

  .side-stack {
    display: grid;
    grid-template-columns: minmax(0, 1.2fr) minmax(260px, 0.8fr);
  }
}

@media (max-width: 760px) {
  .side-stack {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 720px) {
  .title-live {
    display: none;
  }

  .page-signals span:nth-child(n + 3) {
    display: none;
  }

  .stat-card {
    min-height: 132px;
  }

  .stat-value-row .metric-value {
    font-size: 31px;
  }
}
</style>

<style scoped>
/* Direction A · Precision Console */
.metric-deck {
  display: grid;
  grid-template-columns: 1.3fr 1fr 1fr;
  gap: 15px;
}

.metric {
  position: relative;
  min-height: 168px;
  overflow: hidden;
  padding: 18px 19px 15px;
  border: 1px solid var(--border);
  border-radius: 19px;
  background: var(--panel);
  box-shadow: var(--shadow-panel);
  transition: border-color 0.22s ease, box-shadow 0.22s ease, transform 0.22s ease;
}

.metric:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-hover);
}

.metric-cpu {
  border-color: rgba(76, 240, 179, 0.25);
  background:
    radial-gradient(320px 180px at 100% 0%, rgba(89, 255, 193, 0.2), transparent 68%),
    radial-gradient(220px 160px at 5% 110%, rgba(198, 243, 106, 0.08), transparent 70%),
    linear-gradient(145deg, #0a2b1f 0%, #061b12 55%, #04140d 100%);
  box-shadow:
    0 1px 0 rgba(255, 255, 255, 0.08) inset,
    0 26px 50px -32px rgba(4, 28, 18, 0.55);
}

.metric-kicker {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 8px;
  font-weight: 700;
  letter-spacing: 0.11em;
  text-transform: uppercase;
}

.metric-cpu .metric-kicker {
  color: #98b9aa;
}

.metric-icon {
  display: grid;
  width: 28px;
  height: 28px;
  flex: none;
  place-items: center;
  border: 1px solid rgba(11, 151, 103, 0.12);
  border-radius: 8px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 14px;
}

.metric-cpu .metric-icon {
  border-color: rgba(106, 255, 202, 0.18);
  background: rgba(79, 240, 181, 0.1);
  color: #8bffd6;
  box-shadow: 0 0 22px -10px rgba(83, 255, 191, 0.8);
}

.metric-icon.amber {
  border-color: rgba(216, 138, 8, 0.13);
  background: var(--amber-soft);
  color: var(--amber);
}

.metric-icon.copper {
  border-color: rgba(192, 120, 75, 0.14);
  background: var(--copper-soft);
  color: var(--copper);
}

.metric-tag {
  margin-left: auto;
  padding: 3px 7px;
  border-radius: 6px;
  background: var(--panel-soft);
  color: var(--text-muted);
  font-size: 8px;
  font-weight: 500;
}

.metric-cpu .metric-tag {
  background: rgba(94, 243, 185, 0.11);
  color: #9dffda;
}

.metric-cpu-main {
  display: flex;
  align-items: center;
  gap: 17px;
  margin-top: 12px;
}

.cpu-ring {
  position: relative;
  display: grid;
  width: 92px;
  height: 92px;
  flex: none;
  place-items: center;
  border-radius: 50%;
  box-shadow: 0 0 30px -18px var(--accent-bright);
}

.cpu-ring::before {
  content: '';
  position: absolute;
  inset: 7px;
  border-radius: 50%;
  background: #0a2319;
}

.cpu-ring-inner {
  position: relative;
  display: flex;
  align-items: baseline;
  gap: 2px;
}

.cpu-ring-inner strong {
  color: #f2fff9;
  font-family: var(--font-display);
  font-size: 27px;
  line-height: 1;
  letter-spacing: -0.06em;
}

.cpu-ring-inner span {
  color: #8aac9c;
  font-family: var(--mono);
  font-size: 8px;
}

.cpu-ring-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.cpu-ring-copy strong {
  color: #f2fff9;
  font-family: var(--font-display);
  font-size: 28px;
  line-height: 1;
  letter-spacing: -0.06em;
}

.cpu-ring-copy span {
  color: #8aac9c;
  font-family: var(--mono);
  font-size: 8px;
}

.cpu-ring-copy em {
  margin-top: 6px;
  color: #d1ffb3;
  font-family: var(--mono);
  font-size: 7.5px;
  font-style: normal;
}

.metric-meter {
  height: 4px;
  margin-top: 11px;
  overflow: hidden;
  border-radius: 5px;
  background: rgba(255, 255, 255, 0.09);
}

.metric-meter span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, var(--accent-bright), var(--lime));
  box-shadow: 0 0 14px rgba(92, 255, 193, 0.45);
  transition: width 0.45s ease;
}

.metric-cpu .metric-foot {
  color: #8eae9f;
}

.metric-cpu .metric-foot strong {
  color: #d6ffd6;
  font-weight: 500;
}

.metric-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 10px;
  color: var(--text-muted);
  font-size: 9px;
}

.metric-foot strong {
  color: var(--text-secondary);
  font-weight: 500;
}

.queue-main {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  margin-top: 20px;
}

.queue-main strong {
  color: var(--text);
  font-family: var(--font-display);
  font-size: 36px;
  font-weight: 650;
  line-height: 0.9;
  letter-spacing: -0.06em;
}

.queue-main > span:not(.queue-waiting) {
  margin-bottom: 2px;
  color: var(--text-muted);
  font-size: 9px;
}

.queue-main em {
  margin-bottom: 2px;
  color: var(--amber);
  font-family: var(--mono);
  font-size: 18px;
  font-style: normal;
}

.queue-waiting {
  margin-left: auto;
  padding: 4px 7px;
  border-radius: 6px;
  background: var(--amber-soft);
  color: var(--amber);
  font-family: var(--mono);
  font-size: 7px;
}

.queue-strip {
  height: 6px;
  margin-top: 15px;
}

.queue-strip-seg {
  min-width: 3px;
}

.metric-host .host-main {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-top: 19px;
}

.metric-host .host-main strong {
  color: var(--text);
  font-family: var(--font-display);
  font-size: 35px;
  line-height: 1;
  letter-spacing: -0.06em;
}

.metric-host .host-main span {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 9px;
}

.metric-host .host-bars {
  display: flex;
  align-items: flex-end;
  gap: 3px;
  height: 32px;
  margin-top: 10px;
}

.metric-host .host-bars i {
  width: 6px;
  background: color-mix(in srgb, var(--copper) 24%, var(--border));
  opacity: 0.7;
  transition: height 0.45s ease, opacity 0.2s ease, background 0.2s ease;
}

.metric-host .host-bars i.on {
  background: var(--copper);
  opacity: 0.82;
}

.metric-host .host-bars i:nth-child(6n) {
  opacity: 0.84;
}

.metric-host .host-bars i:nth-child(n+8) {
  opacity: 0.72;
}

.metric-host .host-bars i.on:nth-child(n+8) {
  opacity: 0.9;
}

.host-scale {
  display: flex;
  justify-content: space-between;
  margin-top: 8px;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 7px;
}

.copper-copy {
  color: var(--copper) !important;
}

.overview-main {
  grid-template-columns: minmax(0, 1.68fr) minmax(300px, 0.92fr);
  gap: 15px;
}

.topology-panel {
  border-color: rgba(11, 59, 40, 0.18);
  background:
    radial-gradient(500px 260px at 100% 0%, rgba(83, 240, 179, 0.11), transparent 68%),
    linear-gradient(145deg, #09271b 0%, #051a11 58%, #04130c 100%);
  box-shadow:
    0 1px 0 rgba(255, 255, 255, 0.065) inset,
    0 28px 64px -40px rgba(3, 27, 15, 0.68);
}

.topology-panel::before {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(154, 255, 215, 0.032) 1px, transparent 1px),
    linear-gradient(90deg, rgba(154, 255, 215, 0.032) 1px, transparent 1px);
  background-size: 30px 30px;
  mask-image: radial-gradient(circle at 55% 0%, black, transparent 75%);
}

.topology-panel .panel-head {
  position: relative;
  z-index: 1;
  border-bottom-color: rgba(255, 255, 255, 0.075);
}

.topology-panel .panel-head h2 {
  color: #e9fff6;
}

.topology-panel .panel-head .panel-caption {
  color: #6d8d7d;
}

.topology-panel .tag-soft.accent {
  border-color: rgba(111, 255, 204, 0.18);
  background: rgba(66, 225, 164, 0.08);
  color: #91ffd7;
}

.topology-stage {
  position: relative;
  z-index: 1;
  min-height: 300px;
  padding: 18px 18px 22px;
}

.topology-hub {
  position: relative;
  z-index: 3;
  width: 180px;
  margin: 0 auto;
  padding: 10px 12px;
  border: 1px solid rgba(126, 255, 204, 0.22);
  border-radius: 11px;
  background:
    radial-gradient(circle at 50% 0%, rgba(76, 240, 178, 0.18), transparent 65%),
    #09251a;
  color: #e4fff3;
  text-align: center;
  box-shadow: 0 0 42px -18px rgba(63, 230, 169, 0.95);
}

.topology-hub::after {
  content: '';
  position: absolute;
  top: 100%;
  left: 50%;
  width: 1px;
  height: 28px;
  background: linear-gradient(180deg, rgba(83, 231, 172, 0.8), rgba(83, 231, 172, 0.15));
}

.hub-label {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  color: #6e9a85;
  font-family: var(--mono);
  font-size: 6.5px;
  font-weight: 600;
  letter-spacing: 0.08em;
}

.hub-label i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--lime);
  box-shadow: 0 0 8px var(--lime);
}

.topology-hub strong {
  display: block;
  margin-top: 3px;
  font-family: var(--font-display);
  font-size: 14px;
  font-weight: 560;
  letter-spacing: -0.02em;
}

.topology-hub small {
  display: block;
  margin-top: 2px;
  color: #6e9a85;
  font-family: var(--mono);
  font-size: 6.5px;
}

.hub-version {
  display: inline-block;
  margin-top: 5px;
  padding: 2px 5px;
  border-radius: 4px;
  background: rgba(79, 240, 181, 0.1);
  color: #8bffd6;
  font-family: var(--mono);
  font-size: 6px;
}

.topology-bus {
  position: relative;
  height: 1px;
  margin: 28px 8% 0;
  background: linear-gradient(90deg, transparent, rgba(91, 225, 170, 0.48) 12%, rgba(91, 225, 170, 0.48) 88%, transparent);
}

.topology-bus::before {
  content: '';
  position: absolute;
  top: 0;
  left: 50%;
  width: 1px;
  height: 0;
}

.topology-nodes {
  position: relative;
  z-index: 2;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(164px, 1fr));
  gap: 11px;
  margin-top: 27px;
}

.topology-node {
  position: relative;
  min-width: 0;
  padding: 10px;
  border: 1px solid rgba(120, 255, 204, 0.15);
  border-radius: 10px;
  background:
    linear-gradient(150deg, rgba(255, 255, 255, 0.055), rgba(255, 255, 255, 0.014));
  box-shadow:
    0 1px 0 rgba(255, 255, 255, 0.04) inset,
    0 20px 30px -28px rgba(0, 0, 0, 0.9);
  transition: border-color 0.2s ease, background 0.2s ease;
}

.topology-node::before {
  content: '';
  position: absolute;
  top: -28px;
  left: 50%;
  width: 1px;
  height: 28px;
  background: linear-gradient(180deg, rgba(91, 225, 170, 0.48), rgba(91, 225, 170, 0.1));
}

.topology-node.online:hover {
  border-color: rgba(113, 255, 207, 0.32);
  background:
    linear-gradient(150deg, rgba(255, 255, 255, 0.075), rgba(255, 255, 255, 0.018));
}

.topology-node.offline {
  opacity: 0.53;
}

.topology-node.offline::before {
  background: linear-gradient(180deg, rgba(150, 180, 164, 0.22), transparent);
}

.topology-node-head {
  display: flex;
  align-items: center;
  gap: 7px;
}

.topology-node-glyph {
  display: grid;
  width: 21px;
  height: 21px;
  flex: none;
  place-items: center;
  border: 1px solid rgba(106, 248, 193, 0.18);
  border-radius: 6px;
  background: rgba(77, 235, 174, 0.08);
  color: #8effd6;
  font-size: 11px;
}

.topology-node-head > div {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.topology-node-head strong {
  overflow: hidden;
  color: #d9f4e8;
  font-family: var(--mono);
  font-size: 9px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.topology-node-head > div span {
  overflow: hidden;
  color: #587a6a;
  font-family: var(--mono);
  font-size: 6px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.topology-status {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  color: #5ee4ac;
  font-family: var(--mono);
  font-size: 6px;
  letter-spacing: 0.07em;
}

.topology-status i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: currentColor;
}

.topology-status.offline {
  color: #a08071;
}

.topology-node-meter {
  margin-top: 9px;
}

.topology-node-meter > div:first-child {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 5px;
  color: #648676;
  font-family: var(--mono);
  font-size: 6.5px;
}

.topology-node-meter b {
  color: #a9d3c0;
  font-weight: 500;
}

.topology-node-meter .meter {
  height: 3px;
  margin-top: 5px;
  background: rgba(255, 255, 255, 0.075);
  box-shadow: none;
}

.topology-node-meter .meter span {
  background: linear-gradient(90deg, #2bce92, #9dffc6);
  box-shadow: 0 0 10px rgba(72, 238, 169, 0.35);
}

.topology-node.offline .topology-node-meter .meter span {
  width: 0 !important;
}

.topology-tasks {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-height: 43px;
  margin-top: 8px;
}

.topology-task {
  display: flex;
  align-items: center;
  gap: 5px;
  width: 100%;
  min-width: 0;
  padding: 5px 6px;
  border: 1px solid rgba(255, 255, 255, 0.055);
  border-radius: 5px;
  background: rgba(255, 255, 255, 0.035);
  color: #a9cbb9;
  cursor: pointer;
  text-align: left;
  transition: background 0.2s ease, border-color 0.2s ease;
}

.topology-task:hover {
  border-color: rgba(105, 239, 185, 0.2);
  background: rgba(77, 235, 174, 0.07);
}

.topology-task i {
  width: 4px;
  height: 4px;
  flex: none;
  border-radius: 50%;
  background: var(--accent-bright);
  box-shadow: 0 0 6px var(--accent-bright);
}

.topology-task span {
  overflow: hidden;
  color: #b1cfbf;
  font-family: var(--mono);
  font-size: 7.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.topology-task b {
  margin-left: auto;
  color: #d1ffb3;
  font-family: var(--mono);
  font-size: 7px;
  font-weight: 500;
}

.topology-idle,
.topology-task-more {
  padding: 4px 0;
  color: #4f7161;
  font-family: var(--mono);
  font-size: 6.5px;
}

.topology-task-more {
  color: #87a998;
}

.topology-node-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 7px;
  padding-top: 6px;
  border-top: 1px dashed rgba(255, 255, 255, 0.055);
  color: #6b8b7b;
  font-family: var(--mono);
  font-size: 7px;
}

.topology-node-action {
  padding: 0;
  border: 0;
  background: transparent;
  color: #8eb7a3;
  cursor: pointer;
  font-family: var(--mono);
  font-size: 7px;
}

.topology-node-action:hover {
  color: #b6ffdf;
}

.topology-panel .node-policy {
  position: relative;
  z-index: 1;
  border-top-color: rgba(255, 255, 255, 0.075);
  background: rgba(255, 255, 255, 0.018);
  color: #5d8070;
}

.node-policy-legend {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-left: auto;
}

.node-policy-legend i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--accent-bright);
}

.node-policy-legend i.idle {
  background: #5e7b6b;
}

@media (max-width: 1180px) {
  .metric-deck {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .metric-cpu {
    grid-column: span 2;
  }

  .metric-host {
    grid-column: auto;
  }

  .overview-main {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 760px) {
  .metric-deck {
    grid-template-columns: minmax(0, 1fr);
  }

  .metric-cpu {
    grid-column: span 1;
  }

  .metric-host {
    grid-column: span 1;
  }

  .metric {
    min-height: 150px;
  }

  .topology-stage {
    padding: 15px 12px 16px;
  }

  .topology-hub {
    width: 170px;
  }

  .topology-nodes {
    grid-template-columns: minmax(0, 1fr);
  }

  .topology-node::before,
  .topology-bus {
    display: none;
  }

  .node-policy-legend {
    display: none;
  }
}
</style>

<style scoped>
.overview-page {
  gap: 24px;
}

.cluster-alert {
  border-radius: var(--radius-sm);
}

.alert-copy {
  font-size: 14px;
}

.sync-chip {
  height: 40px;
  padding: 0 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--panel);
  color: var(--text-muted);
  font-family: var(--font);
  font-size: 13px;
}

.sync-chip .el-icon {
  font-size: 15px;
}

.title-live {
  height: 24px;
  padding: 0 8px;
  border-radius: 4px;
  font-family: var(--font);
  font-size: 12px;
  letter-spacing: 0;
}

.page-signals {
  gap: 8px 16px;
  margin-top: 4px;
  font-size: 13px;
}

.page-signals span {
  padding: 0;
  border: 0;
  background: transparent;
  font-size: 13px;
}

.metric-deck {
  gap: 16px;
}

.metric {
  min-height: 184px;
  padding: 18px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--panel);
  box-shadow: none;
  transition: none;
}

.metric:hover {
  transform: none;
  box-shadow: none;
}

.metric-cpu {
  border-color: var(--border);
  background: var(--panel);
  box-shadow: none;
  color: var(--text);
}

.metric-cpu::before,
.metric-cpu::after {
  display: none;
}

.metric-kicker {
  gap: 9px;
  color: var(--text-secondary);
  font-family: var(--font);
  font-size: 13px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
}

.metric-tag {
  padding: 2px 7px;
  border-radius: 4px;
  font-family: var(--font);
  font-size: 12px;
  letter-spacing: 0;
}

.metric-cpu .metric-tag {
  border-color: var(--border);
  background: var(--panel-soft);
  color: var(--text-muted);
}

.metric-icon {
  width: 30px;
  height: 30px;
  border-radius: 6px;
}

.metric-cpu-main {
  gap: 18px;
  margin-top: 16px;
}

.cpu-ring {
  width: 82px;
  height: 82px;
  box-shadow: none;
}

.cpu-ring::after {
  inset: 7px;
}

.cpu-ring-inner strong {
  color: var(--text);
  font-size: 21px;
}

.cpu-ring-inner span {
  color: var(--text-muted);
  font-size: 12px;
}

.cpu-ring-copy strong {
  color: var(--text);
  font-size: 40px;
  font-weight: 700;
  letter-spacing: -0.04em;
}

.cpu-ring-copy span {
  color: var(--text-secondary);
  font-family: var(--font);
  font-size: 13px;
  letter-spacing: 0;
}

.cpu-ring-copy em {
  color: var(--text-muted);
  font-size: 12px;
}

.metric-cpu .metric-meter {
  margin-top: 14px;
  background: var(--panel-soft);
}

.metric-cpu .metric-meter span {
  background: var(--accent);
}

.metric-cpu .metric-foot {
  margin-top: 11px;
  color: var(--text-muted);
  font-size: 12px;
}

.metric-cpu .metric-foot strong {
  color: var(--text-secondary);
}

.queue-main {
  gap: 7px;
  margin-top: 18px;
}

.queue-main strong {
  font-size: 44px;
  letter-spacing: -0.05em;
}

.queue-main span {
  font-size: 14px;
}

.queue-main em {
  font-size: 18px;
}

.queue-waiting {
  padding: 2px 7px;
  border-radius: 4px;
  font-family: var(--font);
  font-size: 12px;
  letter-spacing: 0;
}

.queue-strip {
  height: 6px;
  margin-top: 16px;
}

.metric-foot {
  margin-top: 14px;
  color: var(--text-muted);
  font-size: 12px;
}

.host-main {
  gap: 8px;
  margin-top: 18px;
}

.host-main strong {
  font-size: 44px;
  letter-spacing: -0.05em;
}

.host-main span {
  font-family: var(--font);
  font-size: 13px;
  letter-spacing: 0;
}

.host-bars {
  height: 32px;
  gap: 4px;
  margin-top: 13px;
}

.metric-host .host-bars i {
  width: 6px;
}

.host-scale {
  margin-top: 9px;
  font-family: var(--font);
  font-size: 12px;
}

.copper-copy {
  font-size: 12px;
}

.topology-panel {
  border-color: #23372b;
  border-radius: var(--radius);
  background: #0c1a13;
  box-shadow: none;
}

.topology-panel::before {
  display: none;
}

.topology-panel .panel-head {
  border-bottom-color: rgba(255, 255, 255, 0.09);
}

.topology-panel .panel-head h2 {
  color: #eef7f1;
  font-size: 18px;
}

.topology-panel .panel-caption {
  color: #8ca797;
  font-size: 14px;
}

.topology-panel .tag-soft {
  border-color: rgba(75, 195, 148, 0.24);
  background: rgba(75, 195, 148, 0.08);
  color: #9fe0c3;
  font-size: 12px;
}

.topology-panel .el-button {
  border-color: rgba(255, 255, 255, 0.14);
  background: rgba(255, 255, 255, 0.04);
  color: #dce9e1;
}

.topology-stage {
  padding: 24px 20px 18px;
  background-image:
    linear-gradient(rgba(147, 184, 164, 0.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(147, 184, 164, 0.045) 1px, transparent 1px);
  background-size: 32px 32px;
}

.topology-hub {
  min-width: 260px;
  padding: 14px 20px;
  border: 1px solid rgba(122, 198, 163, 0.3);
  border-radius: 8px;
  background: #11231a;
  box-shadow: none;
}

.hub-label {
  font-family: var(--font);
  font-size: 11px;
  letter-spacing: 0;
  text-transform: none;
}

.topology-hub strong {
  margin-top: 4px;
  font-size: 19px;
}

.topology-hub small {
  margin-top: 3px;
  font-size: 12px;
}

.hub-version {
  margin-top: 8px;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 11px;
}

.topology-bus {
  height: 20px;
}

.topology-nodes {
  gap: 12px;
  margin-top: 0;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}

.topology-node {
  min-width: 0;
  padding: 14px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  background: #0f1f17;
  box-shadow: none;
}

.topology-node.online:hover {
  border-color: rgba(255, 255, 255, 0.1);
  background: #0f1f17;
}

.topology-node-head {
  gap: 9px;
}

.topology-node-head > div {
  min-width: 0;
}

.topology-node-head strong {
  font-size: 14px;
}

.topology-node-head > div span {
  font-size: 12px;
}

.topology-node-glyph {
  width: 26px;
  height: 26px;
  border-radius: 5px;
}

.topology-status {
  flex: none;
  padding: 2px 6px;
  border-radius: 3px;
  font-family: var(--font);
  font-size: 11px;
  letter-spacing: 0;
  white-space: nowrap;
}

.topology-node-meter .meter span {
  background: #5fd0a5;
  box-shadow: none;
}

.topology-node-meter {
  margin-top: 13px;
}

.topology-node-meter div:first-child {
  margin-bottom: 6px;
  font-size: 12px;
}

.topology-node-meter b {
  font-size: 12px;
}

.topology-tasks {
  gap: 5px;
  min-height: 58px;
  margin-top: 11px;
  padding-top: 10px;
}

.topology-task {
  min-height: 30px;
  padding: 4px 7px;
  border-radius: 4px;
  font-size: 12px;
}

.topology-task span {
  font-size: 12px;
}

.topology-task b {
  font-size: 12px;
}

.topology-task-more,
.topology-idle {
  padding: 5px 1px;
  font-size: 12px;
}

.topology-node-foot {
  margin-top: 10px;
  font-size: 12px;
}

.topology-node-action {
  font-size: 12px;
}

.node-policy {
  padding: 12px 20px 14px;
  font-size: 12px;
}

.service-body {
  padding: 16px 20px 18px;
}

.service-row {
  padding: 11px 0;
}

.service-symbol {
  width: 30px;
  height: 30px;
  border-radius: 6px;
  font-size: 12px;
}

.service-copy strong {
  font-size: 14px;
}

.service-copy span {
  font-size: 12px;
}

.service-state {
  font-family: var(--font);
  font-size: 12px;
  letter-spacing: 0;
}

.quota-box {
  margin-top: 14px;
  padding: 12px;
  border-radius: 6px;
}

.quota-num {
  font-size: 12px;
}

.quota-foot code {
  font-size: 12px;
}

.quota-foot {
  margin-top: 9px;
  font-size: 12px;
}

.service-foot-row {
  margin-top: 13px;
  font-size: 12px;
}

.status-distribution {
  display: flex;
  flex-direction: column;
  gap: 13px;
  padding: 18px 20px;
}

.dist-label {
  font-size: 13px;
}

.dist-row strong {
  font-size: 13px;
}

.recent-table :deep(td) {
  font-size: 14px;
}

.recent-task strong {
  font-size: 14px;
}

.recent-task .mono {
  font-size: 12px;
}

.recent-glyph {
  width: 30px;
  height: 30px;
  border-radius: 6px;
}

.task-count-pill {
  font-size: 12px;
}

.recent-table :deep(.el-table__cell) {
  font-size: 14px;
}

@media (max-width: 1180px) {
  .metric {
    min-height: 170px;
  }
}

@media (max-width: 760px) {
  .metric {
    padding: 16px;
  }

  .host-main strong,
  .queue-main strong {
    font-size: 38px;
  }

  .topology-stage {
    padding: 18px 14px 14px;
  }

  .topology-hub {
    min-width: 0;
    width: 100%;
  }

  .topology-node-head strong {
    font-size: 14px;
  }

  .node-policy {
    line-height: 1.8;
  }
}
</style>

<style scoped>
.overview-page .panel-caption {
  font-size: 14px;
}

.queue-main > span:not(.queue-waiting) {
  font-size: 14px;
}

.metric-host .host-main span {
  font-family: var(--font);
  font-size: 13px;
}

.quota-foot strong {
  font-family: var(--font);
  font-size: 12px;
}

.recent-table .small,
.recent-table .mono.muted {
  font-size: 12px;
}

.recent-table :deep(.el-table__cell) {
  font-size: 14px;
}
</style>

<style scoped>
.benchmark-intro {
  color: var(--text-muted);
  font-size: 13px;
  line-height: 1.7;
}

.benchmark-form {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin: 16px 0;
}

.benchmark-form :deep(.el-input-number) {
  width: 100%;
}

.benchmark-form .el-button {
  grid-column: 1 / -1;
  justify-self: start;
}

.benchmark-status {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-top: 10px;
  color: var(--text-muted);
  font-size: 13px;
}

.benchmark-status strong {
  color: var(--text);
}

.benchmark-error {
  color: var(--red);
}

.benchmark-selected {
  margin-top: 10px;
  padding: 10px 12px;
  border: 1px solid var(--accent-line);
  border-radius: 6px;
  background: var(--accent-soft);
  color: var(--accent-strong);
  font-size: 13px;
}

.benchmark-table {
  margin-top: 16px;
}

.benchmark-failed {
  color: var(--red);
  font-size: 12px;
}

@media (max-width: 760px) {
  .benchmark-form {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
