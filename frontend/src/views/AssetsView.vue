<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  Box,
  Connection,
  CopyDocument,
  Delete,
  Download,
  FolderOpened,
  Plus,
  Refresh,
  UploadFilled,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { api } from '@/api/client'
import type { Artifact, Dataset, WebhookDelivery, WebhookEndpoint } from '@/types/p2'
import { copyText, formatBytes, formatDate, timeAgo } from '@/utils/format'

const router = useRouter()
type Tab = 'datasets' | 'artifacts' | 'webhooks'
const activeTab = ref<Tab>('datasets')

const datasets = ref<Dataset[]>([])
const artifacts = ref<Artifact[]>([])
const artifactFilter = ref<'all' | 'model' | 'metrics' | 'config'>('all')
const artifactPage = ref(1)
const artifactPageSize = 10
const webhooks = ref<WebhookEndpoint[]>([])
const deliveries = ref<WebhookDelivery[]>([])
const loading = ref(true)
const refreshing = ref(false)
const error = ref<string | null>(null)

const uploadOpen = ref(false)
const uploading = ref(false)
const uploadFile = ref<File | null>(null)
const uploadRef = ref()
const uploadForm = reactive({ name: '', version: 'v1', description: '' })

const webhookOpen = ref(false)
const webhookBusy = ref(false)
const webhookName = ref('')
const webhookUrl = ref('http://127.0.0.1:9099/hook')
const webhookSecret = ref('demo-secret')
const webhookEvents = ref<string[]>([])
const webhookActive = ref(true)
const eventOptions = [
  { value: 'task.succeeded', label: 'task.succeeded' },
  { value: 'task.failed', label: 'task.failed' },
  { value: 'task.cancelled', label: 'task.cancelled' },
  { value: 'task.retry_scheduled', label: 'task.retry_scheduled' },
  { value: 'pipeline.succeeded', label: 'pipeline.succeeded' },
  { value: 'pipeline.failed', label: 'pipeline.failed' },
  { value: '*', label: '* (all events)' },
]
const actionBusy = ref<string | null>(null)
let timer: number | undefined

const activeWebhooks = computed(() => webhooks.value.filter((item) => item.active).length)
const artifactBytes = computed(() => artifacts.value.reduce((sum, item) => sum + item.size_bytes, 0))
const filteredArtifacts = computed(() =>
  artifactFilter.value === 'all'
    ? artifacts.value
    : artifacts.value.filter((item) => item.kind === artifactFilter.value),
)
const pagedArtifacts = computed(() =>
  filteredArtifacts.value.slice(
    (artifactPage.value - 1) * artifactPageSize,
    artifactPage.value * artifactPageSize,
  ),
)
const successfulDeliveries = computed(
  () => deliveries.value.filter((item) => item.status === 'success').length,
)

const artifactFilters: Array<{ key: 'all' | 'model' | 'metrics' | 'config'; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'model', label: '模型权重' },
  { key: 'metrics', label: '指标' },
  { key: 'config', label: '配置' },
]

const tabs: Array<{ key: Tab; label: string; caption: string; icon: unknown }> = [
  { key: 'datasets', label: '数据集', caption: '版本与导入', icon: FolderOpened },
  { key: 'artifacts', label: '模型产物', caption: '权重与结果', icon: Box },
  { key: 'webhooks', label: 'Webhooks', caption: '签名投递', icon: Connection },
]

const kindLabel: Record<string, string> = {
  model: 'model',
  metrics: 'metrics',
  config: 'config',
}

function switchTab(tab: Tab) {
  activeTab.value = tab
}

async function loadDatasets(silent = false) {
  if (!silent) loading.value = true
  try {
    datasets.value = await api.listDatasets()
    error.value = null
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载数据集失败'
  } finally {
    loading.value = false
  }
}

async function loadArtifacts(silent = false) {
  if (!silent) loading.value = true
  try {
    artifacts.value = await api.listArtifacts()
    error.value = null
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载产物失败'
  } finally {
    loading.value = false
  }
}

async function loadWebhooks(silent = false) {
  if (!silent) loading.value = true
  try {
    const [hooks, logs] = await Promise.all([
      api.listWebhooks(),
      api.listWebhookDeliveries(),
    ])
    webhooks.value = hooks
    deliveries.value = logs
    error.value = null
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载 Webhook 失败'
  } finally {
    loading.value = false
  }
}

async function load(silent = false) {
  refreshing.value = true
  try {
    if (activeTab.value === 'datasets') await loadDatasets(silent)
    else if (activeTab.value === 'artifacts') await loadArtifacts(silent)
    else await loadWebhooks(silent)
  } finally {
    refreshing.value = false
  }
}

function resetUpload() {
  uploadFile.value = null
  uploadForm.name = ''
  uploadForm.version = 'v1'
  uploadForm.description = ''
  uploadRef.value?.clearFiles()
}

function openUpload() {
  resetUpload()
  uploadOpen.value = true
}

function onFileChange(file: any) {
  uploadFile.value = file.raw as File
  if (!uploadForm.name) {
    uploadForm.name = file.name.replace(/\.zip$/i, '')
  }
}

function onFileRemove() {
  uploadFile.value = null
}

async function submitUpload() {
  if (!uploadFile.value) {
    ElMessage.warning('请选择一个 .zip 数据集压缩包')
    return
  }
  if (!uploadForm.name.trim()) {
    ElMessage.warning('请填写数据集名称')
    return
  }
  const form = new FormData()
  form.append('file', uploadFile.value)
  form.append('name', uploadForm.name.trim())
  form.append('version', uploadForm.version.trim() || 'v1')
  if (uploadForm.description.trim()) form.append('description', uploadForm.description.trim())
  uploading.value = true
  try {
    const dataset = await api.uploadDataset(form)
    ElMessage.success(`数据集 ${dataset.name} v${dataset.version} 已导入`)
    uploadOpen.value = false
    await loadDatasets(true)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '上传数据集失败')
  } finally {
    uploading.value = false
  }
}

