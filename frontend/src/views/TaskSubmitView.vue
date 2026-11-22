<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import {
  ArrowRight,
  CopyDocument,
  Cpu,
  InfoFilled,
  MagicStick,
  Promotion,
  SetUp,
  Timer,
} from '@element-plus/icons-vue'

import { api } from '@/api/client'
import type { Dataset } from '@/types/p2'
import type { PreviewResponse, TaskCreatePayload, TaskOptions } from '@/types/task'
import { copyText, formatDuration } from '@/utils/format'

const router = useRouter()
const route = useRoute()
const formRef = ref<FormInstance>()
const options = ref<TaskOptions | null>(null)
const datasets = ref<Dataset[]>([])
const loadingOptions = ref(true)
const pageError = ref<string | null>(null)
const submitting = ref(false)
const previewing = ref(false)
const preview = ref<PreviewResponse | null>(null)
const previewError = ref<string | null>(null)

const form = reactive<TaskCreatePayload>({
  name: '',
  model: 'yolov8n.pt',
  dataset: 'coco128.yaml',
  dataset_id: null,
  quality_ack: false,
  epochs: 3,
  imgsz: 160,
  batch_size: 4,
  workers: 0,
  cpu_cores: 4,
  compute_threads: null,
  cache_mode: 'none',
  seed: 0,
  patience: null,
  save_period: 5,
  priority: 50,
  max_runtime_seconds: 1800,
  max_retries: 3,
  notes: '',
})

let previewTimer: number | undefined
let previewSeq = 0

const rules: FormRules = {
  model: [{ required: true, message: '请选择模型权重', trigger: 'change' }],
  dataset: [{ required: true, message: '请选择数据集', trigger: 'change' }],
}

const maxCpu = computed(() => options.value?.limits.cpu_cores?.[1] ?? options.value?.user_cpu_quota ?? 8)
const totalCpus = computed(() => options.value?.total_cpus ?? 8)
const quota = computed(() => options.value?.user_cpu_quota ?? 8)
const maxRuntime = computed(() => options.value?.limits.max_runtime_seconds?.[1] ?? 1800)
const maxRetries = computed(() => options.value?.limits.max_retries?.[1] ?? 3)
const imageSizes = [64, 96, 128, 160, 224, 320, 480, 640]
const backendLabel = computed(() =>
  options.value?.execution_backend === 'ray' ? 'Ray 执行器' : '本地进程',
)
const assetsMissing = computed(
  () => options.value != null && (options.value.models.length === 0 || options.value.datasets.length === 0),
)
const datasetRecord = computed(() => {
  const current = form.dataset
  return datasets.value.find((item) => {
    const yamlName = item.yaml_path.split('/').pop()
    return yamlName === current || item.name === current
  })
})
const qualityBlocked = computed(
  () =>
    datasetRecord.value != null &&
    ['pending', 'auditing', 'blocked', 'failed'].includes(datasetRecord.value.quality_status),
)
const qualityNeedsAck = computed(
  () => datasetRecord.value?.quality_status === 'warning' && !form.quality_ack,
)
const qualityLabel = computed(() => {
  const status = datasetRecord.value?.quality_status
  return {
    passed: '已通过',
    warning: '有警告',
    pending: '待体检',
    auditing: '体检中',
    blocked: '已阻断',
    failed: '体检失败',
  }[status ?? 'pending'] ?? '未体检'
})

const cpuMarks = computed(() => {
  const marks: Record<number, string> = { 1: '1' }
  const max = maxCpu.value
  if (max <= 8) {
    for (let i = 1; i <= max; i += 1) marks[i] = String(i)
  } else {
    marks[1] = '1'
    marks[Math.round(max / 2)] = String(Math.round(max / 2))
    marks[max] = String(max)
  }
  return marks
})

const priorityLabel = computed(() => {
  if (form.priority >= 80) return '高优先级'
  if (form.priority >= 40) return '普通优先级'
  return '低优先级'
})

const timeoutLabel = computed(() => formatDuration(form.max_runtime_seconds))

function warnPolicyLimit(label: string, value: number): void {
  ElMessage.warning(`${label}最多为 ${value}，已按后台配置限制`)
}

function handleRuntimeChange(value: number | undefined) {
  if (value != null && value > maxRuntime.value) {
    form.max_runtime_seconds = maxRuntime.value
    warnPolicyLimit('运行时长', maxRuntime.value)
  }
  schedulePreview()
}

