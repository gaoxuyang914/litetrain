<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowLeft,
  CircleCheck,
  CircleClose,
  Clock,
  Connection,
  CopyDocument,
  Cpu,
  Download,
  Loading,
  Odometer,
  Refresh,
  Timer,
  WarningFilled,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { api, logDownloadUrl } from '@/api/client'
import LogTerminal from '@/components/LogTerminal.vue'
import MetricsPanel from '@/components/MetricsPanel.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { useNotifications } from '@/composables/useNotifications'
import type { Task, TaskEvent, TaskMetrics } from '@/types/task'
import {
  copyText,
  formatBytes,
  formatDate,
  formatDuration,
  formatFullDate,
  isTerminal,
  statusMeta,
  timeAgo,
} from '@/utils/format'

const route = useRoute()
const router = useRouter()
const { notify } = useNotifications()

const task = ref<Task | null>(null)
const metrics = ref<TaskMetrics | null>(null)
const events = ref<TaskEvent[]>([])
const loading = ref(true)
const refreshing = ref(false)
const error = ref<string | null>(null)
const cancelling = ref(false)
const metricsError = ref<string | null>(null)
const logsSection = ref<HTMLElement | null>(null)
let pollTimer: number | undefined
let requestSeq = 0

const taskId = computed(() => String(route.params.id || ''))
const terminal = computed(() => (task.value ? isTerminal(task.value.status) : false))
const statusLabel = computed(() => (task.value ? statusMeta[task.value.status].label : ''))
const progressValue = computed(() => {
  if (!task.value) return 0
  if (task.value.status === 'succeeded') return 100
  return Math.max(0, Math.min(100, task.value.progress))
})
const isPending = computed(() => task.value?.status === 'pending')
const isRunning = computed(() => task.value?.status === 'running')

const statusIcon = computed(() => {
  if (!task.value) return Clock
  if (task.value.status === 'running') return Loading
  if (task.value.status === 'succeeded') return CircleCheck
  if (task.value.status === 'failed') return CircleClose
  if (task.value.status === 'cancelled') return CircleClose
  return Clock
})

const runtimeLabel = computed(() => formatDuration(task.value?.runtime_seconds))
const heartbeatLabel = computed(() =>
  task.value?.last_heartbeat_at ? timeAgo(task.value.last_heartbeat_at) : '—',
)

const summaryStats = computed(() => {
  if (!task.value) return []
  return [
    {
      label: 'CPU 配额',
      value: `${task.value.cpu_cores}`,
      unit: 'cores',
      hint: `优先级 ${task.value.priority}`,
      icon: Cpu,
    },
    {
      label: isRunning.value ? '已运行' : '运行耗时',
      value: runtimeLabel.value,
      unit: '',
      hint: task.value.started_at ? `开始 ${timeAgo(task.value.started_at)}` : '尚未开始',
      icon: Timer,
    },
    {
      label: '执行尝试',
      value: `${task.value.attempt}`,
      unit: `/ ${task.value.max_retries + 1}`,
      hint: task.value.retry_count ? `已自动重试 ${task.value.retry_count} 次` : '首次尝试',
      icon: Refresh,
    },
    {
      label: '执行器',
      value: task.value.executor ?? (isPending.value ? 'queued' : '—'),
      unit: '',
      hint: task.value.pid ? `pid ${task.value.pid}` : '调度中',
      icon: Connection,
    },
  ]
})

function statusHint(taskValue: Task): string {
  if (taskValue.status === 'pending') {
    return taskValue.queue_position
      ? `正在队列中等待 · 排队位置 #${taskValue.queue_position}`
      : '正在等待调度器分配 CPU 资源'
  }
  if (taskValue.status === 'running') {
    return `worker 正在 CPU 上训练 · 最近心跳 ${heartbeatLabel.value}`
  }
  if (taskValue.status === 'succeeded') {
    return `训练成功完成 · 总耗时 ${formatDuration(taskValue.runtime_seconds)}`
  }
  if (taskValue.status === 'failed') {
    return taskValue.failure_reason || '训练失败'
  }
  return '任务已取消，CPU 资源已释放'
}

