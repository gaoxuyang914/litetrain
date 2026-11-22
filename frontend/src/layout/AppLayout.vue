<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Bell,
  BellFilled,
  DataAnalysis,
  Expand,
  Fold,
  FolderOpened,
  MagicStick,
  Menu as MenuIcon,
  Moon,
  Search,
  Share,
  Sunny,
  SwitchButton,
  Tickets,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

import { api } from '@/api/client'
import { useAuth } from '@/composables/useAuth'
import { useNotifications } from '@/composables/useNotifications'
import { useTheme } from '@/composables/useTheme'
import type { ClusterOverview } from '@/types/task'

const route = useRoute()
const router = useRouter()
const auth = useAuth()
const { isDark, toggleTheme } = useTheme()
const { enabled: notificationsEnabled, setEnabled } = useNotifications()

const mobileNavOpen = ref(false)
const navSearch = ref('')
const overview = ref<ClusterOverview | null>(null)
const SIDEBAR_KEY = 'litetrain.sidebar.collapsed'
const sidebarCollapsed = ref(localStorage.getItem(SIDEBAR_KEY) === '1')
let statusTimer: number | undefined

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

const navGroups: Array<{
  label: string
  items: Array<{ name: string; label: string; icon: typeof DataAnalysis; path: string; permission?: string }>
}> = [
  {
    label: '工作台',
    items: [
      { name: 'overview', label: '集群总览', icon: DataAnalysis, path: '/overview' },
      { name: 'tasks', label: '任务列表', icon: Tickets, path: '/tasks' },
      { name: 'task-submit', label: '提交任务', icon: MagicStick, path: '/tasks/new', permission: 'platform.submit' },
    ],
  },
  {
    label: '编排与资产',
    items: [
      { name: 'pipelines', label: '训练流水线', icon: Share, path: '/pipelines' },
      { name: 'assets', label: '数据与产物', icon: FolderOpened, path: '/assets' },
    ],
  },
]

const title = computed(() => {
  if (route.name === 'task-detail') return '任务详情'
  return (route.meta.title as string) || '训练工作台'
})

const eyebrow = computed(() => (route.meta.eyebrow as string) || '训练工作台')

const onlineNodeCount = computed(
  () => overview.value?.nodes.filter((node) => node.status === 'online').length ?? 0,
)

const cpuLabel = computed(
  () => `${overview.value?.cpu.allocated ?? 0} / ${overview.value?.cpu.total ?? '—'}`,
)
const userName = computed(() => auth.user.value?.username ?? '成员')
const userInitial = computed(() => userName.value.slice(0, 1).toUpperCase())

async function logout() {
  await auth.logout()
  await router.replace({ name: 'login' })
}

function isActive(name: string): boolean {
  if (name === 'tasks') return route.name === 'tasks'
  return route.name === name
}

function searchTasks() {
  const query = navSearch.value.trim()
  if (!query) return
  router.push({ name: 'tasks', query: { q: query } })
  navSearch.value = ''
  mobileNavOpen.value = false
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
  window.addEventListener('keydown', onShortcut)
})

watch(
  () => route.fullPath,
  () => refreshStatus(),
)

