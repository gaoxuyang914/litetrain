<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  CircleClose,
  Download,
  Plus,
  Refresh,
  Share,
  Timer,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { api } from '@/api/client'
import StatusBadge from '@/components/StatusBadge.vue'
import type { Artifact, Pipeline, PipelineCreatePayload, PipelineStage } from '@/types/p2'
import type { TaskOptions } from '@/types/task'
import {
  formatDate,
  formatDuration,
  isTerminal,
  timeAgo,
} from '@/utils/format'

const router = useRouter()
const options = ref<TaskOptions | null>(null)
const pipelines = ref<Pipeline[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(true)
const refreshing = ref(false)
const error = ref<string | null>(null)
const createOpen = ref(false)
const creating = ref(false)
const cancellingId = ref<string | null>(null)
const resumingId = ref<string | null>(null)
const detailOpen = ref(false)
const detailLoading = ref(false)
const detail = ref<Pipeline | null>(null)
let timer: number | undefined

const form = reactive<PipelineCreatePayload>({
  name: '',
  source_dataset: 'coco128.yaml',
  model: 'yolov8n.pt',
  epochs: 1,
  imgsz: 96,
  batch_size: 4,
  workers: 0,
  cpu_cores: 4,
  priority: 50,
  max_runtime_seconds: 600,
  max_retries: 0,
  max_images: 24,
  val_ratio: 0.2,
  seed: 42,
  dedupe: true,
  eval_split: 'val',
  eval_conf: 0.001,
  eval_iou: 0.6,
  eval_max_det: 300,
  notes: '',
})

const hasActive = computed(() => pipelines.value.some((item) => !isTerminal(item.status)))
const maxRuntime = computed(() => options.value?.limits.max_runtime_seconds?.[1] ?? 600)
const maxRetries = computed(() => options.value?.limits.max_retries?.[1] ?? 3)
const maxCpu = computed(
  () => options.value?.limits.cpu_cores?.[1] ?? options.value?.user_cpu_quota ?? 8,
)

function warnPolicyLimit(label: string, value: number): void {
  ElMessage.warning(`${label}最多为 ${value}，已按后台配置限制`)
}

function handleRuntimeChange(value: number | undefined) {
  if (value != null && value > maxRuntime.value) {
    form.max_runtime_seconds = maxRuntime.value
    warnPolicyLimit('最大运行时长', maxRuntime.value)
  }
}

function handleRetriesChange(value: number | undefined) {
  if (value != null && value > maxRetries.value) {
    form.max_retries = maxRetries.value
    warnPolicyLimit('失败重试次数', maxRetries.value)
  }
}

function handleCpuChange(value: number | undefined) {
  if (value != null && value > maxCpu.value) {
    form.cpu_cores = maxCpu.value
    warnPolicyLimit('每阶段 CPU 核数', maxCpu.value)
  }
}

async function refreshPolicyLimits() {
  try {
    const data = await api.getOptions()
    options.value = data
    if (form.max_runtime_seconds > maxRuntime.value) {
      form.max_runtime_seconds = maxRuntime.value
      warnPolicyLimit('最大运行时长', maxRuntime.value)
    }
    if (form.max_retries > maxRetries.value) {
      form.max_retries = maxRetries.value
      warnPolicyLimit('失败重试次数', maxRetries.value)
    }
    if (form.cpu_cores > maxCpu.value) {
      form.cpu_cores = maxCpu.value
      warnPolicyLimit('每阶段 CPU 核数', maxCpu.value)
    }
  } catch {
    // The create endpoint still validates the current policy.
  }
}
const stageDefinitions = [
  { key: 'preprocess', label: '预处理', hint: '去重、校验、分层切分', index: '01' },
  { key: 'train', label: '训练', hint: 'YOLOv8n，CPU', index: '02' },
  { key: 'evaluate', label: '评估', hint: '阈值、类别 AP、延迟', index: '03' },
]

const stageMeta: Record<string, { label: string; tone: string }> = {
  waiting: { label: '等待前序', tone: 'waiting' },
  pending: { label: '排队中', tone: 'pending' },
  running: { label: '运行中', tone: 'running' },
  succeeded: { label: '已完成', tone: 'succeeded' },
  failed: { label: '失败', tone: 'failed' },
  cancelled: { label: '已取消', tone: 'cancelled' },
}

function stageStatus(status: string) {
  return stageMeta[status] ?? { label: status, tone: 'waiting' }
}

async function loadOptions() {
  if (options.value) return
  try {
    const data = await api.getOptions()
    options.value = data
    form.model = data.models.includes(data.defaults.model) ? data.defaults.model : data.models[0] ?? data.defaults.model
    form.source_dataset = data.datasets.includes(data.defaults.dataset)
      ? data.defaults.dataset
      : data.datasets[0] ?? data.defaults.dataset
    form.cpu_cores = Math.min(
      4,
      data.limits.cpu_cores?.[1] ?? data.user_cpu_quota,
    )
    form.max_runtime_seconds = Math.min(
      data.defaults.max_runtime_seconds,
      data.limits.max_runtime_seconds?.[1] ?? data.defaults.max_runtime_seconds,
    )
    form.max_retries = Math.min(
      form.max_retries,
      data.limits.max_retries?.[1] ?? form.max_retries,
    )
  } catch {
    // Keep the form defaults; the create operation surfaces the exact error.
  }
}

async function load(silent = false) {
  if (!silent) loading.value = true
  refreshing.value = true
  try {
    const data = await api.listPipelines(page.value, pageSize.value)
    pipelines.value = data.items
    total.value = data.total
    error.value = null
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载流水线失败'
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

function openCreate() {
  loadOptions()
  createOpen.value = true
}

async function submitCreate() {
  if (!form.model || !form.source_dataset) {
    ElMessage.warning('请选择模型和数据源')
    return
  }
  if (form.max_runtime_seconds > maxRuntime.value) {
    warnPolicyLimit('最大运行时长', maxRuntime.value)
    return
  }
  if (form.max_retries > maxRetries.value) {
    warnPolicyLimit('失败重试次数', maxRetries.value)
    return
  }
  if (form.cpu_cores > maxCpu.value) {
    warnPolicyLimit('每阶段 CPU 核数', maxCpu.value)
    return
  }
  creating.value = true
  try {
    const pipeline = await api.createPipeline({
      ...form,
      name: form.name?.trim() || undefined,
      notes: form.notes?.trim() || undefined,
    })
    ElMessage.success(`流水线 ${pipeline.id} 已创建，预处理阶段进入队列`)
    createOpen.value = false
    page.value = 1
    await load(true)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '创建流水线失败')
  } finally {
    creating.value = false
  }
}

async function openDetail(pipeline: Pipeline) {
  detailOpen.value = true
  detailLoading.value = true
  try {
    detail.value = await api.getPipeline(pipeline.id)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载流水线详情失败')
  } finally {
    detailLoading.value = false
  }
}

function canResumePipeline(pipeline: Pipeline): boolean {
  return (
    ['failed', 'cancelled'].includes(pipeline.status) &&
    pipeline.stages.some(
      (stage) => stage.status === 'succeeded' || stage.task?.status === 'succeeded',
    )
  )
}

async function resumePipeline(pipeline: Pipeline) {
  resumingId.value = pipeline.id
  try {
    await api.resumePipeline(pipeline.id)
    ElMessage.success('流水线已从最近成功阶段继续')
    await load(true)
    if (detail.value?.id === pipeline.id) {
      detail.value = await api.getPipeline(pipeline.id)
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '继续流水线失败')
  } finally {
    resumingId.value = null
  }
}

async function cancelPipeline(pipeline: Pipeline) {
  try {
    await ElMessageBox.confirm(
      `取消流水线「${pipeline.name}」？当前阶段任务会被终止，后续阶段不会执行。`,
      '取消流水线',
      { confirmButtonText: '确认取消', cancelButtonText: '返回', type: 'warning' },
    )
  } catch {
    return
  }
  cancellingId.value = pipeline.id
  try {
    await api.cancelPipeline(pipeline.id)
    ElMessage.success('流水线已取消')
    await load(true)
    if (detail.value?.id === pipeline.id) {
      detail.value = await api.getPipeline(pipeline.id)
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '取消失败')
  } finally {
    cancellingId.value = null
  }
}

function openTask(taskId: string) {
  router.push({ name: 'task-detail', params: { id: taskId } })
}

function openArtifact(artifact: Artifact) {
  window.open(artifact.download_url, '_blank', 'noopener')
}

function stageTask(pipeline: Pipeline, key: string) {
  return pipeline.stages.find((stage) => stage.stage_key === key) ?? null
}

function stageHint(pipeline: Pipeline, key: string) {
  const stage = stageTask(pipeline, key)
  if (!stage) return '未创建'
  const meta = stageStatus(stage.status)
  return stage.task ? `${meta.label} · ${formatDuration(stage.task.runtime_seconds)}` : meta.label
}

function pipelineConfigSummary(pipeline: Pipeline) {
  const cfg = pipeline.config
  return `${cfg.epochs} 轮，${cfg.imgsz} px，${cfg.cpu_cores} 核，验证 ${Math.round((cfg.val_ratio ?? 0.2) * 100)}%`
}

const stageLabels: Record<string, string> = {
  preprocess: '预处理',
  train: '训练',
  evaluate: '评估',
}

function stageLabel(key: string): string {
  return stageLabels[key] ?? key
}

function metricPercent(value: number | undefined | null): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return `${(value * 100).toFixed(1)}%`
}

function metricFixed(value: number | undefined | null, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return value.toFixed(digits)
}

function preprocessCount(stage: PipelineStage, key: string): number {
  const counts = stage.details?.counts ?? {}
  return Number(counts[key] ?? 0)
}

function evaluationOverall(stage: PipelineStage, key: string): number | null {
  const value = stage.details?.overall?.[key] ?? stage.metrics?.[`eval/${key}`]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function stageClassRows(stage: PipelineStage): Array<Record<string, any>> {
  return Array.isArray(stage.details?.per_class) ? stage.details.per_class : []
}

onMounted(async () => {
  await Promise.all([load(), loadOptions()])
  window.addEventListener('litetrain:settings-updated', refreshPolicyLimits)
  timer = window.setInterval(() => {
    if (!document.hidden && (hasActive.value || pipelines.value.length === 0)) load(true)
  }, 3000)
})

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
  window.removeEventListener('litetrain:settings-updated', refreshPolicyLimits)
})
</script>