function eventTone(level: string, eventType: string): string {
  if (eventType === 'succeeded') return 'success'
  if (eventType === 'failed') return 'error'
  if (eventType === 'cancelled') return 'cancelled'
  if (eventType === 'retry_scheduled') return 'warning'
  if (eventType === 'scheduled' || eventType === 'started') return 'accent'
  return level === 'warning' ? 'warning' : 'neutral'
}

function eventTypeLabel(type: string): string {
  const labels: Record<string, string> = {
    submitted: '提交',
    scheduled: '调度',
    recovered: '恢复',
    succeeded: '完成',
    failed: '失败',
    cancelled: '取消',
    retry_scheduled: '重试',
    worker_launched: '启动',
    started: '开始',
  }
  return labels[type] ?? type.replaceAll('_', ' ')
}

async function loadAll(initial = false) {
  const seq = ++requestSeq
  if (!initial) refreshing.value = true
  try {
    const current = await api.getTask(taskId.value)
    if (seq !== requestSeq) return
    const previous = task.value
    if (previous && previous.status !== current.status) {
      notify(current, previous.status)
    }
    task.value = current
    const [metricData, eventData] = await Promise.all([
      api.getTaskMetrics(taskId.value).catch((err: Error) => {
        metricsError.value = err.message
        return null
      }),
      api.getTaskEvents(taskId.value).catch(() => [] as TaskEvent[]),
    ])
    if (seq !== requestSeq) return
    if (metricData) {
      metrics.value = metricData
      metricsError.value = null
    }
    events.value = eventData
    error.value = null
    if (terminal.value) stopPolling()
  } catch (err) {
    if (initial) {
      error.value = err instanceof Error ? err.message : '无法加载任务详情'
    } else {
      ElMessage.error(err instanceof Error ? err.message : '刷新任务失败')
    }
  } finally {
    if (seq === requestSeq) {
      loading.value = false
      refreshing.value = false
    }
  }
}

async function refreshSilently() {
  if (!task.value || terminal.value || document.hidden) return
  try {
    const current = await api.getTask(taskId.value)
    const previous = task.value
    if (previous.status !== current.status) {
      notify(current, previous.status)
    }
    task.value = current
    if (task.value.status === 'running') {
      const [metricData, eventData] = await Promise.all([
        api.getTaskMetrics(taskId.value).catch(() => null),
        api.getTaskEvents(taskId.value).catch(() => events.value),
      ])
      if (metricData) metrics.value = metricData
      if (eventData) events.value = eventData
    }
    if (terminal.value) {
      stopPolling()
      await loadAll(false)
    }
  } catch {
    // Keep the last known snapshot; the user can retry manually.
  }
}

function startPolling() {
  stopPolling()
  pollTimer = window.setInterval(refreshSilently, 2500)
}

function stopPolling() {
  if (pollTimer) {
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }
}

async function copy(value: string | null | undefined, label = '内容') {
  if (!value) return
  const ok = await copyText(value)
  ElMessage({
    message: ok ? `${label}已复制` : '复制失败',
    type: ok ? 'success' : 'warning',
  })
}

async function cancel() {
  if (!task.value) return
  try {
    await ElMessageBox.confirm(
      '取消后正在运行的 worker 会被终止，CPU 配额立即释放。确定取消这个任务吗？',
      '取消训练任务',
      {
        confirmButtonText: '确认取消',
        cancelButtonText: '返回',
        type: 'warning',
      },
    )
  } catch {
    return
  }
  cancelling.value = true
  try {
    const result = await api.cancelTask(taskId.value)
    task.value = result.task
    ElMessage.success(result.message)
    stopPolling()
    await loadAll(false)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '取消失败')
  } finally {
    cancelling.value = false
  }
}

function downloadLog() {
  window.open(logDownloadUrl(taskId.value), '_blank', 'noopener')
}

function goBack() {
  if (window.history.length > 1) router.back()
  else router.push({ name: 'tasks' })
}

