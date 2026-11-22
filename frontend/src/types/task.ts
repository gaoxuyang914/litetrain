export type TaskStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export type TaskDistributionWindow =
  | '5m'
  | '15m'
  | '30m'
  | '1h'
  | '3h'
  | '6h'
  | '12h'
  | '24h'
  | '7d'
  | '30d'
  | '90d'
  | '180d'
  | 'all'

export interface TaskDistribution {
  window: TaskDistributionWindow
  label: string
  since: string | null
  generated_at: string
  total: number
  counts: Record<TaskStatus, number>
}

export interface Task {
  id: string
  name: string
  owner: string
  status: TaskStatus
  priority: number
  kind: 'train' | 'preprocess' | 'evaluate' | string
  model: string
  dataset: string
  dataset_id: string | null
  dataset_sha256: string | null
  epochs: number
  imgsz: number
  batch_size: number
  workers: number
  device: string
  command: string
  notes: string | null
  cpu_cores: number
  compute_threads: number | null
  cache_mode: string
  seed: number
  patience: number | null
  save_period: number
  resume_from_task_id: string | null
  resume_mode: string | null
  checkpoint_epoch: number | null
  stop_reason: string | null
  preempted_by_task_id?: string | null
  preempted_at?: string | null
  preempt_count?: number
  max_runtime_seconds: number
  max_retries: number
  retry_count: number
  attempt: number
  submitted_at: string
  queued_at: string
  started_at: string | null
  finished_at: string | null
  last_heartbeat_at: string | null
  cancel_requested_at: string | null
  executor: string | null
  pid: number | null
  exit_code: number | null
  failure_reason: string | null
  progress: number
  latest_cpu_percent: number | null
  latest_memory_mb: number | null
  runtime_seconds: number
  queue_position: number | null
  log_size_bytes: number
}

export interface TaskListResponse {
  items: Task[]
  total: number
  page: number
  page_size: number
  pages: number
  status_counts: Record<TaskStatus, number>
}

export interface TaskCreatePayload {
  name?: string
  model: string
  dataset: string
  dataset_id?: string | null
  quality_ack?: boolean
  epochs: number
  imgsz: number
  batch_size: number
  workers: number
  cpu_cores: number
  compute_threads?: number | null
  cache_mode?: 'none' | 'disk' | 'ram'
  seed?: number
  patience?: number | null
  save_period?: number
  priority: number
  max_runtime_seconds: number
  max_retries: number
  notes?: string | null
}

export interface TaskCheckpoint {
  epoch: number
  path: string
  size_bytes: number
  metrics: Record<string, number>
  is_best: boolean
  created_at: string
}

export interface TaskCheckpointList {
  items: TaskCheckpoint[]
  total: number
  fallback_available: boolean
}

export interface TaskAttempt {
  attempt: number
  status: TaskStatus
  started_at: string | null
  finished_at: string | null
  runtime_seconds: number | null
  exit_code: number | null
  failure_reason: string | null
  progress: number
  cpu_cores: number
}

export interface TaskManifest {
  task_id: string
  manifest: {
    dataset?: {
      id?: string | null
      name?: string | null
      version?: string | null
      content_sha256?: string | null
      quality_status?: string
    }
    model?: { name?: string; sha256?: string | null }
    config?: Record<string, unknown>
    runtime?: Record<string, unknown>
  }
  dataset_sha256: string | null
  model_sha256: string | null
  git_commit: string | null
  created_at: string
}

export interface TaskCompareItem {
  task_id: string
  name: string
  status: TaskStatus
  dataset_id: string | null
  dataset_sha256: string | null
  config: Record<string, unknown>
  final_metrics: Record<string, number>
  runtime_seconds: number
  peak_memory_mb: number | null
  manifest: TaskManifest['manifest'] | null
}

export interface TaskCompareResponse {
  compatible: boolean
  reason: string | null
  items: TaskCompareItem[]
  metric_deltas: Record<string, number>
}

export interface TaskOptions {
  models: string[]
  datasets: string[]
  total_cpus: number
  user_cpu_quota: number
  execution_backend: string
  defaults: {
    model: string
    dataset: string
    epochs: number
    imgsz: number
    batch_size: number
    workers: number
    cpu_cores: number
    priority: number
    max_runtime_seconds: number
    max_retries: number
  }
  limits: Record<string, [number, number]>
}

export interface RuntimeSettings {
  default_max_runtime_seconds: number
  heartbeat_timeout_seconds: number
  default_max_retries: number
  default_cpu_cores: number
  user_cpu_quota: number
  total_cpus: number
  max_runtime_seconds_limit: number
  limits: Record<string, [number, number]>
  updated_at: string | null
}

export interface PreviewResponse {
  command: string
  equivalent_yolo_command: string
  estimated_note: string
}

export interface LogChunk {
  task_id: string
  start_offset: number
  next_offset: number
  content: string
  eof: boolean
  file_size: number
  task_status: TaskStatus
}

export interface TaskEvent {
  id: number
  task_id: string
  event_type: string
  level: string
  message: string
  created_at: string
}

export interface TaskMetrics {
  task_id: string
  series: Array<{
    name: string
    points: Array<{ epoch: number; step: number; value: number }>
  }>
  resources: Array<{
    cpu_percent: number
    memory_mb: number
    process_count: number
    sampled_at: string
  }>
  final_metrics: Record<string, number>
}

export interface CancelResponse {
  task: Task
  message: string
}

export interface NodeInfo {
  id: string
  name: string
  hostname: string
  status: string
  total_cpus: number
  allocated_cpus: number
  free_cpus: number
  running_tasks: number
  active_tasks: Array<{
    id: string
    name: string
    progress: number
    cpu_cores: number
    status: string
  }>
  cpu_utilization_percent: number
  host_percent: number
  last_seen: string
}

export interface ClusterOverview {
  cpu: {
    total: number
    budget: number
    allocated: number
    available: number
    utilization_percent: number
    host_percent: number
    host_logical_cores: number
    process_percent: number
  }
  memory: {
    total_mb: number
    used_mb: number
    available_mb: number
    percent: number
  }
  tasks: {
    pending: number
    running: number
    succeeded: number
    failed: number
    cancelled: number
    total: number
  }
  nodes: NodeInfo[]
  quota: {
    owner: string
    limit_cpus: number
    used_cpus: number
    available_cpus: number
  }
  services: {
    execution_backend: string
    execution_mode: string
    ray_connected: boolean
    ray_address: string
    ray_version: string | null
    ray_cpus: number | null
    redis_connected: boolean
    redis_version: string | null
    scheduler_leader: boolean
  }
  uptime_seconds: number
  updated_at: string
}

export interface TaskQuery {
  page?: number
  page_size?: number
  status?: string
  q?: string
  sort_by?: string
  sort_order?: 'asc' | 'desc'
}
