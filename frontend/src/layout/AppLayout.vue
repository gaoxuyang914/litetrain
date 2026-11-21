<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Bell,
  BellFilled,
  DataAnalysis,
  Document,
  Expand,
  Fold,
  FolderOpened,
  MagicStick,
  Menu as MenuIcon,
  Moon,
  Share,
  Sunny,
  Tickets,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

import { api } from '@/api/client'
import { useNotifications } from '@/composables/useNotifications'
import { useTheme } from '@/composables/useTheme'
import type { ClusterOverview } from '@/types/task'

const route = useRoute()
const router = useRouter()
const { isDark, toggleTheme } = useTheme()
const { enabled: notificationsEnabled, setEnabled } = useNotifications()

const mobileNavOpen = ref(false)
const overview = ref<ClusterOverview | null>(null)
const clock = ref(new Date().toLocaleTimeString('zh-CN', { hour12: false }))
const SIDEBAR_KEY = 'litetrain.sidebar.collapsed'
const sidebarCollapsed = ref(localStorage.getItem(SIDEBAR_KEY) === '1')
let statusTimer: number | undefined
let clockTimer: number | undefined

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  localStorage.setItem(SIDEBAR_KEY, sidebarCollapsed.value ? '1' : '0')
}

function onShortcut(event: KeyboardEvent) {
  if (!(event.metaKey || event.ctrlKey) || event.altKey) return
  if (event.code !== 'Period') return
  const target = event.target as HTMLElement | null
  if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return
  event.preventDefault()
  toggleSidebar()
}

const navGroups = [
  {
    label: 'Workspace',
    items: [
      { name: 'overview', label: '集群总览', caption: 'CPU / 队列', icon: DataAnalysis, path: '/overview' },
      { name: 'tasks', label: '任务列表', caption: '提交与追踪', icon: Tickets, path: '/tasks' },
      { name: 'task-submit', label: '提交任务', caption: 'YOLO · CPU', icon: MagicStick, path: '/tasks/new' },
    ],
  },
  {
    label: 'Orchestration',
    items: [
      { name: 'pipelines', label: '训练流水线', caption: '预处理 → 评估', icon: Share, path: '/pipelines' },
      { name: 'assets', label: '数据与产物', caption: '资产 · Webhook', icon: FolderOpened, path: '/assets' },
    ],
  },
]

const title = computed(() => {
  if (route.name === 'task-detail') return '任务详情'
  return (route.meta.title as string) || '训练工作台'
})

const eyebrow = computed(() => ((route.meta.eyebrow as string) || 'Workbench').toUpperCase())

const onlineNodeCount = computed(
  () => overview.value?.nodes.filter((node) => node.status === 'online').length ?? 0,
)

const backendLabel = computed(() =>
  overview.value?.services.execution_backend === 'ray' ? 'RAY' : 'LOCAL',
)

const cpuLabel = computed(
  () => `${overview.value?.cpu.allocated ?? 0} / ${overview.value?.cpu.total ?? '—'}`,
)

function isActive(name: string): boolean {
  if (name === 'tasks') return route.name === 'tasks'
  return route.name === name
}

async function refreshStatus() {
  try {
    overview.value = await api.getOverview()
  } catch {
    // The page-level errors are more useful; keep the chrome quiet.
  }
}

async function toggleNotifications() {
  const result = await setEnabled(!notificationsEnabled.value)
  ElMessage({
    message: result.message,
    type: result.ok ? (notificationsEnabled.value ? 'success' : 'info') : 'warning',
  })
}

function navigate(name: string) {
  mobileNavOpen.value = false
  router.push({ name })
}

onMounted(() => {
  refreshStatus()
  statusTimer = window.setInterval(refreshStatus, 10_000)
  clockTimer = window.setInterval(() => {
    clock.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  }, 1000)
  window.addEventListener('keydown', onShortcut)
})

watch(
  () => route.fullPath,
  () => refreshStatus(),
)

onBeforeUnmount(() => {
  if (statusTimer) window.clearInterval(statusTimer)
  if (clockTimer) window.clearInterval(clockTimer)
  window.removeEventListener('keydown', onShortcut)
})
</script>