async function removeDataset(dataset: any) {
  try {
    await ElMessageBox.confirm(
      `从注册表移除数据集「${dataset.name} v${dataset.version}」？历史训练产物不会被删除。`,
      '移除数据集',
      { confirmButtonText: '移除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  actionBusy.value = dataset.id
  try {
    await api.deleteDataset(dataset.id)
    ElMessage.success('数据集已从注册表移除')
    await loadDatasets(true)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '移除失败')
  } finally {
    actionBusy.value = null
  }
}

function downloadDataset(dataset: any) {
  window.open(api.datasetDownloadUrl(dataset.id), '_blank', 'noopener')
}

function openTask(taskId: string) {
  router.push({ name: 'task-detail', params: { id: taskId } })
}

function downloadArtifact(artifact: any) {
  window.open(artifact.download_url, '_blank', 'noopener')
}

function openWebhookCreate() {
  webhookName.value = ''
  webhookUrl.value = 'http://127.0.0.1:9099/hook'
  webhookSecret.value = 'demo-secret'
  webhookEvents.value = ['task.succeeded', 'task.failed', 'pipeline.succeeded', 'pipeline.failed']
  webhookActive.value = true
  webhookOpen.value = true
}

async function submitWebhook() {
  if (!webhookName.value.trim() || !webhookUrl.value.trim()) {
    ElMessage.warning('请填写 Webhook 名称和 URL')
    return
  }
  if (!webhookEvents.value.length) {
    ElMessage.warning('请至少选择一种事件')
    return
  }
  webhookBusy.value = true
  try {
    const endpoint = await api.createWebhook({
      name: webhookName.value.trim(),
      url: webhookUrl.value.trim(),
      secret: webhookSecret.value.trim() || undefined,
      events: webhookEvents.value,
      active: webhookActive.value,
    })
    ElMessage.success(`Webhook ${endpoint.name} 已创建`)
    webhookOpen.value = false
    await loadWebhooks(true)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '创建 Webhook 失败')
  } finally {
    webhookBusy.value = false
  }
}

async function toggleWebhook(endpoint: WebhookEndpoint) {
  actionBusy.value = endpoint.id
  try {
    await api.updateWebhook(endpoint.id, !endpoint.active)
    await loadWebhooks(true)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '更新失败')
  } finally {
    actionBusy.value = null
  }
}

async function testWebhook(endpoint: WebhookEndpoint) {
  actionBusy.value = endpoint.id
  try {
    await api.testWebhook(endpoint.id)
    ElMessage.success('测试投递已入队，最多 1–2 秒后到达接收端')
    window.setTimeout(() => loadWebhooks(true), 1600)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '测试发送失败')
  } finally {
    actionBusy.value = null
  }
}