watch(
  () => sidebarCollapsed.value,
  (collapsed) => {
    document.documentElement.classList.toggle('sidebar-expanded', !collapsed)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  if (statusTimer) window.clearInterval(statusTimer)
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
          <small>CPU 训练平台</small>
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

      <label class="nav-search">
        <el-icon><Search /></el-icon>
        <input
          v-model="navSearch"
          type="search"
          placeholder="搜索任务…"
          aria-label="搜索任务"
          @keyup.enter="searchTasks"
        />
      </label>

      <div class="nav-block" v-for="group in navGroups" :key="group.label">
        <p class="nav-label">{{ group.label }}</p>
        <nav class="nav-list">
          <button
            v-for="item in group.items"
            v-show="!item.permission || auth.hasPermission(item.permission)"
            :key="item.name"
            type="button"
            class="nav-item"
            :class="{ active: isActive(item.name) }"
            :title="sidebarCollapsed ? item.label : undefined"
            :aria-label="item.label"
            @click="navigate(item.name)"
          >
            <span class="nav-icon">
              <el-icon><component :is="item.icon" /></el-icon>
            </span>
            <span class="nav-copy">
              <strong>{{ item.label }}</strong>
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
          <span class="system-label">集群状态</span>
          <span class="system-live"><i></i>实时</span>
        </div>
        <div class="system-cpu">
          <div class="system-cpu-main">
            <strong>{{ overview?.cpu.allocated ?? 0 }}</strong>
            <span>/ {{ overview?.cpu.total ?? '—' }} 核</span>
          </div>
          <span class="system-cpu-pct">
            {{ Math.round(overview?.cpu.utilization_percent ?? 0) }}%
          </span>
        </div>
        <div class="system-meter">
          <span :style="{ width: `${overview?.cpu.utilization_percent ?? 0}%` }"></span>
        </div>
        <div class="system-row">
          <span><i class="system-dot" :class="{ ok: overview?.services.ray_connected }"></i>Ray 集群</span>
          <span class="system-value" :class="{ ok: overview?.services.ray_connected }">
            {{ overview?.services.ray_connected ? '在线' : '离线' }}
          </span>
        </div>
        <div class="system-row">
          <span><i class="system-dot" :class="{ ok: overview?.services.redis_connected }"></i>Redis 消息</span>
          <span class="system-value" :class="{ ok: overview?.services.redis_connected }">
            {{ overview?.services.redis_connected ? '在线' : '本地' }}
          </span>
        </div>
        <div class="system-row">
          <span><i class="system-dot accent"></i>节点</span>
          <span class="system-value accent">{{ onlineNodeCount }} 在线</span>
        </div>
        <p class="system-foot">{{ cpuLabel }} 核已分配</p>
        <button class="system-action" type="button" @click="navigate('task-submit')">
          <el-icon><MagicStick /></el-icon>
          提交训练
        </button>
      </div>
    </aside>

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
              <small>集群</small>
              <strong>{{ cpuLabel }} 核</strong>
            </span>
          </button>

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

          <span class="topbar-user">{{ userName }}</span>
          <el-tooltip content="退出登录" placement="bottom">
            <button class="icon-button" type="button" aria-label="退出登录" @click="logout">
              <el-icon><SwitchButton /></el-icon>
            </button>
          </el-tooltip>
          <span class="avatar" :title="userName">{{ userInitial }}</span>
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
        <span>LiteTrain 2.5 · CPU-only YOLO training control plane</span>
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

<style scoped>
/* Operations ledger: flat dark rail, paper workspace, no glass frame. */
.app-shell {
  gap: 0;
  min-height: 100vh;
  padding: 0;
  background: var(--bg);
}

.sidebar {
  top: 0;
  width: 228px;
  height: 100vh;
  padding: 18px 12px 14px;
  border-right: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 0;
  background: #0a1711;
  box-shadow: none;
}

.brand {
  gap: 9px;
  padding: 3px 5px 20px;
}

.brand-copy strong {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.03em;
}

.brand-copy small {
  color: #789889;
  font-size: 12px;
  letter-spacing: 0;
}

.brand::after {
  background: rgba(255, 255, 255, 0.08);
}

.nav-label {
  padding: 0 8px;
  color: #6f897b;
  font-family: var(--font);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
}

.nav-label::after {
  background: rgba(255, 255, 255, 0.06);
}

.nav-item {
  gap: 10px;
  padding: 8px;
  border: 1px solid transparent;
  border-radius: 6px;
  color: #a3b8ac;
  transition: background-color 0.16s ease, color 0.16s ease;
}

.nav-item:hover {
  background: rgba(255, 255, 255, 0.045);
  color: #e2eee7;
  transform: none;
}

.nav-item.active {
  border-color: rgba(75, 195, 148, 0.16);
  background: rgba(75, 195, 148, 0.08);
  box-shadow: none;
  color: #f0faf4;
}

.nav-item.active::before {
  top: 8px;
  bottom: 8px;
  left: 0;
  width: 2px;
  background: #4bc394;
}

.nav-icon {
  width: 30px;
  height: 30px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 6px;
  background: transparent;
}

.nav-icon .el-icon {
  font-size: 16px;
}

.nav-item.active .nav-icon {
  border-color: rgba(75, 195, 148, 0.18);
  background: rgba(75, 195, 148, 0.08);
  color: #81dcb7;
  box-shadow: none;
}

.nav-copy strong {
  color: #b9cbbf;
  font-size: 14px;
  font-weight: 500;
  letter-spacing: 0;
}

.nav-copy small {
  color: #6f897b;
  font-size: 12px;
  letter-spacing: 0;
}

.nav-item.active .nav-copy strong {
  color: #f0faf4;
}

.nav-item.active .nav-copy small {
  color: #8fb5a3;
}

.nav-chevron {
  color: transparent;
  font-size: 12px;
}

.nav-item:hover .nav-chevron {
  color: #789889;
}

.nav-count {
  height: 18px;
  min-width: 18px;
  border-radius: 4px;
  font-size: 10px;
}

.system-card {
  overflow: hidden;
  padding: 12px;
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 8px;
  background: transparent;
}

.system-aurora {
  display: none;
}

.system-label {
  color: #8ba695;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
}

.system-live {
  color: #74d8ae;
  font-size: 12px;
  letter-spacing: 0;
}

.system-cpu-main strong {
  font-size: 26px;
}

.system-cpu-main span,
.system-cpu-pct {
  font-size: 12px;
}

.system-row {
  color: #89a191;
  font-size: 12px;
}

.system-value {
  font-size: 12px;
  letter-spacing: 0;
}

.system-foot {
  color: #627e6e;
  font-size: 12px;
  letter-spacing: 0;
}

.sidebar-doc {
  padding: 9px 7px;
  color: #8ba695;
  font-size: 13px;
}

.sidebar-doc-arrow {
  font-size: 12px;
}

.sidebar-doc:hover {
  background: rgba(255, 255, 255, 0.04);
}

.sidebar-bottom {
  margin-top: 10px;
}

.sidebar-signature {
  padding: 10px 7px 0;
  color: #5f7a6b;
  font-size: 12px;
  letter-spacing: 0;
}

.sidebar-signature .mono {
  color: #7e998a;
  font-size: 12px;
}

.workspace {
  min-width: 0;
  background: var(--bg);
  border: 0;
  border-radius: 0;
  box-shadow: none;
}

.nav-backdrop {
  background: rgba(4, 12, 9, 0.5);
  backdrop-filter: none;
}

.icon-button:hover {
  transform: none;
  box-shadow: none;
  border-color: var(--border-strong);
  background: var(--panel-soft);
}

.topbar {
  height: 64px;
  padding: 0 28px;
  border-bottom: 1px solid var(--border);
  border-radius: 0;
  background: var(--panel);
  box-shadow: none;
  backdrop-filter: none;
}

.crumb {
  gap: 8px;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--text-muted);
  font-size: 13px;
  letter-spacing: 0;
  text-transform: none;
  box-shadow: none;
}

.crumb-root {
  color: var(--text-secondary);
  font-weight: 500;
}

.crumb-root i {
  width: 6px;
  height: 6px;
  box-shadow: none;
}

.crumb strong {
  color: var(--text);
  font-weight: 600;
}

.cluster-chip {
  height: 36px;
  padding: 3px 9px 3px 5px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--panel);
  box-shadow: none;
}