function handleRetriesChange(value: number | undefined) {
  if (value != null && value > maxRetries.value) {
    form.max_retries = maxRetries.value
    warnPolicyLimit('失败重试次数', maxRetries.value)
  }
  schedulePreview()
}

const coreStress = computed(() => {
  const ratio = form.cpu_cores / Math.max(1, totalCpus.value)
  if (ratio >= 0.9) return { label: '独占大部分算力', tone: 'high' }
  if (ratio >= 0.6) return { label: '较高并发占用', tone: 'mid' }
  return { label: '轻量资源占用', tone: 'low' }
})

function normalizedPayload(): TaskCreatePayload {
  return {
    ...form,
    name: form.name?.trim() ?? '',
    notes: form.notes?.trim() || null,
    epochs: Number(form.epochs),
    imgsz: Number(form.imgsz),
    batch_size: Number(form.batch_size),
    workers: Number(form.workers),
    cpu_cores: Number(form.cpu_cores),
    priority: Number(form.priority),
    max_runtime_seconds: Number(form.max_runtime_seconds),
    max_retries: Number(form.max_retries),
  }
}

function schedulePreview() {
  if (!options.value) return
  if (previewTimer) window.clearTimeout(previewTimer)
  previewTimer = window.setTimeout(runPreview, 320)
}

async function runPreview() {
  const seq = ++previewSeq
  previewing.value = true
  try {
    const result = await api.previewTask(normalizedPayload())
    if (seq !== previewSeq) return
    preview.value = result
    previewError.value = null
  } catch (err) {
    if (seq !== previewSeq) return
    previewError.value = err instanceof Error ? err.message : '无法生成预览'
  } finally {
    if (seq === previewSeq) previewing.value = false
  }
}

async function loadOptions() {
  try {
    const [data, datasetList] = await Promise.all([api.getOptions(), api.listDatasets()])
    options.value = data
    datasets.value = datasetList
    pageError.value = null
    const requestedModel =
      typeof route.query.model === 'string' && data.models.includes(route.query.model)
        ? route.query.model
        : null
    Object.assign(form, {
      model:
        requestedModel ??
        (data.models.includes(data.defaults.model) ? data.defaults.model : data.models[0] ?? data.defaults.model),
      dataset: data.datasets.includes(data.defaults.dataset)
        ? data.defaults.dataset
        : data.datasets[0] ?? data.defaults.dataset,
      epochs: data.defaults.epochs,
      imgsz: data.defaults.imgsz,
      batch_size: data.defaults.batch_size,
      cpu_cores: Math.min(
        data.defaults.cpu_cores,
        data.limits.cpu_cores?.[1] ?? data.user_cpu_quota,
      ),
      priority: data.defaults.priority,
      max_runtime_seconds: Math.min(
        data.defaults.max_runtime_seconds,
        data.limits.max_runtime_seconds?.[1] ?? data.defaults.max_runtime_seconds,
      ),
      max_retries: Math.min(
        data.defaults.max_retries,
        data.limits.max_retries?.[1] ?? data.defaults.max_retries,
      ),
      workers: data.defaults.workers,
    })
    const selected = datasetRecord.value
    form.dataset_id = selected?.id ?? null
    await nextTick()
    runPreview()
  } catch (err) {
    pageError.value = err instanceof Error ? err.message : '无法读取训练平台配置'
  } finally {
    loadingOptions.value = false
  }
}

async function submit() {
  if (!formRef.value) return
  if (form.max_runtime_seconds > maxRuntime.value) {
    warnPolicyLimit('运行时长', maxRuntime.value)
    return
  }
  if (form.max_retries > maxRetries.value) {
    warnPolicyLimit('失败重试次数', maxRetries.value)
    return
  }
  if (form.cpu_cores > maxCpu.value) {
    warnPolicyLimit('CPU 核数', maxCpu.value)
    return
  }
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  if (qualityBlocked.value) {
    ElMessage.warning(`数据集${qualityLabel.value}，请先处理数据质量报告`)
    return
  }
  if (qualityNeedsAck.value) {
    ElMessage.warning('该数据集有质量警告，请先确认再提交')
    return
  }
  submitting.value = true
  try {
    const task = await api.createTask(normalizedPayload())
    ElMessage.success({
      message: `任务 ${task.id} 已提交，正在进入调度队列`,
      duration: 3200,
    })
    router.push({ name: 'tasks' })
  } catch (err) {
    ElMessage.error({
      message: err instanceof Error ? err.message : '提交失败',
      duration: 5000,
    })
  } finally {
    submitting.value = false
  }
}