<template>
  <div class="app-shell" :class="{ 'sidebar-collapsed': sidebarCollapsed }">
    <aside class="sidebar" :class="{ open: mobileNavOpen }">
      <div class="brand" @click="navigate('overview')">
        <span class="brand-mark" aria-hidden="true">
          <i></i><i></i><i></i><i></i>
        </span>
        <span class="brand-copy">
          <strong>LiteTrain</strong>
          <small>CPU TRAINING LAB</small>
        </span>
        <el-tooltip
          :content="sidebarCollapsed ? '展开侧栏 (Ctrl + .)' : '收起侧栏 (Ctrl + .)'"
          placement="right"
        >
          <button
            class="sidebar-toggle"
            type="button"
            :aria-label="sidebarCollapsed ? '展开侧栏' : '收起侧栏'"
            @click.stop="toggleSidebar"
          >
            <el-icon>
              <Expand v-if="sidebarCollapsed" />
              <Fold v-else />
            </el-icon>
          </button>
        </el-tooltip>
      </div>

      <div class="nav-block" v-for="group in navGroups" :key="group.label">
        <p class="nav-label">{{ group.label }}</p>
        <nav class="nav-list">
          <button
            v-for="item in group.items"
            :key="item.name"
            type="button"
            class="nav-item"
            :class="{ active: isActive(item.name) }"
            :title="sidebarCollapsed ? `${item.label} · ${item.caption}` : undefined"
            :aria-label="item.label"
            @click="navigate(item.name)"
          >
            <span class="nav-icon">
              <el-icon><component :is="item.icon" /></el-icon>
            </span>
            <span class="nav-copy">
              <strong>{{ item.label }}</strong>
              <small>{{ item.caption }}</small>
            </span>
            <span v-if="item.name === 'tasks' && overview?.tasks.pending" class="nav-count">
              {{ overview.tasks.pending }}
            </span>
            <span v-else-if="item.name === 'tasks' && overview?.tasks.running" class="nav-count live">
              {{ overview.tasks.running }}
            </span>
            <span v-else class="nav-chevron">↗</span>
          </button>
        </nav>
      </div>

      <div class="sidebar-spacer"></div>

      <div class="system-card">
        <div class="system-aurora"></div>
        <div class="system-card-head">
          <span class="system-label">CLUSTER PULSE</span>
          <span class="system-live"><i></i>LIVE</span>
        </div>
        <div class="system-cpu">
          <div class="system-cpu-main">
            <strong>{{ overview?.cpu.allocated ?? 0 }}</strong>
            <span>/ {{ overview?.cpu.total ?? '—' }} CPU</span>
          </div>
          <span class="system-cpu-pct">
            {{ Math.round(overview?.cpu.utilization_percent ?? 0) }}%
          </span>
        </div>
        <div class="system-meter">
          <span :style="{ width: `${overview?.cpu.utilization_percent ?? 0}%` }"></span>
        </div>
        <div class="system-row">
          <span><i class="system-dot" :class="{ ok: overview?.services.ray_connected }"></i>Ray cluster</span>
          <span class="system-value" :class="{ ok: overview?.services.ray_connected }">
            {{ overview?.services.ray_connected ? 'ONLINE' : 'OFFLINE' }}
          </span>
        </div>
        <div class="system-row">
          <span><i class="system-dot" :class="{ ok: overview?.services.redis_connected }"></i>Redis bus</span>
          <span class="system-value" :class="{ ok: overview?.services.redis_connected }">
            {{ overview?.services.redis_connected ? 'ONLINE' : 'LOCAL' }}
          </span>
        </div>
        <div class="system-row">
          <span><i class="system-dot accent"></i>Nodes</span>
          <span class="system-value accent">{{ onlineNodeCount }} ONLINE</span>
        </div>
        <p class="system-foot">{{ cpuLabel }} cores allocated</p>
      </div>

      <div class="sidebar-bottom">
        <a class="sidebar-doc" href="/docs" target="_blank" rel="noreferrer">
          <el-icon><Document /></el-icon>
          <span>API 文档 / OpenAPI</span>
          <span class="sidebar-doc-arrow">↗</span>
        </a>
        <div class="sidebar-signature">
          <span class="mono">v2.2.1</span>
          <span>CPU only · local first</span>
        </div>
      </div>    </aside>

    <div v-if="mobileNavOpen" class="nav-backdrop" @click="mobileNavOpen = false"></div>

    <div class="workspace">
      <header class="topbar">
        <div class="topbar-left">
          <button class="icon-button mobile-only" type="button" aria-label="打开导航" @click="mobileNavOpen = true">
            <el-icon><MenuIcon /></el-icon>
          </button>
          <div class="crumb">
            <span class="crumb-root"><i></i>LiteTrain</span>
            <span class="crumb-slash">/</span>
            <span>{{ eyebrow }}</span>
            <span class="crumb-slash">/</span>
            <strong>{{ title }}</strong>
          </div>
        </div>

        <div class="topbar-right">
          <button class="cluster-chip" type="button" title="打开集群总览" @click="navigate('overview')">
            <span class="cluster-orb"><i></i></span>
            <span class="cluster-chip-copy">
              <small>CLUSTER</small>
              <strong>{{ cpuLabel }} CPU</strong>
            </span>
          </button>
          <span class="topbar-divider"></span>
          <div class="service-pills">
            <span class="service-pill" :class="{ online: overview?.services.redis_connected }">
              <i></i>
              Redis
            </span>
            <span class="service-pill" :class="{ online: overview?.services.ray_connected }">
              <i></i>
              Ray
            </span>
          </div>

          <el-tooltip
            :content="notificationsEnabled ? '关闭任务完成通知' : '开启任务完成通知'"
            placement="bottom"
          >
            <button
              class="icon-button"
              :class="{ highlighted: notificationsEnabled }"
              type="button"
              aria-label="通知"
              @click="toggleNotifications"
            >
              <el-icon>
                <BellFilled v-if="notificationsEnabled" />
                <Bell v-else />
              </el-icon>
            </button>
          </el-tooltip>

          <el-tooltip :content="isDark ? '切换到浅色模式' : '切换到深色模式'" placement="bottom">
            <button class="icon-button" type="button" aria-label="切换主题" @click="toggleTheme">
              <el-icon>
                <Sunny v-if="isDark" />
                <Moon v-else />
              </el-icon>
            </button>
          </el-tooltip>

          <span class="live-clock mono">
            <i></i>
            <span>{{ backendLabel }}</span>
            <em>{{ clock }}</em>
          </span>

          <span class="avatar" title="demo owner">LT</span>
        </div>
      </header>

      <main class="app-content">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </main>

      <footer class="app-footer">
        <span>LiteTrain 2.2.1 · CPU-only YOLO training control plane</span>
        <span class="mono">FastAPI · Redis · Ray · SQLite</span>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  min-height: 100vh;
}

