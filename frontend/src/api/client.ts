import axios, { AxiosError } from 'axios'

import type {
  AuthUser,
  LoginResponse,
  UserCreatePayload,
  UserUpdatePayload,
} from '@/types/auth'
import type {
  CancelResponse,
  ClusterOverview,
  LogChunk,
  PreviewResponse,
  Task,
  TaskCheckpoint,
  TaskCheckpointList,
  TaskAttempt,
  TaskCompareResponse,
  TaskCreatePayload,
  TaskDistribution,
  TaskEvent,
  TaskListResponse,
  TaskManifest,
  TaskMetrics,
  TaskOptions,
  TaskQuery,
  RuntimeSettings,
} from '@/types/task'
import type {
  Artifact,
  BenchmarkRun,
  Dataset,
  DatasetQuality,
  ModelWeight,
  NodeTopologyItem,
  NodeTopologyResponse,
  Pipeline,
  PipelineCreatePayload,
  PipelineListResponse,
  WebhookDelivery,
  WebhookEndpoint,
} from '@/types/p2'

export class ApiError extends Error {
  status: number | null

  constructor(message: string, status: number | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function extractMessage(error: unknown): string {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail
        .map((item) => (typeof item?.msg === 'string' ? item.msg : JSON.stringify(item)))
        .join('；')
    }
    if (error.code === 'ERR_NETWORK') return '无法连接到后端服务，请确认 API 已在 8000 端口运行'
    if (error.response?.status) return `请求失败（HTTP ${error.response.status}）`
    return error.message
  }
  if (error instanceof Error) return error.message
  return '未知错误'
}

const http = axios.create({
  baseURL: '/api/v1',
  timeout: 20_000,
  headers: { 'Content-Type': 'application/json' },
})

export const AUTH_TOKEN_KEY = 'litetrain.auth.token'
export const AUTH_USER_KEY = 'litetrain.auth.user'

http.interceptors.request.use((config) => {
  const token = localStorage.getItem(AUTH_TOKEN_KEY)
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    const status = error instanceof AxiosError ? error.response?.status ?? null : null
    if (status === 401 && localStorage.getItem(AUTH_TOKEN_KEY)) {
      const url = error instanceof AxiosError ? error.config?.url ?? '' : ''
      if (!url.endsWith('/auth/login')) {
        localStorage.removeItem(AUTH_TOKEN_KEY)
        localStorage.removeItem(AUTH_USER_KEY)
        if (window.location.pathname !== '/login') {
          window.location.assign('/login')
        }
      }
    }
    return Promise.reject(new ApiError(extractMessage(error), status))
  },
)