function applyPreset(kind: 'quick' | 'standard') {
  if (kind === 'quick') {
    Object.assign(form, {
      epochs: 1,
      imgsz: 96,
      batch_size: 4,
      cpu_cores: Math.min(4, maxCpu.value),
      max_runtime_seconds: Math.min(300, maxRuntime.value),
      priority: 50,
    })
  } else {
    Object.assign(form, {
      epochs: 3,
      imgsz: 160,
      batch_size: 4,
      cpu_cores: Math.min(6, maxCpu.value),
      max_runtime_seconds: Math.min(1800, maxRuntime.value),
      priority: 60,
    })
  }
}

async function copyCommand() {
  if (!preview.value) return
  const ok = await copyText(preview.value.command)
  ElMessage({
    message: ok ? '命令已复制' : '复制失败',
    type: ok ? 'success' : 'warning',
  })
}

watch(form, schedulePreview, { deep: true })

watch(
  () => form.dataset,
  () => {
    const selected = datasetRecord.value
    form.dataset_id = selected?.id ?? null
    form.quality_ack = false
    if (selected && ['pending', 'auditing'].includes(selected.quality_status)) {
      window.setTimeout(async () => {
        try {
          datasets.value = await api.listDatasets()
        } catch {
          // The submit gate still protects the API path.
        }
      }, 1500)
    }
  },
)

async function refreshPolicyLimits() {
  try {
    const data = await api.getOptions()
    options.value = data
    if (form.max_runtime_seconds > maxRuntime.value) {
      form.max_runtime_seconds = maxRuntime.value
      warnPolicyLimit('运行时长', maxRuntime.value)
    }
    if (form.max_retries > maxRetries.value) {
      form.max_retries = maxRetries.value
      warnPolicyLimit('失败重试次数', maxRetries.value)
    }
    if (form.cpu_cores > maxCpu.value) {
      form.cpu_cores = maxCpu.value
      warnPolicyLimit('CPU 核数', maxCpu.value)
    }
    schedulePreview()
  } catch {
    // The next submit will still be rejected by the server if policy changed.
  }
}

async function refreshModelOptions() {
  try {
    const data = await api.getOptions()
    options.value = data
    if (!data.models.includes(form.model)) {
      form.model = data.models[0] ?? form.model
    }
  } catch {
    // A stale model name will still be rejected by the server on preview/submit.
  }
}

onMounted(() => {
  loadOptions()
  window.addEventListener('litetrain:settings-updated', refreshPolicyLimits)
  window.addEventListener('litetrain:models-updated', refreshModelOptions)
})

onBeforeUnmount(() => {
  if (previewTimer) window.clearTimeout(previewTimer)
  window.removeEventListener('litetrain:settings-updated', refreshPolicyLimits)
  window.removeEventListener('litetrain:models-updated', refreshModelOptions)
})
</script>

