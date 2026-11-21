<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  CircleClose,
  CopyDocument,
  Document,
  Plus,
  Refresh,
  Search,
  View,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { api } from '@/api/client'
import StatusBadge from '@/components/StatusBadge.vue'
import { useNotifications } from '@/composables/useNotifications'
import type { Task, TaskListResponse, TaskStatus } from '@/types/task'
import {
  formatDate,
  formatDuration,
  formatPercent,
  isTerminal,
  shortId,
  statusMeta,
  statusOrder,
  timeAgo,
} from '@/utils/format'

const router = useRouter()
const { notify } = useNotifications()

const response = ref<TaskListResponse | null>(null)
const tasks = computed(() => response.value?.items ?? [])
const loading = ref(true)
const refreshing = ref(false)
const error = ref<string | null>(null)
const autoRefresh = ref(true)
const page = ref(1)
const pageSize = ref(10)
const statusFilter = ref<'all' | TaskStatus>('all')
const searchInput = ref('')
const appliedSearch = ref('')
const cancellingId = ref<string | null>(null)
const lastStatuses = new Map<string, TaskStatus>()
let timer: number | undefined
let requestSeq = 0

const filterChips = computed(() => [
  { value: 'all' as const, label: '全部', count: response.value?.total ?? 0 },
  ...statusOrder.map((status) => ({
    value: status,
    label: statusMeta[status].label,
    count: response.value?.status_counts?.[status] ?? 0,
  })),
])

const hasActiveTasks = computed(() =>
  tasks.value.some((task) => !isTerminal(task.status)),
)

async function fetchTasks(silent = false) {
  const seq = ++requestSeq
  if (!silent) refreshing.value = true
  try {
    const data = await api.listTasks({
      page: page.value,
      page_size: pageSize.value,
      status: statusFilter.value === 'all' ? undefined : statusFilter.value,
      q: appliedSearch.value || undefined,
      sort_by: 'submitted_at',
      sort_order: 'desc',
    })
    if (seq !== requestSeq) return
    for (const task of data.items) {
      const previous = lastStatuses.get(task.id)
      if (previous && previous !== task.status) {
        notify(task, previous)
      }
      lastStatuses.set(task.id, task.status)
    }
    response.value = data
    error.value = null
    if (data.pages > 0 && page.value > data.pages) {
      page.value = data.pages
    }
  } catch (err) {
    if (!silent) error.value = err instanceof Error ? err.message : '加载任务失败'
  } finally {
    if (!silent) loading.value = false
    refreshing.value = false
  }
}

function selectStatus(value: 'all' | TaskStatus) {
  if (statusFilter.value === value) return
  statusFilter.value = value
  page.value = 1
  fetchTasks()
}

function applySearch() {
  appliedSearch.value = searchInput.value.trim()
  page.value = 1
  fetchTasks()
}

function clearSearch() {
  searchInput.value = ''
  appliedSearch.value = ''
  page.value = 1
  fetchTasks()
}

function openDetail(task: any) {
  router.push({ name: 'task-detail', params: { id: task.id } })
}

function openLogs(task: any) {
  router.push({ name: 'task-detail', params: { id: task.id }, query: { tab: 'logs' } })
}

async function copyId(id: string) {
  try {
    await navigator.clipboard.writeText(id)
    ElMessage.success(`已复制 ${id}`)
  } catch {
    ElMessage.warning('复制失败，请手动选择任务 ID')
  }
}

async function cancelTask(task: any) {
  try {
    await ElMessageBox.confirm(
      `取消任务「${task.name}」？${
        task.status === 'running' ? '正在运行的训练进程会被终止，CPU 资源将自动释放。' : '任务会立即移出调度队列。'
      }`,
      '取消任务',
      {
        confirmButtonText: '确认取消',
        cancelButtonText: '继续等待',
        type: 'warning',
        confirmButtonClass: 'el-button--danger',
      },
    )
  } catch {
    return
  }
  cancellingId.value = task.id
  try {
    const result = await api.cancelTask(task.id)
    ElMessage.success(result.message)
    await fetchTasks(true)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '取消失败')
  } finally {
    cancellingId.value = null
  }
}

