export type TaskStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface Task {
  id: string
  name: string
  owner: string
  status: TaskStatus
  priority: number
  model: string
  dataset: string
  epochs: number
  imgsz: number
  batch_size: number
  workers: number
  device: string
  command: string
  notes: string | null
  cpu_cores: number
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
  epochs: number
  imgsz: number
  batch_size: number
  workers: number
  cpu_cores: number
  priority: number
  max_runtime_seconds: number
  max_retries: number
  notes?: string | null
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