<template>
  <div class="page submit-page">
    <div class="page-head">
      <div class="page-heading">
        <span class="eyebrow">新建训练</span>
        <h1 class="page-title">提交训练任务</h1>
      </div>
      <div class="page-actions">
        <el-button :icon="Timer" @click="applyPreset('quick')">快速试跑 · 1 轮</el-button>
        <el-button :icon="MagicStick" @click="applyPreset('standard')">标准示例 · 3 轮</el-button>
      </div>
    </div>

    <el-alert v-if="pageError" :title="pageError" type="error" show-icon :closable="false" />

    <el-alert
      v-if="assetsMissing"
      title="训练资产未就绪"
      type="warning"
      show-icon
      :closable="false"
    >
      <span>
        请先运行 <code class="code-inline">python scripts/download_assets.py</code> 下载
        yolov8n.pt 和 coco128，然后刷新此页面。
      </span>
    </el-alert>

    <div class="submit-layout">
      <div class="form-column">
        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          label-position="top"
          class="task-form"
          @submit.prevent
        >
          <section class="panel form-section">
            <div class="section-heading">
              <div>
                <h2>任务标识</h2>
                <p>给这次实验一个可读的名字，留空时平台会自动生成。</p>
              </div>
            </div>
            <div class="form-grid">
              <el-form-item label="任务名称" prop="name" class="span-2">
                <el-input
                  v-model="form.name"
                  maxlength="100"
                  show-word-limit
                  placeholder="例如：YOLOv8n / coco128 / CPU 基线"
                />
              </el-form-item>
              <el-form-item label="备注" class="span-2">
                <el-input
                  v-model="form.notes"
                  type="textarea"
                  :rows="2"
                  maxlength="500"
                  show-word-limit
                  placeholder="可选：记录本次实验的目的或超参数改动"
                />
              </el-form-item>
            </div>
          </section>

          <section class="panel form-section">
            <div class="section-heading">
              <div>
                <h2>训练配方</h2>
                <p>使用内置 coco128 数据集，权重会在 CPU 设备上训练。</p>
              </div>
              <span class="section-badge"><el-icon><SetUp /></el-icon> YOLO 检测</span>
            </div>

            <el-skeleton v-if="loadingOptions" :rows="4" animated />

            <div v-else class="form-grid">
              <el-form-item label="模型权重" prop="model">
                <el-select v-model="form.model" placeholder="选择权重" class="full">
                  <el-option v-for="model in options?.models ?? []" :key="model" :label="model" :value="model" />
                  <el-option v-if="!(options?.models ?? []).length" label="未发现 .pt 权重" value="" disabled />
                </el-select>
              </el-form-item>

              <el-form-item label="数据集" prop="dataset">
                <el-select v-model="form.dataset" placeholder="选择数据集" class="full">
                  <el-option
                    v-for="dataset in options?.datasets ?? []"
                    :key="dataset"
                    :label="dataset"
                    :value="dataset"
                  />
                  <el-option v-if="!(options?.datasets ?? []).length" label="未发现 YAML 数据集" value="" disabled />
                </el-select>
              </el-form-item>

              <div
                v-if="datasetRecord"
                class="quality-gate span-2"
                :class="{
                  blocked: qualityBlocked,
                  warning: datasetRecord.quality_status === 'warning',
                }"
              >
                <div>
                  <strong>数据质量：{{ qualityLabel }}</strong>
                  <span v-if="qualityBlocked">
                    该数据集尚未通过体检，请到数据与产物页处理后再提交。
                  </span>
                  <span v-else-if="datasetRecord.quality_status === 'warning'">
                    存在警告项，确认后才能训练。建议先查看质量报告。
                  </span>
                  <span v-else>
                    {{ datasetRecord.image_count }} 张图 / {{ datasetRecord.label_count }} 个标签，可以进入训练。
                  </span>
                </div>
                <el-checkbox
                  v-if="datasetRecord.quality_status === 'warning'"
                  v-model="form.quality_ack"
                >
                  我已确认质量警告
                </el-checkbox>
                <el-button text size="small" @click="router.push({ name: 'assets' })">
                  查看报告
                </el-button>
              </div>

              <el-form-item label="训练轮数">
                <el-input-number v-model="form.epochs" :min="1" :max="50" :step="1" controls-position="right" class="full" />
              </el-form-item>

              <el-form-item label="图像尺寸">
                <el-select v-model="form.imgsz" class="full">
                  <el-option v-for="size in imageSizes" :key="size" :label="`${size} × ${size}`" :value="size" />
                </el-select>
              </el-form-item>

              <el-form-item label="批大小">
                <el-input-number v-model="form.batch_size" :min="1" :max="64" :step="1" controls-position="right" class="full" />
              </el-form-item>

              <el-form-item label="数据线程">
                <el-input-number v-model="form.workers" :min="0" :max="8" :step="1" controls-position="right" class="full" />
                <span class="field-hint">Windows / Ray 演示建议保持 0</span>
              </el-form-item>
            </div>
          </section>

          <section class="panel form-section">
            <div class="section-heading">
              <div>
                <h2>资源与调度</h2>
                <p>平台按逻辑 CPU 核数分配资源；资源不足时任务会排队，不会超卖。</p>
              </div>
              <span class="section-badge subtle"><el-icon><Cpu /></el-icon> 仅 CPU</span>
            </div>

            <div class="resource-grid">
              <div class="resource-block">
                <div class="resource-label-row">
                  <span class="el-form-item__label">CPU 核数</span>
                  <span class="resource-value">
                    <strong>{{ form.cpu_cores }}</strong>
                    <small>/ {{ maxCpu }} 核</small>
                  </span>
                </div>
                <el-slider
                  v-model="form.cpu_cores"
                  :min="1"
                  :max="maxCpu"
                  :step="1"
                  :marks="cpuMarks"
                  show-stops
                />
                <div class="resource-note">
                  <span class="tag-soft" :class="coreStress.tone">{{ coreStress.label }}</span>
                  <span>单用户配额 {{ quota }} 核，集群上限 {{ totalCpus }} 核</span>
                </div>
              </div>

              <div class="resource-block compact-block">
                <span class="el-form-item__label">计算线程</span>
                <el-input-number
                  v-model="form.compute_threads"
                  :min="1"
                  :max="form.cpu_cores"
                  :step="1"
                  controls-position="right"
                />
                <span class="field-hint">留空时按 CPU 配额；推荐值来自 Benchmark</span>
              </div>

              <div class="resource-block compact-block">
                <span class="el-form-item__label">数据缓存</span>
                <el-select v-model="form.cache_mode" class="full">
                  <el-option label="不缓存" value="none" />
                  <el-option label="磁盘缓存" value="disk" />
                  <el-option label="内存缓存" value="ram" />
                </el-select>
                <span class="field-hint">先跑 Benchmark，确认 I/O 瓶颈后再开启</span>
              </div>

              <div class="resource-block">
                <div class="resource-label-row">
                  <span class="el-form-item__label">优先级</span>
                  <span class="resource-value">
                    <strong>{{ form.priority }}</strong>
                    <small>{{ priorityLabel }}</small>
                  </span>
                </div>
                <el-slider v-model="form.priority" :min="0" :max="100" :step="5" show-stops :marks="{ 0: '低', 50: '普通', 80: '高', 100: '顶' }" />
                <div class="resource-note">
                  <span>数值越大越先被调度</span>
                  <span class="mono">优先级 {{ form.priority }}</span>
                </div>
              </div>

              <div class="resource-block compact-block">
                <span class="el-form-item__label">最大运行时长</span>
                <div class="inline-field">
                  <el-input-number
                    v-model="form.max_runtime_seconds"
                    :min="options?.limits.max_runtime_seconds?.[0] ?? 10"
                    :max="86400"
                    :step="10"
                    controls-position="right"
                    @change="handleRuntimeChange"
                  />
                  <span class="field-suffix">秒</span>
                </div>
                <span class="field-hint">超时后平台会终止训练进程并释放资源 · 当前 {{ timeoutLabel }} · 后台上限 {{ formatDuration(maxRuntime) }}</span>
              </div>

              <div class="resource-block compact-block">
                <span class="el-form-item__label">失败自动重试</span>
                <div class="inline-field">
                  <el-input-number
                    v-model="form.max_retries"
                    :min="0"
                    :max="5"
                    :step="1"
                    controls-position="right"
                    @change="handleRetriesChange"
                  />
                  <span class="field-suffix">次</span>
                </div>
                <span class="field-hint">仅对训练进程失败自动重试；超时或取消不会重试 · 后台上限 {{ maxRetries }} 次</span>
              </div>
            </div>
          </section>

          <div class="form-note">
            <el-icon><InfoFilled /></el-icon>
            <span>
              任务提交后可在任务详情页查看增量日志、YOLO 指标和资源采样。取消排队或运行中的任务都会立即回收 CPU 配额。
            </span>
          </div>
        </el-form>
      </div>

      <aside class="preview-column">
        <section class="panel preview-panel">
          <div class="preview-head">
            <div>
              <span class="soft-label">执行预览</span>
              <h2>任务预览</h2>
            </div>
            <span class="preview-status">
              <i></i>{{ options?.execution_backend === 'ray' ? 'Ray 执行器' : '本地 CPU' }}
            </span>
          </div>

          <div class="preview-summary">
            <div class="preview-core">
              <div class="core-orb">
                <strong>{{ form.cpu_cores }}</strong>
                <span>核</span>
              </div>
              <div class="core-copy">
                <strong>{{ form.epochs }} 轮 × {{ form.imgsz }} px</strong>
                <span>{{ form.model }} · {{ form.dataset }}</span>
              </div>
            </div>
            <div class="preview-meta">
              <span><el-icon><Cpu /></el-icon>优先级 {{ form.priority }}</span>
              <span><el-icon><Timer /></el-icon>超时 {{ timeoutLabel }}</span>
            </div>
          </div>

          <div class="preview-card">
            <div class="preview-card-head">
              <span>训练命令</span>
              <button type="button" title="复制命令" @click="copyCommand">
                <el-icon><CopyDocument /></el-icon>
              </button>
            </div>
            <pre v-if="preview">{{ preview.command }}</pre>
            <div v-else-if="previewError" class="preview-error">{{ previewError }}</div>
            <div v-else class="preview-skeleton">
              <el-skeleton :rows="3" animated />
            </div>
            <span v-if="previewing" class="preview-loading">更新预览中…</span>
          </div>

          <div class="preview-equivalent" v-if="preview">
            <span class="soft-label">等价 YOLO 命令</span>
            <code>{{ preview.equivalent_yolo_command }}</code>
          </div>

          <div class="preview-estimate" v-if="preview">
            <el-icon><Timer /></el-icon>
            <span>{{ preview.estimated_note }}</span>
          </div>

          <div class="preview-policy">
            <div class="policy-row">
              <span class="dot"></span>
              <span>先到先得 + 优先级调度</span>
              <strong>{{ totalCpus }} 核</strong>
            </div>
            <div class="policy-row">
              <span class="dot amber"></span>
              <span>资源不足自动排队</span>
              <strong>{{ backendLabel }}</strong>
            </div>
            <div class="policy-row">
              <span class="dot violet"></span>
              <span>心跳与超时自动回收</span>
              <strong>看门狗</strong>
            </div>
          </div>

          <el-button
            type="primary"
            size="large"
            class="submit-button"
            :loading="submitting"
            :icon="Promotion"
            @click="submit"
          >
            提交 YOLO 训练任务
            <el-icon class="submit-arrow"><ArrowRight /></el-icon>
          </el-button>
          <p class="submit-hint">
            提交即进入调度队列。可在任务列表实时跟进状态与日志。
          </p>
        </section>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.submit-page {
  gap: 18px;
}