<template>
  <div class="page pipelines-page">
    <div class="page-head">
      <div class="page-heading">
        <span class="eyebrow">流水线编排</span>
        <h1 class="page-title">训练流水线</h1>
      </div>
      <div class="page-actions">
        <div class="auto-refresh">
          <span class="refresh-dot" :class="{ live: hasActive }"></span>
          <span>{{ hasActive ? '流水线执行中' : '自动刷新' }}</span>
        </div>
        <el-button class="page-icon-button" :icon="Refresh" :loading="refreshing" title="刷新" @click="load(true)" />
        <el-button type="primary" :icon="Plus" @click="openCreate">新建流水线</el-button>
      </div>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />

    <section class="panel pipeline-guide">
      <div class="guide-head">
        <div>
          <span class="soft-label">流水线结构</span>
          <h2>一个流水线，三个可审计阶段</h2>
        </div>
        <span class="tag-soft accent"><span class="dot"></span>串行执行 · 可恢复</span>
      </div>
      <div class="guide-stages">
        <div v-for="(stage, index) in stageDefinitions" :key="stage.key" class="guide-stage">
          <span class="guide-index mono">{{ stage.index }}</span>
          <div>
            <strong>{{ stage.label }}</strong>
            <span>{{ stage.hint }}</span>
          </div>
          <span v-if="index < stageDefinitions.length - 1" class="guide-arrow">→</span>
        </div>
        <div class="guide-artifacts">
          <span class="soft-label">阶段产物</span>
          <strong>dataset.yaml + best.pt</strong>
          <span>自动登记到数据与产物页</span>
        </div>
      </div>
    </section>

    <div v-if="loading" class="pipeline-skeleton">
      <div v-for="idx in 3" :key="idx" class="panel skeleton-pipeline"><el-skeleton :rows="4" animated /></div>
    </div>

    <div v-else-if="pipelines.length === 0" class="panel empty-pipeline">
      <div class="empty-state">
        <span class="empty-icon"><el-icon><Share /></el-icon></span>
        <strong>还没有流水线</strong>
        <span>用一个串行流水线验证预处理、训练和评估的自动衔接，以及阶段产物的下载。</span>
        <el-button type="primary" plain size="small" @click="openCreate">创建第一条流水线</el-button>
      </div>
    </div>

    <div v-else class="pipeline-list">
      <article
        v-for="pipeline in pipelines"
        :key="pipeline.id"
        class="panel pipeline-card"
        :class="{
          'is-running': pipeline.status === 'running',
          'is-failed': pipeline.status === 'failed',
          'is-succeeded': pipeline.status === 'succeeded',
        }"
      >
        <div class="pipeline-card-head">
          <div class="pipeline-title-block">
            <div class="pipeline-title-row">
              <StatusBadge :status="pipeline.status" />
              <span class="pipeline-id mono">{{ pipeline.id }}</span>
              <span class="pipeline-time">{{ timeAgo(pipeline.submitted_at) }}</span>
            </div>
            <h2>{{ pipeline.name }}</h2>
            <div class="pipeline-meta">
              <span>{{ pipeline.config.source_dataset }}</span>
              <span>{{ pipeline.config.model }}</span>
              <span>{{ pipelineConfigSummary(pipeline) }}</span>
            </div>
          </div>
          <div class="pipeline-head-actions">
            <button class="row-action" type="button" title="查看阶段详情" @click="openDetail(pipeline)">
              <el-icon><Share /></el-icon>
            </button>
            <el-button
              v-if="canResumePipeline(pipeline)"
              size="small"
              text
              type="primary"
              :loading="resumingId === pipeline.id"
              :icon="Refresh"
              @click="resumePipeline(pipeline)"
            >
              继续流水线
            </el-button>
            <el-button
              v-if="!isTerminal(pipeline.status)"
              size="small"
              text
              type="danger"
              :loading="cancellingId === pipeline.id"
              :icon="CircleClose"
              @click="cancelPipeline(pipeline)"
            >
              取消
            </el-button>
          </div>
        </div>

        <div class="pipeline-progress-row">
          <div class="pipeline-progress">
            <span :style="{ width: `${Math.max(2, pipeline.progress)}%` }"></span>
          </div>
          <span class="mono">{{ pipeline.progress.toFixed(1) }}%</span>
          <span v-if="pipeline.current_stage" class="current-stage">
            current · {{ pipeline.current_stage }}
          </span>
        </div>

        <div class="stage-row">
          <div
            v-for="(definition, index) in stageDefinitions"
            :key="definition.key"
            class="stage-tile"
            :class="[stageStatus(stageTask(pipeline, definition.key)?.status ?? 'waiting').tone]"
          >
            <div class="stage-top">
              <span class="stage-index mono">{{ definition.index }}</span>
              <span class="stage-name">{{ definition.label }}</span>
              <span class="stage-status">
                {{ stageStatus(stageTask(pipeline, definition.key)?.status ?? 'waiting').label }}
              </span>
            </div>
            <div class="stage-hint">
              <template v-if="stageTask(pipeline, definition.key)?.task">
                <button type="button" class="stage-task-link" @click="openTask(stageTask(pipeline, definition.key)!.task!.id)">
                  <span class="mono">{{ stageTask(pipeline, definition.key)!.task!.id }}</span>
                  <el-icon><Share /></el-icon>
                </button>
                <span>{{ stageHint(pipeline, definition.key) }}</span>
              </template>
              <span v-else>{{ stageHint(pipeline, definition.key) }}</span>
            </div>
            <span v-if="index < stageDefinitions.length - 1" class="stage-connector"></span>
          </div>
        </div>

        <div v-if="pipeline.failure_reason" class="pipeline-failure">
          <el-icon><Timer /></el-icon>
          <span>{{ pipeline.failure_reason }}</span>
        </div>

        <div class="pipeline-foot">
          <span>提交 {{ formatDate(pipeline.submitted_at) }}</span>
          <span v-if="pipeline.started_at">开始 {{ timeAgo(pipeline.started_at) }}</span>
          <span v-if="pipeline.finished_at">结束 {{ formatDate(pipeline.finished_at) }}</span>
          <span v-if="pipeline.artifacts.length" class="artifact-inline">
            <el-icon><Download /></el-icon>{{ pipeline.artifacts.length }} artifacts
          </span>
        </div>
      </article>
    </div>

    <div v-if="total > pageSize" class="pipeline-pager">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :page-sizes="[10, 20, 50]"
        :total="total"
        layout="sizes, prev, pager, next"
        background
        @current-change="load(true)"
        @size-change="load(true)"
      />
    </div>

    <el-dialog v-model="createOpen" title="新建训练流水线" width="720px" class="pipeline-dialog">
      <div class="create-intro">
        <el-icon><Share /></el-icon>
        <span>创建后会依次执行 <strong>预处理 → 训练 → 评估</strong>。三阶段共享一套 CPU 与优先级配置。</span>
      </div>
      <el-form label-position="top" class="pipeline-form">
        <div class="form-grid">
          <el-form-item label="流水线名称" class="span-2">
            <el-input v-model="form.name" maxlength="100" placeholder="留空自动生成" />
          </el-form-item>
          <el-form-item label="数据源">
            <el-select v-model="form.source_dataset" class="full">
              <el-option v-for="dataset in options?.datasets ?? []" :key="dataset" :label="dataset" :value="dataset" />
            </el-select>
          </el-form-item>
          <el-form-item label="基础模型">
            <el-select v-model="form.model" class="full">
              <el-option v-for="model in options?.models ?? []" :key="model" :label="model" :value="model" />
            </el-select>
          </el-form-item>
          <el-form-item label="训练轮数">
            <el-input-number v-model="form.epochs" :min="1" :max="20" controls-position="right" class="full" />
          </el-form-item>
          <el-form-item label="Image size">
            <el-input-number v-model="form.imgsz" :min="64" :max="640" :step="32" controls-position="right" class="full" />
          </el-form-item>
          <el-form-item label="批大小">
            <el-input-number v-model="form.batch_size" :min="1" :max="32" controls-position="right" class="full" />
          </el-form-item>
          <div class="form-section span-2">
            <strong>预处理策略</strong>
            <span>先做图片去重和标签校验，再按固定种子切分训练/验证集，避免原始 split 泄漏。</span>
          </div>
          <el-form-item label="保留图片数上限">
            <el-input-number v-model="form.max_images" :min="2" :max="128" controls-position="right" class="full" />
          </el-form-item>
          <el-form-item label="验证集比例">
            <el-input-number
              v-model="form.val_ratio"
              :min="0.05"
              :max="0.5"
              :step="0.05"
              :precision="2"
              controls-position="right"
              class="full"
            />
          </el-form-item>
          <el-form-item label="切分随机种子">
            <el-input-number v-model="form.seed" :min="0" :max="2147483647" controls-position="right" class="full" />
          </el-form-item>
          <el-form-item label="去重与标签校验">
            <div class="switch-field">
              <el-switch v-model="form.dedupe" />
              <span>{{ form.dedupe ? '开启：去除重复图片，丢弃缺失/非法标签' : '关闭：保留重复图片' }}</span>
            </div>
          </el-form-item>
          <div class="form-section span-2">
            <strong>评估策略</strong>
            <span>固定使用验证集生成整体指标、类别 AP、阈值配置和 CPU 推理延迟；不使用训练集评估，避免指标失真。</span>
          </div>
          <el-form-item label="评估数据">
            <div class="fixed-policy">
              <strong>验证集 val</strong>
              <span>固定</span>
            </div>
          </el-form-item>
          <el-form-item label="置信度阈值">
            <el-input-number
              v-model="form.eval_conf"
              :min="0.001"
              :max="0.5"
              :step="0.01"
              :precision="3"
              controls-position="right"
              class="full"
            />
          </el-form-item>
          <el-form-item label="IoU 阈值">
            <el-input-number
              v-model="form.eval_iou"
              :min="0.1"
              :max="0.95"
              :step="0.05"
              :precision="2"
              controls-position="right"
              class="full"
            />
          </el-form-item>
          <el-form-item label="最大检测数">
            <el-input-number v-model="form.eval_max_det" :min="10" :max="1000" :step="10" controls-position="right" class="full" />
          </el-form-item>
          <el-form-item label="每阶段 CPU">
            <el-input-number
              v-model="form.cpu_cores"
              :min="1"
              :max="64"
              controls-position="right"
              class="full"
              @change="handleCpuChange"
            />
            <span class="form-hint">后台上限 {{ maxCpu }} 核</span>
          </el-form-item>
          <el-form-item label="优先级">
            <el-input-number v-model="form.priority" :min="0" :max="100" :step="5" controls-position="right" class="full" />
          </el-form-item>
          <el-form-item label="最大运行时长（秒）">
            <el-input-number
              v-model="form.max_runtime_seconds"
              :min="10"
              :max="86400"
              :step="60"
              controls-position="right"
              class="full"
              @change="handleRuntimeChange"
            />
            <span class="form-hint">后台上限 {{ maxRuntime }} 秒</span>
          </el-form-item>
          <el-form-item label="失败重试">
            <el-input-number
              v-model="form.max_retries"
              :min="0"
              :max="5"
              controls-position="right"
              class="full"
              @change="handleRetriesChange"
            />
            <span class="form-hint">后台上限 {{ maxRetries }} 次</span>
          </el-form-item>
          <el-form-item label="备注" class="span-2">
            <el-input v-model="form.notes" maxlength="500" show-word-limit placeholder="可选：记录流水线目的" />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="createOpen = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">创建并调度</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailOpen" title="流水线详情" size="620px">
      <div v-loading="detailLoading" class="pipeline-detail">
        <template v-if="detail">
          <div class="detail-summary">
            <StatusBadge :status="detail.status" />
            <h3>{{ detail.name }}</h3>
            <span class="mono">{{ detail.id }}</span>
            <p>{{ detail.config.source_dataset }} · {{ pipelineConfigSummary(detail) }}</p>
          </div>
          <div class="detail-progress">
            <span :style="{ width: `${Math.max(2, detail.progress)}%` }"></span>
          </div>
          <div class="detail-stage-list">
            <div v-for="stage in detail.stages" :key="stage.id" class="detail-stage">
              <div class="detail-stage-heading">
                <span class="soft-label">{{ stageLabel(stage.stage_key) }}</span>
                <span :class="['stage-pill', stageStatus(stage.status).tone]">
                  {{ stageStatus(stage.status).label }}
                </span>
              </div>
              <p v-if="stage.message">{{ stage.message }}</p>
              <div v-if="stage.stage_key === 'preprocess' && stage.details?.counts" class="stage-report">
                <div class="stage-kpis">
                  <span><strong>{{ preprocessCount(stage, 'kept_images') }}</strong><small>保留图片</small></span>
                  <span><strong>{{ preprocessCount(stage, 'train_images') }}</strong><small>训练集</small></span>
                  <span><strong>{{ preprocessCount(stage, 'val_images') }}</strong><small>验证集</small></span>
                  <span><strong>{{ preprocessCount(stage, 'duplicate_images') }}</strong><small>去重</small></span>
                  <span><strong>{{ preprocessCount(stage, 'invalid_labels') }}</strong><small>无效标签</small></span>
                </div>
                <p class="stage-report-note">
                  seed {{ stage.details.policy?.seed ?? 42 }} · 去重
                  {{ stage.details.policy?.dedupe === false ? '关闭' : '开启' }} ·
                  验证比例 {{ metricPercent(stage.details.policy?.val_ratio) }}
                </p>
                <p v-for="warning in stage.details.warnings ?? []" :key="warning" class="stage-warning">{{ warning }}</p>
              </div>
              <div v-else-if="stage.stage_key === 'evaluate' && stage.details?.overall" class="stage-report">
                <div class="stage-kpis stage-kpis-eval">
                  <span><strong>{{ metricPercent(evaluationOverall(stage, 'map50')) }}</strong><small>mAP50</small></span>
                  <span><strong>{{ metricPercent(evaluationOverall(stage, 'map50_95')) }}</strong><small>mAP50-95</small></span>
                  <span><strong>{{ metricPercent(evaluationOverall(stage, 'precision')) }}</strong><small>精确率</small></span>
                  <span><strong>{{ metricPercent(evaluationOverall(stage, 'recall')) }}</strong><small>召回率</small></span>
                  <span><strong>{{ metricFixed(evaluationOverall(stage, 'latency_ms'), 1) }}</strong><small>ms / 图</small></span>
                  <span><strong>{{ metricFixed(evaluationOverall(stage, 'fps'), 1) }}</strong><small>FPS</small></span>
                </div>
                <div v-if="stageClassRows(stage).length" class="class-report">
                  <div class="class-report-head">
                    <span>类别 AP50-95</span>
                    <small>{{ stage.details.per_class.length }} 类</small>
                  </div>
                  <div v-for="row in stageClassRows(stage).slice(0, 8)" :key="row.class_id" class="class-row">
                    <span>{{ row.class }}</span>
                    <strong class="mono">{{ metricPercent(row.ap50_95) }}</strong>
                  </div>
                  <small v-if="stageClassRows(stage).length > 8" class="class-more">
                    仅展示前 8 类，完整报告见 evaluation.json
                  </small>
                </div>
                <p class="stage-report-note">
                  split {{ stage.details.config?.split ?? 'val' }} · conf
                  {{ stage.details.config?.conf ?? 0.001 }} · IoU {{ stage.details.config?.iou ?? 0.6 }}
                </p>
              </div>
              <div v-if="stage.task" class="detail-task">
                <div>
                  <strong>{{ stage.task.name }}</strong>
                  <span class="mono">{{ stage.task.id }} · {{ formatDuration(stage.task.runtime_seconds) }}</span>
                </div>
                <el-button size="small" text @click="openTask(stage.task.id)">打开任务</el-button>
              </div>
              <span v-else class="detail-waiting">该阶段尚未创建任务</span>
            </div>
          </div>
          <div class="detail-artifacts">
            <span class="soft-label">产物</span>
            <div v-if="detail.artifacts.length" class="artifact-list">
              <button v-for="artifact in detail.artifacts" :key="artifact.id" type="button" @click="openArtifact(artifact)">
                <el-icon><Download /></el-icon>
                <span>{{ artifact.name }}</span>
                <small>{{ (artifact.size_bytes / 1024 / 1024).toFixed(1) }} MB</small>
              </button>
            </div>
            <p v-else class="empty-copy">训练完成后，best.pt / results.csv 会自动登记。</p>
          </div>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.pipelines-page {
  gap: 16px;
}