async function scrollToLogsIfRequested() {
  if (route.query.tab !== 'logs') return
  await nextTick()
  logsSection.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

watch(
  () => route.params.id,
  async () => {
    stopPolling()
    loading.value = true
    task.value = null
    metrics.value = null
    events.value = []
    await loadAll(true)
    if (!terminal.value) startPolling()
    await scrollToLogsIfRequested()
  },
)

watch(terminal, (value, oldValue) => {
  if (value && !oldValue) {
    void loadAll(false)
  }
})

onMounted(async () => {
  await loadAll(true)
  if (!terminal.value) startPolling()
  await scrollToLogsIfRequested()
})

onBeforeUnmount(() => stopPolling())
</script>

<template>
  <div class="page detail-page">
    <div class="page-head detail-head">
      <div class="page-heading">
        <button class="back-link" type="button" @click="goBack">
          <el-icon><ArrowLeft /></el-icon>
          返回任务列表
        </button>
        <div class="detail-title-row">
          <span class="eyebrow">Job detail / {{ taskId }}</span>
          <StatusBadge v-if="task" :status="task.status" />
        </div>
        <h1 class="page-title">{{ task?.name ?? '任务详情' }}</h1>
        <div v-if="task" class="detail-subline">
          <button type="button" class="id-copy mono" @click="copy(task.id, '任务 ID')">
            {{ task.id }} <el-icon><CopyDocument /></el-icon>
          </button>
          <span class="dot-separator">·</span>
          <span>owner {{ task.owner }}</span>
          <span class="dot-separator">·</span>
          <span>{{ formatFullDate(task.submitted_at) }}</span>
        </div>
      </div>
      <div class="page-actions">
        <el-button
          :icon="Refresh"
          :loading="refreshing"
          circle
          title="刷新详情"
          @click="loadAll(false)"
        />
        <el-button :icon="Download" @click="downloadLog">下载日志</el-button>
        <el-button
          v-if="task && !terminal"
          type="danger"
          plain
          :icon="CircleClose"
          :loading="cancelling"
          @click="cancel"
        >
          取消任务
        </el-button>
      </div>
    </div>

    <div v-if="loading" class="detail-skeleton">
      <div class="panel skeleton-hero"><el-skeleton :rows="4" animated /></div>
      <div class="grid detail-main-grid">
        <div class="panel skeleton-logs"><el-skeleton :rows="10" animated /></div>
        <div class="panel skeleton-side"><el-skeleton :rows="8" animated /></div>
      </div>
    </div>

    <el-result
      v-else-if="error"
      icon="error"
      title="任务加载失败"
      :sub-title="error"
    >
      <template #extra>
        <el-button type="primary" @click="loadAll(true)">重新加载</el-button>
        <el-button @click="router.push({ name: 'tasks' })">返回列表</el-button>
      </template>
    </el-result>

    <template v-else-if="task">
      <section class="panel job-hero" :class="`state-${task.status}`">
        <div class="hero-top">
          <div class="hero-status-icon" :class="`state-${task.status}`">
            <el-icon :class="{ spinning: task.status === 'running' }">
              <component :is="statusIcon" />
            </el-icon>
          </div>
          <div class="hero-copy">
            <div class="hero-eyebrow">
              <span>{{ statusLabel }}</span>
              <span v-if="task.executor" class="mono">via {{ task.executor }}</span>
            </div>
            <p class="hero-hint" :class="{ failure: task.status === 'failed' }">
              {{ statusHint(task) }}
            </p>
          </div>
          <div class="hero-progress-number">
            <strong>{{ isPending ? `#${task.queue_position ?? '—'}` : `${progressValue.toFixed(1)}%` }}</strong>
            <span>{{ isPending ? 'queue' : 'progress' }}</span>
          </div>
        </div>

        <div class="hero-progress">
          <span
            :class="`state-${task.status}`"
            :style="{ width: `${Math.max(isPending ? 2 : 0.5, progressValue)}%` }"
          ></span>
        </div>

        <div class="hero-stats">
          <div v-for="(item, index) in summaryStats" :key="index" class="hero-stat">
            <span class="hero-stat-icon"><el-icon><component :is="item.icon" /></el-icon></span>
            <div>
              <span class="hero-stat-label">{{ item.label }}</span>
              <div class="hero-stat-value">
                <strong class="mono">{{ item.value }}</strong>
                <small v-if="item.unit">{{ item.unit }}</small>
              </div>
              <span class="hero-stat-hint">{{ item.hint }}</span>
            </div>
          </div>
          <div class="hero-stat">
            <span class="hero-stat-icon"><el-icon><Odometer /></el-icon></span>
            <div>
              <span class="hero-stat-label">运行心跳</span>
              <div class="hero-stat-value">
                <strong class="mono">{{ heartbeatLabel }}</strong>
              </div>
              <span class="hero-stat-hint">
                {{ task.status === 'running' ? '每 4 秒上报 worker 资源' : '训练进程已结束' }}
              </span>
            </div>
          </div>
        </div>

        <div v-if="task.status === 'failed' && task.failure_reason" class="failure-strip">
          <el-icon><WarningFilled /></el-icon>
          <span>{{ task.failure_reason }}</span>
          <button type="button" @click="copy(task.failure_reason, '失败原因')">
            <el-icon><CopyDocument /></el-icon>
          </button>
        </div>
      </section>

      <div class="detail-main-grid">
        <div class="detail-main-column">
          <section id="task-logs" ref="logsSection" class="panel logs-panel">
            <div class="panel-head">
              <div>
                <h2><span class="head-index">01</span> 训练日志</h2>
                <p class="panel-caption">按 offset 增量读取 · SSE 实时推送 · 虚拟滚动渲染</p>
              </div>
              <div class="log-head-meta">
                <span class="tag-soft" :class="{ accent: isRunning }">
                  <span class="dot" :class="{ pulse: isRunning }"></span>
                  {{ isPending ? '等待 worker' : isRunning ? '实时流' : '日志已封存' }}
                </span>
                <span class="mono log-bytes">{{ formatBytes(task.log_size_bytes) }}</span>
              </div>
            </div>
            <div class="log-panel-body">
              <LogTerminal :task-id="task.id" :status="task.status" />
            </div>
          </section>

          <section class="panel config-panel">
            <div class="panel-head">
              <div>
                <h2><span class="head-index">02</span> 执行配置</h2>
                <p class="panel-caption">任务参数会原样传递给 worker / ultralytics</p>
              </div>
              <el-button text :icon="CopyDocument" @click="copy(task.command, '执行命令')">
                复制命令
              </el-button>
            </div>
            <div class="config-body">
              <div class="config-grid">
                <div class="config-item">
                  <span>Model</span>
                  <strong class="mono">{{ task.model }}</strong>
                </div>
                <div class="config-item">
                  <span>Dataset</span>
                  <strong class="mono">{{ task.dataset }}</strong>
                </div>
                <div class="config-item">
                  <span>Epochs</span>
                  <strong class="mono">{{ task.epochs }}</strong>
                </div>
                <div class="config-item">
                  <span>Image size</span>
                  <strong class="mono">{{ task.imgsz }} × {{ task.imgsz }}</strong>
                </div>
                <div class="config-item">
                  <span>Batch</span>
                  <strong class="mono">{{ task.batch_size }}</strong>
                </div>
                <div class="config-item">
                  <span>Workers</span>
                  <strong class="mono">{{ task.workers }}</strong>
                </div>
                <div class="config-item">
                  <span>Device</span>
                  <strong class="mono">{{ task.device }}</strong>
                </div>
                <div class="config-item">
                  <span>Timeout</span>
                  <strong class="mono">{{ formatDuration(task.max_runtime_seconds) }}</strong>
                </div>
              </div>
              <div class="command-full">
                <div class="command-label">
                  <span class="soft-label">Worker command</span>
                  <span class="mono">pid {{ task.pid ?? '—' }}</span>
                </div>
                <code>{{ task.command }}</code>
              </div>
              <div v-if="task.notes" class="notes-box">
                <span class="soft-label">Notes</span>
                <p>{{ task.notes }}</p>
              </div>
            </div>
          </section>
        </div>

        <aside class="detail-side">
          <section class="panel metrics-card">
            <div class="panel-head">
              <div>
                <h2><span class="head-index">03</span> 训练指标</h2>
                <p class="panel-caption">loss / mAP / precision / recall</p>
              </div>
              <span class="tag-soft" :class="{ accent: isRunning }">
                {{ isRunning ? 'updating' : 'snapshot' }}
              </span>
            </div>
            <div class="metrics-body">
              <el-alert
                v-if="metricsError"
                :title="metricsError"
                type="warning"
                :closable="false"
                show-icon
              />
              <MetricsPanel :task="task" :metrics="metrics" :loading="refreshing" />
            </div>
          </section>

          <section class="panel events-card">
            <div class="panel-head">
              <div>
                <h2><span class="head-index">04</span> 事件时间线</h2>
                <p class="panel-caption">状态流转与资源动作</p>
              </div>
              <span class="mono event-count">{{ events.length }}</span>
            </div>
            <div class="events-body">
              <div v-if="events.length === 0" class="empty-state events-empty">
                <span class="empty-icon"><el-icon><Clock /></el-icon></span>
                <strong>暂无事件</strong>
                <span>任务状态变化后会出现在这里。</span>
              </div>
              <ol v-else class="event-list">
                <li v-for="event in events" :key="event.id" class="event-item">
                  <span class="event-marker" :class="eventTone(event.level, event.event_type)"></span>
                  <div class="event-content">
                    <div class="event-top">
                      <span class="event-type">{{ eventTypeLabel(event.event_type) }}</span>
                      <span class="event-time" :title="formatFullDate(event.created_at)">
                        {{ timeAgo(event.created_at) }}
                      </span>
                    </div>
                    <p>{{ event.message }}</p>
                  </div>
                </li>
              </ol>
            </div>
          </section>

          <section class="panel side-info-card">
            <div class="panel-head">
              <h2><span class="head-index">05</span> 生命周期</h2>
            </div>
            <div class="lifecycle-body">
              <div class="lifecycle-item">
                <span>提交</span>
                <strong>{{ formatDate(task.submitted_at) }}</strong>
              </div>
              <div class="lifecycle-item">
                <span>开始</span>
                <strong>{{ formatDate(task.started_at) }}</strong>
              </div>
              <div class="lifecycle-item">
                <span>结束</span>
                <strong>{{ formatDate(task.finished_at) }}</strong>
              </div>
              <div class="lifecycle-item">
                <span>最近心跳</span>
                <strong>{{ formatDate(task.last_heartbeat_at) }}</strong>
              </div>
              <div class="lifecycle-item">
                <span>退出码</span>
                <strong class="mono">{{ task.exit_code ?? '—' }}</strong>
              </div>
              <div class="lifecycle-item">
                <span>失败原因</span>
                <strong class="mono reason-value">{{ task.failure_reason ?? '—' }}</strong>
              </div>
            </div>
          </section>
        </aside>
      </div>
    </template>
  </div>
</template>

<style scoped>
.detail-page {
  gap: 16px;
}

.detail-head {
  align-items: flex-start;
}

.back-link {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  width: fit-content;
  margin-bottom: 5px;
  padding: 0;
  border: none;
  background: none;
  color: var(--text-muted);
  cursor: pointer;
  font-family: var(--mono);
  font-size: 9px;
  transition: color 0.15s ease;
}

.back-link:hover {
  color: var(--accent);
}

.detail-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.detail-subline {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  margin-top: 3px;
  color: var(--text-muted);
  font-size: 9.5px;
}

.id-copy {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0;
  border: none;
  background: none;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 9.5px;
}

.id-copy:hover {
  color: var(--accent);
}

.dot-separator {
  color: var(--border-strong);
}

.detail-skeleton {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.skeleton-hero {
  min-height: 190px;
  padding: 24px;
}

.detail-main-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(340px, 0.8fr);
  gap: 16px;
  align-items: start;
}

.skeleton-logs,
.skeleton-side {
  min-height: 500px;
  padding: 24px;
}

.job-hero {
  position: relative;
  overflow: hidden;
  padding: 19px 21px 0;
  background:
    radial-gradient(550px 220px at 100% 0%, var(--accent-soft), transparent 70%),
    var(--panel);
}

.job-hero.state-failed {
  background:
    radial-gradient(550px 220px at 100% 0%, var(--red-soft), transparent 70%),
    var(--panel);
}

.job-hero.state-cancelled {
  background:
    radial-gradient(550px 220px at 100% 0%, var(--panel-soft), transparent 70%),
    var(--panel);
}

.hero-top {
  display: flex;
  align-items: center;
  gap: 12px;
}

.hero-status-icon {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  flex: none;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--panel-soft);
  color: var(--text-secondary);
  font-size: 21px;
}

