import axios, { AxiosError } from 'axios'

import type {
  CancelResponse,
  ClusterOverview,
  LogChunk,
  PreviewResponse,
  Task,
  TaskCreatePayload,
  TaskEvent,
  TaskListResponse,
  TaskMetrics,
  TaskOptions,
  TaskQuery,
} from '@/types/task'

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

http.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    const status = error instanceof AxiosError ? error.response?.status ?? null : null
    return Promise.reject(new ApiError(extractMessage(error), status))
  },
)

export const api = {
  async getOverview(): Promise<ClusterOverview> {
    const { data } = await http.get<ClusterOverview>('/cluster/overview')
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
}

export function logDownloadUrl(taskId: string): string {
  return `/api/v1/tasks/${taskId}/logs/download`
}

export function logStreamUrl(taskId: string, offset = 0): string {
  return `/api/v1/tasks/${taskId}/logs/stream?offset=${offset}`
}