async function removeWebhook(endpoint: WebhookEndpoint) {
  try {
    await ElMessageBox.confirm(`删除 Webhook「${endpoint.name}」？`, '删除 Webhook', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  actionBusy.value = endpoint.id
  try {
    await api.deleteWebhook(endpoint.id)
    await loadWebhooks(true)
    ElMessage.success('Webhook 已删除')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  } finally {
    actionBusy.value = null
  }
}

async function copySecret(secret: string) {
  const ok = await copyText(secret)
  ElMessage({ message: ok ? '签名密钥已复制' : '复制失败', type: ok ? 'success' : 'warning' })
}

function deliveryTone(status: string) {
  if (status === 'success') return 'success'
  if (status === 'failed') return 'failed'
  return 'pending'
}

watch(activeTab, (tab) => {
  load(tab === 'datasets' ? false : true)
})

watch(artifactFilter, () => {
  artifactPage.value = 1
})

onMounted(async () => {
  await Promise.all([load(), api.listArtifacts().then((items) => (artifacts.value = items)).catch(() => undefined), api.listWebhooks().then((items) => (webhooks.value = items)).catch(() => undefined), api.listWebhookDeliveries().then((items) => (deliveries.value = items)).catch(() => undefined)])
  loading.value = false
  timer = window.setInterval(() => {
    if (document.hidden) return
    if (activeTab.value === 'webhooks') loadWebhooks(true)
  }, 4000)
})

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <div class="page assets-page">
    <div class="page-head">
      <div class="page-heading">
        <span class="eyebrow">资产与自动化</span>
        <h1 class="page-title">数据与产物</h1>
        <p class="page-subtitle">
          管理可复用数据集版本、下载 YOLO 训练权重与结果，并通过签名 Webhook 把任务和流水线状态推送到外部系统。
        </p>
      </div>
      <div class="page-actions">
        <el-button :icon="Refresh" :loading="refreshing" circle title="刷新" @click="load(true)" />
        <el-button v-if="activeTab === 'datasets'" type="primary" :icon="UploadFilled" @click="openUpload">
          导入数据集
        </el-button>
        <el-button v-else-if="activeTab === 'webhooks'" type="primary" :icon="Plus" @click="openWebhookCreate">
          新建 Webhook
        </el-button>
      </div>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />

    <section class="asset-summary">
      <div class="asset-stat">
        <span class="asset-icon"><el-icon><FolderOpened /></el-icon></span>
        <div><strong>{{ datasets.length }}</strong><span>个数据集</span></div>
        <small>版本化数据集</small>
      </div>
      <div class="asset-stat">
        <span class="asset-icon violet"><el-icon><Box /></el-icon></span>
        <div><strong>{{ artifacts.length }}</strong><span>个产物</span></div>
        <small>{{ formatBytes(artifactBytes) }}</small>
      </div>
      <div class="asset-stat">
        <span class="asset-icon amber"><el-icon><Connection /></el-icon></span>
        <div><strong>{{ activeWebhooks }}</strong><span>个 Webhook</span></div>
        <small>{{ successfulDeliveries }} 次投递成功</small>
      </div>
      <div class="asset-note">
        <span class="soft-label">本地资产存储</span>
        <p>训练完成后自动扫描 <code>weights/best.pt</code>、<code>last.pt</code> 与 <code>results.csv</code>，计算 SHA-256 并提供下载。</p>
      </div>
    </section>

    <section class="panel assets-panel">
      <div class="asset-tabs-row">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          type="button"
          class="asset-tab"
          :class="{ active: activeTab === tab.key }"
          @click="switchTab(tab.key)"
        >
          <el-icon><component :is="tab.icon" /></el-icon>
          <span>
            <strong>{{ tab.label }}</strong>
            <small>{{ tab.caption }}</small>
          </span>
        </button>
        <span class="tab-spacer"></span>
        <span class="tag-soft" :class="{ accent: activeTab === 'webhooks' && activeWebhooks }">
          <span class="dot" :class="{ pulse: activeTab === 'webhooks' && activeWebhooks }"></span>
          {{ activeTab === 'datasets' ? '数据登记' : activeTab === 'artifacts' ? '模型产物' : '投递箱' }}
        </span>
      </div>

      <el-skeleton v-if="loading" :rows="7" animated class="asset-skeleton" />

      <template v-else-if="activeTab === 'datasets'">
        <el-table :data="datasets" row-key="id" class="asset-table">
          <el-table-column label="数据集" min-width="230">
            <template #default="{ row }">
              <div class="dataset-cell">
                <span class="dataset-icon"><el-icon><FolderOpened /></el-icon></span>
                <div>
                  <strong>{{ row.name }}</strong>
                  <span class="mono">{{ row.version }} · {{ row.yaml_path }}</span>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="来源" width="100">
            <template #default="{ row }">
              <span class="soft-tag" :class="{ builtin: row.is_builtin }">
                {{ row.is_builtin ? '内置' : '已上传' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="规模" min-width="120">
            <template #default="{ row }">
              <span class="mono muted">{{ row.image_count }} 张图 · {{ row.label_count }} 个标签</span>
            </template>
          </el-table-column>
          <el-table-column label="类别" width="70" align="center">
            <template #default="{ row }"><span class="count-pill">{{ row.class_count }}</span></template>
          </el-table-column>
          <el-table-column label="大小" width="95">
            <template #default="{ row }"><span class="mono">{{ formatBytes(row.size_bytes) }}</span></template>
          </el-table-column>
          <el-table-column label="导入时间" width="110">
            <template #default="{ row }"><span class="muted small">{{ timeAgo(row.created_at) }}</span></template>
          </el-table-column>
          <el-table-column label="" width="140" align="right">
            <template #default="{ row }">
              <div class="dataset-actions">
                <el-button v-if="!row.is_builtin" text size="small" :icon="Download" @click="downloadDataset(row)">下载</el-button>
                <el-button v-if="!row.is_builtin" text size="small" :loading="actionBusy === row.id" :icon="Delete" @click="removeDataset(row)" />
              </div>
            </template>
          </el-table-column>
          <template #empty>
            <div class="empty-state">
              <span class="empty-icon"><el-icon><FolderOpened /></el-icon></span>
              <strong>还没有数据集</strong>
              <span>上传一个包含 YOLO dataset.yaml、images 和 labels 的 zip 压缩包。</span>
              <el-button type="primary" plain size="small" @click="openUpload">导入数据集</el-button>
            </div>
          </template>
        </el-table>
        <div class="dataset-mobile-list">
          <article v-for="row in datasets" :key="row.id" class="dataset-mobile-card">
            <div class="mobile-card-head">
              <span class="dataset-icon"><el-icon><FolderOpened /></el-icon></span>
              <div><strong>{{ row.name }}</strong><span class="mono">{{ row.version }} · {{ formatBytes(row.size_bytes) }}</span></div>
              <span class="soft-tag" :class="{ builtin: row.is_builtin }">{{ row.is_builtin ? '内置' : '已上传' }}</span>
            </div>
            <div class="mobile-card-meta">
              <span>{{ row.image_count }} 张图</span>
              <span>{{ row.label_count }} 个标签</span>
              <span>{{ row.class_count }} 个类别</span>
            </div>
            <div class="mobile-card-actions">
              <el-button v-if="!row.is_builtin" size="small" text :icon="Download" @click="downloadDataset(row)">下载</el-button>
              <el-button v-if="!row.is_builtin" size="small" text :loading="actionBusy === row.id" :icon="Delete" @click="removeDataset(row)" />
            </div>
          </article>
        </div>
        <div class="dataset-cta">
          <div class="dataset-cta-copy">
            <span class="soft-label">从数据到权重</span>
            <h3>用版本化数据启动一条三阶段流水线</h3>
            <p>预处理自动生成安全子集，训练完成后权重与指标会自动回到资产页，来源可追溯。</p>
          </div>
          <div class="dataset-cta-mark" aria-hidden="true">
            <span></span><span></span><span></span>
          </div>
          <div class="dataset-cta-actions">
            <el-button type="primary" @click="router.push({ name: 'pipelines' })">前往训练流水线</el-button>
            <el-button text @click="openUpload">上传新版本</el-button>
          </div>
        </div>
      </template>

      <template v-else-if="activeTab === 'artifacts'">
        <div class="artifact-toolbar">
          <span class="soft-label">筛选</span>
          <button
            v-for="filter in artifactFilters"
            :key="filter.key"
            type="button"
            class="filter-chip"
            :class="{ active: artifactFilter === filter.key }"
            @click="artifactFilter = filter.key"
          >
            {{ filter.label }}
          </button>
          <span class="filter-count mono">{{ filteredArtifacts.length }} / {{ artifacts.length }}</span>
        </div>
        <el-table :data="pagedArtifacts" row-key="id" class="asset-table">
          <el-table-column label="产物" min-width="220">
            <template #default="{ row }">
              <div class="artifact-cell">
                <span class="artifact-icon" :class="row.kind"><el-icon><Box /></el-icon></span>
                <div>
                  <strong>{{ row.name }}</strong>
                  <span class="mono">{{ row.kind }} · {{ formatBytes(row.size_bytes) }}</span>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="任务" min-width="150">
            <template #default="{ row }">
              <button class="link-button mono" type="button" @click="openTask(row.task_id)">{{ row.task_id }}</button>
            </template>
          </el-table-column>
          <el-table-column label="SHA-256" min-width="150">
            <template #default="{ row }">
              <span class="mono hash">{{ row.sha256.slice(0, 16) }}…</span>
            </template>
          </el-table-column>
          <el-table-column label="生成时间" width="110">
            <template #default="{ row }"><span class="muted small">{{ timeAgo(row.created_at) }}</span></template>
          </el-table-column>
          <el-table-column label="" width="100" align="right">
            <template #default="{ row }">
              <el-button size="small" :icon="Download" @click="downloadArtifact(row)">下载</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <div class="empty-state">
              <span class="empty-icon"><el-icon><Box /></el-icon></span>
              <strong>还没有模型产物</strong>
              <span>完成一次训练或流水线后，best.pt、last.pt 和 results.csv 会自动出现在这里。</span>
            </div>
          </template>
        </el-table>
        <div class="artifact-mobile-list">
          <article v-for="row in pagedArtifacts" :key="row.id" class="artifact-mobile-card">
            <div class="mobile-card-head">
              <span class="artifact-icon" :class="row.kind"><el-icon><Box /></el-icon></span>
              <div><strong>{{ row.name }}</strong><span class="mono">{{ row.kind }} · {{ formatBytes(row.size_bytes) }}</span></div>
              <el-button size="small" :icon="Download" @click="downloadArtifact(row)">下载</el-button>
            </div>
            <div class="mobile-card-meta">
              <button class="link-button mono" type="button" @click="openTask(row.task_id)">{{ row.task_id }}</button>
              <span class="mono">sha {{ row.sha256.slice(0, 10) }}…</span>
            </div>
          </article>
        </div>
        <div v-if="filteredArtifacts.length > artifactPageSize" class="artifact-pager">
          <el-pagination
            v-model:current-page="artifactPage"
            :page-size="artifactPageSize"
            :total="filteredArtifacts.length"
            layout="prev, pager, next"
            background
            small
          />
        </div>
      </template>

      <template v-else>
        <div class="webhook-layout">
          <div class="webhook-list">
            <div v-if="webhooks.length === 0" class="webhook-empty">
              <span class="empty-icon"><el-icon><Connection /></el-icon></span>
              <strong>还没有 Webhook</strong>
              <span>注册一个接收端，订阅任务或流水线终态事件。先运行 <code class="code-inline">python scripts/webhook_receiver.py</code> 可得到本地接收器。</span>
              <el-button type="primary" plain size="small" @click="openWebhookCreate">新建 Webhook</el-button>
            </div>
            <article v-for="endpoint in webhooks" :key="endpoint.id" class="webhook-card" :class="{ inactive: !endpoint.active }">
              <div class="webhook-card-head">
                <span class="webhook-dot" :class="{ active: endpoint.active }"></span>
                <div>
                  <strong>{{ endpoint.name }}</strong>
                  <span class="mono">{{ endpoint.url }}</span>
                </div>
                <el-switch
                  :model-value="endpoint.active"
                  :loading="actionBusy === endpoint.id"
                  size="small"
                  @change="toggleWebhook(endpoint)"
                />
              </div>
              <div class="webhook-events">
                <span v-for="event in endpoint.events" :key="event" class="event-pill mono">{{ event }}</span>
              </div>
              <div class="webhook-stats">
                <span><strong>{{ endpoint.success_count }}</strong> ok</span>
                <span><strong>{{ endpoint.failed_count }}</strong> failed</span>
                <span><strong>{{ endpoint.pending_count }}</strong> pending</span>
                <button type="button" class="secret-button mono" @click="copySecret(endpoint.secret)">
                  <el-icon><CopyDocument /></el-icon> secret
                </button>
              </div>
              <div class="webhook-actions">
                <el-button size="small" text :loading="actionBusy === endpoint.id" @click="testWebhook(endpoint)">发送测试</el-button>
                <el-button size="small" text type="danger" :icon="Delete" @click="removeWebhook(endpoint)" />
              </div>
            </article>
          </div>

          <section class="delivery-panel">
            <div class="delivery-head">
              <div>
                <span class="soft-label">投递队列</span>
                <h3>最近投递</h3>
              </div>
              <span class="tag-soft"><span class="dot"></span>HMAC-SHA256</span>
            </div>
            <div v-if="deliveries.length === 0" class="delivery-empty">
              触发一次任务终态或点击「发送测试」后，这里会出现实际 HTTP 投递记录。
            </div>
            <div v-else class="delivery-list">
              <div v-for="delivery in deliveries.slice(0, 12)" :key="delivery.id" class="delivery-row">
                <span class="delivery-marker" :class="deliveryTone(delivery.status)"></span>
                <div class="delivery-copy">
                  <strong class="mono">{{ delivery.event_type }}</strong>
                  <span>{{ delivery.response_code ?? '—' }} · 第 {{ delivery.attempts }}/{{ delivery.max_attempts }} 次尝试</span>
                </div>
                <span class="delivery-status mono" :class="deliveryTone(delivery.status)">{{ delivery.status }}</span>
                <span class="delivery-time">{{ timeAgo(delivery.created_at) }}</span>
              </div>
            </div>
          </section>
        </div>
      </template>
    </section>

    <el-dialog v-model="uploadOpen" title="导入 YOLO 数据集" width="560px">
      <el-upload
        ref="uploadRef"
        drag
        :auto-upload="false"
        :limit="1"
        accept=".zip"
        :on-change="onFileChange"
        :on-remove="onFileRemove"
      >
        <el-icon class="upload-icon"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖拽 zip 到此处，或 <em>点击选择</em></div>
        <template #tip>
          <div class="el-upload__tip">压缩包需包含带 train/val 的 YOLO dataset YAML 和 images/labels，最大 512 MB。</div>
        </template>
      </el-upload>
      <el-form label-position="top" class="upload-form">
        <el-form-item label="数据集名称">
          <el-input v-model="uploadForm.name" maxlength="120" placeholder="例如：my-widgets" />
        </el-form-item>
        <el-form-item label="版本">
          <el-input v-model="uploadForm.version" maxlength="40" placeholder="v1" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="uploadForm.description" type="textarea" :rows="2" maxlength="300" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadOpen = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="submitUpload">导入并登记</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="webhookOpen" title="新建 Webhook" width="600px">
      <div class="webhook-help">
        投递会携带 <code>X-LiteTrain-Event</code>、<code>X-LiteTrain-Delivery</code>、<code>X-LiteTrain-Timestamp</code>
        和 <code>X-LiteTrain-Signature</code>。签名 = HMAC-SHA256(secret, timestamp + "." + raw body)。
      </div>
      <el-form label-position="top">
        <el-form-item label="名称">
          <el-input v-model="webhookName" maxlength="100" placeholder="例如：CI notification relay" />
        </el-form-item>
        <el-form-item label="接收 URL">
          <el-input v-model="webhookUrl" placeholder="http://127.0.0.1:9099/hook" />
        </el-form-item>
        <el-form-item label="签名密钥（留空自动生成）">
          <el-input v-model="webhookSecret" placeholder="选填" />
        </el-form-item>
        <el-form-item label="订阅事件">
          <el-checkbox-group v-model="webhookEvents" class="event-checkboxes">
            <el-checkbox v-for="option in eventOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="webhookActive" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="webhookOpen = false">取消</el-button>
        <el-button type="primary" :loading="webhookBusy" @click="submitWebhook">创建 Webhook</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.assets-page {
  gap: 16px;
}

.asset-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 0.55fr)) minmax(260px, 1.35fr);
  gap: 10px;
}

.asset-stat,
.asset-note {
  display: flex;
  align-items: center;
  gap: 9px;
  min-height: 71px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 13px;
  background: var(--panel);
  box-shadow: var(--shadow-sm);
}

.asset-icon {
  display: grid;
  place-items: center;
  width: 31px;
  height: 31px;
  flex: none;
  border-radius: 8px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 15px;
}

.asset-icon.violet {
  background: var(--violet-soft);
  color: var(--violet);
}

.asset-icon.amber {
  background: var(--amber-soft);
  color: var(--amber);
}

.asset-stat > div {
  display: flex;
  align-items: baseline;
  gap: 5px;
}

.asset-stat strong {
  color: var(--text);
  font-family: var(--mono);
  font-size: 22px;
  font-weight: 680;
  letter-spacing: -0.05em;
}

.asset-stat span {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 8px;
  line-height: 1.3;
}

.asset-stat small {
  margin-left: auto;
  color: var(--text-muted);
  font-size: 8px;
  text-align: right;
}

.asset-note {
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 4px;
  border-style: dashed;
  box-shadow: none;
  background: transparent;
}

.asset-note p {
  color: var(--text-muted);
  font-size: 9px;
  line-height: 1.6;
}

.asset-note code {
  color: var(--text-secondary);
  font-family: var(--mono);
  font-size: 8px;
}

.assets-panel {
  overflow: hidden;
}

.asset-tabs-row {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
}

.asset-tab {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 9px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  text-align: left;
}

.asset-tab .el-icon {
  font-size: 15px;
}

.asset-tab > span {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.asset-tab strong {
  color: var(--text-secondary);
  font-size: 10.5px;
  font-weight: 650;
}

.asset-tab small {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 7.5px;
}

.asset-tab:hover {
  background: var(--panel-soft);
}

.asset-tab.active {
  border-color: var(--border);
  background: var(--accent-soft);
}

.asset-tab.active .el-icon,
.asset-tab.active strong {
  color: var(--accent);
}

.tab-spacer {
  flex: 1;
}

.asset-skeleton {
  padding: 20px;
}

.artifact-toolbar {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 9px 14px;
  border-bottom: 1px dashed var(--border);
  background: var(--panel-soft);
}

.filter-chip {
  padding: 3px 7px;
  border: 1px solid transparent;
  border-radius: 5px;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  font-family: var(--mono);
  font-size: 8px;
}

.filter-chip:hover {
  color: var(--text-secondary);
}

.filter-chip.active {
  border-color: var(--border);
  background: var(--panel);
  color: var(--accent);
}

.filter-count {
  margin-left: auto;
  color: var(--text-muted);
  font-size: 8px;
}

.artifact-pager {
  display: flex;
  justify-content: flex-end;
  padding: 10px 14px;
  border-top: 1px solid var(--border);
}

.asset-table :deep(.el-table__row) {
  transition: background 0.15s ease;
}

.asset-table :deep(.el-table__row):hover {
  background: var(--panel-soft);
}

.dataset-cell,
.artifact-cell {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
}

.dataset-actions {
  display: flex;
  justify-content: flex-end;
  gap: 2px;
  white-space: nowrap;
}

.dataset-actions :deep(.el-button + .el-button) {
  margin-left: 2px;
}

.dataset-icon,
.artifact-icon {
  display: grid;
  place-items: center;
  width: 29px;
  height: 29px;
  flex: none;
  border-radius: 8px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 13px;
}

.artifact-icon.violet {
  background: var(--violet-soft);
  color: var(--violet);
}

.artifact-icon.amber {
  background: var(--amber-soft);
  color: var(--amber);
}

.dataset-cell > div,
.artifact-cell > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.dataset-cell strong,
.artifact-cell strong {
  overflow: hidden;
  color: var(--text);
  font-size: 11px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dataset-cell span,
.artifact-cell span {
  overflow: hidden;
  color: var(--text-muted);
  font-size: 8.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.soft-tag {
  display: inline-flex;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--violet-soft);
  color: var(--violet);
  font-family: var(--mono);
  font-size: 7.5px;
}

.soft-tag.builtin {
  background: var(--accent-soft);
  color: var(--accent);
}

.count-pill {
  display: inline-grid;
  place-items: center;
  min-width: 22px;
  height: 19px;
  border-radius: 5px;
  background: var(--panel-soft);
  color: var(--text-secondary);
  font-family: var(--mono);
  font-size: 9px;
}

.hash {
  color: var(--text-muted);
  font-size: 8.5px;
}

.link-button {
  border: none;
  background: none;
  padding: 0;
  color: var(--accent);
  cursor: pointer;
  font-size: 8.5px;
}

.link-button:hover {
  text-decoration: underline;
}

.small {
  font-size: 9px;
}

.webhook-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(300px, 0.9fr);
  min-height: 390px;
}

.webhook-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
  border-right: 1px solid var(--border);
}

.webhook-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 300px;
  color: var(--text-muted);
  font-size: 9.5px;
  text-align: center;
}