.cluster-chip:hover {
  border-color: var(--border-strong);
  background: var(--panel-soft);
  transform: none;
}

.cluster-chip-copy small {
  color: var(--text-muted);
  font-size: 12px;
  letter-spacing: 0;
}

.cluster-chip-copy strong {
  color: var(--text);
  font-size: 12px;
  letter-spacing: 0;
}

.topbar-right {
  gap: 8px;
}

.service-pill {
  font-size: 12px;
}

.icon-button {
  width: 36px;
  height: 36px;
  border-radius: 6px;
}

.live-clock {
  font-size: 12px;
}

.avatar {
  width: 34px;
  height: 34px;
  font-size: 12px;
}

.app-content {
  max-width: 1500px;
  padding: 28px 32px 36px;
}

.app-footer {
  margin: 0 32px;
  padding: 14px 0 18px;
  font-size: 12px;
}

.app-footer .mono {
  font-size: 12px;
  letter-spacing: 0;
}

@media (max-width: 1000px) {
  .sidebar {
    width: 272px;
    padding: 18px 12px 14px;
    border-radius: 0 10px 10px 0;
  }

  .topbar {
    padding: 0 20px;
  }

  .sidebar-toggle {
    display: none;
  }

  .app-content {
    padding: 24px 20px 32px;
  }

  .app-footer {
    margin: 0 20px;
    padding-bottom: 16px;
  }
}

