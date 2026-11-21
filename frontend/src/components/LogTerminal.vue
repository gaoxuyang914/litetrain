<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  ArrowDownBold,
  CopyDocument,
  Download,
  Refresh,
  Search,
  VideoPause,
  VideoPlay,
  WarningFilled,
} from '@element-plus/icons-vue'

import { api, logDownloadUrl, logStreamUrl } from '@/api/client'
import type { TaskStatus } from '@/types/task'
import { copyText, formatBytes, isTerminal } from '@/utils/format'

const props = defineProps<{
  taskId: string
  status: TaskStatus
}>()

type ConnectionMode = 'loading' | 'sse' | 'polling' | 'ended' | 'error'

const ROW_HEIGHT = 22
const OVERSCAN = 14
const MAX_LINES = 25_000
const INITIAL_LIMIT = 131_072

const scroller = ref<HTMLElement | null>(null)
const lines = ref<string[]>([])
const partial = ref('')
const version = ref(0)
const offset = ref(0)
const fileSize = ref(0)
const follow = ref(true)
const newLines = ref(0)
const loading = ref(true)
const loadError = ref<string | null>(null)
const mode = ref<ConnectionMode>('loading')
const searchInput = ref('')
const highlightKeyword = ref('')
const truncated = ref(false)
const viewportHeight = ref(440)
const scrollTop = ref(0)

let eventSource: EventSource | null = null
let pollTimer: number | undefined
let searchTimer: number | undefined
let resizeObserver: ResizeObserver | undefined
let disposed = false
let requestToken = 0
let stableEofPolls = 0

const lineCount = computed(() => {
  void version.value
  return lines.value.length + (partial.value ? 1 : 0)
})

const totalHeight = computed(() => lineCount.value * ROW_HEIGHT)

const visibleStart = computed(() => {
  const raw = Math.floor(scrollTop.value / ROW_HEIGHT) - OVERSCAN
  return Math.max(0, raw)
})

const visibleEnd = computed(() => {
  const visible = Math.ceil(viewportHeight.value / ROW_HEIGHT) + OVERSCAN * 2
  return Math.min(lineCount.value, visibleStart.value + visible)
})

const visibleIndices = computed(() => {
  void version.value
  const result: number[] = []
  for (let i = visibleStart.value; i < visibleEnd.value; i += 1) result.push(i)
  return result
})

const matches = computed(() => {
  void version.value
  const needle = highlightKeyword.value.trim().toLowerCase()
  if (!needle) return 0
  let count = 0
  for (const line of lines.value) {
    if (line.toLowerCase().includes(needle)) count += 1
  }
  if (partial.value && partial.value.toLowerCase().includes(needle)) count += 1
  return count
})

const connectionLabel = computed(() => {
  if (mode.value === 'sse') return '实时连接'
  if (mode.value === 'polling') return '轮询中'
  if (mode.value === 'ended') return '日志已结束'
  if (mode.value === 'error') return '连接断开'
  return '连接中'
})

const connectionTone = computed(() => {
  if (mode.value === 'sse') return 'live'
  if (mode.value === 'polling') return 'polling'
  if (mode.value === 'ended') return 'ended'
  if (mode.value === 'error') return 'error'
  return 'connecting'
})

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function renderLine(text: string): string {
  const escaped = escapeHtml(text)
  const keyword = highlightKeyword.value.trim()
  if (!keyword) return escaped
  const escapedKeyword = escapeHtml(keyword)
  try {
    const pattern = new RegExp(escapeRegExp(escapedKeyword), 'gi')
    return escaped.replace(pattern, (match) => `<mark class="log-match">${match}</mark>`)
  } catch {
    return escaped
  }
}

function getLine(index: number): string {
  if (index < lines.value.length) return lines.value[index] ?? ''
  return partial.value
}

function appendText(content: string) {
  if (!content) return
  const previouslyEmpty = lineCount.value === 0
  const combined = partial.value + content
  const segments = combined.split('\n')
  partial.value = segments.pop() ?? ''
  let added = 0
  if (segments.length) {
    added = segments.length
    lines.value.push(...segments)
    if (lines.value.length > MAX_LINES) {
      const excess = lines.value.length - MAX_LINES
      lines.value.splice(0, excess)
      truncated.value = true
    }
  }
  version.value += 1
  if (!follow.value && !previouslyEmpty) {
    newLines.value += Math.max(1, added)
  }
  if (follow.value) {
    void scrollToBottom()
  }
}