.sidebar {
  position: sticky;
  top: 0;
  z-index: 30;
  display: flex;
  flex-direction: column;
  width: 246px;
  height: 100vh;
  flex: none;
  padding: 24px 16px 18px;
  overflow-y: auto;
  border-right: 1px solid rgba(255, 255, 255, 0.06);
  background:
    radial-gradient(380px 280px at 50% -10%, rgba(46, 199, 141, 0.12), transparent 70%),
    linear-gradient(180deg, var(--sidebar), #081611 100%);
  color: #dceae3;
}

.brand {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 0 8px 26px;
  cursor: pointer;
  user-select: none;
}

.brand-mark {
  display: grid;
  grid-template-columns: repeat(2, 9px);
  grid-template-rows: repeat(2, 9px);
  gap: 3px;
  width: 25px;
  height: 25px;
  padding: 2px;
  flex: none;
}

.brand-mark i {
  border-radius: 3px;
  background: #37d49a;
}

.brand-mark i:nth-child(2) {
  opacity: 0.46;
}

.brand-mark i:nth-child(3) {
  opacity: 0.46;
}

.brand-mark i:nth-child(4) {
  background: #f2b84b;
}

.brand-copy {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.brand-copy strong {
  color: #f5fbf7;
  font-size: 17px;
  font-weight: 720;
  letter-spacing: -0.035em;
}

.brand-copy small {
  color: #6e8e7f;
  font-family: var(--mono);
  font-size: 8px;
  font-weight: 700;
  letter-spacing: 0.15em;
}

.nav-block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.nav-label {
  padding: 0 10px;
  color: #557466;
  font-family: var(--mono);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.nav-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 11px;
  width: 100%;
  padding: 10px 10px;
  border: 1px solid transparent;
  border-radius: 12px;
  background: transparent;
  color: #91aa9e;
  cursor: pointer;
  text-align: left;
  transition: background 0.18s ease, color 0.18s ease, border-color 0.18s ease;
}

.nav-item:hover {
  background: rgba(255, 255, 255, 0.04);
  color: #e4f2eb;
}

.nav-item.active {
  border-color: rgba(46, 199, 141, 0.2);
  background: linear-gradient(90deg, rgba(46, 199, 141, 0.15), rgba(46, 199, 141, 0.04));
  color: #f1fbf6;
}

.nav-item.active::before {
  content: '';
  width: 3px;
  height: 23px;
  margin-left: -11px;
  border-radius: 0 3px 3px 0;
  background: #37d49a;
}

.nav-item .el-icon {
  flex: none;
  font-size: 17px;
}

.nav-copy {
  display: flex;
  flex-direction: column;
  gap: 1px;
  flex: 1;
  min-width: 0;
}

.nav-copy strong {
  font-size: 12.5px;
  font-weight: 600;
}

.nav-copy small {
  color: #5e7d6e;
  font-family: var(--mono);
  font-size: 9px;
}

.nav-item.active .nav-copy small {
  color: #7fa996;
}

.nav-count {
  display: grid;
  place-items: center;
  min-width: 20px;
  height: 19px;
  padding: 0 5px;
  border-radius: 6px;
  background: rgba(242, 184, 75, 0.13);
  color: #f2b84b;
  font-family: var(--mono);
  font-size: 9.5px;
  font-weight: 700;
}

.nav-count.live {
  background: rgba(46, 199, 141, 0.15);
  color: #46d59f;
}

.sidebar-spacer {
  flex: 1;
  min-height: 26px;
}

.system-card {
  padding: 12px;
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 13px;
  background: rgba(255, 255, 255, 0.035);
}

.system-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 11px;
}

.system-label {
  color: #5f7d6e;
  font-family: var(--mono);
  font-size: 8px;
  font-weight: 700;
  letter-spacing: 0.12em;
}

.system-live {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #43d09a;
  font-family: var(--mono);
  font-size: 8px;
  font-weight: 700;
}

.system-live i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 0 3px rgba(67, 208, 154, 0.1);
}

.system-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 5px 0;
  color: #7e9c8b;
  font-size: 10.5px;
}

.system-value {
  color: #a8c1b4;
  font-family: var(--mono);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.06em;
}

.system-value.ok {
  color: #3fd59c;
}

.system-value.accent {
  color: #f2b84b;
  text-transform: uppercase;
}