.auto-refresh {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 8.5px;
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

.pipeline-guide {
  padding: 16px 20px;
  background:
    radial-gradient(620px 220px at 100% 0%, var(--accent-soft), transparent 70%),
    var(--panel);
}

.guide-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.guide-head h2 {
  margin-top: 3px;
  color: var(--text);
  font-size: 15px;
  font-weight: 680;
  letter-spacing: -0.02em;
}

.guide-stages {
  display: flex;
  align-items: stretch;
  gap: 10px;
  flex-wrap: wrap;
}

.guide-stage {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 170px;
  flex: 1;
  padding: 10px 11px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--panel-soft);
}

.guide-index {
  color: var(--accent);
  font-size: 9px;
}

.guide-stage div {
  display: flex;
  flex-direction: column;
  gap: 1px;
  flex: 1;
}

.guide-stage strong {
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 650;
}

.guide-stage span:not(.guide-index):not(.guide-arrow) {
  color: var(--text-muted);
  font-size: 8.5px;
}

.guide-arrow {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 14px;
}

.guide-artifacts {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 2px;
  min-width: 190px;
  flex: 0.9;
  padding: 10px 12px;
  border-left: 1px dashed var(--border-strong);
}

.guide-artifacts strong {
  color: var(--accent);
  font-family: var(--mono);
  font-size: 10px;
}