function canCancel(task: any): boolean {
  return !isTerminal(task.status)
}

function priorityTone(priority: number): string {
  if (priority >= 70) return 'high'
  if (priority >= 40) return 'mid'
  return 'low'
}

watch([page, pageSize], () => fetchTasks())

onMounted(async () => {
  await fetchTasks()
  timer = window.setInterval(() => {
    if (autoRefresh.value && !document.hidden) fetchTasks(true)
  }, 2500)
})

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <div class="page task-list-page">
    <div class="page-head">
      <div class="page-heading">
        <span class="eyebrow">训练任务</span>
        <h1 class="page-title">任务列表</h1>
        <p class="page-subtitle">
          所有训练任务按提交时间倒序排列。状态筛选、任务 ID 搜索和分页都直接由后端 SQLite 查询支持。
        </p>
      </div>
      <div class="page-actions">
        <div class="auto-refresh">
          <span class="refresh-dot" :class="{ live: autoRefresh && hasActiveTasks }"></span>
          <span>自动刷新</span>
          <el-switch v-model="autoRefresh" size="small" />
        </div>
        <el-button
          :icon="Refresh"
          circle
          :loading="refreshing"
          title="立即刷新"
          @click="fetchTasks()"
        />
        <el-button type="primary" :icon="Plus" @click="router.push({ name: 'task-submit' })">
          提交任务
        </el-button>
      </div>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />

    <section class="panel filter-panel">
      <div class="status-chips" role="tablist" aria-label="状态筛选">
        <button
          v-for="chip in filterChips"
          :key="chip.value"
          type="button"
          class="status-chip"
          :class="{ active: statusFilter === chip.value }"
          @click="selectStatus(chip.value)"
        >
          <span class="chip-dot" :class="chip.value"></span>
          <span>{{ chip.label }}</span>
          <span class="chip-count">{{ chip.count }}</span>
        </button>
      </div>
      <div class="search-wrap">
        <el-input
          v-model="searchInput"
          :prefix-icon="Search"
          clearable
          placeholder="搜索任务 ID 或名称"
          class="search-input"
          @keyup.enter="applySearch"
          @clear="clearSearch"
        />
        <el-button type="primary" plain @click="applySearch">搜索</el-button>
      </div>
    </section>

    <section class="panel table-panel">
      <div class="table-meta">
        <div>
          <span class="soft-label">任务索引</span>
          <p>
            <strong>{{ response?.total ?? 0 }}</strong> 个任务
            <span v-if="appliedSearch" class="filter-note">
              · 关键词 “{{ appliedSearch }}”
            </span>
            <span v-if="statusFilter !== 'all'" class="filter-note">
              · {{ statusMeta[statusFilter].label }}
            </span>
          </p>
        </div>
        <span class="table-hint">点击任务行查看详情</span>
      </div>

      <el-skeleton v-if="loading" :rows="7" animated class="table-skeleton" />

      <template v-else>
        <el-table
          :data="tasks"
          row-key="id"
          class="task-table"
          @row-click="openDetail"
        >
          <el-table-column label="任务" min-width="230">
            <template #default="{ row }">
              <div class="task-cell">
                <span class="priority-rail" :class="priorityTone(row.priority)"></span>
                <div class="task-identity">
                  <div class="task-title-line">
                    <strong :title="row.name">{{ row.name }}</strong>
                    <span v-if="row.retry_count > 0" class="retry-chip">R{{ row.retry_count }}</span>
                  </div>
                  <button class="task-id" type="button" @click.stop="copyId(row.id)">
                    <span class="mono">{{ row.id }}</span>
                    <el-icon><CopyDocument /></el-icon>
                  </button>
                </div>
              </div>
            </template>
          </el-table-column>

          <el-table-column label="状态" width="104">
            <template #default="{ row }">
              <StatusBadge :status="row.status" />
            </template>
          </el-table-column>

          <el-table-column label="训练配置" min-width="145">
            <template #default="{ row }">
              <div class="config-cell">
                <span class="mono">{{ row.epochs }} 轮 · {{ row.imgsz }} px · 批 {{ row.batch_size }}</span>
                <span class="config-model">{{ row.model }}</span>
              </div>
            </template>
          </el-table-column>

          <el-table-column label="CPU" width="60" align="center">
            <template #default="{ row }">
              <span class="cpu-chip">{{ row.cpu_cores }}</span>
            </template>
          </el-table-column>

          <el-table-column label="优先级" width="76" align="center">
            <template #default="{ row }">
              <span class="priority-chip" :class="priorityTone(row.priority)">{{ row.priority }}</span>
            </template>
          </el-table-column>

          <el-table-column label="进度 / 耗时" min-width="130">
            <template #default="{ row }">
              <div v-if="row.status === 'running'" class="progress-cell">
                <div class="row between">
                  <span class="mono progress-value">{{ formatPercent(row.progress, 1) }}</span>
                  <span class="progress-sub">运行中</span>
                </div>
                <div class="mini-meter">
                  <span :style="{ width: `${Math.max(2, row.progress)}%` }"></span>
                </div>
              </div>
              <div v-else-if="row.status === 'pending'" class="pending-cell">
                <span class="queue-number">#{{ row.queue_position ?? '—' }}</span>
                <span>排队等待</span>
              </div>
              <span v-else class="mono duration-value">{{ formatDuration(row.runtime_seconds) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="提交时间" min-width="104">
            <template #default="{ row }">
              <el-tooltip :content="formatDate(row.submitted_at)" placement="top">
                <span class="submitted-cell">{{ timeAgo(row.submitted_at) }}</span>
              </el-tooltip>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="152" align="right" fixed="right">
            <template #default="{ row }">
              <div class="action-group" @click.stop>
                <el-tooltip content="查看详情" placement="top">
                  <button class="row-action" type="button" @click="openDetail(row)">
                    <el-icon><View /></el-icon>
                  </button>
                </el-tooltip>
                <el-tooltip content="查看日志" placement="top">
                  <button class="row-action" type="button" @click="openLogs(row)">
                    <el-icon><Document /></el-icon>
                  </button>
                </el-tooltip>
                <el-tooltip v-if="canCancel(row)" content="取消任务" placement="top">
                  <button
                    class="row-action danger"
                    type="button"
                    :disabled="cancellingId === row.id"
                    @click="cancelTask(row)"
                  >
                    <el-icon><CircleClose /></el-icon>
                  </button>
                </el-tooltip>
                <span v-else class="terminal-mark mono">{{ row.exit_code == null ? 'end' : `exit ${row.exit_code}` }}</span>
              </div>
            </template>
          </el-table-column>

          <template #empty>
            <div class="empty-state">
              <span class="empty-icon"><el-icon><Document /></el-icon></span>
              <strong>没有匹配的任务</strong>
              <span>
                调整筛选条件，或提交一个内置的 YOLO CPU 训练任务开始使用。
              </span>
              <el-button type="primary" plain size="small" @click="router.push({ name: 'task-submit' })">
                提交第一个任务
              </el-button>
            </div>
          </template>
        </el-table>

        <div class="task-mobile-list">
          <article v-for="row in tasks" :key="row.id" class="task-mobile-card">
            <div class="task-mobile-head">
              <span class="priority-rail" :class="priorityTone(row.priority)"></span>
              <div class="task-mobile-name">
                <strong>{{ row.name }}</strong>
                <span class="mono">{{ row.id }}</span>
              </div>
              <StatusBadge :status="row.status" />
            </div>
            <div class="task-mobile-meta">
              <span class="mono">{{ row.epochs }} 轮 · {{ row.imgsz }} px · 批 {{ row.batch_size }}</span>
              <span class="cpu-chip">{{ row.cpu_cores }} 核</span>
              <span class="priority-chip" :class="priorityTone(row.priority)">P{{ row.priority }}</span>
            </div>
            <div class="task-mobile-foot">
              <span class="mono">
                {{ row.status === 'running' ? formatPercent(row.progress, 1) : row.status === 'pending' ? `#${row.queue_position ?? '—'} queued` : formatDuration(row.runtime_seconds) }}
              </span>
              <span>{{ timeAgo(row.submitted_at) }}</span>
              <span class="task-mobile-actions">
                <el-button text size="small" @click="openDetail(row)">详情</el-button>
                <el-button text size="small" @click="openLogs(row)">日志</el-button>
                <el-button v-if="canCancel(row)" text size="small" type="danger" :loading="cancellingId === row.id" @click="cancelTask(row)">取消</el-button>
              </span>
            </div>
          </article>
        </div>

        <div class="pagination-bar">
          <span class="pagination-note">
            第 {{ response?.page ?? 1 }} / {{ Math.max(1, response?.pages ?? 1) }} 页
            · 每页 {{ pageSize }} 条
            <span class="mono subtle-id" v-if="tasks.length">· {{ shortId(tasks[0]?.id) }}</span>
          </span>
          <el-pagination
            v-model:current-page="page"
            v-model:page-size="pageSize"
            :page-sizes="[10, 20, 50]"
            :total="response?.total ?? 0"
            layout="sizes, prev, pager, next"
            background
          />
        </div>
      </template>
    </section>
  </div>
</template>

<style scoped>
.task-list-page {
  gap: 16px;
}

.auto-refresh {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 32px;
  padding: 0 9px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--panel);
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 9px;
}

.refresh-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--text-muted);
}