@media (max-width: 560px) {
  .app-content {
    padding: 20px 16px 28px;
  }

  .app-footer {
    margin: 0 16px;
    font-size: 12px;
  }

  .crumb > *:not(strong) {
    display: none;
  }

  .crumb {
    gap: 0;
    min-width: 0;
  }

  .crumb strong {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}
</style>

<style scoped>
/* Ultralytics platform-inspired light shell */
.sidebar {
  width: 228px;
  padding: 18px 12px 14px;
  border-right: 1px solid var(--sidebar-line);
  background: var(--sidebar);
  color: var(--text);
}

.brand {
  gap: 9px;
  padding: 4px 6px 22px;
}

.brand-mark i {
  background: var(--accent);
  border-radius: 2px;
}

.brand-mark i:nth-child(2),
.brand-mark i:nth-child(3) {
  background: color-mix(in srgb, var(--accent) 44%, white);
}

.brand-mark i:nth-child(4) {
  background: #06b6d4;
}

html.dark .brand-mark i:nth-child(4) {
  background: #22d3ee;
}

.brand-copy strong {
  color: var(--text);
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.03em;
}

.brand-copy small {
  color: var(--text-muted);
  font-family: var(--font);
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0;
}

.brand::after {
  background: var(--sidebar-line);
}

.sidebar-toggle {
  border-color: var(--border);
  background: var(--panel);
  color: var(--text-muted);
}

.sidebar-toggle:hover {
  border-color: var(--accent-line);
  background: var(--accent-soft);
  color: var(--accent);
}

.nav-label {
  padding: 0 8px;
  color: var(--text-muted);
  font-family: var(--font);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.02em;
  text-transform: none;
}

.nav-label::after {
  background: var(--sidebar-line);
}

.nav-item {
  gap: 9px;
  padding: 8px;
  border: 1px solid transparent;
  border-radius: 8px;
  color: var(--text-secondary);
}

.nav-item:hover {
  background: var(--panel-soft);
  color: var(--text);
  transform: none;
}

.nav-item.active {
  border-color: transparent;
  background: var(--accent-soft);
  box-shadow: none;
  color: var(--accent);
}

.nav-item.active::before {
  top: 9px;
  bottom: 9px;
  left: 0;
  width: 2px;
  border-radius: 0 2px 2px 0;
  background: var(--accent);
}

.nav-icon {
  width: 28px;
  height: 28px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--text-muted);
}

.nav-item:hover .nav-icon {
  color: var(--text-secondary);
}

.nav-item.active .nav-icon {
  border-color: transparent;
  background: color-mix(in srgb, var(--accent) 9%, transparent);
  color: var(--accent);
  box-shadow: none;
}

.nav-icon .el-icon {
  font-size: 16px;
}

.nav-copy strong {
  color: var(--text-secondary);
  font-size: 14px;
  font-weight: 500;
}

.nav-copy small {
  color: var(--text-muted);
  font-size: 11px;
}

.nav-item.active .nav-copy strong {
  color: var(--accent);
  font-weight: 600;
}

.nav-item.active .nav-copy small {
  color: var(--accent);
  opacity: 0.7;
}

.nav-chevron {
  color: transparent;
}

.nav-item:hover .nav-chevron {
  color: var(--text-muted);
}

.nav-count {
  height: 18px;
  min-width: 18px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 10px;
}

.nav-count.live {
  background: var(--success-soft);
  color: var(--success);
}

.system-card {
  overflow: hidden;
  padding: 13px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--panel);
  box-shadow: none;
}

.system-aurora {
  display: none;
}

.system-label {
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.01em;
}

.system-live {
  color: var(--success);
  font-size: 11px;
  letter-spacing: 0;
}

.system-cpu-main strong {
  color: var(--text);
  font-family: var(--font-display);
  font-size: 26px;
  font-weight: 600;
}

.system-cpu-main span,
.system-cpu-pct {
  color: var(--text-muted);
  font-size: 12px;
}