.guide-artifacts > span:last-child {
  color: var(--text-muted);
  font-size: 8px;
}

.pipeline-skeleton {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.skeleton-pipeline {
  min-height: 170px;
  padding: 20px;
}

.empty-pipeline {
  min-height: 280px;
}

.pipeline-list {
  display: flex;
  flex-direction: column;
  gap: 11px;
}

.pipeline-card {
  padding: 16px 18px 12px;
}

.pipeline-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.pipeline-title-block {
  min-width: 0;
}

.pipeline-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.pipeline-id {
  color: var(--text-muted);
  font-size: 8.5px;
}

.pipeline-time {
  color: var(--text-muted);
  font-size: 8.5px;
}

.pipeline-title-block h2 {
  margin-top: 6px;
  color: var(--text);
  font-size: 16px;
  font-weight: 680;
  letter-spacing: -0.02em;
}

.pipeline-meta {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  margin-top: 3px;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 8.5px;
}

.pipeline-meta span + span::before {
  content: '，';
  margin-right: 4px;
  color: var(--border-strong);
}

.pipeline-head-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: none;
}

.row-action {
  display: inline-grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
}

.row-action:hover {
  border-color: var(--accent-line);
  background: var(--accent-soft);
  color: var(--accent);
}

.pipeline-progress-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 12px 0;
}