.submit-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(330px, 0.8fr);
  gap: 18px;
  align-items: start;
}

.form-column {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.task-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.form-section {
  padding: 20px 22px 22px;
}

.section-heading {
  display: flex;
  align-items: flex-start;
  gap: 11px;
  margin-bottom: 20px;
}

.section-number {
  display: inline-grid;
  place-items: center;
  width: 24px;
  height: 24px;
  flex: none;
  border: 1px solid var(--accent-line);
  border-radius: 7px;
  background: var(--accent-soft);
  color: var(--accent);
  font-family: var(--mono);
  font-size: 9px;
  font-weight: 700;
}

.section-heading > div {
  min-width: 0;
  flex: 1;
}

.section-heading h2 {
  color: var(--text);
  font-size: 14px;
  font-weight: 650;
  letter-spacing: -0.01em;
}

.section-heading p {
  margin-top: 3px;
  color: var(--text-muted);
  font-size: 10.5px;
  line-height: 1.6;
}

.section-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex: none;
  padding: 4px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--panel-soft);
  color: var(--text-secondary);
  font-family: var(--mono);
  font-size: 8px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.section-badge .el-icon {
  color: var(--accent);
  font-size: 11px;
}

.section-badge.subtle {
  color: var(--text-muted);
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 2px 18px;
}