.hero-status-icon.state-running {
  border-color: var(--accent-line);
  background: var(--accent-soft);
  color: var(--accent);
}

.hero-status-icon.state-succeeded {
  border-color: rgba(22, 134, 95, 0.22);
  background: rgba(22, 134, 95, 0.09);
  color: #16865f;
}

.hero-status-icon.state-failed {
  border-color: color-mix(in srgb, var(--red) 22%, transparent);
  background: var(--red-soft);
  color: var(--red);
}

.hero-status-icon.state-cancelled {
  color: var(--text-muted);
}

.hero-status-icon .spinning {
  animation: spin 1.3s linear infinite;
}

.hero-copy {
  flex: 1;
  min-width: 0;
}

.hero-eyebrow {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 8.5px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.hero-eyebrow .mono {
  padding-left: 7px;
  border-left: 1px solid var(--border);
  color: var(--text-secondary);
  font-size: 8px;
}

.hero-hint {
  overflow: hidden;
  margin-top: 3px;
  color: var(--text-secondary);
  font-size: 11.5px;
  text-overflow: ellipsis;
}

.hero-hint.failure {
  color: var(--red);
  font-family: var(--mono);
  font-size: 9.5px;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.hero-progress-number {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  flex: none;
}

.hero-progress-number strong {
  color: var(--text);
  font-family: var(--mono);
  font-size: 26px;
  font-weight: 650;
  letter-spacing: -0.05em;
  line-height: 1;
}

.hero-progress-number span {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 8px;
  letter-spacing: 0.08em;
}

.hero-progress {
  height: 5px;
  margin: 16px 0 15px;
  overflow: hidden;
  border-radius: 4px;
  background: var(--panel-soft);
  box-shadow: inset 0 0 0 1px var(--border);
}

.hero-progress > span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--accent);
  transition: width 0.45s ease;
}