.pipeline-progress {
  flex: 1;
  height: 4px;
  overflow: hidden;
  border-radius: 3px;
  background: var(--panel-soft);
}

.pipeline-progress span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, var(--accent), #48dfa9);
  transition: width 0.5s ease;
}

.pipeline-progress-row > .mono {
  color: var(--text-secondary);
  font-size: 9px;
}

.current-stage {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 8px;
}

.stage-row {
  display: flex;
  gap: 8px;
}

.stage-tile {
  position: relative;
  min-width: 0;
  flex: 1;
  padding: 9px 10px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--panel);
}

.stage-tile.running {
  border-color: var(--accent-line);
  background: var(--accent-soft);
}

.stage-tile.succeeded {
  border-color: color-mix(in srgb, var(--accent) 24%, var(--border));
}

.stage-tile.failed {
  border-color: color-mix(in srgb, var(--red) 25%, var(--border));
  background: var(--red-soft);
}

.stage-top {
  display: flex;
  align-items: center;
  gap: 6px;
}

.stage-index {
  color: var(--text-muted);
  font-size: 8px;
}

.stage-name {
  color: var(--text-secondary);
  font-size: 10px;
  font-weight: 650;
}

.stage-status {
  margin-left: auto;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 8px;
}

.stage-tile.running .stage-status {
  color: var(--accent);
}

.stage-tile.succeeded .stage-status {
  color: var(--success);
}

.stage-tile.failed .stage-status {
  color: var(--red);
}

.stage-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 4px;
  color: var(--text-muted);
  font-size: 8.5px;
}

.stage-task-link {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 0;
  border: none;
  background: none;
  color: var(--accent);
  cursor: pointer;
  font-family: var(--mono);
  font-size: 8px;
}

.stage-task-link:hover {
  text-decoration: underline;
}

.stage-task-link .el-icon {
  font-size: 9px;
}

.stage-connector {
  position: absolute;
  top: 50%;
  right: -10px;
  z-index: 1;
  width: 10px;
  height: 1px;
  background: var(--border-strong);
}

.pipeline-failure {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: 9px;
  padding: 6px 8px;
  border-radius: 5px;
  background: var(--red-soft);
  color: var(--red);
  font-family: var(--mono);
  font-size: 8px;
}

