import type { TaskStatus } from '@/types/task'

export function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null
  let normalized = value
  if (!/Z$|[+-]\d{2}:?\d{2}$/.test(normalized)) {
    normalized = `${normalized}Z`
  }
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatDate(value: string | null | undefined): string {
  const date = parseDate(value)
  if (!date) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}

export function formatFullDate(value: string | null | undefined): string {
  const date = parseDate(value)
  if (!date) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}

export function timeAgo(value: string | null | undefined): string {
  const date = parseDate(value)
  if (!date) return '—'
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000))
  if (seconds < 10) return '刚刚'
  if (seconds < 60) return `${seconds} 秒前`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  return `${days} 天前`
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0) return '—'
  const total = Math.floor(seconds)
  if (total < 60) return `${total}s`
  const minutes = Math.floor(total / 60)
  const remain = total % 60
  if (minutes < 60) return `${minutes}m ${remain.toString().padStart(2, '0')}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${(minutes % 60).toString().padStart(2, '0')}m`
}

export function formatBytes(bytes: number | null | undefined): string {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return '—'
  return `${value.toFixed(digits)}%`
}

export function shortId(id: string | null | undefined): string {
  if (!id) return '—'
  return id.length > 8 ? `${id.slice(0, 4)}…${id.slice(-4)}` : id
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

export interface StatusMeta {
  label: string
  short: string
  tone: string
  color: string
}

export const statusMeta: Record<TaskStatus, StatusMeta> = {
  pending: { label: '等待调度', short: '等待', tone: 'pending', color: '#d97706' },
  running: { label: '运行中', short: '运行', tone: 'running', color: '#0c9b6a' },
  succeeded: { label: '训练成功', short: '成功', tone: 'succeeded', color: '#16865f' },
  failed: { label: '训练失败', short: '失败', tone: 'failed', color: '#d64545' },
  cancelled: { label: '已取消', short: '取消', tone: 'cancelled', color: '#7c8c84' },
}

export const statusOrder: TaskStatus[] = [
  'pending',
  'running',
  'succeeded',
  'failed',
  'cancelled',
]

export function isTerminal(status: TaskStatus): boolean {
  return ['succeeded', 'failed', 'cancelled'].includes(status)
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    const area = document.createElement('textarea')
    area.value = text
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(area)
    return ok
  }
}