.hero-progress > span.state-running {
  background: linear-gradient(90deg, var(--accent), #48dfa9);
}

.hero-progress > span.state-succeeded {
  background: #16865f;
}

.hero-progress > span.state-failed {
  background: var(--red);
}

.hero-progress > span.state-cancelled {
  background: var(--text-muted);
}

.hero-progress > span.state-pending {
  background: var(--amber);
}

.hero-stats {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 0;
  border-top: 1px solid var(--border);
}

.hero-stat {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
  padding: 13px 13px 14px 0;
}

.hero-stat + .hero-stat {
  padding-left: 14px;
  border-left: 1px solid var(--border);
}

.hero-stat-icon {
  display: grid;
  place-items: center;
  width: 25px;
  height: 25px;
  flex: none;
  border-radius: 7px;
  background: var(--panel-soft);
  color: var(--text-muted);
  font-size: 12px;
}

.hero-stat-value {
  display: flex;
  align-items: baseline;
  gap: 3px;
}

.hero-stat-value strong {
  overflow: hidden;
  color: var(--text);
  font-family: var(--mono);
  font-size: 12.5px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hero-stat-value small {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 7.5px;
}

.hero-stat-label {
  display: block;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 7.5px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.hero-stat-hint {
  display: block;
  overflow: hidden;
  margin-top: 2px;
  color: var(--text-muted);
  font-size: 8.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.failure-strip {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 0 -21px;
  padding: 9px 21px;
  border-top: 1px solid color-mix(in srgb, var(--red) 20%, transparent);
  background: var(--red-soft);
  color: var(--red);
  font-family: var(--mono);
  font-size: 9px;
}

.failure-strip span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.failure-strip .el-icon {
  flex: none;
}

.failure-strip button {
  display: inline-grid;
  place-items: center;
  width: 20px;
  height: 20px;
  flex: none;
  margin-left: auto;
  border: 1px solid color-mix(in srgb, var(--red) 25%, transparent);
  border-radius: 5px;
  background: transparent;
  color: var(--red);
  cursor: pointer;
  font-size: 10px;
}

.detail-main-column,
.detail-side {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.detail-side {
  position: sticky;
  top: 78px;
}

.logs-panel {
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

.log-head-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.log-bytes {
  color: var(--text-muted);
  font-size: 8.5px;
}

.log-panel-body {
  padding: 12px;
}

.config-panel {
  overflow: hidden;
}

.config-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px 18px 18px;
}

.config-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--border);
}

.config-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  padding: 10px 11px;
  background: var(--panel-soft);
}

.config-item span {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 7.5px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.config-item strong {
  overflow: hidden;
  color: var(--text-secondary);
  font-family: var(--mono);
  font-size: 10px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.command-full {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.command-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.command-label .mono {
  color: var(--text-muted);
  font-size: 8px;
}

.command-full code {
  display: block;
  padding: 10px 12px;
  overflow-x: auto;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--panel-soft);
  color: var(--text-secondary);
  font-family: var(--mono);
  font-size: 9.5px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-all;
}

.notes-box {
  padding: 10px 12px;
  border-left: 2px solid var(--accent-line);
  border-radius: 0 8px 8px 0;
  background: var(--accent-soft);
}

.notes-box p {
  margin-top: 3px;
  color: var(--text-secondary);
  font-size: 10px;
  line-height: 1.6;
}

.metrics-card,
.events-card,
.side-info-card {
  overflow: hidden;
}

.metrics-body {
  padding: 12px;
}

.events-body {
  max-height: 320px;
  overflow-y: auto;
  padding: 4px 17px 10px;
}

.events-empty {
  min-height: 150px;
}

.events-empty .empty-icon {
  width: 34px;
  height: 34px;
  font-size: 15px;
}

.event-list {
  display: flex;
  flex-direction: column;
  gap: 0;
  margin: 0;
  padding: 0;
  list-style: none;
}

.event-item {
  position: relative;
  display: flex;
  gap: 9px;
  padding: 9px 0;
}

.event-item:not(:last-child)::after {
  content: '';
  position: absolute;
  top: 20px;
  bottom: -9px;
  left: 4px;
  width: 1px;
  background: var(--border);
}

.event-marker {
  width: 9px;
  height: 9px;
  flex: none;
  margin-top: 4px;
  border: 2px solid var(--panel);
  border-radius: 50%;
  background: var(--border-strong);
  box-shadow: 0 0 0 1px var(--border);
}

.event-marker.success {
  background: var(--accent);
}

.event-marker.error {
  background: var(--red);
}

.event-marker.warning {
  background: var(--amber);
}

.event-marker.cancelled {
  background: var(--text-muted);
}

.event-marker.accent {
  background: var(--violet);
}

.event-content {
  min-width: 0;
  flex: 1;
}

.event-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.event-type {
  color: var(--text-secondary);
  font-family: var(--mono);
  font-size: 8.5px;
  font-weight: 650;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.event-time {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 7.5px;
  white-space: nowrap;
}

.event-content p {
  margin-top: 2px;
  color: var(--text-muted);
  font-size: 9.5px;
  line-height: 1.55;
  word-break: break-word;
}

.event-count {
  color: var(--text-muted);
  font-size: 9px;
}

.lifecycle-body {
  display: flex;
  flex-direction: column;
  padding: 4px 17px 12px;
}

.lifecycle-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px dashed var(--border);
  font-size: 9px;
}

.lifecycle-item:last-child {
  border-bottom: none;
}

.lifecycle-item span {
  flex: none;
  color: var(--text-muted);
}

.lifecycle-item strong {
  overflow: hidden;
  color: var(--text-secondary);
  font-family: var(--mono);
  font-size: 8.5px;
  font-weight: 500;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lifecycle-item .reason-value {
  max-width: 62%;
  white-space: normal;
  word-break: break-word;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 1180px) {
  .detail-main-grid {
    grid-template-columns: minmax(0, 1.35fr) minmax(300px, 0.8fr);
  }

  .hero-stats {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .hero-stat:nth-child(4) {
    padding-left: 0;
    border-left: none;
  }

  .hero-stat:nth-child(n + 4) {
    border-top: 1px solid var(--border);
  }

  .config-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 980px) {
  .detail-main-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .detail-side {
    position: static;
    display: grid;
    grid-template-columns: minmax(0, 1.25fr) minmax(280px, 0.75fr);
  }

  .events-card {
    grid-column: 1;
  }

  .side-info-card {
    grid-column: 2;
  }
}

@media (max-width: 720px) {
  .detail-side {
    display: flex;
  }

  .hero-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .hero-stat:nth-child(3),
  .hero-stat:nth-child(5) {
    padding-left: 0;
    border-left: none;
  }

  .hero-stat:nth-child(n + 3) {
    border-top: 1px solid var(--border);
  }

  .job-hero {
    padding: 15px 15px 0;
  }

  .failure-strip {
    margin: 0 -15px;
    padding: 9px 15px;
  }

  .hero-progress-number strong {
    font-size: 22px;
  }

  .log-head-meta {
    display: none;
  }

  .config-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