.span-2 {
  grid-column: span 2;
}

.full {
  width: 100%;
}

.field-hint {
  display: block;
  margin-top: 4px;
  color: var(--text-muted);
  font-size: 9px;
  line-height: 1.5;
}

.resource-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.resource-block {
  min-width: 0;
  padding: 14px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--panel-soft);
}

.resource-label-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.resource-value {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  color: var(--accent);
}

.resource-value strong {
  font-family: var(--mono);
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.05em;
}

.resource-value small {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 8.5px;
}

.resource-block :deep(.el-slider) {
  margin: 17px 4px 12px;
}

.resource-block :deep(.el-slider__marks-text) {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 8px;
}

.resource-note {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  color: var(--text-muted);
  font-size: 8.5px;
}

.resource-note .tag-soft {
  padding: 3px 6px;
  font-size: 8px;
}

.resource-note .tag-soft.low {
  border-color: var(--accent-line);
  background: var(--accent-soft);
  color: var(--accent);
}

.resource-note .tag-soft.mid {
  border-color: color-mix(in srgb, var(--amber) 30%, transparent);
  background: var(--amber-soft);
  color: var(--amber);
}

.resource-note .tag-soft.high {
  border-color: color-mix(in srgb, var(--red) 30%, transparent);
  background: var(--red-soft);
  color: var(--red);
}

.compact-block {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.inline-field {
  display: flex;
  align-items: center;
  gap: 8px;
}

.inline-field :deep(.el-input-number) {
  flex: 1;
}

.field-suffix {
  color: var(--text-muted);
  font-size: 10px;
}

.form-note {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 11px 14px;
  border: 1px dashed var(--border-strong);
  border-radius: 10px;
  background: color-mix(in srgb, var(--panel-soft) 65%, transparent);
  color: var(--text-muted);
  font-size: 9.5px;
  line-height: 1.6;
}

.form-note .el-icon {
  margin-top: 1px;
  flex: none;
  color: var(--accent);
  font-size: 13px;
}

.preview-column {
  position: sticky;
  top: 84px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}

.preview-panel {
  padding: 19px;
  border-color: var(--accent-line);
  background:
    radial-gradient(380px 260px at 100% 0%, var(--accent-soft), transparent 70%),
    var(--panel);
}

.preview-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 16px;
}