.webhook-empty strong {
  color: var(--text-secondary);
  font-size: 12px;
}

.webhook-empty .empty-icon {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  border: 1px solid var(--border);
  border-radius: 12px;
  color: var(--accent);
  font-size: 19px;
}

.webhook-card {
  padding: 11px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--panel);
}

.webhook-card.inactive {
  opacity: 0.6;
}

.webhook-card-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.webhook-dot {
  width: 6px;
  height: 6px;
  flex: none;
  border-radius: 50%;
  background: var(--text-muted);
}

.webhook-dot.active {
  background: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.webhook-card-head > div {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
  flex: 1;
}

.webhook-card-head strong {
  color: var(--text-secondary);
  font-size: 10.5px;
}

.webhook-card-head span {
  overflow: hidden;
  color: var(--text-muted);
  font-size: 8px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.webhook-events {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  margin-top: 8px;
}

.event-pill {
  padding: 2px 5px;
  border-radius: 4px;
  background: var(--panel-soft);
  color: var(--text-muted);
  font-size: 7.5px;
}

.webhook-stats {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 7.5px;
}

.webhook-stats strong {
  color: var(--text-secondary);
  font-weight: 600;
}

.secret-button {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  margin-left: auto;
  border: none;
  background: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 7.5px;
}

.secret-button:hover {
  color: var(--accent);
}

.webhook-actions {
  display: flex;
  justify-content: flex-end;
  gap: 2px;
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed var(--border);
}

.delivery-panel {
  padding: 16px;
  background: var(--panel-soft);
}

.delivery-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border);
}

