<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  ArrowRight,
  Coin,
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
import type { NodeTopologyItem } from '@/types/p2'
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
const compact = ref(false)
let timer: number | undefined

function syncViewport() {
  compact.value = window.innerWidth < 1120
}

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

function openTask(task: Task) {
  router.push({ name: 'task-detail', params: { id: task.id } })
}

onMounted(async () => {
  syncViewport()
  window.addEventListener('resize', syncViewport)
  await load()
  timer = window.setInterval(() => load(true), 3000)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', syncViewport)
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <div class="page overview-page">
    <div class="page-head">
      <div class="page-heading">
        <span class="eyebrow">Cluster overview</span>
        <div class="title-row">
          <h1 class="page-title">集群总览</h1>
          <span class="title-live"><i></i>LIVE</span>
        </div>
        <p class="page-subtitle">
          查看逻辑 CPU 配额、队列压力与 Ray 执行器状态。资源释放后，等待中的任务会自动被调度。
        </p>
        <div class="page-signals">
          <span><i class="signal-dot"></i>{{ onlineNodes.length }} nodes online</span>
          <span>Ray {{ services?.ray_cpus ?? '—' }} CPU</span>
          <span>{{ overview?.tasks.pending ?? 0 }} queued</span>
          <span class="mono">uptime {{ formatDuration(overview?.uptime_seconds ?? 0) }}</span>
        </div>
      </div>
      <div class="page-actions">
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
      <section class="grid cols-4 stat-grid">
        <article class="panel stat-card accent-card">
          <div class="stat-top">
            <span class="stat-icon"><el-icon><Cpu /></el-icon></span>
            <span class="stat-kicker">CPU 调度配额</span>
            <span class="stat-trend">{{ formatPercent(cpu?.utilization_percent) }}</span>
          </div>
          <div class="stat-value-row">
            <strong class="metric-value">{{ cpu?.allocated ?? 0 }}</strong>
            <span class="stat-denom">/ {{ cpu?.total ?? 0 }} <small>cores</small></span>
          </div>
          <div class="meter accent">
            <span :style="{ width: `${cpu?.utilization_percent ?? 0}%` }"></span>
          </div>
          <div class="stat-foot">
            <span>{{ cpu?.available ?? 0 }} 核可调度</span>
            <span class="stat-inline"><i></i>进程 {{ formatPercent(cpu?.process_percent) }}</span>
          </div>
        </article>

        <article class="panel stat-card">
          <div class="stat-top">
            <span class="stat-icon amber"><el-icon><Odometer /></el-icon></span>
            <span class="stat-kicker">任务队列</span>
            <span class="stat-trend neutral">实时</span>
          </div>
          <div class="stat-value-row">
            <strong class="metric-value">{{ overview.tasks.running }}</strong>
            <span class="stat-denom"><small>运行中</small></span>
            <span class="stat-separator">·</span>
            <strong class="metric-value queue-count">{{ overview.tasks.pending }}</strong>
            <span class="stat-denom"><small>排队</small></span>
          </div>
          <div class="queue-strip">
            <span
              v-for="row in statusRows"
              :key="row.key"
              class="queue-strip-seg"
              :style="{ flexGrow: Math.max(0.14, row.value), background: row.color }"
            ></span>
          </div>
          <div class="stat-foot">
            <span>累计 {{ overview.tasks.total }} 个任务</span>
            <span class="stat-inline">{{ overview.tasks.succeeded }} 成功</span>
          </div>
        </article>

        <article class="panel stat-card">
          <div class="stat-top">
            <span class="stat-icon violet"><el-icon><Monitor /></el-icon></span>
            <span class="stat-kicker">主机 CPU 负载</span>
            <span class="stat-trend neutral">{{ cpu?.host_logical_cores ?? '—' }} threads</span>
          </div>
          <div class="host-load-row">
            <strong class="metric-value">{{ formatPercent(cpu?.host_percent) }}</strong>
            <div class="host-bars" aria-hidden="true">
              <i
                v-for="idx in 12"
                :key="idx"
                :class="{ on: (cpu?.host_percent ?? 0) >= idx * 8.3 }"
              ></i>
            </div>
          </div>
          <div class="meter violet">
            <span :style="{ width: `${cpu?.host_percent ?? 0}%` }"></span>
          </div>
          <div class="stat-foot">
            <span>主机真实占用</span>
            <span class="stat-inline">逻辑核 {{ cpu?.host_logical_cores ?? '—' }}</span>
          </div>
        </article>

        <article class="panel stat-card">
          <div class="stat-top">
            <span class="stat-icon blue"><el-icon><Coin /></el-icon></span>
            <span class="stat-kicker">主机内存</span>
            <span class="stat-trend neutral">{{ memoryGB.total }} GB</span>
          </div>
          <div class="stat-value-row">
            <strong class="metric-value">{{ formatPercent(overview.memory.percent, 0) }}</strong>
            <span class="stat-denom"><small>已使用</small></span>
          </div>
          <div class="meter blue">
            <span :style="{ width: `${overview.memory.percent}%` }"></span>
          </div>
          <div class="stat-foot">
            <span>{{ memoryGB.used }} / {{ memoryGB.total }} GB</span>
            <span class="stat-inline">{{ memoryGB.available }} GB 可用</span>
          </div>
        </article>
      </section>

      <section class="grid overview-main">
        <div class="panel nodes-panel">
          <div class="panel-head">
            <div>
              <h2><span class="head-index">01</span> 节点资源</h2>
              <p class="panel-caption">CPU 配额按逻辑核心分配，表中同时展示主机真实负载</p>
            </div>
            <div class="panel-head-actions">
              <span class="tag-soft accent"><span class="dot"></span>{{ onlineNodes.length }} online</span>
              <el-button size="small" :icon="Share" @click="openTopologyDialog">多节点模拟</el-button>
            </div>
          </div>

          <el-table :data="overview.nodes" class="node-table" row-key="id">
            <el-table-column label="节点" :min-width="compact ? 140 : 170">
              <template #default="{ row }">
                <div class="node-name">
                  <span class="node-glyph"><el-icon><Cpu /></el-icon></span>
                  <div>
                    <strong>{{ row.name }}</strong>
                    <span>{{ row.hostname }} · {{ row.id }}</span>
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="状态" :width="compact ? 64 : 82">
              <template #default="{ row }">
                <span class="node-status" :class="{ online: row.status === 'online' }">
                  <i></i>{{ row.status === 'online' ? '在线' : row.status }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="调度占用" :min-width="compact ? 120 : 150">
              <template #default="{ row }">
                <div class="node-meter-cell">
                  <div class="row between node-meter-top">
                    <span class="mono">{{ row.allocated_cpus }} / {{ row.total_cpus }} 核</span>
                    <strong>{{ formatPercent(row.cpu_utilization_percent, 0) }}</strong>
                  </div>
                  <div class="meter compact">
                    <span :style="{ width: `${row.cpu_utilization_percent}%` }"></span>
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column v-if="!compact" label="主机负载" min-width="135">
              <template #default="{ row }">
                <div class="node-meter-cell">
                  <div class="row between node-meter-top">
                    <span class="mono">{{ formatPercent(row.host_percent) }}</span>
                  </div>
                  <div class="meter compact violet">
                    <span :style="{ width: `${row.host_percent}%` }"></span>
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column v-if="!compact" label="运行任务" width="84" align="center">
              <template #default="{ row }">
                <span class="task-count-pill">{{ row.running_tasks }}</span>
              </template>
            </el-table-column>
            <el-table-column v-if="!compact" label="操作" width="76" align="center">
              <template #default="{ row }">
                <el-button
                  size="small"
                  text
                  :loading="nodeActionBusy === row.id"
                  @click="toggleNode(row)"
                >
                  {{ row.status === 'online' ? '下线' : '上线' }}
                </el-button>
              </template>
            </el-table-column>
          </el-table>

          <div class="node-policy">
            <span><el-icon><Timer /></el-icon> 心跳超时自动回收</span>
            <span>调度粒度为 1 CPU 核</span>
            <span>Ray 逻辑资源 {{ services?.ray_cpus ?? '—' }} CPU</span>
          </div>
        </div>

        <div class="side-stack">
          <div class="panel services-panel">
            <div class="panel-head">
              <h2><span class="head-index">02</span> 服务与配额</h2>
              <span class="tag-soft" :class="{ accent: services?.ray_connected && services?.redis_connected }">
                <span class="dot"></span>control plane
              </span>
            </div>
            <div class="panel-body service-body">
              <div class="service-row">
                <div class="service-symbol ray"><span>R</span></div>
                <div class="service-copy">
                  <strong>Ray execution</strong>
                  <span>{{ services?.ray_address }} · v{{ services?.ray_version ?? '—' }}</span>
                </div>
                <span class="service-state" :class="{ ok: services?.ray_connected }">
                  {{ services?.ray_connected ? 'CONNECTED' : 'LOCAL FALLBACK' }}
                </span>
              </div>
              <div class="service-row">
                <div class="service-symbol redis"><span>R</span></div>
                <div class="service-copy">
                  <strong>Redis coordination</strong>
                  <span>scheduler lock · pub/sub events · v{{ services?.redis_version ?? '—' }}</span>
                </div>
                <span class="service-state" :class="{ ok: services?.redis_connected }">
                  {{ services?.redis_connected ? 'CONNECTED' : 'DEGRADED' }}
                </span>
              </div>

              <div class="quota-box">
                <div class="row between">
                  <span class="soft-label">Single user quota</span>
                  <span class="mono quota-num">
                    {{ quota?.used_cpus ?? 0 }} / {{ quota?.limit_cpus ?? 0 }} CPU
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
                <span><el-icon><DataLine /></el-icon> 调度器 {{ services?.execution_mode === 'auto' ? 'auto' : services?.execution_mode }}</span>
                <span><el-icon><Timer /></el-icon> 服务运行 {{ formatDuration(overview.uptime_seconds) }}</span>
              </div>
            </div>
          </div>

          <div class="panel queue-panel">
            <div class="panel-head">
              <h2><span class="head-index">03</span> 状态分布</h2>
              <span class="panel-caption">all time</span>
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
            <h2><span class="head-index">04</span> 最近任务</h2>
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
              <span class="mono muted small">{{ row.epochs }}ep · {{ row.imgsz }}px · {{ row.batch_size }}bs</span>
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
            把平台的 <strong>{{ cpuBudget }} CPU</strong> 预算拆成多个逻辑节点，调度器会按节点空闲容量与负载分配任务。
          </p>
          <p>任务仍在本机 Ray 集群执行；该功能用于演示多节点资源账本、节点亲和与容量碎片化。</p>
        </div>
        <div class="topology-rows">
          <div v-for="(node, index) in topologyDraft" :key="index" class="topology-row">
            <span class="topology-index mono">{{ String(index + 1).padStart(2, '0') }}</span>
            <el-input v-model="node.name" placeholder="节点名称" maxlength="60" />
            <el-input-number v-model="node.total_cpus" :min="1" :max="cpuBudget" controls-position="right" />
            <span class="topology-unit">CPU</span>
            <el-button text :disabled="topologyDraft.length <= 1" @click="removeTopologyRow(index)">
              移除
            </el-button>
          </div>
        </div>
        <el-button text :icon="Plus" @click="addTopologyRow">添加节点</el-button>
        <div class="topology-summary">
          <span>模拟总容量</span>
          <strong class="mono" :class="{ overloaded: topologyTotal > cpuBudget }">
            {{ topologyTotal }} / {{ cpuBudget }} CPU
          </strong>
        </div>
        <template #footer>
          <el-button @click="topologyOpen = false">取消</el-button>
          <el-button type="primary" :loading="topologyBusy" @click="applyTopology">
            应用拓扑
          </el-button>
        </template>
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
  background: linear-gradient(90deg, var(--violet), #a59bff);
}

.meter.blue span {
  background: linear-gradient(90deg, #4f8dd6, #77b4f2);
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