function applyWindow() {
  const scroll = scroller.value
  if (!scroll) return
  viewportHeight.value = scroll.clientHeight
  scrollTop.value = scroll.scrollTop
}

async function scrollToBottom(force = false) {
  if (!follow.value && !force) return
  await nextTick()
  const scroll = scroller.value
  if (!scroll) return
  scroll.scrollTop = scroll.scrollHeight
  scrollTop.value = scroll.scrollTop
  newLines.value = 0
}

function onScroll() {
  const scroll = scroller.value
  if (!scroll) return
  scrollTop.value = scroll.scrollTop
  const atBottom = scroll.scrollTop + scroll.clientHeight >= scroll.scrollHeight - 4
  if (!atBottom && follow.value) {
    follow.value = false
  }
}

function toggleFollow() {
  follow.value = !follow.value
  if (follow.value) {
    newLines.value = 0
    void scrollToBottom(true)
  }
}

function clearTimers() {
  if (pollTimer) {
    window.clearTimeout(pollTimer)
    pollTimer = undefined
  }
}

function closeStream() {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
  clearTimers()
}

function finishStream() {
  closeStream()
  mode.value = 'ended'
}

async function pollOnce(token: number) {
  if (disposed || token !== requestToken) return
  try {
    const chunk = await api.getLogs(props.taskId, offset.value, 65_536)
    if (disposed || token !== requestToken) return
    appendText(chunk.content)
    offset.value = chunk.next_offset
    fileSize.value = chunk.file_size
    loadError.value = null
    stableEofPolls = chunk.eof && isTerminal(chunk.task_status) ? stableEofPolls + 1 : 0
    if (stableEofPolls >= 2) {
      finishStream()
      return
    }
    pollTimer = window.setTimeout(() => pollOnce(token), 1000)
  } catch (err) {
    if (disposed || token !== requestToken) return
    if (!loadError.value) {
      loadError.value = err instanceof Error ? err.message : '日志读取失败'
    }
    mode.value = 'polling'
    pollTimer = window.setTimeout(() => pollOnce(token), 2000)
  }
}

function startPolling(token: number) {
  closeStream()
  mode.value = 'polling'
  stableEofPolls = 0
  pollOnce(token)
}

function startSse(token: number) {
  if (typeof EventSource === 'undefined') {
    startPolling(token)
    return
  }
  closeStream()
  mode.value = 'sse'
  const source = new EventSource(logStreamUrl(props.taskId, offset.value))
  eventSource = source

  source.addEventListener('ready', () => {
    mode.value = 'sse'
  })

  source.addEventListener('log', (event: MessageEvent) => {
    if (disposed || token !== requestToken) return
    try {
      const payload = JSON.parse(event.data) as { offset: number; content: string; file_size: number }
      appendText(payload.content)
      offset.value = payload.offset
      fileSize.value = payload.file_size
      loadError.value = null
    } catch {
      // Ignore malformed frames; the next incremental request recovers.
    }
  })

  source.addEventListener('end', () => {
    if (disposed || token !== requestToken) return
    finishStream()
  })

  source.onerror = () => {
    if (disposed || token !== requestToken) return
    source.close()
    eventSource = null
    if (mode.value !== 'ended') startPolling(token)
  }
}

async function bootstrap() {
  const token = ++requestToken
  disposed = false
  closeStream()
  lines.value = []
  partial.value = ''
  version.value += 1
  offset.value = 0
  fileSize.value = 0
  follow.value = true
  newLines.value = 0
  truncated.value = false
  loading.value = true
  loadError.value = null
  mode.value = 'loading'
  stableEofPolls = 0
  requestAnimationFrame(applyWindow)
  try {
    const chunk = await api.getLogs(props.taskId, 0, INITIAL_LIMIT)
    if (token !== requestToken || disposed) return
    appendText(chunk.content)
    offset.value = chunk.next_offset
    fileSize.value = chunk.file_size
    loading.value = false
    await scrollToBottom(true)
    if (chunk.eof && isTerminal(chunk.task_status)) {
      finishStream()
    } else if (isTerminal(chunk.task_status) && props.status !== chunk.task_status) {
      // Status in the chunk is authoritative; still drain any tail from SSE.
      startSse(token)
    } else {
      startSse(token)
    }
  } catch (err) {
    if (token !== requestToken || disposed) return
    loading.value = false
    mode.value = 'error'
    loadError.value = err instanceof Error ? err.message : '无法加载任务日志'
  }
}