.delivery-head h3 {
  margin-top: 2px;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 650;
}

.delivery-empty {
  padding: 30px 15px;
  color: var(--text-muted);
  font-size: 9px;
  line-height: 1.7;
}

.delivery-list {
  display: flex;
  flex-direction: column;
}

.delivery-row {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  padding: 8px 0;
  border-bottom: 1px dashed var(--border);
}

.delivery-marker {
  width: 6px;
  height: 6px;
  flex: none;
  margin-top: 4px;
  border-radius: 50%;
  background: var(--text-muted);
}

.delivery-marker.success {
  background: var(--accent);
}

.delivery-marker.failed {
  background: var(--red);
}

.delivery-marker.pending {
  background: var(--amber);
}

.delivery-copy {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
  flex: 1;
}

.delivery-copy strong {
  color: var(--text-secondary);
  font-size: 8px;
  font-weight: 500;
}

.delivery-copy span {
  color: var(--text-muted);
  font-size: 7.5px;
}

.delivery-status {
  flex: none;
  font-size: 7px;
  color: var(--text-muted);
}

.delivery-status.success {
  color: var(--accent);
}

.delivery-status.failed {
  color: var(--red);
}

.delivery-status.pending {
  color: var(--amber);
}

.delivery-time {
  flex: none;
  width: 45px;
  color: var(--text-muted);
  font-size: 7px;
  text-align: right;
}