.pipeline-foot {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 10px;
  padding-top: 9px;
  border-top: 1px dashed var(--border);
  color: var(--text-muted);
  font-size: 8px;
}

.artifact-inline {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: var(--violet);
}

.pipeline-pager {
  display: flex;
  justify-content: flex-end;
}

/* Dialog / drawer */
.create-intro {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  margin-bottom: 16px;
  padding: 9px 11px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel-soft);
  color: var(--text-muted);
  font-size: 9.5px;
  line-height: 1.6;
}

.create-intro .el-icon {
  margin-top: 2px;
  color: var(--accent);
}

.create-intro strong {
  color: var(--accent);
}

.pipeline-form .form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 16px;
}

.pipeline-form .span-2 {
  grid-column: span 2;
}

.form-section {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin-top: 4px;
  padding: 10px 0 2px;
  border-top: 1px solid var(--border);
}

.form-section strong {
  color: var(--text);
  font-size: 12px;
  font-weight: 600;
}

.form-section span {
  color: var(--text-muted);
  font-size: 11px;
  line-height: 1.55;
}

.form-hint {
  display: block;
  margin-top: 3px;
  color: var(--text-muted);
  font-size: 10px;
  line-height: 1.4;
}

.fixed-policy {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  padding: 0 11px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--panel-soft);
}

.fixed-policy strong {
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
}

.fixed-policy span {
  padding: 1px 5px;
  border-radius: 3px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 9px;
}

.switch-field {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  color: var(--text-muted);
  font-size: 11px;
  line-height: 1.45;
}

.full {
  width: 100%;
}

.pipeline-detail {
  min-height: 220px;
}

.detail-summary {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.detail-summary h3 {
  color: var(--text);
  font-size: 19px;
  letter-spacing: -0.02em;
}

.detail-summary .mono {
  color: var(--text-muted);
  font-size: 9px;
}

.detail-summary p {
  color: var(--text-secondary);
  font-size: 10px;
}

.detail-progress {
  height: 5px;
  margin: 14px 0;
  overflow: hidden;
  border-radius: 3px;
  background: var(--panel-soft);
}

.detail-progress span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, var(--accent), #48dfa9);
}

.detail-stage-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.detail-stage {
  padding: 11px 12px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--panel-soft);
}

.detail-stage-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.stage-pill {
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 7.5px;
}

.stage-pill.running {
  background: var(--accent-soft);
  color: var(--accent);
}

.stage-pill.succeeded {
  background: rgba(22, 134, 95, 0.1);
  color: var(--success);
}

.stage-pill.failed {
  background: var(--red-soft);
  color: var(--red);
}

.stage-pill.pending {
  background: var(--amber-soft);
  color: var(--amber);
}

.detail-stage p {
  margin-top: 5px;
  color: var(--red);
  font-family: var(--mono);
  font-size: 8.5px;
}

.stage-report {
  margin-top: 8px;
  padding: 9px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel);
}

.stage-kpis {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 6px;
}

.stage-kpis-eval {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.stage-kpis span {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
  padding: 5px 6px;
  border-radius: 6px;
  background: var(--panel-soft);
}

.stage-kpis strong {
  color: var(--text);
  font-family: var(--mono);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

.stage-kpis small {
  overflow: hidden;
  color: var(--text-muted);
  font-size: 8px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stage-report-note,
.detail-stage .stage-report-note {
  margin: 7px 0 0;
  color: var(--text-muted);
  font-family: inherit;
  font-size: 8.5px;
  line-height: 1.5;
}

.stage-warning,
.detail-stage .stage-warning {
  margin: 4px 0 0;
  color: var(--amber);
  font-family: inherit;
  font-size: 8.5px;
  line-height: 1.5;
}

.class-report {
  margin-top: 8px;
  padding-top: 7px;
  border-top: 1px solid var(--border);
}

.class-report-head,
.class-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.class-report-head {
  margin-bottom: 4px;
  color: var(--text-muted);
  font-size: 8.5px;
}

.class-row {
  padding: 3px 0;
  color: var(--text-secondary);
  font-size: 9px;
}

.class-row strong {
  color: var(--accent);
  font-size: 9px;
  font-weight: 600;
}

.class-more {
  display: block;
  margin-top: 4px;
  color: var(--text-muted);
  font-size: 8px;
}

.detail-task {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 7px;
  padding-top: 7px;
  border-top: 1px dashed var(--border);
}

.detail-task div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.detail-task strong {
  color: var(--text-secondary);
  font-size: 10px;
}

.detail-task .mono {
  color: var(--text-muted);
  font-size: 8px;
}

.detail-waiting {
  display: block;
  margin-top: 5px;
  color: var(--text-muted);
  font-size: 8.5px;
}

.detail-artifacts {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}

.artifact-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 7px;
}

.artifact-list button {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 8px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: var(--panel-soft);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 9px;
  text-align: left;
}

.artifact-list button:hover {
  border-color: var(--accent-line);
  color: var(--accent);
}

.artifact-list small {
  margin-left: auto;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 7.5px;
}

.empty-copy {
  margin-top: 6px;
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
  .stage-row {
    flex-direction: column;
  }

  .stage-connector {
    display: none;
  }

  .guide-artifacts {
    border-left: none;
    padding-left: 0;
  }

  .pipeline-form .form-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .pipeline-form .span-2 {
    grid-column: span 1;
  }
}

@media (max-width: 640px) {
  .pipeline-card {
    padding: 14px;
  }

  .pipeline-card-head {
    flex-direction: column;
  }

  .pipeline-title-block h2 {
    font-size: 14px;
  }

  .pipeline-guide {
    padding: 14px;
  }

  .guide-artifacts {
    display: none;
  }
}
</style>

<style scoped>
/* Premium orchestration surface */
.pipeline-guide {
  position: relative;
  padding: 21px 24px 22px;
  overflow: hidden;
  border-color: rgba(92, 245, 185, 0.17);
  background:
    radial-gradient(520px 260px at 102% -22%, rgba(84, 255, 190, 0.2), transparent 66%),
    radial-gradient(420px 240px at 0% 120%, rgba(112, 94, 242, 0.18), transparent 64%),
    linear-gradient(145deg, #09271b 0%, #061a11 58%, #04130c 100%);
  box-shadow:
    0 1px 0 rgba(255, 255, 255, 0.075) inset,
    0 30px 70px -46px rgba(2, 25, 14, 0.65);
}

.pipeline-guide::before {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(154, 255, 215, 0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(154, 255, 215, 0.035) 1px, transparent 1px);
  background-size: 34px 34px;
  mask-image: radial-gradient(circle at 60% 0%, black, transparent 72%);
}

.pipeline-guide::after {
  content: '';
  position: absolute;
  top: -1px;
  left: 11%;
  right: 11%;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(139, 255, 211, 0.45), transparent);
}

.pipeline-guide > * {
  position: relative;
  z-index: 1;
}

.pipeline-guide .guide-head h2 {
  color: #f2fff9;
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 580;
  letter-spacing: -0.03em;
}

.pipeline-guide .soft-label {
  color: #6d8e7e;
}

.pipeline-guide .tag-soft.accent {
  border-color: rgba(111, 255, 204, 0.18);
  background: rgba(66, 225, 164, 0.08);
  color: #91ffd7;
  box-shadow: 0 0 22px -14px rgba(74, 255, 190, 0.8);
}

.pipeline-guide .guide-stage {
  border-color: rgba(255, 255, 255, 0.075);
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.055), rgba(255, 255, 255, 0.018));
  box-shadow:
    0 1px 0 rgba(255, 255, 255, 0.04) inset,
    0 20px 36px -34px rgba(0, 0, 0, 0.9);
}