.system-meter {
  height: 3px;
  margin: 8px 0 6px;
  overflow: hidden;
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.08);
}

.system-meter span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #2ec78d, #f2b84b);
  transition: width 0.5s ease;
}

.system-foot {
  color: #537162;
  font-family: var(--mono);
  font-size: 8.5px;
}

.sidebar-doc {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 12px;
  padding: 8px 10px;
  border-radius: 8px;
  color: #5d7a6b;
  font-size: 10px;
  transition: color 0.18s ease, background 0.18s ease;
}

.sidebar-doc:hover {
  background: rgba(255, 255, 255, 0.04);
  color: #c3d7cc;
}

.workspace {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  height: 62px;
  padding: 0 28px;
  border-bottom: 1px solid var(--border);
  background: color-mix(in srgb, var(--bg) 84%, transparent);
  backdrop-filter: blur(18px);
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.crumb {
  display: flex;
  align-items: center;
  gap: 8px;
  overflow: hidden;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 10px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  white-space: nowrap;
}

.crumb-root {
  color: var(--accent);
  font-weight: 700;
}

.crumb-slash {
  color: var(--border-strong);
}

.crumb strong {
  overflow: hidden;
  color: var(--text-secondary);
  font-weight: 600;
  text-overflow: ellipsis;
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.service-pills {
  display: flex;
  gap: 6px;
  margin-right: 2px;
}

.service-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 7px;
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 9px;
  font-weight: 600;
}

.service-pill i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--text-muted);
}

.service-pill.online {
  border-color: var(--accent-line);
  color: var(--accent);
}

.service-pill.online i {
  background: var(--accent);
}

.icon-button {
  display: inline-grid;
  place-items: center;
  width: 34px;
  height: 34px;
  flex: none;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--panel);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.18s ease;
}

.icon-button:hover {
  border-color: var(--accent-line);
  background: var(--accent-soft);
  color: var(--accent);
}

.icon-button.highlighted {
  border-color: var(--accent-line);
  background: var(--accent-soft);
  color: var(--accent);
}

.icon-button .el-icon {
  font-size: 16px;
}

.live-clock {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: 4px;
  padding: 5px 8px;
  border-radius: 7px;
  background: var(--panel-soft);
  color: var(--text-muted);
  font-size: 8.5px;
  font-weight: 600;
  letter-spacing: 0.09em;
}

.live-clock i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.app-content {
  width: 100%;
  max-width: 1600px;
  margin: 0 auto;
  padding: 26px 28px 36px;
  flex: 1;
}

.app-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 28px 20px;
  color: var(--text-muted);
  font-size: 10px;
  flex-wrap: wrap;
}

.app-footer .mono {
  color: var(--text-muted);
  font-size: 9px;
  letter-spacing: 0.05em;
}

.mobile-only {
  display: none;
}

.nav-backdrop {
  display: none;
}

@media (max-width: 1000px) {
  .sidebar {
    position: fixed;
    inset: 0 auto 0 0;
    transform: translateX(-103%);
    transition: transform 0.24s cubic-bezier(0.22, 1, 0.36, 1);
    box-shadow: 20px 0 80px rgba(0, 0, 0, 0.35);
  }

  .sidebar.open {
    transform: translateX(0);
  }

  .nav-backdrop {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 25;
    background: rgba(4, 12, 9, 0.42);
    backdrop-filter: blur(3px);
  }

  .mobile-only {
    display: inline-grid;
  }

  .topbar {
    padding: 0 18px;
  }

  .app-content {
    padding: 20px 18px 30px;
  }

  .app-footer {
    padding: 14px 18px 20px;
  }

  .service-pills {
    display: none;
  }
}

@media (max-width: 640px) {
  .crumb {
    font-size: 9px;
  }

  .crumb-root,
  .crumb-slash:first-of-type {
    display: none;
  }

  .live-clock {
    display: none;
  }

  .topbar {
    height: 56px;
  }

  .app-footer {
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
  }
}
</style>

<style scoped>
/* LiteTrain 2.1 visual shell */
.app-shell {
  gap: 10px;
  min-height: 100vh;
  padding: 10px;
  background:
    radial-gradient(700px 420px at 8% -6%, rgba(45, 222, 160, 0.17), transparent 66%),
    radial-gradient(900px 560px at 94% 106%, rgba(112, 94, 242, 0.14), transparent 64%),
    linear-gradient(145deg, #03100a 0%, #071a12 48%, #030c08 100%);
}

.sidebar {
  top: 10px;
  width: 264px;
  height: calc(100vh - 20px);
  padding: 22px 14px 14px;
  border: 1px solid rgba(255, 255, 255, 0.075);
  border-radius: 22px;
  background:
    radial-gradient(420px 300px at 65% -10%, rgba(45, 222, 160, 0.14), transparent 70%),
    linear-gradient(180deg, rgba(10, 31, 22, 0.98), rgba(4, 15, 10, 0.99));
  box-shadow:
    0 30px 90px -46px rgba(0, 0, 0, 0.9),
    0 1px 0 rgba(255, 255, 255, 0.045) inset;
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, 0.14) transparent;
}