.preview-head h2 {
  margin-top: 3px;
  color: var(--text);
  font-size: 17px;
  font-weight: 680;
  letter-spacing: -0.02em;
}

.preview-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 7px;
  border: 1px solid var(--accent-line);
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-family: var(--mono);
  font-size: 7.5px;
  font-weight: 700;
  letter-spacing: 0.05em;
}

.preview-status i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: currentColor;
  animation: status-blink 2s ease-in-out infinite;
}

.preview-summary {
  padding: 14px;
  border: 1px solid var(--border);
  border-radius: 13px;
  background: var(--panel-soft);
}

.preview-core {
  display: flex;
  align-items: center;
  gap: 12px;
}

.core-orb {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  flex: none;
  border: 1px solid var(--accent-line);
  border-radius: 14px;
  background: var(--accent-soft);
  color: var(--accent);
}

.core-orb strong {
  font-family: var(--mono);
  font-size: 21px;
  font-weight: 700;
  line-height: 1;
  letter-spacing: -0.05em;
}

.core-orb span {
  margin-top: 2px;
  font-family: var(--mono);
  font-size: 7px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.core-copy {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.core-copy strong {
  color: var(--text);
  font-size: 12px;
  font-weight: 600;
}

.core-copy span {
  overflow: hidden;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preview-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 11px;
  padding-top: 10px;
  border-top: 1px dashed var(--border);
  color: var(--text-muted);
  font-size: 8.5px;
}

.preview-meta span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.preview-meta .el-icon {
  color: var(--accent);
  font-size: 11px;
}

.preview-card {
  position: relative;
  margin-top: 12px;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 11px;
  background: var(--terminal);
}

.preview-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  color: #587a69;
  font-family: var(--mono);
  font-size: 7.5px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.preview-card-head button {
  display: inline-grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 5px;
  background: transparent;
  color: #789888;
  cursor: pointer;
  font-size: 10px;
}

.preview-card pre {
  max-height: 170px;
  margin: 0;
  padding: 11px 12px;
  overflow: auto;
  color: #b8d8c8;
  font-family: var(--mono);
  font-size: 9.5px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
}

.preview-error {
  padding: 12px;
  color: #f5a3a3;
  font-size: 9.5px;
  line-height: 1.55;
}

.preview-skeleton {
  padding: 10px;
}

.preview-loading {
  position: absolute;
  right: 10px;
  bottom: 7px;
  color: #4ea47c;
  font-family: var(--mono);
  font-size: 7.5px;
}

.preview-equivalent {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin-top: 11px;
}

.preview-equivalent code {
  display: block;
  padding: 8px 9px;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel-soft);
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 8px;
  line-height: 1.6;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preview-estimate {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: 10px;
  color: var(--text-muted);
  font-size: 8.5px;
}

.preview-estimate .el-icon {
  color: var(--amber);
  font-size: 11px;
}

.preview-policy {
  display: flex;
  flex-direction: column;
  gap: 7px;
  margin: 15px 0;
  padding: 11px 12px;
  border: 1px dashed var(--border);
  border-radius: 10px;
}

.policy-row {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--text-secondary);
  font-size: 9px;
}

.policy-row .dot {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--accent);
}

.policy-row .dot.amber {
  background: var(--amber);
}

.policy-row .dot.violet {
  background: var(--violet);
}

.policy-row strong {
  margin-left: auto;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 8px;
  font-weight: 500;
}

.submit-button {
  width: 100%;
  height: 43px;
  border-radius: 11px;
  font-size: 12.5px;
}

.submit-arrow {
  margin-left: 5px;
}

.submit-hint {
  margin-top: 8px;
  color: var(--text-muted);
  font-size: 8.5px;
  line-height: 1.5;
  text-align: center;
}

.tips-card {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 14px 16px;
}

.tips-icon {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  flex: none;
  border-radius: 8px;
  background: var(--amber-soft);
  color: var(--amber);
  font-size: 14px;
}

.tips-card h3 {
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 650;
}

.tips-card p {
  margin-top: 3px;
  color: var(--text-muted);
  font-size: 9px;
  line-height: 1.65;
}

@keyframes status-blink {
  0%,
  100% {
    opacity: 0.45;
  }
  50% {
    opacity: 1;
  }
}