.pipeline-guide .guide-index {
  color: #9cffd8;
  text-shadow: 0 0 14px rgba(95, 255, 198, 0.45);
}

.pipeline-guide .guide-stage strong {
  color: #e6fff4;
}

.pipeline-guide .guide-stage span:not(.guide-index):not(.guide-arrow) {
  color: #6d8f7e;
}

.pipeline-guide .guide-arrow {
  color: #466c5a;
}

.pipeline-guide .guide-artifacts {
  border-left-color: rgba(255, 255, 255, 0.09);
}

.pipeline-guide .guide-artifacts .soft-label {
  color: #5e7e6d;
}

.pipeline-guide .guide-artifacts strong {
  color: #c6f36a;
  text-shadow: 0 0 18px rgba(198, 243, 106, 0.16);
}

.pipeline-guide .guide-artifacts > span:last-child {
  color: #5f7f6e;
}

.pipeline-card {
  padding: 18px 20px 14px;
  border-radius: 20px;
  background:
    linear-gradient(155deg, color-mix(in srgb, var(--panel) 96%, white), var(--panel));
}

.pipeline-card::before {
  content: '';
  position: absolute;
  top: 16px;
  bottom: 16px;
  left: 0;
  width: 2px;
  border-radius: 0 3px 3px 0;
  background: linear-gradient(180deg, transparent, var(--border-strong), transparent);
}

.pipeline-card.is-running::before {
  background: linear-gradient(180deg, transparent, var(--accent-bright), transparent);
  box-shadow: 0 0 16px var(--accent);
}

.pipeline-card.is-failed::before {
  background: linear-gradient(180deg, transparent, var(--red), transparent);
}

.pipeline-card.is-succeeded::before {
  background: linear-gradient(180deg, transparent, var(--accent), transparent);
}

.pipeline-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-hover);
}

.pipeline-title-block h2 {
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 600;
  letter-spacing: -0.035em;
}

.pipeline-title-row {
  gap: 9px;
}

.pipeline-id {
  color: var(--text-muted);
  font-size: 8px;
  letter-spacing: 0.02em;
}

.pipeline-meta {
  margin-top: 5px;
  color: var(--text-muted);
}

.pipeline-progress-row {
  margin: 14px 0;
}

.pipeline-progress {
  height: 5px;
  background: color-mix(in srgb, var(--panel-soft) 68%, var(--border));
  box-shadow: 0 1px 0 var(--panel-highlight) inset;
}

.pipeline-progress span {
  background: linear-gradient(90deg, var(--accent-strong), var(--accent-bright) 58%, var(--lime));
  box-shadow: 0 0 14px rgba(57, 223, 160, 0.35);
}

.stage-row {
  gap: 10px;
}

.stage-tile {
  padding: 11px 12px;
  border-color: color-mix(in srgb, var(--border) 88%, transparent);
  border-radius: 11px;
  background:
    linear-gradient(145deg, color-mix(in srgb, var(--panel-soft) 82%, transparent), var(--panel));
  transition: border-color 0.2s ease, background 0.2s ease;
}

.stage-tile:hover {
  border-color: var(--accent-line);
}

.stage-tile.succeeded {
  border-color: color-mix(in srgb, var(--accent) 22%, var(--border));
  background:
    linear-gradient(145deg, color-mix(in srgb, var(--accent-soft) 44%, var(--panel)), var(--panel));
}

.stage-tile.running {
  border-color: color-mix(in srgb, var(--accent) 36%, var(--border));
  background:
    radial-gradient(180px 80px at 100% 0%, var(--accent-soft), transparent 70%),
    var(--panel);
  box-shadow: 0 0 30px -24px var(--accent);
}

.stage-index {
  color: var(--accent);
  font-size: 8px;
  font-weight: 650;
}

.stage-name {
  font-size: 10.5px;
  font-weight: 650;
}

.stage-task-link {
  color: var(--accent-strong);
}

.stage-connector {
  right: -12px;
  width: 12px;
  background: linear-gradient(90deg, var(--border-strong), transparent);
}

.pipeline-foot {
  margin-top: 12px;
  padding-top: 10px;
  border-top-color: color-mix(in srgb, var(--border) 65%, transparent);
}

.pipeline-failure {
  border: 1px solid color-mix(in srgb, var(--red) 16%, transparent);
}

.pipeline-dialog .create-intro {
  background:
    radial-gradient(240px 120px at 100% 0%, var(--accent-soft), transparent 70%),
    var(--panel-soft);
}

.pipeline-dialog :deep(.el-dialog__body) {
  max-height: calc(100vh - 280px);
  overflow-y: auto;
}

:global(.pipeline-dialog) {
  margin-top: 8vh !important;
}

:global(.pipeline-dialog .el-dialog__body) {
  max-height: calc(100vh - 280px);
  overflow-y: auto;
}

.detail-summary h3 {
  font-family: var(--font-display);
  font-weight: 600;
}

.detail-stage {
  border-radius: 11px;
}

@media (max-width: 900px) {
  .pipeline-guide {
    padding: 17px;
  }

  .pipeline-card {
    padding: 16px;
  }
}
</style>