.brand {
  position: relative;
  gap: 11px;
  padding: 2px 8px 22px;
}

.brand::after {
  content: '';
  position: absolute;
  right: 8px;
  bottom: 10px;
  left: 8px;
  height: 1px;
  background: linear-gradient(90deg, rgba(255, 255, 255, 0.09), transparent);
}

.brand-mark {
  width: 31px;
  height: 31px;
  padding: 4px;
  grid-template-columns: repeat(2, 10px);
  grid-template-rows: repeat(2, 10px);
  gap: 2px;
  border: 1px solid rgba(94, 255, 193, 0.13);
  border-radius: 9px;
  background: rgba(60, 230, 168, 0.06);
  box-shadow: 0 0 30px -14px rgba(63, 233, 170, 0.85);
}

.brand-mark i {
  border-radius: 2px;
  background: linear-gradient(145deg, #8bffd2, #24b47c);
  box-shadow: 0 0 12px -6px #57f5b8;
}

.brand-mark i:nth-child(2),
.brand-mark i:nth-child(3) {
  opacity: 0.34;
  box-shadow: none;
}

.brand-mark i:nth-child(4) {
  background: linear-gradient(145deg, #ffe19a, #c48b27);
}

.brand-copy strong {
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 620;
  letter-spacing: -0.045em;
}

.brand-copy small {
  color: #5d7d6d;
  font-size: 7.5px;
  letter-spacing: 0.19em;
}

.brand-badge {
  margin-left: auto;
  align-self: flex-start;
  padding: 2px 5px;
  border: 1px solid rgba(198, 243, 106, 0.22);
  border-radius: 4px;
  background: rgba(198, 243, 106, 0.07);
  color: #c6f36a;
  font-family: var(--mono);
  font-size: 7px;
  font-weight: 700;
  letter-spacing: 0.06em;
}

.nav-block {
  gap: 7px;
  margin-bottom: 17px;
}

.nav-label {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 0 9px;
  color: #4d6c5d;
  font-size: 7.5px;
  font-weight: 650;
  letter-spacing: 0.21em;
}

.nav-label::after {
  content: '';
  height: 1px;
  flex: 1;
  background: linear-gradient(90deg, rgba(255, 255, 255, 0.075), transparent);
}

.nav-list {
  gap: 3px;
}

.nav-item {
  position: relative;
  gap: 10px;
  padding: 7px 9px;
  overflow: hidden;
  border-radius: 12px;
  transition:
    background 0.2s ease,
    color 0.2s ease,
    border-color 0.2s ease,
    transform 0.2s ease;
}

.nav-item:hover {
  background: rgba(255, 255, 255, 0.045);
  transform: translateX(2px);
}

.nav-item.active {
  border-color: rgba(78, 232, 174, 0.17);
  background:
    linear-gradient(100deg, rgba(62, 225, 165, 0.16), rgba(62, 225, 165, 0.035) 72%, transparent);
  box-shadow:
    0 1px 0 rgba(255, 255, 255, 0.055) inset,
    0 14px 32px -26px rgba(62, 255, 184, 0.65);
}

.nav-item.active::before {
  top: 11px;
  bottom: 11px;
  left: 0;
  width: 2px;
  height: auto;
  margin: 0;
  border-radius: 0 3px 3px 0;
  background: linear-gradient(180deg, #74ffd0, #22b67f);
  box-shadow: 0 0 16px rgba(74, 255, 193, 0.7);
}

.nav-icon {
  display: grid;
  width: 29px;
  height: 29px;
  flex: none;
  place-items: center;
  border: 1px solid rgba(255, 255, 255, 0.045);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.032);
  color: #668578;
  transition: all 0.2s ease;
}

.nav-icon .el-icon {
  font-size: 14px;
}

.nav-item.active .nav-icon {
  border-color: rgba(87, 238, 181, 0.17);
  background: rgba(63, 230, 169, 0.11);
  color: #86ffd4;
  box-shadow: 0 0 18px -10px rgba(63, 230, 169, 0.9);
}

.nav-copy strong {
  color: #aec7bb;
  font-size: 12px;
  font-weight: 570;
  letter-spacing: 0.005em;
}

.nav-copy small {
  color: #536f61;
  font-size: 8px;
  letter-spacing: 0.02em;
}

.nav-item.active .nav-copy strong {
  color: #f1fff8;
}

.nav-item.active .nav-copy small {
  color: #7fa996;
}

.nav-chevron {
  margin-left: auto;
  color: transparent;
  font-family: var(--mono);
  font-size: 10px;
  transition: color 0.2s ease;
}

.nav-item:hover .nav-chevron {
  color: #628577;
}

.nav-count {
  height: 18px;
  min-width: 18px;
  border-radius: 5px;
  font-size: 9px;
  box-shadow: 0 0 0 3px rgba(242, 184, 75, 0.04);
}

.nav-count.live {
  box-shadow: 0 0 0 3px rgba(70, 213, 159, 0.045);
}

.sidebar-spacer {
  min-height: 18px;
}

.system-card {
  position: relative;
  overflow: hidden;
  padding: 13px;
  border-color: rgba(255, 255, 255, 0.075);
  border-radius: 16px;
  background:
    linear-gradient(160deg, rgba(255, 255, 255, 0.052), rgba(255, 255, 255, 0.016));
  box-shadow:
    0 1px 0 rgba(255, 255, 255, 0.045) inset,
    0 20px 40px -32px rgba(0, 0, 0, 0.9);
}

.system-aurora {
  position: absolute;
  top: -52px;
  right: -40px;
  width: 150px;
  height: 150px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(61, 230, 168, 0.24), transparent 68%);
  filter: blur(8px);
  pointer-events: none;
}

.system-card-head {
  position: relative;
  z-index: 1;
  margin-bottom: 9px;
}

.system-label {
  color: #587768;
  font-size: 7.5px;
  letter-spacing: 0.16em;
}

.system-live {
  font-size: 7px;
}

.system-cpu {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.system-cpu-main {
  display: flex;
  align-items: baseline;
  gap: 4px;
}

.system-cpu-main strong {
  color: #f0fff8;
  font-family: var(--font-display);
  font-size: 23px;
  font-weight: 600;
  letter-spacing: -0.06em;
  line-height: 1;
}

.system-cpu-main span {
  color: #5e7c6e;
  font-family: var(--mono);
  font-size: 8px;
}

.system-cpu-pct {
  color: #5de4ad;
  font-family: var(--mono);
  font-size: 8px;
}

.system-meter {
  position: relative;
  z-index: 1;
  height: 3px;
  margin: 9px 0 10px;
  background: rgba(255, 255, 255, 0.075);
}

.system-meter span {
  background: linear-gradient(90deg, #20b77e, #8bffd2);
  box-shadow: 0 0 12px rgba(73, 255, 190, 0.42);
}

.system-row {
  position: relative;
  z-index: 1;
  padding: 3.5px 0;
  color: #748f81;
  font-size: 9.5px;
}

.system-row > span:first-child {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.system-dot {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #526c5f;
}

.system-dot.ok {
  background: #46dca4;
  box-shadow: 0 0 0 3px rgba(70, 220, 164, 0.08);
}

.system-dot.accent {
  background: #c6f36a;
}

.system-value {
  color: #88a395;
  font-size: 7.5px;
  letter-spacing: 0.09em;
}

.system-value.ok {
  color: #5de4ad;
}

.system-value.accent {
  color: #c6f36a;
}

.system-foot {
  position: relative;
  z-index: 1;
  margin-top: 8px;
  color: #4f6d5e;
  font-family: var(--mono);
  font-size: 7px;
}

.sidebar-bottom {
  margin-top: 11px;
}

.sidebar-doc {
  margin-top: 0;
  padding: 8px 9px;
  border: 1px solid transparent;
  border-radius: 9px;
  color: #5d7a6b;
  font-size: 9px;
}

.sidebar-doc span {
  flex: 1;
}

.sidebar-doc-arrow {
  color: #476456;
  font-family: var(--mono);
  font-size: 9px;
}

.sidebar-doc:hover {
  border-color: rgba(255, 255, 255, 0.055);
  background: rgba(255, 255, 255, 0.035);
}

.sidebar-signature {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 9px 9px 0;
  color: #3f5c4d;
  font-size: 7px;
  letter-spacing: 0.03em;
}

.sidebar-signature .mono {
  color: #5b7a6b;
  font-size: 8px;
}

.workspace {
  position: relative;
  min-width: 0;
  flex: 1;
  background:
    radial-gradient(900px 440px at 78% -8%, var(--accent-soft), transparent 62%),
    radial-gradient(760px 460px at 5% 105%, var(--violet-soft), transparent 64%),
    var(--bg);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 22px;
  box-shadow:
    0 40px 120px -64px rgba(0, 0, 0, 0.55),
    0 1px 0 rgba(255, 255, 255, 0.07) inset;
}

.topbar {
  top: 0;
  z-index: 20;
  height: 68px;
  padding: 0 30px;
  border-bottom: 1px solid color-mix(in srgb, var(--border) 84%, transparent);
  border-radius: 22px 22px 0 0;
  background: color-mix(in srgb, var(--bg) 76%, transparent);
  box-shadow: 0 16px 34px -34px rgba(9, 35, 24, 0.38);
  backdrop-filter: blur(24px) saturate(1.12);
}

.crumb {
  gap: 8px;
  padding: 6px 10px;
  border: 1px solid color-mix(in srgb, var(--border) 72%, transparent);
  border-radius: 999px;
  background: color-mix(in srgb, var(--panel) 54%, transparent);
  font-size: 8.5px;
  letter-spacing: 0.06em;
  box-shadow: 0 1px 0 var(--panel-highlight) inset;
}

.crumb-root {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.crumb-root i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.topbar-right {
  gap: 7px;
}

.cluster-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 38px;
  padding: 4px 10px 4px 6px;
  border: 1px solid var(--border);
  border-radius: 11px;
  background: color-mix(in srgb, var(--panel) 72%, transparent);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.2s ease;
  box-shadow: 0 1px 0 var(--panel-highlight) inset;
}

.cluster-chip:hover {
  border-color: var(--accent-line);
  background: var(--panel);
  transform: translateY(-1px);
}

.cluster-orb {
  position: relative;
  display: grid;
  width: 28px;
  height: 28px;
  flex: none;
  place-items: center;
  border-radius: 50%;
  background:
    radial-gradient(circle at 35% 28%, rgba(255, 255, 255, 0.26), transparent 44%),
    conic-gradient(var(--accent) 0 68%, color-mix(in srgb, var(--border) 90%, transparent) 68% 100%);
  box-shadow: 0 0 18px -8px var(--accent);
}

.cluster-orb::after {
  content: '';
  position: absolute;
  inset: 4px;
  border-radius: 50%;
  background: var(--panel);
}

.cluster-orb i {
  position: relative;
  z-index: 1;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 7px var(--accent);
}

.cluster-chip-copy {
  display: flex;
  flex-direction: column;
  gap: 0;
  text-align: left;
}

.cluster-chip-copy small {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 6.5px;
  font-weight: 700;
  letter-spacing: 0.12em;
}

.cluster-chip-copy strong {
  color: var(--text-secondary);
  font-family: var(--mono);
  font-size: 8.5px;
  font-weight: 600;
}

.topbar-divider {
  width: 1px;
  height: 20px;
  margin: 0 2px;
  background: var(--border);
}

.service-pill {
  height: 24px;
  padding: 0 8px;
  border-color: color-mix(in srgb, var(--border) 82%, transparent);
  background: color-mix(in srgb, var(--panel) 54%, transparent);
  font-size: 8px;
}

.service-pill.online {
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent-soft) 50%, transparent);
}

.icon-button {
  width: 36px;
  height: 36px;
  border-color: color-mix(in srgb, var(--border) 82%, transparent);
  border-radius: 11px;
  background: color-mix(in srgb, var(--panel) 68%, transparent);
  box-shadow: 0 1px 0 var(--panel-highlight) inset;
}

.icon-button:hover {
  transform: translateY(-1px);
  box-shadow:
    0 8px 20px -14px color-mix(in srgb, var(--accent) 70%, transparent),
    0 1px 0 var(--panel-highlight) inset;
}

.live-clock {
  gap: 6px;
  height: 36px;
  margin-left: 2px;
  padding: 0 9px;
  border: 1px solid color-mix(in srgb, var(--border) 80%, transparent);
  border-radius: 10px;
  background: color-mix(in srgb, var(--panel) 60%, transparent);
  font-size: 8px;
  letter-spacing: 0.06em;
}

.live-clock em {
  padding-left: 7px;
  border-left: 1px solid var(--border);
  color: var(--text-secondary);
  font-style: normal;
  font-variant-numeric: tabular-nums;
}

.avatar {
  display: grid;
  width: 36px;
  height: 36px;
  margin-left: 2px;
  place-items: center;
  border: 1px solid color-mix(in srgb, var(--accent) 28%, var(--border));
  border-radius: 50%;
  background:
    radial-gradient(circle at 30% 22%, rgba(255, 255, 255, 0.3), transparent 40%),
    linear-gradient(145deg, var(--accent), var(--accent-strong));
  color: #fff;
  font-family: var(--mono);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.04em;
  cursor: default;
  box-shadow:
    0 0 0 3px var(--panel),
    0 0 0 4px var(--border);
}

.app-content {
  position: relative;
  z-index: 1;
  max-width: 1660px;
  padding: 29px 32px 44px;
}

.app-footer {
  position: relative;
  z-index: 1;
  margin: 0 32px;
  padding: 15px 0 18px;
  border-top: 1px solid color-mix(in srgb, var(--border) 78%, transparent);
  color: var(--text-muted);
  font-size: 8.5px;
  letter-spacing: 0.02em;
}

.app-footer .mono {
  font-size: 8px;
}

@media (max-width: 1320px) {
  .cluster-chip {
    display: none;
  }

  .topbar-divider {
    display: none;
  }
}

@media (max-width: 1000px) {
  .app-shell {
    gap: 0;
    padding: 0;
    background: var(--bg);
  }

  .sidebar {
    top: 0;
    height: 100dvh;
    border: 0;
    border-radius: 0;
  }

  .workspace {
    border: 0;
    border-radius: 0;
    box-shadow: none;
  }

  .topbar {
    top: 0;
    padding: 0 18px;
    border-radius: 0;
  }

  .app-content {
    padding: 20px 18px 30px;
  }

  .app-footer {
    margin: 0 18px;
    padding: 14px 0 18px;
  }

  .avatar {
    display: none;
  }
}

@media (max-width: 640px) {
  .topbar {
    height: 58px;
  }

  .live-clock em {
    display: none;
  }

  .live-clock {
    width: 33px;
    padding: 0;
    justify-content: center;
  }

  .live-clock > span {
    display: none;
  }

  .topbar-divider {
    display: none;
  }
}
</style>

<style scoped>
/* Compact, collapsible navigation rail */
.sidebar {
  width: 228px;
  padding: 20px 11px 14px;
  transition:
    width 0.24s var(--ease-out),
    padding 0.24s var(--ease-out);
}

.brand {
  gap: 8px;
  padding: 2px 5px 20px;
}

.brand-copy strong {
  font-size: 17.5px;
}

.brand-badge {
  display: none;
}

.sidebar-toggle {
  display: grid;
  width: 28px;
  height: 28px;
  margin-left: auto;
  flex: none;
  place-items: center;
  border: 1px solid rgba(102, 233, 182, 0.2);
  border-radius: 8px;
  background: rgba(63, 230, 169, 0.07);
  color: #a4cdbb;
  cursor: pointer;
  transition:
    color 0.2s ease,
    background 0.2s ease,
    border-color 0.2s ease,
    transform 0.18s ease;
}

.sidebar-toggle .el-icon {
  font-size: 14px;
}

.sidebar-toggle:hover {
  border-color: rgba(87, 238, 181, 0.38);
  background: rgba(63, 230, 169, 0.15);
  color: #c1ffe8;
  transform: translateX(1px);
}

.nav-item {
  gap: 8px;
  padding: 7px 8px;
}

.nav-icon {
  width: 28px;
  height: 28px;
}

.nav-copy strong {
  font-size: 11.5px;
}

.nav-copy small {
  font-size: 7.5px;
}

.system-card {
  padding: 12px 11px;
}

@media (min-width: 1001px) {
  .app-shell.sidebar-collapsed .sidebar {
    width: 82px;
    padding: 16px 8px 12px;
  }

  .app-shell.sidebar-collapsed .brand {
    justify-content: center;
    gap: 5px;
    padding: 2px 0 18px;
  }

  .app-shell.sidebar-collapsed .brand-copy,
  .app-shell.sidebar-collapsed .brand-badge {
    display: none;
  }

  .app-shell.sidebar-collapsed .brand-mark {
    width: 29px;
    height: 29px;
    padding: 4px;
  }

  .app-shell.sidebar-collapsed .sidebar-toggle {
    width: 26px;
    height: 26px;
    margin-left: 0;
  }

  .app-shell.sidebar-collapsed .brand::after {
    right: 4px;
    left: 4px;
  }

  .app-shell.sidebar-collapsed .nav-block {
    margin-bottom: 10px;
  }

  .app-shell.sidebar-collapsed .nav-block + .nav-block {
    padding-top: 10px;
    border-top: 1px solid rgba(255, 255, 255, 0.06);
  }

  .app-shell.sidebar-collapsed .nav-label,
  .app-shell.sidebar-collapsed .nav-copy,
  .app-shell.sidebar-collapsed .nav-chevron {
    display: none;
  }

  .app-shell.sidebar-collapsed .nav-item {
    justify-content: center;
    gap: 0;
    padding: 6px;
  }

  .app-shell.sidebar-collapsed .nav-icon {
    width: 32px;
    height: 32px;
  }

  .app-shell.sidebar-collapsed .nav-count {
    position: absolute;
    top: 2px;
    right: 2px;
    min-width: 13px;
    height: 13px;
    padding: 0 3px;
    border-radius: 4px;
    font-size: 7px;
    box-shadow: none;
  }

  .app-shell.sidebar-collapsed .nav-count.live {
    box-shadow: none;
  }

  .app-shell.sidebar-collapsed .sidebar-spacer {
    min-height: 8px;
  }

  .app-shell.sidebar-collapsed .system-card {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 10px 6px;
    border-radius: 13px;
  }

  .app-shell.sidebar-collapsed .system-card-head,
  .app-shell.sidebar-collapsed .system-row,
  .app-shell.sidebar-collapsed .system-foot {
    display: none;
  }

  .app-shell.sidebar-collapsed .system-cpu {
    justify-content: center;
    width: 100%;
  }

  .app-shell.sidebar-collapsed .system-cpu-main {
    align-items: center;
    flex-direction: column;
    gap: 0;
  }

  .app-shell.sidebar-collapsed .system-cpu-main strong {
    font-size: 15px;
  }

  .app-shell.sidebar-collapsed .system-cpu-main span,
  .app-shell.sidebar-collapsed .system-cpu-pct {
    display: none;
  }

  .app-shell.sidebar-collapsed .system-meter {
    width: 100%;
    height: 2px;
    margin: 6px 0 0;
  }

  .app-shell.sidebar-collapsed .system-aurora {
    top: auto;
    right: 50%;
    bottom: -70px;
    transform: translateX(50%);
  }

  .app-shell.sidebar-collapsed .sidebar-bottom {
    margin-top: 8px;
  }

  .app-shell.sidebar-collapsed .sidebar-doc {
    justify-content: center;
    padding: 8px 6px;
  }

  .app-shell.sidebar-collapsed .sidebar-doc span,
  .app-shell.sidebar-collapsed .sidebar-doc-arrow,
  .app-shell.sidebar-collapsed .sidebar-signature span:last-child {
    display: none;
  }

  .app-shell.sidebar-collapsed .sidebar-signature {
    justify-content: center;
    padding: 8px 0 0;
  }
}

@media (max-width: 1000px) {
  .sidebar {
    width: 250px;
    padding: 22px 14px 14px;
  }

  .sidebar-toggle {
    display: none;
  }
}
</style>