.refresh-dot.live {
  background: var(--accent);
  animation: soft-pulse 2s ease-out infinite;
}

.refresh-dot.live::after {
  content: none;
}

.filter-panel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 11px 14px;
  flex-wrap: wrap;
}

.status-chips {
  display: flex;
  align-items: center;
  gap: 3px;
  flex-wrap: wrap;
}

.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 9px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 11px;
  font-weight: 550;
  transition: all 0.17s ease;
}

.status-chip:hover {
  background: var(--panel-soft);
  color: var(--text-secondary);
}

.status-chip.active {
  border-color: var(--border);
  background: var(--panel-soft);
  color: var(--text);
  box-shadow: var(--shadow-sm);
}

.chip-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent);
}

.chip-dot.pending {
  background: var(--amber);
}

.chip-dot.running {
  background: var(--accent);
}

.chip-dot.succeeded {
  background: #16865f;
}

.chip-dot.failed {
  background: var(--red);
}

.chip-dot.cancelled {
  background: var(--text-muted);
}

.chip-count {
  display: inline-grid;
  place-items: center;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 5px;
  background: color-mix(in srgb, var(--text-muted) 8%, transparent);
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 8.5px;
}

.status-chip.active .chip-count {
  background: var(--accent-soft);
  color: var(--accent);
}