.system-meter {
  background: var(--panel-soft);
}

.system-meter span {
  background: var(--accent);
  box-shadow: none;
}

.system-row {
  color: var(--text-muted);
  font-size: 12px;
}

.system-value {
  color: var(--text-muted);
  font-size: 11px;
  letter-spacing: 0;
}

.system-value.ok {
  color: var(--success);
}

.system-value.accent {
  color: var(--accent);
}

.system-foot {
  color: var(--text-muted);
  font-size: 11px;
  letter-spacing: 0;
}

.system-action {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  height: 32px;
  margin-top: 11px;
  border: 1px solid #111111;
  border-radius: 7px;
  background: #111111;
  color: #ffffff;
  font: inherit;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
}

.system-action:hover {
  background: #27272a;
}

.sidebar-doc {
  padding: 9px 7px;
  color: var(--text-muted);
  font-size: 13px;
}

.sidebar-doc:hover {
  background: var(--panel-soft);
  color: var(--text);
}

.sidebar-doc-arrow {
  color: var(--text-muted);
  font-size: 12px;
}

.sidebar-signature {
  padding: 10px 7px 0;
  color: var(--text-muted);
  font-size: 11px;
}

.sidebar-signature .mono {
  color: var(--text-muted);
  font-size: 11px;
}

.workspace {
  background: var(--bg);
}

.topbar {
  height: 64px;
  padding: 0 28px;
  border-bottom: 1px solid var(--border);
  border-radius: 0;
  background: var(--panel);
  box-shadow: none;
  backdrop-filter: none;
}

.crumb {
  color: var(--text-muted);
  font-size: 13px;
}

.crumb-root i {
  background: var(--accent);
  box-shadow: none;
}

.crumb strong {
  color: var(--text);
}

.cluster-chip {
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--panel);
  box-shadow: none;
}

.cluster-chip:hover {
  border-color: var(--border-strong);
  background: var(--panel-soft);
  transform: none;
}

.cluster-chip-copy small {
  color: var(--text-muted);
  font-size: 10px;
}

.cluster-chip-copy strong {
  color: var(--text);
  font-size: 12px;
}

.service-pill {
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--panel);
  color: var(--text-muted);
  font-size: 12px;
}

.service-pill.online {
  color: var(--text-secondary);
}

.icon-button {
  width: 36px;
  height: 36px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--panel);
  color: var(--text-secondary);
}

.icon-button:hover {
  border-color: var(--border-strong);
  background: var(--panel-soft);
  transform: none;
  box-shadow: none;
}

.live-clock {
  color: var(--text-muted);
  font-size: 12px;
}

.avatar {
  background: var(--accent);
  color: #fff;
  font-size: 12px;
}

.app-content {
  max-width: 1500px;
  padding: 26px 30px 34px;
}

.app-footer {
  margin: 0 30px;
  padding: 14px 0 18px;
  border-top: 1px solid var(--border);
  color: var(--text-muted);
  font-size: 12px;
}

.app-footer .mono {
  color: var(--text-muted);
  font-size: 11px;
}

.nav-search {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  margin: 0 4px 18px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel);
  color: var(--text-muted);
}

.nav-search:focus-within {
  border-color: var(--accent-line);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.nav-search .el-icon {
  flex: none;
  font-size: 14px;
}

.nav-search input {
  width: 100%;
  min-width: 0;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--text);
  font: inherit;
  font-size: 13px;
}

.nav-search input::placeholder {
  color: var(--text-muted);
}

.sidebar-collapsed .nav-search {
  display: none;
}

.sidebar-collapsed .system-action {
  display: none;
}

@media (max-width: 1000px) {
  .sidebar {
    width: 272px;
    border-radius: 0 14px 14px 0;
  }

  .topbar {
    padding: 0 20px;
  }

  .app-content {
    padding: 22px 20px 30px;
  }

  .app-footer {
    margin: 0 20px;
  }
}

@media (max-width: 560px) {
  .app-content {
    padding: 18px 16px 26px;
  }

  .app-footer {
    margin: 0 16px;
    font-size: 11px;
  }
}

.topbar-user {
  max-width: 110px;
  overflow: hidden;
  color: var(--text-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 760px) {
  .topbar-user {
    display: none;
  }
}

</style>