<style scoped>
.pipelines-page {
  gap: 24px;
}

.auto-refresh {
  height: 40px;
  font-size: 13px;
}

.pipeline-guide {
  padding: 20px;
  overflow: hidden;
  border: 1px solid #263a2e;
  border-radius: var(--radius);
  background: #0c1a13;
  box-shadow: none;
}

.pipeline-guide::before,
.pipeline-guide::after {
  display: none;
}

.pipeline-guide .guide-head h2 {
  margin-top: 4px;
  color: #eef7f1;
  font-family: var(--font);
  font-size: 22px;
  font-weight: 600;
}

.pipeline-guide .soft-label {
  color: #91ab9c;
  font-size: 12px;
}

.pipeline-guide .tag-soft.accent {
  border-color: rgba(75, 195, 148, 0.25);
  background: rgba(75, 195, 148, 0.08);
  color: #9fe0c3;
  font-size: 12px;
  box-shadow: none;
}

.guide-stages {
  margin-top: 18px;
}

.guide-stage {
  padding: 12px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 6px;
  background: transparent;
  box-shadow: none;
}

.pipeline-guide .guide-index {
  color: #77b999;
  font-size: 12px;
  text-shadow: none;
}

.pipeline-guide .guide-stage strong {
  color: #e6fff4;
  font-size: 14px;
}

.pipeline-guide .guide-stage span:not(.guide-index):not(.guide-arrow) {
  color: #8ca797;
  font-size: 12px;
}

.pipeline-guide .guide-arrow {
  color: #5d806d;
}

.guide-artifacts {
  border-left-color: rgba(255, 255, 255, 0.1);
}

.pipeline-guide .guide-artifacts strong {
  color: #b7d9c7;
  font-size: 14px;
  text-shadow: none;
}

.pipeline-guide .guide-artifacts > span:last-child {
  color: #8ca797;
  font-size: 12px;
}

.pipeline-card {
  padding: 20px;
  border-radius: var(--radius);
  box-shadow: none;
  transition: border-color 0.16s ease;
}

.pipeline-card::before {
  display: none;
}

.pipeline-card:hover {
  border-color: var(--border-strong);
  transform: none;
  box-shadow: none;
}

.pipeline-card.is-running,
.pipeline-card.is-failed,
.pipeline-card.is-succeeded {
  box-shadow: none;
}

.pipeline-title-block h2 {
  font-family: var(--font);
  font-size: 18px;
  font-weight: 600;
}

.pipeline-id {
  color: var(--text-muted);
  font-size: 12px;
}

.pipeline-time {
  font-size: 12px;
}

.pipeline-progress-row .mono {
  font-size: 12px;
}

.pipeline-meta {
  margin-top: 6px;
  font-size: 13px;
}

.pipeline-progress-row {
  margin: 16px 0;
}

.pipeline-progress {
  height: 6px;
  border-radius: 3px;
}

.pipeline-progress span {
  border-radius: 3px;
  background: var(--accent);
  box-shadow: none;
}

.stage-row {
  gap: 12px;
}

.stage-tile {
  padding: 12px;
  border-radius: 6px;
  box-shadow: none;
}

.stage-tile:hover {
  border-color: var(--border-strong);
}

.stage-index {
  color: var(--text-muted);
  font-size: 12px;
}

.stage-name {
  font-size: 13px;
  font-weight: 600;
}

.stage-status {
  font-size: 12px;
}

.stage-hint,
.stage-task-link {
  font-size: 12px;
  line-height: 1.6;
}

.stage-connector {
  color: var(--border-strong);
}

.pipeline-foot {
  margin-top: 14px;
  padding-top: 12px;
  font-size: 13px;
}

.pipeline-failure {
  font-size: 13px;
}

.detail-stage {
  border-radius: 6px;
}

.detail-task strong {
  font-size: 13px;
}

.detail-task .mono,
.detail-waiting {
  font-size: 12px;
}

.create-intro {
  font-size: 14px;
  line-height: 1.7;
}

.pipeline-pager {
  font-size: 13px;
}

@media (max-width: 900px) {
  .pipeline-guide {
    padding: 16px;
  }
}
</style>

<style scoped>
/* Ultralytics-inspired light pipeline surfaces */
.pipeline-guide {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--panel);
  box-shadow: none;
}

.pipeline-guide .guide-head h2 {
  color: var(--text);
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 600;
  letter-spacing: 0;
}

.pipeline-guide .soft-label {
  color: var(--text-muted);
  font-size: 12px;
}

.pipeline-guide .tag-soft.accent {
  border-color: var(--accent-line);
  background: var(--accent-soft);
  color: var(--accent);
  box-shadow: none;
}

.pipeline-guide .guide-stage {
  border-color: var(--border);
  border-radius: 10px;
  background: var(--panel-soft);
  box-shadow: none;
}

.pipeline-guide .guide-index {
  color: var(--accent);
  font-size: 12px;
  text-shadow: none;
}

.pipeline-guide .guide-stage strong {
  color: var(--text);
  font-size: 14px;
}

.pipeline-guide .guide-stage span:not(.guide-index):not(.guide-arrow) {
  color: var(--text-muted);
  font-size: 12px;
}

.pipeline-guide .guide-arrow {
  color: var(--text-muted);
}

.pipeline-guide .guide-artifacts {
  border-left-color: var(--border);
}

.pipeline-guide .guide-artifacts .soft-label {
  color: var(--text-muted);
}

.pipeline-guide .guide-artifacts strong {
  color: var(--accent);
  font-size: 14px;
  text-shadow: none;
}

.pipeline-guide .guide-artifacts > span:last-child {
  color: var(--text-muted);
  font-size: 12px;
}

.pipeline-card {
  border-radius: var(--radius);
  box-shadow: none;
}

.pipeline-card:hover {
  box-shadow: none;
}

.stage-tile {
  border-radius: 10px;
  background: var(--panel-soft);
  box-shadow: none;
}

.stage-tile.succeeded {
  border-color: color-mix(in srgb, var(--success) 24%, var(--border));
  background: var(--success-soft);
}

.stage-tile.running {
  border-color: var(--accent-line);
  background: var(--accent-soft);
  box-shadow: none;
}

.stage-task-link {
  color: var(--accent);
}

.stage-pill {
  border-radius: 999px;
}

.stage-pill.succeeded {
  background: var(--success-soft);
  color: var(--success);
}

.stage-connector {
  background: var(--border-strong);
}
</style>