@media (max-width: 1100px) {
  .submit-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .preview-column {
    position: static;
  }
}

@media (max-width: 760px) {
  .form-grid,
  .resource-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .span-2 {
    grid-column: span 1;
  }

  .form-section {
    padding: 17px;
  }

  .section-badge {
    display: none;
  }
}
</style>

<style scoped>
.submit-page {
  gap: 24px;
}

.submit-layout {
  gap: 20px;
}

.form-column {
  gap: 16px;
}

.form-section {
  padding: 20px;
}

.section-heading {
  gap: 12px;
  margin-bottom: 18px;
}

.section-heading h2 {
  font-size: 20px;
  font-weight: 600;
}

.section-heading p {
  margin-top: 4px;
  font-size: 14px;
  line-height: 1.7;
}

.section-badge {
  padding: 4px 8px;
  border-radius: 4px;
  font-family: var(--font);
  font-size: 12px;
  letter-spacing: 0;
}

.form-grid {
  gap: 16px;
}

.resource-grid {
  gap: 16px;
}

.resource-block {
  padding: 16px;
  border-radius: 6px;
}

.resource-label-row {
  margin-bottom: 8px;
}

.resource-value strong {
  font-size: 24px;
}

.resource-value small {
  font-size: 12px;
}

.resource-note,
.field-hint {
  font-size: 12px;
  line-height: 1.6;
}

.resource-note .tag-soft {
  font-size: 12px;
}

.field-suffix {
  font-size: 13px;
}

.form-note span {
  font-size: 13px;
  line-height: 1.7;
}

.resource-block :deep(.el-slider__marks-text) {
  font-size: 12px;
}

.preview-panel {
  padding: 20px;
  border-color: var(--border);
  background: var(--panel);
  box-shadow: none;
}

.preview-head h2 {
  margin-top: 3px;
  font-size: 22px;
  font-weight: 600;
}

.preview-status {
  padding: 4px 8px;
  border-radius: 4px;
  font-family: var(--font);
  font-size: 12px;
  letter-spacing: 0;
}

.preview-summary {
  margin-top: 18px;
  padding: 16px;
  border-radius: 6px;
}

.core-orb {
  width: 56px;
  height: 56px;
  border-radius: 50%;
}

.core-orb strong {
  font-size: 24px;
}

.core-orb span {
  font-size: 12px;
}

.core-copy strong {
  font-size: 15px;
}

.core-copy span {
  margin-top: 3px;
  font-size: 12px;
}

.preview-meta {
  margin-top: 14px;
  font-size: 12px;
}

.preview-card {
  margin-top: 14px;
  border-radius: 6px;
}

.preview-card-head {
  padding: 9px 12px;
  font-size: 12px;
}

.preview-card pre {
  font-size: 12px;
  line-height: 1.7;
}

.preview-equivalent {
  margin-top: 12px;
}

.preview-equivalent code {
  font-size: 12px;
}

.preview-estimate {
  margin-top: 12px;
  font-size: 12px;
}

.preview-policy {
  margin-top: 16px;
}

.policy-row {
  padding: 10px 0;
  font-size: 13px;
}

.policy-row strong {
  font-size: 12px;
}

.submit-button {
  min-height: 48px;
  margin-top: 16px;
  font-size: 15px;
}

.submit-hint {
  margin-top: 10px;
  font-size: 12px;
  line-height: 1.6;
}

.tips-card {
  padding: 18px;
  border-radius: var(--radius);
  box-shadow: none;
}

.tips-card h3 {
  font-size: 16px;
}

.tips-card p {
  font-size: 14px;
  line-height: 1.7;
}

.preset-note {
  font-size: 12px;
}

@media (max-width: 1100px) {
  .preview-panel {
    position: static;
  }
}

@media (max-width: 760px) {
  .form-section {
    padding: 16px;
  }

  .section-heading h2 {
    font-size: 19px;
  }

  .preview-panel {
    padding: 16px;
  }
}
</style>

<style scoped>
.quality-gate {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: -2px 0 14px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--panel-soft);
}

.quality-gate > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.quality-gate strong {
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 600;
}

.quality-gate span {
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.6;
}

.quality-gate.blocked {
  border-color: rgba(178, 61, 61, 0.22);
  background: var(--red-soft);
}

.quality-gate.warning {
  border-color: rgba(154, 106, 30, 0.24);
  background: var(--amber-soft);
}

@media (max-width: 760px) {
  .quality-gate {
    align-items: flex-start;
    flex-wrap: wrap;
  }
}
</style>