function retry() {
  bootstrap()
}

async function copyLogs() {
  const text = lines.value.concat(partial.value ? [partial.value] : []).join('\n')
  if (!text) {
    ElMessage.info('当前还没有可复制的日志')
    return
  }
  const ok = await copyText(text)
  ElMessage({
    message: ok ? '已复制当前已加载的日志' : '复制失败，请使用下载按钮',
    type: ok ? 'success' : 'warning',
  })
}

function downloadLogs() {
  window.open(logDownloadUrl(props.taskId), '_blank', 'noopener')
}

function onSearchInput() {
  if (searchTimer) window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => {
    highlightKeyword.value = searchInput.value
  }, 120)
}

watch(
  () => props.taskId,
  () => bootstrap(),
)

watch(
  () => props.status,
  (status) => {
    if (!isTerminal(status)) return
    if (mode.value === 'sse' || mode.value === 'loading') {
      // The SSE endpoint emits an explicit end event after draining the file;
      // this is only a safety net if the stream never closed.
      window.setTimeout(() => {
        if (!disposed && (mode.value === 'sse' || mode.value === 'loading')) {
          startPolling(++requestToken)
        }
      }, 2500)
    }
  },
)

onMounted(() => {
  resizeObserver = new ResizeObserver(() => {
    applyWindow()
  })
  if (scroller.value) resizeObserver.observe(scroller.value)
  bootstrap()
})

onBeforeUnmount(() => {
  disposed = true
  closeStream()
  if (searchTimer) window.clearTimeout(searchTimer)
  resizeObserver?.disconnect()
})
</script>

<template>
  <section class="log-terminal">
    <header class="term-head">
      <div class="term-dots" aria-hidden="true"><i></i><i></i><i></i></div>
      <span class="term-title mono">litetrain@cpu:~/runs/{{ taskId }}</span>
      <div class="term-head-right">
        <span class="conn-badge" :class="connectionTone">
          <i></i>{{ connectionLabel }}
        </span>
        <span class="term-size mono">{{ formatBytes(fileSize) }}</span>
      </div>
    </header>

    <div class="term-toolbar">
      <div class="term-search">
        <el-input
          v-model="searchInput"
          size="small"
          :prefix-icon="Search"
          placeholder="搜索并高亮日志"
          clearable
          @input="onSearchInput"
        />
        <span v-if="highlightKeyword" class="match-count mono">{{ matches }} 处匹配</span>
      </div>
      <div class="term-actions">
        <button
          class="term-action"
          type="button"
          :title="follow ? '暂停自动滚动' : '继续自动滚动'"
          @click="toggleFollow"
        >
          <el-icon>
            <VideoPause v-if="follow" />
            <VideoPlay v-else />
          </el-icon>
          <span>{{ follow ? '暂停滚动' : '继续滚动' }}</span>
        </button>
        <button class="term-action" type="button" title="复制已加载日志" @click="copyLogs">
          <el-icon><CopyDocument /></el-icon>
          <span>复制</span>
        </button>
        <button class="term-action" type="button" title="下载完整日志" @click="downloadLogs">
          <el-icon><Download /></el-icon>
          <span>下载</span>
        </button>
      </div>
    </div>

    <div ref="scroller" class="term-scroll" @scroll="onScroll">
      <div class="term-space" :style="{ height: `${totalHeight}px` }">
        <div
          class="term-window"
          :style="{ transform: `translateY(${visibleStart * ROW_HEIGHT}px)` }"
        >
          <div v-for="index in visibleIndices" :key="index" class="term-row" :style="{ height: `${ROW_HEIGHT}px` }">
            <span class="line-number">{{ index + 1 }}</span>
            <span class="line-text" v-html="renderLine(getLine(index))"></span>
          </div>
        </div>
        <div v-if="lineCount === 0" class="term-empty">
          <template v-if="loading">
            <span class="term-spinner"></span>
            <span>正在读取任务日志…</span>
          </template>
          <template v-else-if="loadError">
            <span class="empty-error"><el-icon><WarningFilled /></el-icon>{{ loadError }}</span>
            <button type="button" class="retry-link" @click="retry">
              <el-icon><Refresh /></el-icon>重新连接
            </button>
          </template>
          <template v-else>
            <span class="empty-prompt">$</span>
            <span>等待训练进程输出日志…</span>
          </template>
        </div>
      </div>

      <div v-if="loadError && lineCount > 0" class="stream-error">
        <el-icon><WarningFilled /></el-icon>
        <span>{{ loadError }}</span>
        <button type="button" @click="retry">重试</button>
      </div>

      <button v-if="!follow && newLines > 0" class="jump-latest" type="button" @click="toggleFollow">
        <el-icon><ArrowDownBold /></el-icon>
        回到最新，{{ newLines }} 条
      </button>
    </div>

    <footer class="term-foot">
      <span class="mono">
        {{ lineCount.toLocaleString() }} 行
        <span v-if="truncated" class="truncated-note">· 仅保留最近 {{ MAX_LINES.toLocaleString() }} 行</span>
      </span>
      <span class="mono">偏移 {{ offset }} · {{ follow ? '跟随' : '已暂停' }}</span>
    </footer>
  </section>