.search-wrap {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: min(360px, 100%);
}

.search-input {
  width: 250px;
}

.table-panel {
  overflow: hidden;
}

.table-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 15px 20px 13px;
  border-bottom: 1px solid var(--border);
}

.table-meta p {
  margin-top: 2px;
  color: var(--text-secondary);
  font-size: 11.5px;
}

.table-meta p strong {
  color: var(--text);
  font-family: var(--mono);
  font-size: 14px;
}

.filter-note {
  color: var(--accent);
}

.table-hint {
  color: var(--text-muted);
  font-size: 9.5px;
}

.table-skeleton {
  padding: 20px;
}

.task-table {
  cursor: pointer;
}

.task-table :deep(.el-table__row:hover) .task-title-line strong {
  color: var(--accent);
}

.task-cell {
  display: flex;
  align-items: stretch;
  gap: 11px;
  min-width: 0;
}

.priority-rail {
  width: 3px;
  flex: none;
  border-radius: 3px;
  background: var(--border);
}

.priority-rail.high {
  background: var(--amber);
}

.priority-rail.mid {
  background: var(--accent);
}

.priority-rail.low {
  background: var(--border-strong);
}

.task-identity {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.task-title-line {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.task-title-line strong {
  overflow: hidden;
  color: var(--text);
  font-size: 12.5px;
  font-weight: 620;
  letter-spacing: -0.01em;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: color 0.15s ease;
}

.retry-chip {
  flex: none;
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--violet-soft);
  color: var(--violet);
  font-family: var(--mono);
  font-size: 8px;
  font-weight: 700;
}

.task-id {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  width: fit-content;
  padding: 0;
  border: none;
  background: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 9.5px;
}

.task-id .el-icon {
  opacity: 0;
  font-size: 10px;
  transition: opacity 0.15s ease;
}

.task-id:hover {
  color: var(--accent);
}

.task-id:hover .el-icon {
  opacity: 1;
}

.config-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.config-cell .mono {
  color: var(--text-secondary);
  font-size: 9.5px;
}

.config-model {
  overflow: hidden;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 8.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cpu-chip {
  display: inline-grid;
  place-items: center;
  min-width: 25px;
  height: 21px;
  padding: 0 6px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--panel-soft);
  color: var(--text-secondary);
  font-family: var(--mono);
  font-size: 10px;
  font-weight: 600;
}

.priority-chip {
  display: inline-block;
  padding: 2px 7px;
  border-radius: 5px;
  font-family: var(--mono);
  font-size: 9px;
  font-weight: 600;
}

.priority-chip.high {
  background: var(--amber-soft);
  color: var(--amber);
}

.priority-chip.mid {
  background: var(--accent-soft);
  color: var(--accent);
}

.priority-chip.low {
  background: var(--panel-soft);
  color: var(--text-muted);
}

.progress-cell {
  min-width: 110px;
}

.progress-value {
  color: var(--accent);
  font-size: 9.5px;
  font-weight: 600;
}

.progress-sub {
  color: var(--text-muted);
  font-size: 8.5px;
}

.mini-meter {
  height: 4px;
  margin-top: 5px;
  overflow: hidden;
  border-radius: 3px;
  background: var(--panel-soft);
}

.mini-meter span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, var(--accent), #49dba6);
  transition: width 0.4s ease;
}