.upload-icon {
  color: var(--accent);
  font-size: 28px;
}

.upload-form {
  margin-top: 14px;
}

.webhook-help {
  margin-bottom: 14px;
  padding: 9px 11px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel-soft);
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 8px;
  line-height: 1.7;
}

.webhook-help code {
  color: var(--text-secondary);
  font-size: 7.5px;
}

.event-checkboxes {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
}

.event-checkboxes :deep(.el-checkbox__label) {
  font-family: var(--mono);
  font-size: 9px;
}

@media (max-width: 1100px) {
  .asset-summary {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .asset-note {
    grid-column: span 3;
  }

  .webhook-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .webhook-list {
    border-right: none;
    border-bottom: 1px solid var(--border);
  }
}

@media (max-width: 700px) {
  .asset-summary {
    grid-template-columns: minmax(0, 1fr);
  }

  .asset-note {
    grid-column: span 1;
  }

  .asset-tabs-row {
    flex-wrap: wrap;
  }

  .tab-spacer {
    display: none;
  }

  .asset-tab {
    flex: 1;
  }
}
</style>

<style scoped>
/* Asset command center */
.asset-summary {
  gap: 12px;
}

.asset-stat,
.asset-note {
  min-height: 78px;
  border-radius: 17px;
  transition:
    transform 0.22s ease,
    box-shadow 0.22s ease,
    border-color 0.22s ease;
}

.asset-stat {
  position: relative;
  overflow: hidden;
  background:
    linear-gradient(155deg, color-mix(in srgb, var(--panel) 96%, white), var(--panel));
}

.asset-stat::before {
  content: '';
  position: absolute;
  top: -50px;
  right: -38px;
  width: 120px;
  height: 120px;
  border-radius: 50%;
  background: var(--accent-soft);
  opacity: 0.58;
  pointer-events: none;
}

.asset-stat:nth-child(2)::before {
  background: var(--violet-soft);
}

.asset-stat:nth-child(3)::before {
  background: var(--amber-soft);
}

.asset-stat:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-hover);
}

.asset-icon {
  width: 34px;
  height: 34px;
  border: 1px solid color-mix(in srgb, currentColor 13%, transparent);
  border-radius: 10px;
  font-size: 16px;
  box-shadow: 0 0 28px -18px currentColor;
}

.asset-stat strong {
  font-family: var(--font-display);
  font-size: 27px;
  font-weight: 650;
  letter-spacing: -0.055em;
}