</template>

<style scoped>
.log-terminal {
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--terminal);
  box-shadow: 0 18px 45px rgba(2, 12, 8, 0.16);
}

.term-head {
  display: flex;
  align-items: center;
  gap: 11px;
  min-height: 38px;
  padding: 0 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  background: rgba(255, 255, 255, 0.018);
}

.term-dots {
  display: flex;
  gap: 5px;
  flex: none;
}

.term-dots i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #325044;
}

.term-dots i:nth-child(1) {
  background: #dd6565;
}

.term-dots i:nth-child(2) {
  background: #d8a549;
}

.term-dots i:nth-child(3) {
  background: #42b988;
}

.term-title {
  overflow: hidden;
  color: #6d8f7e;
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.term-head-right {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-left: auto;
  flex: none;
}

.conn-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 6px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 5px;
  color: #6e8d7e;
  font-family: var(--mono);
  font-size: 7px;
  font-weight: 600;
  letter-spacing: 0.03em;
}

.conn-badge i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: currentColor;
}

.conn-badge.live {
  border-color: rgba(46, 199, 141, 0.25);
  color: #44d49d;
}

.conn-badge.live i {
  animation: term-blink 1.6s ease-in-out infinite;
}

.conn-badge.polling {
  color: #d9a84b;
}

.conn-badge.ended {
  color: #6d8f7e;
}

.conn-badge.error {
  border-color: rgba(224, 101, 101, 0.25);
  color: #e47676;
}

.term-size {
  color: #567969;
  font-size: 8px;
}

.term-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 11px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  background: rgba(4, 14, 10, 0.4);
}

.term-search {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
  max-width: 340px;
}

.term-search :deep(.el-input__wrapper) {
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.045);
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.07) inset;
}

.term-search :deep(.el-input__inner) {
  height: 24px;
  color: #b6ccbf;
  font-family: var(--mono);
  font-size: 9px;
}

.term-search :deep(.el-input__inner::placeholder) {
  color: #4f6c5e;
}

.term-search :deep(.el-input__prefix) {
  color: #5b806d;
}

.match-count {
  flex: none;
  color: #d6a84a;
  font-size: 8px;
  white-space: nowrap;
}

.term-actions {
  display: flex;
  align-items: center;
  gap: 3px;
  flex: none;
}

.term-action {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 25px;
  padding: 0 6px;
  border: 1px solid transparent;
  border-radius: 5px;
  background: transparent;
  color: #678575;
  cursor: pointer;
  font-family: var(--mono);
  font-size: 8px;
  transition: all 0.15s ease;
}

.term-action:hover {
  border-color: rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.04);
  color: #a9c6b7;
}

.term-action .el-icon {
  font-size: 11px;
}

