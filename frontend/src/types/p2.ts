import type { TaskStatus } from './task'

export interface NodeTopologyItem {
  name: string
  total_cpus: number
}

export interface NodeTopologyResponse {
  nodes: import('./task').NodeInfo[]
  total_cpus: number
}

export interface Dataset {
  id: string
  name: string
  version: string
  description: string | null
  yaml_path: string
  source_filename: string | null
  content_sha256: string | null
  image_count: number
  label_count: number
  class_count: number
  size_bytes: number
  status: string
  quality_status: 'pending' | 'auditing' | 'passed' | 'warning' | 'blocked' | 'failed'
  quality_score: number | null
  quality_acknowledged: boolean
  is_builtin: boolean
  created_at: string
}

export interface QualityIssue {
  code: string
  severity: 'blocking' | 'warning'
  message: string
  count: number
  examples: string[]
}

export interface DatasetQuality {
  dataset_id: string
  status: Dataset['quality_status']
  score: number | null
  engine_version: string | null
  checked_at: string | null
  summary: Record<string, unknown>
  issues: QualityIssue[]
  report_url: string | null
}

export interface BenchmarkRun {
  id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  dataset: string | null
  host: Record<string, unknown>
  matrix: {
    configs?: Array<Record<string, unknown>>
    results?: Array<Record<string, unknown>>
  }
  selected: Record<string, unknown> | null
  log_path: string | null
  error: string | null
  created_at: string
  finished_at: string | null
}

export interface Artifact {
  id: string
  task_id: string
  pipeline_id: string | null
  kind: string
  name: string
  size_bytes: number
  sha256: string
  created_at: string
  download_url: string
}

export interface ModelWeight {
  name: string
  size_bytes: number
  modified_at: string
  sha256: string | null
  is_builtin: boolean
}

export interface PipelineStageTask {
  id: string
  name: string
  status: TaskStatus
  progress: number
  runtime_seconds: number
  failure_reason: string | null
  log_size_bytes: number
}

export interface PipelineStage {
  id: string
  stage_key: 'preprocess' | 'train' | 'evaluate' | string
  sequence: number
  status: string
  message: string | null
  task: PipelineStageTask | null
  metrics: Record<string, number>
  details: Record<string, any>
  started_at: string | null
  finished_at: string | null
}

export interface PipelineConfig {
  source_dataset: string
  source_dataset_path?: string
  model: string
  epochs: number
  imgsz: number
  batch_size: number
  workers: number
  cpu_cores: number
  compute_threads?: number | null
  cache_mode?: 'none' | 'disk' | 'ram'
  priority: number
  max_runtime_seconds: number
  max_retries: number
  max_images: number
  val_ratio?: number
  seed?: number
  dedupe?: boolean
  eval_split?: 'train' | 'val'
  eval_conf?: number
  eval_iou?: number
  eval_max_det?: number
  notes?: string | null
}

export interface Pipeline {
  id: string
  name: string
  owner: string
  status: TaskStatus
  config: PipelineConfig
  failure_reason: string | null
  submitted_at: string
  started_at: string | null
  finished_at: string | null
  progress: number
  current_stage: string | null
  stages: PipelineStage[]
  artifacts: Artifact[]
}

export interface PipelineCreatePayload {
  name?: string
  source_dataset: string
  model: string
  epochs: number
  imgsz: number
  batch_size: number
  workers: number
  cpu_cores: number
  compute_threads?: number | null
  cache_mode?: 'none' | 'disk' | 'ram'
  priority: number
  max_runtime_seconds: number
  max_retries: number
  max_images: number
  val_ratio: number
  seed: number
  dedupe: boolean
  eval_split: 'train' | 'val'
  eval_conf: number
  eval_iou: number
  eval_max_det: number
  notes?: string | null
}

export interface PipelineListResponse {
  items: Pipeline[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface WebhookEndpoint {
  id: string
  name: string
  url: string
  secret: string
  events: string[]
  active: boolean
  created_at: string
  delivery_count: number
  pending_count: number
  success_count: number
  failed_count: number
}

export interface WebhookDelivery {
  id: string
  endpoint_id: string
  event_type: string
  task_id: string | null
  pipeline_id: string | null
  status: string
  attempts: number
  max_attempts: number
  next_attempt_at: string
  response_code: number | null
  response_excerpt: string | null
  created_at: string
  updated_at: string
}
