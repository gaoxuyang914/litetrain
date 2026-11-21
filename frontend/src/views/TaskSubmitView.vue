<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import {
  ArrowRight,
  CircleCheck,
  CopyDocument,
  Cpu,
  InfoFilled,
  MagicStick,
  Promotion,
  SetUp,
  Timer,
} from '@element-plus/icons-vue'

import { api } from '@/api/client'
import type { PreviewResponse, TaskCreatePayload, TaskOptions } from '@/types/task'
import { copyText, formatDuration } from '@/utils/format'

const router = useRouter()
const formRef = ref<FormInstance>()
const options = ref<TaskOptions | null>(null)
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
  epochs: 3,
  imgsz: 160,
  batch_size: 4,
  workers: 0,
  cpu_cores: 4,
  priority: 50,
  max_runtime_seconds: 1800,
  max_retries: 0,
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
const imageSizes = [64, 96, 128, 160, 224, 320, 480, 640]
const backendLabel = computed(() =>
  options.value?.execution_backend === 'ray' ? 'Ray actor' : 'local process',
)
const assetsMissing = computed(
  () => options.value != null && (options.value.models.length === 0 || options.value.datasets.length === 0),
)

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
    const data = await api.getOptions()
    options.value = data
    pageError.value = null
    Object.assign(form, {
      model: data.models.includes(data.defaults.model) ? data.defaults.model : data.models[0] ?? data.defaults.model,
      dataset: data.datasets.includes(data.defaults.dataset)
        ? data.defaults.dataset
        : data.datasets[0] ?? data.defaults.dataset,
      epochs: data.defaults.epochs,
      imgsz: data.defaults.imgsz,
      batch_size: data.defaults.batch_size,
      cpu_cores: Math.min(data.defaults.cpu_cores, data.user_cpu_quota),
      priority: data.defaults.priority,
      max_runtime_seconds: data.defaults.max_runtime_seconds,
      max_retries: data.defaults.max_retries,
      workers: data.defaults.workers,
    })
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
  try {
    await formRef.value.validate()
  } catch {
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
      max_runtime_seconds: 300,
      priority: 50,
    })
  } else {
    Object.assign(form, {
      epochs: 3,
      imgsz: 160,
      batch_size: 4,
      cpu_cores: Math.min(6, maxCpu.value),
      max_runtime_seconds: 1800,
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

onMounted(loadOptions)

onBeforeUnmount(() => {
  if (previewTimer) window.clearTimeout(previewTimer)
})
</script>

<template>
  <div class="page submit-page">
    <div class="page-head">
      <div class="page-heading">
        <span class="eyebrow">Compose training</span>
        <h1 class="page-title">提交训练任务</h1>
        <p class="page-subtitle">
          选择 coco128 样本与 YOLOv8n 权重，提交后在 CPU 上训练。平台会先计算资源，再通过
          {{ backendLabel }} 启动隔离的 worker。
        </p>
      </div>
      <div class="page-actions">
        <el-button :icon="Timer" @click="applyPreset('quick')">快速试跑 · 1ep</el-button>
        <el-button :icon="MagicStick" @click="applyPreset('standard')">标准示例 · 3ep</el-button>
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
              <span class="section-number">01</span>
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
                  placeholder="例如：YOLOv8n · coco128 · CPU baseline"
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
              <span class="section-number">02</span>
              <div>
                <h2>训练配方</h2>
                <p>使用内置 coco128 数据集，权重会在 CPU device 上训练。</p>
              </div>
              <span class="section-badge"><el-icon><SetUp /></el-icon> YOLO detect</span>
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

              <el-form-item label="Epochs">
                <el-input-number v-model="form.epochs" :min="1" :max="50" :step="1" controls-position="right" class="full" />
              </el-form-item>

              <el-form-item label="图像尺寸">
                <el-select v-model="form.imgsz" class="full">
                  <el-option v-for="size in imageSizes" :key="size" :label="`${size} × ${size}`" :value="size" />
                </el-select>
              </el-form-item>

              <el-form-item label="Batch size">
                <el-input-number v-model="form.batch_size" :min="1" :max="64" :step="1" controls-position="right" class="full" />
              </el-form-item>

              <el-form-item label="Dataloader workers">
                <el-input-number v-model="form.workers" :min="0" :max="8" :step="1" controls-position="right" class="full" />
                <span class="field-hint">Windows / Ray 演示建议保持 0</span>
              </el-form-item>
            </div>
          </section>

          <section class="panel form-section">
            <div class="section-heading">
              <span class="section-number">03</span>
              <div>
                <h2>资源与调度</h2>
                <p>平台按逻辑 CPU 核数分配资源；资源不足时任务会排队，不会超卖。</p>
              </div>
              <span class="section-badge subtle"><el-icon><Cpu /></el-icon> CPU only</span>
            </div>

            <div class="resource-grid">
              <div class="resource-block">
                <div class="resource-label-row">
                  <span class="el-form-item__label">CPU 核数</span>
                  <span class="resource-value">
                    <strong>{{ form.cpu_cores }}</strong>
                    <small>/ {{ maxCpu }} cores</small>
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
                  <span>单用户配额 {{ quota }} CPU，集群上限 {{ totalCpus }} CPU</span>
                </div>
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
                  <span class="mono">priority {{ form.priority }}</span>
                </div>
              </div>

              <div class="resource-block compact-block">
                <span class="el-form-item__label">最大运行时长</span>
                <div class="inline-field">
                  <el-input-number
                    v-model="form.max_runtime_seconds"
                    :min="options?.limits.max_runtime_seconds?.[0] ?? 10"
                    :max="options?.limits.max_runtime_seconds?.[1] ?? 86400"
                    :step="60"
                    controls-position="right"
                  />
                  <span class="field-suffix">秒</span>
                </div>
                <span class="field-hint">超时后平台会终止 worker 并释放资源 · 当前 {{ timeoutLabel }}</span>
              </div>

              <div class="resource-block compact-block">
                <span class="el-form-item__label">失败自动重试</span>
                <div class="inline-field">
                  <el-input-number v-model="form.max_retries" :min="0" :max="5" :step="1" controls-position="right" />
                  <span class="field-suffix">次</span>
                </div>
                <span class="field-hint">仅对 worker 失败自动重试；超时/取消不会重试</span>
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
              <span class="soft-label">Execution preview</span>
              <h2>任务预览</h2>
            </div>
            <span class="preview-status">
              <i></i>{{ options?.execution_backend === 'ray' ? 'RAY ACTOR' : 'LOCAL CPU' }}
            </span>
          </div>

          <div class="preview-summary">
            <div class="preview-core">
              <div class="core-orb">
                <strong>{{ form.cpu_cores }}</strong>
                <span>cores</span>
              </div>
              <div class="core-copy">
                <strong>{{ form.epochs }} × epoch · {{ form.imgsz }} px</strong>
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
              <span>Worker command</span>
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
            <span class="soft-label">Equivalent YOLO CLI</span>
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
              <strong>{{ totalCpus }} CPU</strong>
            </div>
            <div class="policy-row">
              <span class="dot amber"></span>
              <span>资源不足自动排队</span>
              <strong>{{ backendLabel }}</strong>
            </div>
            <div class="policy-row">
              <span class="dot violet"></span>
              <span>心跳 & 超时自动回收</span>
              <strong>watchdog</strong>
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

        <section class="panel tips-card">
          <div class="tips-icon"><el-icon><CircleCheck /></el-icon></div>
          <div>
            <h3>推荐演示路径</h3>
            <p>
              第一次使用建议「快速试跑 · 1ep」：coco128 的 128 张图在 96px 下约 20 秒完成，
              足以看到完整的状态流转和日志输出。
            </p>
          </div>
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