export const api = {
  async login(username: string, password: string, client: 'platform' | 'admin' = 'platform'): Promise<LoginResponse> {
    const { data } = await http.post<LoginResponse>('/auth/login', { username, password, client })
    return data
  },

  async getMe(): Promise<AuthUser> {
    const { data } = await http.get<AuthUser>('/auth/me')
    return data
  },

  async logout(): Promise<void> {
    await http.post('/auth/logout')
  },

  async listUsers(): Promise<AuthUser[]> {
    const { data } = await http.get<{ items: AuthUser[]; total: number }>('/admin/users')
    return data.items
  },

  async createUser(payload: UserCreatePayload): Promise<AuthUser> {
    const { data } = await http.post<AuthUser>('/admin/users', payload)
    return data
  },

  async updateUser(userId: string, payload: UserUpdatePayload): Promise<AuthUser> {
    const { data } = await http.patch<AuthUser>(`/admin/users/${userId}`, payload)
    return data
  },

  async getOverview(): Promise<ClusterOverview> {
    const { data } = await http.get<ClusterOverview>('/cluster/overview')
    return data
  },

  async getTaskDistribution(window: string): Promise<TaskDistribution> {
    const { data } = await http.get<TaskDistribution>('/cluster/task-distribution', {
      params: { window },
    })
    return data
  },

  async listTasks(query: TaskQuery = {}): Promise<TaskListResponse> {
    const { data } = await http.get<TaskListResponse>('/tasks', { params: query })
    return data
  },

  async getTask(taskId: string): Promise<Task> {
    const { data } = await http.get<Task>(`/tasks/${taskId}`)
    return data
  },

  async createTask(payload: TaskCreatePayload): Promise<Task> {
    const { data } = await http.post<Task>('/tasks', payload)
    return data
  },

  async cancelTask(taskId: string): Promise<CancelResponse> {
    const { data } = await http.post<CancelResponse>(`/tasks/${taskId}/cancel`)
    return data
  },

  async getOptions(): Promise<TaskOptions> {
    const { data } = await http.get<TaskOptions>('/tasks/options')
    return data
  },

  async getRuntimeSettings(): Promise<RuntimeSettings> {
    const { data } = await http.get<RuntimeSettings>('/settings')
    return data
  },

  async updateRuntimeSettings(
    payload: Partial<Omit<RuntimeSettings, 'limits' | 'total_cpus' | 'max_runtime_seconds_limit' | 'updated_at'>>,
  ): Promise<RuntimeSettings> {
    const { data } = await http.patch<RuntimeSettings>('/settings', payload)
    return data
  },

  async previewTask(payload: TaskCreatePayload): Promise<PreviewResponse> {
    const { data } = await http.post<PreviewResponse>('/tasks/preview', payload)
    return data
  },

  async getLogs(taskId: string, offset = 0, limit = 65_536): Promise<LogChunk> {
    const { data } = await http.get<LogChunk>(`/tasks/${taskId}/logs`, {
      params: { offset, limit },
    })
    return data
  },

  async getTaskEvents(taskId: string): Promise<TaskEvent[]> {
    const { data } = await http.get<{ items: TaskEvent[] }>(`/tasks/${taskId}/events`)
    return data.items
  },

  async getTaskMetrics(taskId: string): Promise<TaskMetrics> {
    const { data } = await http.get<TaskMetrics>(`/tasks/${taskId}/metrics`)
    return data
  },

  async getTaskManifest(taskId: string): Promise<TaskManifest> {
    const { data } = await http.get<TaskManifest>(`/tasks/${taskId}/manifest`)
    return data
  },

  async getTaskCheckpoints(taskId: string): Promise<TaskCheckpointList> {
    const { data } = await http.get<TaskCheckpointList>(`/tasks/${taskId}/checkpoints`)
    return data
  },

  async getTaskAttempts(taskId: string): Promise<TaskAttempt[]> {
    const { data } = await http.get<{ items: TaskAttempt[]; total: number }>(
      `/tasks/${taskId}/attempts`,
    )
    return data.items
  },

  async resumeTask(
    taskId: string,
    mode: 'full' | 'weights',
    checkpointEpoch?: number | null,
  ): Promise<Task> {
    const { data } = await http.post<Task>(`/tasks/${taskId}/resume`, {
      mode,
      checkpoint_epoch: checkpointEpoch ?? null,
    })
    return data
  },

  async rerunTask(taskId: string): Promise<Task> {
    const { data } = await http.post<Task>(`/tasks/${taskId}/rerun`)
    return data
  },

  async compareTasks(taskIds: string[]): Promise<TaskCompareResponse> {
    const { data } = await http.get<TaskCompareResponse>('/tasks/compare', {
      params: { ids: taskIds.join(',') },
    })
    return data
  },

  async applyNodeTopology(nodes: NodeTopologyItem[]): Promise<NodeTopologyResponse> {
    const { data } = await http.post<NodeTopologyResponse>('/cluster/nodes/topology', { nodes })
    return data
  },

  async setNodeStatus(nodeId: string, status: 'online' | 'offline') {
    const { data } = await http.patch(`/cluster/nodes/${nodeId}`, { status })
    return data
  },

  async startBenchmark(payload: {
    dataset: string
    model: string
    imgsz: number
    batch: number
    threads: number[]
    workers: number[]
    epochs?: number
  }): Promise<BenchmarkRun> {
    const { data } = await http.post<BenchmarkRun>('/cluster/benchmarks', payload)
    return data
  },

  async latestBenchmark(): Promise<BenchmarkRun | null> {
    const { data } = await http.get<BenchmarkRun | null>('/cluster/benchmarks/latest')
    return data
  },

  async getBenchmark(id: string): Promise<BenchmarkRun> {
    const { data } = await http.get<BenchmarkRun>(`/cluster/benchmarks/${id}`)
    return data
  },

  async listPipelines(
    page = 1,
    pageSize = 10,
    filters: { status?: string; query?: string } = {},
  ): Promise<PipelineListResponse> {
    const { data } = await http.get<PipelineListResponse>('/pipelines', {
      params: { page, page_size: pageSize, status: filters.status, query: filters.query },
    })
    return data
  },

  async getPipeline(pipelineId: string): Promise<Pipeline> {
    const { data } = await http.get<Pipeline>(`/pipelines/${pipelineId}`)
    return data
  },

  async createPipeline(payload: PipelineCreatePayload): Promise<Pipeline> {
    const { data } = await http.post<Pipeline>('/pipelines', payload)
    return data
  },

  async rerunPipeline(pipelineId: string): Promise<Pipeline> {
    const { data } = await http.post<Pipeline>(`/pipelines/${pipelineId}/rerun`)
    return data
  },

  async cancelPipeline(pipelineId: string): Promise<Pipeline> {
    const { data } = await http.post<Pipeline>(`/pipelines/${pipelineId}/cancel`)
    return data
  },

  async resumePipeline(pipelineId: string): Promise<Pipeline> {
    const { data } = await http.post<Pipeline>(`/pipelines/${pipelineId}/resume`)
    return data
  },

  async listDatasets(): Promise<Dataset[]> {
    const { data } = await http.get<{ items: Dataset[]; total: number }>('/datasets')
    return data.items
  },

  async uploadDataset(form: FormData): Promise<Dataset> {
    const { data } = await http.post<Dataset>('/datasets/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 300_000,
    })
    return data
  },

  async listModelWeights(): Promise<ModelWeight[]> {
    const { data } = await http.get<{ items: ModelWeight[]; total: number }>('/models')
    return data.items
  },

  async uploadModelWeight(file: File): Promise<ModelWeight> {
    const form = new FormData()
    form.append('file', file)
    const { data } = await http.post<ModelWeight>('/models/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 600_000,
    })
    return data
  },

  async getDatasetQuality(datasetId: string): Promise<DatasetQuality> {
    const { data } = await http.get<DatasetQuality>(`/datasets/${datasetId}/quality`)
    return data
  },

  async auditDataset(datasetId: string): Promise<DatasetQuality> {
    const { data } = await http.post<DatasetQuality>(`/datasets/${datasetId}/audit`)
    return data
  },

  async acknowledgeDatasetQuality(datasetId: string, note?: string): Promise<DatasetQuality> {
    const { data } = await http.post<DatasetQuality>(`/datasets/${datasetId}/quality/ack`, {
      note: note ?? null,
    })
    return data
  },

  datasetQualityReportUrl(datasetId: string): string {
    return `/api/v1/datasets/${datasetId}/quality/report`
  },

  async deleteDataset(datasetId: string): Promise<void> {
    await http.delete(`/datasets/${datasetId}`)
  },

  datasetDownloadUrl(datasetId: string): string {
    return `/api/v1/datasets/${datasetId}/download`
  },

  async listArtifacts(params: {
    task_id?: string
    pipeline_id?: string
    kind?: string
  } = {}): Promise<Artifact[]> {
    const { data } = await http.get<{ items: Artifact[]; total: number }>('/artifacts', { params })
    return data.items
  },

  artifactDownloadUrl(artifact: Artifact): string {
    return artifact.download_url
  },

  async listWebhooks(): Promise<WebhookEndpoint[]> {
    const { data } = await http.get<{ items: WebhookEndpoint[]; total: number }>('/webhooks')
    return data.items
  },

  async createWebhook(payload: {
    name: string
    url: string
    secret?: string
    events: string[]
    active: boolean
  }): Promise<WebhookEndpoint> {
    const { data } = await http.post<WebhookEndpoint>('/webhooks', payload)
    return data
  },

  async updateWebhook(endpointId: string, active: boolean): Promise<WebhookEndpoint> {
    const { data } = await http.patch<WebhookEndpoint>(`/webhooks/${endpointId}`, { active })
    return data
  },

  async deleteWebhook(endpointId: string): Promise<void> {
    await http.delete(`/webhooks/${endpointId}`)
  },

  async testWebhook(endpointId: string): Promise<{ accepted: boolean; delivery_id: string }> {
    const { data } = await http.post<{ accepted: boolean; delivery_id: string }>(
      `/webhooks/${endpointId}/test`,
    )
    return data
  },

  async listWebhookDeliveries(endpointId?: string): Promise<WebhookDelivery[]> {
    const { data } = await http.get<{ items: WebhookDelivery[]; total: number }>(
      '/webhooks/deliveries',
      { params: { endpoint_id: endpointId, limit: 100 } },
    )
    return data.items
  },
}

export function logDownloadUrl(taskId: string): string {
  return `/api/v1/tasks/${taskId}/logs/download`
}

export function logStreamUrl(taskId: string, offset = 0): string {
  return `/api/v1/tasks/${taskId}/logs/stream?offset=${offset}`
}
