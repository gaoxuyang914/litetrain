import { computed, ref } from 'vue'

import type { Task, TaskStatus } from '@/types/task'
import { isTerminal, statusMeta } from '@/utils/format'

const STORAGE_KEY = 'litetrain.notifications'
const enabled = ref(localStorage.getItem(STORAGE_KEY) === '1')
const permission = ref<NotificationPermission>(
  typeof Notification === 'undefined' ? 'denied' : Notification.permission,
)

const supported = computed(
  () => typeof window !== 'undefined' && 'Notification' in window,
)

async function setEnabled(value: boolean): Promise<{ ok: boolean; message: string }> {
  if (!value) {
    enabled.value = false
    localStorage.setItem(STORAGE_KEY, '0')
    return { ok: true, message: '浏览器通知已关闭' }
  }
  if (!supported.value) {
    return { ok: false, message: '当前浏览器不支持桌面通知' }
  }
  if (Notification.permission === 'denied') {
    return { ok: false, message: '通知权限已被拒绝，请在浏览器站点设置中开启' }
  }
  if (Notification.permission !== 'granted') {
    const result = await Notification.requestPermission()
    permission.value = result
    if (result !== 'granted') {
      return { ok: false, message: '未获得通知权限' }
    }
  }
  enabled.value = true
  localStorage.setItem(STORAGE_KEY, '1')
  return { ok: true, message: '任务完成时将发送浏览器通知' }
}

function notify(task: Task, previous?: TaskStatus) {
  if (!enabled.value || !supported.value || Notification.permission !== 'granted') return
  if (previous && isTerminal(previous)) return
  if (!isTerminal(task.status)) return
  const meta = statusMeta[task.status]
  const body =
    task.status === 'succeeded'
      ? `${task.name} 已完成，共 ${task.epochs} 个 epoch`
      : task.status === 'failed'
        ? `${task.name} 训练失败${task.failure_reason ? `：${task.failure_reason.slice(0, 110)}` : ''}`
        : `${task.name} 已取消`
  try {
    const notification = new Notification(`LiteTrain · ${meta.label}`, {
      body,
      tag: `litetrain-${task.id}-${task.status}`,
      icon: '/favicon.svg',
    })
    notification.onclick = () => {
      window.focus()
      window.location.href = `/tasks/${task.id}`
      notification.close()
    }
  } catch {
    // Some browsers only allow notifications from a service worker; ignore.
  }
}

export function useNotifications() {
  return {
    enabled,
    permission,
    supported,
    setEnabled,
    notify,
  }
}