.asset-stat span {
  font-size: 7.5px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.asset-stat small {
  color: var(--text-muted);
  font-size: 7.5px;
  white-space: nowrap;
}

.asset-note {
  position: relative;
  overflow: hidden;
  border: 1px solid rgba(84, 234, 174, 0.17);
  background:
    radial-gradient(260px 150px at 104% -10%, rgba(82, 255, 188, 0.18), transparent 65%),
    radial-gradient(200px 140px at -8% 120%, rgba(198, 243, 106, 0.07), transparent 70%),
    linear-gradient(145deg, #0a291d, #05170f 72%);
  box-shadow:
    0 1px 0 rgba(255, 255, 255, 0.06) inset,
    0 24px 48px -38px rgba(5, 30, 19, 0.72);
}

.asset-note .soft-label {
  color: #729283;
}

.asset-note p {
  color: #7e9b8c;
  font-size: 8.5px;
}

.asset-note code {
  color: #c6f36a;
}

.assets-panel {
  border-radius: 21px;
}

.asset-tabs-row {
  gap: 5px;
  padding: 11px 17px;
  background:
    linear-gradient(100deg, color-mix(in srgb, var(--panel-soft) 58%, transparent), transparent 62%);
}

.asset-tab {
  gap: 9px;
  padding: 6px 10px;
  border-radius: 10px;
}

.asset-tab .el-icon {
  display: grid;
  width: 29px;
  height: 29px;
  place-items: center;
  border: 1px solid color-mix(in srgb, var(--border) 82%, transparent);
  border-radius: 8px;
  background: color-mix(in srgb, var(--panel) 50%, transparent);
  font-size: 14px;
  transition: all 0.2s ease;
}

.asset-tab strong {
  font-size: 11px;
  font-weight: 620;
}

.asset-tab small {
  font-size: 7px;
  letter-spacing: 0.03em;
}

.asset-tab:hover {
  border-color: var(--border);
  background: color-mix(in srgb, var(--panel) 56%, transparent);
}

.asset-tab.active {
  border-color: var(--accent-line);
  background:
    linear-gradient(110deg, var(--accent-soft), color-mix(in srgb, var(--accent-soft) 22%, transparent));
  box-shadow:
    0 1px 0 var(--panel-highlight) inset,
    0 10px 24px -24px var(--accent);
}

.asset-tab.active .el-icon {
  border-color: color-mix(in srgb, var(--accent) 22%, transparent);
  background: var(--accent-soft);
  color: var(--accent);
  box-shadow: 0 0 18px -10px var(--accent);
}

.asset-tab.active strong {
  color: var(--accent-strong);
}

html.dark .asset-tab.active strong {
  color: var(--accent);
}

.asset-skeleton {
  padding: 22px;
}

.dataset-icon,
.artifact-icon {
  width: 31px;
  height: 31px;
  border: 1px solid color-mix(in srgb, currentColor 11%, transparent);
  border-radius: 9px;
  box-shadow: 0 0 22px -18px currentColor;
}

.dataset-cell strong,
.artifact-cell strong {
  font-weight: 600;
}

.artifact-toolbar {
  padding: 9px 17px;
  background: color-mix(in srgb, var(--panel-soft) 62%, transparent);
}

.filter-chip {
  padding: 4px 8px;
  border-radius: 6px;
  font-size: 8px;
}

.filter-chip.active {
  border-color: var(--accent-line);
  background: var(--accent-soft);
  color: var(--accent-strong);
}

.asset-table :deep(.el-table__row:hover) {
  background: color-mix(in srgb, var(--accent-soft) 34%, var(--panel-soft));
}

.artifact-pager {
  background: color-mix(in srgb, var(--panel-soft) 46%, transparent);
}

.dataset-cta {
  position: relative;
  display: flex;
  align-items: center;
  gap: 22px;
  margin: 5px 17px 17px;
  padding: 17px 20px;
  overflow: hidden;
  border: 1px solid rgba(95, 238, 183, 0.16);
  border-radius: 15px;
  background:
    radial-gradient(300px 170px at 92% -20%, rgba(74, 243, 178, 0.2), transparent 67%),
    radial-gradient(240px 160px at 15% 120%, rgba(198, 243, 106, 0.07), transparent 70%),
    linear-gradient(130deg, #09271b, #061a11 62%, #05150e);
  box-shadow:
    0 1px 0 rgba(255, 255, 255, 0.06) inset,
    0 24px 50px -40px rgba(3, 26, 15, 0.7);
}

.dataset-cta-copy {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  gap: 3px;
  max-width: 640px;
}

.dataset-cta-copy .soft-label {
  color: #6e9080;
  font-size: 8px;
}

.dataset-cta-copy h3 {
  color: #effff8;
  font-family: var(--font-display);
  font-size: 16px;
  font-weight: 580;
  letter-spacing: -0.025em;
}

.dataset-cta-copy p {
  color: #7e9b8c;
  font-size: 9px;
  line-height: 1.65;
}

.dataset-cta-actions {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 5px;
  margin-left: auto;
  flex: none;
}

.dataset-cta-actions .el-button.is-text {
  color: #9ab7a8;
}

.dataset-cta-mark {
  position: relative;
  display: flex;
  align-items: flex-end;
  gap: 4px;
  width: 54px;
  height: 35px;
  flex: none;
  opacity: 0.65;
}

.dataset-cta-mark span {
  width: 11px;
  border: 1px solid rgba(120, 255, 203, 0.24);
  border-radius: 3px 3px 1px 1px;
  background: linear-gradient(180deg, rgba(82, 255, 188, 0.28), rgba(82, 255, 188, 0.04));
}

.dataset-cta-mark span:nth-child(1) {
  height: 17px;
}

.dataset-cta-mark span:nth-child(2) {
  height: 30px;
}

.dataset-cta-mark span:nth-child(3) {
  height: 23px;
}

.dataset-cta-mark::after {
  content: '';
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(126, 255, 208, 0.28), transparent);
}

.webhook-layout {
  min-height: 410px;
}

.webhook-list {
  padding: 18px;
  background:
    radial-gradient(500px 240px at 0% 0%, var(--accent-soft), transparent 70%);
}

.webhook-card {
  position: relative;
  padding: 13px 14px;
  overflow: hidden;
  border-radius: 13px;
  background:
    linear-gradient(150deg, color-mix(in srgb, var(--panel) 96%, white), var(--panel));
  transition:
    transform 0.2s ease,
    border-color 0.2s ease,
    box-shadow 0.2s ease;
}

.webhook-card::before {
  content: '';
  position: absolute;
  top: 12px;
  bottom: 12px;
  left: 0;
  width: 2px;
  border-radius: 0 2px 2px 0;
  background: linear-gradient(180deg, transparent, var(--accent), transparent);
  opacity: 0.7;
}

.webhook-card:hover {
  transform: translateY(-1px);
  border-color: var(--accent-line);
  box-shadow: 0 20px 40px -36px rgba(7, 42, 28, 0.5);
}

.webhook-card-head strong {
  font-size: 11px;
  font-weight: 620;
}

.webhook-card-head span {
  font-size: 7.5px;
}

.webhook-events {
  margin-top: 9px;
}

.event-pill {
  padding: 2.5px 6px;
  border: 1px solid color-mix(in srgb, var(--border) 75%, transparent);
  border-radius: 5px;
  background: color-mix(in srgb, var(--panel-soft) 72%, transparent);
  font-size: 7px;
}

.webhook-stats {
  margin-top: 9px;
}

.webhook-actions {
  margin-top: 8px;
  padding-top: 7px;
}

.delivery-panel {
  padding: 18px;
  background:
    radial-gradient(420px 260px at 100% 0%, var(--violet-soft), transparent 68%),
    color-mix(in srgb, var(--panel-soft) 88%, var(--panel));
}

.delivery-head h3 {
  font-family: var(--font-display);
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.02em;
}

.delivery-row {
  padding: 9px 0;
}

.delivery-copy strong {
  font-size: 8px;
}

.webhook-help {
  background:
    linear-gradient(150deg, color-mix(in srgb, var(--panel-soft) 82%, transparent), var(--panel));
}

.dataset-mobile-list,
.artifact-mobile-list {
  display: none;
}

.dataset-mobile-card,
.artifact-mobile-card {
  padding: 13px;
  border: 1px solid var(--border);
  border-radius: 13px;
  background: var(--panel);
}

.mobile-card-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.mobile-card-head > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.mobile-card-head strong {
  overflow: hidden;
  color: var(--text);
  font-size: 11px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mobile-card-head .mono {
  overflow: hidden;
  color: var(--text-muted);
  font-size: 7.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mobile-card-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 9px;
  padding-top: 8px;
  border-top: 1px dashed var(--border);
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 7.5px;
}

.mobile-card-actions {
  display: flex;
  justify-content: flex-end;
  gap: 2px;
  margin-top: 3px;
}

.mobile-card-actions :deep(.el-button + .el-button) {
  margin-left: 2px;
}

.dataset-mobile-list,
.artifact-mobile-list {
  display: none;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
}

@media (max-width: 1000px) {
  .asset-table {
    display: none;
  }

  .dataset-mobile-list,
  .artifact-mobile-list {
    display: flex;
  }

  .artifact-mobile-list + .artifact-pager {
    border-top: none;
  }
}

@media (max-width: 1100px) {
  .asset-note {
    min-height: 70px;
  }
}

@media (max-width: 760px) {
  .dataset-cta {
    flex-wrap: wrap;
    gap: 12px;
    margin: 5px 12px 14px;
  }

  .dataset-cta-mark {
    display: none;
  }

  .dataset-cta-actions {
    width: 100%;
    margin-left: 0;
  }
}
</style>

<style scoped>
.assets-page {
  gap: 24px;
}

.asset-summary {
  gap: 12px;
}

.asset-stat {
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--panel);
  box-shadow: none;
  flex-wrap: wrap;
  align-content: center;
  row-gap: 4px;
}

.asset-stat > div {
  flex: 1;
  min-width: 0;
}

.asset-stat small {
  flex: 0 0 100%;
  margin-left: 0;
  padding-left: 43px;
  box-sizing: border-box;
  text-align: left;
  white-space: normal;
}

.asset-stat:hover {
  transform: none;
  box-shadow: none;
}

.asset-icon {
  width: 34px;
  height: 34px;
  border-radius: 6px;
}

.asset-stat strong {
  font-size: 28px;
  font-weight: 600;
}

.asset-stat div span {
  font-family: var(--font);
  font-size: 13px;
  letter-spacing: 0;
  white-space: nowrap;
}

.asset-stat small {
  font-family: var(--font);
  font-size: 12px;
}

.asset-note {
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--panel);
  box-shadow: none;
}

.asset-note .soft-label {
  color: var(--accent-strong);
  font-size: 12px;
}

.asset-note p {
  color: var(--text-secondary);
  font-size: 14px;
  line-height: 1.7;
}

.asset-tabs-row {
  padding: 10px 12px;
}

.asset-tab {
  min-height: 48px;
  padding: 8px 12px;
  border-radius: 6px;
}

.asset-tab strong {
  font-size: 14px;
}

.asset-tab small {
  font-size: 12px;
}

.asset-table :deep(td) {
  font-size: 14px;
}

.dataset-cell strong,
.artifact-cell strong {
  font-size: 14px;
}

.dataset-cell .mono,
.artifact-cell .mono,
.link-button,
.hash {
  font-size: 12px;
}

.dataset-cell strong,
.artifact-cell strong {
  font-size: 14px;
}

.soft-tag {
  font-size: 12px;
  line-height: 1.5;
  border-color: var(--border);
  background: var(--panel-soft);
  color: var(--text-secondary);
}

.count-pill {
  font-size: 11px;
}

.muted.small {
  font-size: 12px;
}

.asset-note code {
  padding: 1px 4px;
  border-radius: 3px;
  background: var(--panel-soft);
  color: var(--text);
  font-size: 12px;
}

.mobile-card-head .mono,
.mobile-card-meta {
  font-size: 12px;
}

.mobile-card-meta {
  line-height: 1.5;
}

.filter-chip {
  min-height: 32px;
  padding: 4px 10px;
  font-size: 13px;
}

.filter-count {
  font-size: 12px;
}

.dataset-cta {
  border-radius: var(--radius);
}

.dataset-cta h3 {
  font-size: 22px;
  font-weight: 600;
}

.dataset-cta p {
  font-size: 14px;
}

.webhook-card {
  padding: 16px;
  border-radius: var(--radius);
  box-shadow: none;
}

.webhook-card:hover {
  transform: none;
  box-shadow: none;
}

.webhook-card strong {
  font-size: 14px;
}

.webhook-card code,
.webhook-card small {
  font-size: 12px;
}

.delivery-head h3 {
  font-size: 18px;
}

.delivery-row {
  padding: 11px 0;
}

.delivery-copy strong {
  font-size: 13px;
}

.delivery-copy span,
.delivery-status,
.delivery-time {
  font-size: 12px;
}

.webhook-empty,
.delivery-empty {
  font-size: 14px;
}

.dataset-cta .soft-label {
  color: #9fe0c3;
  font-size: 12px;
}

.dataset-cta {
  background: #0c1a13;
}

.mobile-card-head strong {
  font-size: 14px;
}

@media (max-width: 1000px) {
  .asset-tabs-row > .tag-soft {
    display: none;
  }

  .asset-stat {
    padding: 14px;
  }

  .asset-tab {
    flex: 1 1 0;
    min-width: 0;
    justify-content: center;
    padding: 8px 6px;
  }

  .asset-tab .el-icon {
    width: 24px;
    height: 24px;
    font-size: 13px;
  }

  .asset-tab strong {
    font-size: 13px;
    white-space: nowrap;
  }

  .asset-tab small {
    display: none;
  }
}
</style>