.pending-cell {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-muted);
  font-size: 9.5px;
}

.queue-number {
  color: var(--amber);
  font-family: var(--mono);
  font-size: 9.5px;
  font-weight: 600;
}

.duration-value {
  color: var(--text-secondary);
  font-size: 10px;
}

.submitted-cell {
  color: var(--text-muted);
  font-size: 10px;
}

.action-group {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
}

.row-action {
  display: inline-grid;
  place-items: center;
  width: 27px;
  height: 27px;
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  transition: all 0.16s ease;
}

.row-action:hover {
  border-color: var(--border);
  background: var(--panel-soft);
  color: var(--accent);
}

.row-action.danger:hover {
  border-color: color-mix(in srgb, var(--red) 24%, transparent);
  background: var(--red-soft);
  color: var(--red);
}

.row-action:disabled {
  cursor: wait;
  opacity: 0.4;
}

.row-action .el-icon {
  font-size: 14px;
}

.terminal-mark {
  min-width: 42px;
  color: var(--text-muted);
  font-size: 8px;
  text-align: center;
}

.pagination-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 13px 18px;
  border-top: 1px solid var(--border);
  flex-wrap: wrap;
}

.pagination-note {
  color: var(--text-muted);
  font-size: 9.5px;
}

.subtle-id {
  color: var(--text-muted);
  font-size: 9px;
}