.term-scroll {
  position: relative;
  height: clamp(370px, 52vh, 620px);
  overflow: auto;
  padding: 10px 0;
  overscroll-behavior: contain;
  scrollbar-color: #2a493b transparent;
}

.term-space {
  position: relative;
  min-width: 100%;
}

.term-window {
  position: absolute;
  top: 0;
  left: 0;
  min-width: 100%;
  width: max-content;
  will-change: transform;
}

.term-row {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr);
  align-items: center;
  min-width: 100%;
  width: max-content;
  line-height: 22px;
}

.term-row:hover {
  background: rgba(255, 255, 255, 0.025);
}

.line-number {
  position: sticky;
  left: 0;
  z-index: 1;
  height: 100%;
  padding-right: 10px;
  border-right: 1px solid rgba(255, 255, 255, 0.045);
  background: var(--terminal);
  color: #355747;
  font-family: var(--mono);
  font-size: 8px;
  line-height: 22px;
  text-align: right;
  user-select: none;
}

.line-text {
  padding: 0 12px;
  color: #afc4b7;
  font-family: var(--mono);
  font-size: 10.5px;
  line-height: 22px;
  white-space: pre;
}

.line-text :deep(.log-match) {
  padding: 1px 1px;
  border-radius: 2px;
  background: rgba(242, 184, 75, 0.22);
  color: #f5d38d;
}

.term-empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  min-height: 240px;
  color: #587565;
  font-family: var(--mono);
  font-size: 9.5px;
}

.term-empty .empty-prompt {
  color: #43d09a;
  font-size: 18px;
}

.term-spinner {
  width: 18px;
  height: 18px;
  border: 1.5px solid rgba(255, 255, 255, 0.09);
  border-top-color: #43d09a;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.empty-error {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #dd7d7d;
}

.retry-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: transparent;
  color: #d7b16a;
  cursor: pointer;
  font-family: var(--mono);
  font-size: 9px;
}

.stream-error {
  position: sticky;
  left: 14px;
  bottom: 10px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: calc(100% - 28px);
  padding: 6px 9px;
  border: 1px solid rgba(220, 110, 110, 0.24);
  border-radius: 7px;
  background: rgba(80, 20, 20, 0.75);
  color: #f09a9a;
  font-size: 8.5px;
}

.stream-error button {
  border: none;
  background: none;
  color: #f1c780;
  cursor: pointer;
  font-size: 8.5px;
  text-decoration: underline;
}

.jump-latest {
  position: sticky;
  left: 50%;
  bottom: 12px;
  display: flex;
  align-items: center;
  gap: 5px;
  width: fit-content;
  margin: 0 auto;
  padding: 6px 11px;
  border: 1px solid rgba(46, 199, 141, 0.25);
  border-radius: 999px;
  background: #102a1f;
  color: #6fe0b1;
  cursor: pointer;
  font-family: var(--mono);
  font-size: 8.5px;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.3);
}

.term-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 28px;
  padding: 5px 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  background: rgba(255, 255, 255, 0.018);
  color: #4e6e5f;
  font-size: 7.5px;
  flex-wrap: wrap;
}

.truncated-note {
  color: #b8914e;
}

@keyframes term-blink {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 640px) {
  .term-title {
    display: none;
  }

  .term-toolbar {
    flex-wrap: wrap;
  }

  .term-search {
    max-width: 100%;
    width: 100%;
  }

  .term-actions {
    width: 100%;
    justify-content: flex-end;
  }

  .term-scroll {
    height: 420px;
  }

  .line-text {
    font-size: 10px;
  }
}
</style>

<style scoped>
.term-title {
  font-size: 12px;
}

.conn-badge,
.term-size {
  font-size: 11px;
}

.term-action {
  font-size: 12px;
}

.match-count {
  font-size: 11px;
}

.term-foot {
  font-size: 12px;
}

.line-number {
  font-size: 10px;
}

.term-empty {
  font-size: 13px;
}

.line-text {
  font-size: 12px;
}

@media (max-width: 760px) {
  .term-title {
    max-width: 40%;
    font-size: 11px;
  }

  .line-text {
    font-size: 11.5px;
  }

  .term-action span {
    display: none;
  }
}
</style>