@keyframes soft-pulse {
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

@media (max-width: 900px) {
  .filter-panel {
    align-items: stretch;
  }

  .search-wrap,
  .search-input {
    width: 100%;
  }

  .status-chips {
    width: 100%;
  }

  .auto-refresh {
    display: none;
  }
}

@media (max-width: 700px) {
  .table-hint,
  .pagination-note {
    display: none;
  }

  .pagination-bar {
    justify-content: center;
  }

  .table-meta {
    padding: 13px 15px;
  }
}
</style>

<style scoped>
.task-mobile-list {
  display: none;
}

.task-mobile-card {
  padding: 13px;
  border: 1px solid var(--border);
  border-radius: 13px;
  background: var(--panel);
}

.task-mobile-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.task-mobile-head .priority-rail {
  height: 26px;
}

.task-mobile-name {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.task-mobile-name strong {
  overflow: hidden;
  color: var(--text);
  font-size: 11px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-mobile-name span {
  color: var(--text-muted);
  font-size: 7.5px;
}

.task-mobile-meta {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  margin-top: 9px;
  padding-top: 8px;
  border-top: 1px dashed var(--border);
  color: var(--text-muted);
  font-size: 8px;
}

.task-mobile-foot {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-top: 8px;
  color: var(--text-muted);
  font-size: 8px;
}

.task-mobile-actions {
  display: inline-flex;
  align-items: center;
  gap: 1px;
  margin-left: auto;
}

@media (max-width: 1000px) {
  .search-wrap {
    width: 100%;
    min-width: 0;
  }

  .search-input {
    width: auto;
    min-width: 0;
    flex: 1;
  }

  .filter-panel {
    padding: 10px;
  }

  .task-table {
    display: none;
  }

  .task-mobile-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 12px;
  }

  .pagination-bar {
    padding-top: 0;
  }
}
</style>

<style scoped>
.task-list-page {
  gap: 24px;
}

.auto-refresh {
  height: 40px;
  font-size: 13px;
}

.filter-panel {
  padding: 14px 16px;
}

.status-chip {
  min-height: 32px;
  padding: 5px 10px;
  font-size: 13px;
}

.chip-count {
  font-size: 12px;
}

.terminal-mark {
  font-size: 12px;
}

.search-input {
  min-width: 240px;
}

.table-meta {
  padding: 16px 18px;
}

.table-meta p {
  margin-top: 4px;
  font-size: 14px;
}

.table-meta p strong {
  font-size: 24px;
  font-weight: 600;
}

.table-hint,
.filter-note {
  font-size: 12px;
}

.task-table :deep(td) {
  font-size: 14px;
}

.task-title-line strong {
  font-size: 14px;
}

.task-id,
.task-id .mono {
  font-size: 12px;
}

.config-cell .mono,
.config-model {
  font-size: 12px;
}

.cpu-chip,
.priority-chip {
  font-size: 12px;
}

.progress-value,
.progress-sub,
.pending-cell,
.duration-value,
.submitted-cell {
  font-size: 12px;
}

.row-action {
  width: 34px;
  height: 34px;
}

.terminal-mark {
  font-size: 11px;
}

.pagination-note {
  font-size: 12px;
}

.task-mobile-card {
  padding: 14px;
}

.task-mobile-name strong {
  font-size: 14px;
}

.task-mobile-name span {
  font-size: 12px;
}

.task-mobile-meta,
.task-mobile-foot {
  font-size: 12px;
}

.task-mobile-foot .el-button {
  min-height: 32px;
  font-size: 13px;
}

@media (max-width: 1000px) {
  .search-input {
    min-width: 0;
  }

  .pagination-bar {
    justify-content: center;
  }

  .pagination-bar :deep(.el-pagination__sizes),
  .pagination-note {
    display: none;
  }
}
</style>
