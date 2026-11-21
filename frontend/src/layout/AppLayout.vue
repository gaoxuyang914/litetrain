<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Bell,
  BellFilled,
  DataAnalysis,
  Document,
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
let statusTimer: number | undefined

const navItems = [
  { name: 'overview', label: '集群总览', caption: 'CPU / 队列', icon: DataAnalysis, path: '/overview' },
  { name: 'tasks', label: '任务列表', caption: '提交与追踪', icon: Tickets, path: '/tasks' },
  { name: 'task-submit', label: '提交任务', caption: 'YOLO · CPU', icon: MagicStick, path: '/tasks/new' },
  { name: 'pipelines', label: '训练流水线', caption: '预处理 → 评估', icon: Share, path: '/pipelines' },
  { name: 'assets', label: '数据与产物', caption: '资产 · Webhook', icon: FolderOpened, path: '/assets' },
]

const title = computed(() => {
  if (route.name === 'task-detail') return '任务详情'
  return (route.meta.title as string) || '训练工作台'
})

const eyebrow = computed(() => ((route.meta.eyebrow as string) || 'Workbench').toUpperCase())

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
})

watch(
  () => route.fullPath,
  () => refreshStatus(),
)

onBeforeUnmount(() => {
  if (statusTimer) window.clearInterval(statusTimer)
})
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar" :class="{ open: mobileNavOpen }">
      <div class="brand" @click="navigate('overview')">
        <span class="brand-mark" aria-hidden="true">
          <i></i><i></i><i></i><i></i>
        </span>
        <span class="brand-copy">
          <strong>LiteTrain</strong>
          <small>CPU TRAINING LAB</small>
        </span>
      </div>

      <div class="nav-block">
        <p class="nav-label">工作台</p>
        <nav class="nav-list">
          <button
            v-for="item in navItems"
            :key="item.name"
            type="button"
            class="nav-item"
            :class="{ active: isActive(item.name) }"
            @click="navigate(item.name)"
          >
            <el-icon><component :is="item.icon" /></el-icon>
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
          </button>
        </nav>
      </div>

      <div class="sidebar-spacer"></div>

      <div class="system-card">
        <div class="system-card-head">
          <span class="system-label">SYSTEM PULSE</span>
          <span class="system-live"><i></i>LIVE</span>
        </div>
        <div class="system-row">
          <span>Ray cluster</span>
          <span class="system-value" :class="{ ok: overview?.services.ray_connected }">
            {{ overview?.services.ray_connected ? 'ONLINE' : 'OFFLINE' }}
          </span>
        </div>
        <div class="system-row">
          <span>Redis bus</span>
          <span class="system-value" :class="{ ok: overview?.services.redis_connected }">
            {{ overview?.services.redis_connected ? 'ONLINE' : 'LOCAL' }}
          </span>
        </div>
        <div class="system-row">
          <span>Executor</span>
          <span class="system-value accent">{{ overview?.services.execution_backend ?? '...' }}</span>
        </div>
        <div class="system-meter">
          <span :style="{ width: `${overview?.cpu.utilization_percent ?? 0}%` }"></span>
        </div>
        <p class="system-foot">
          {{ overview?.cpu.allocated ?? 0 }} / {{ overview?.cpu.total ?? '—' }} CPU allocated
        </p>
      </div>

      <a class="sidebar-doc" href="/docs" target="_blank" rel="noreferrer">
        <el-icon><Document /></el-icon>
        API 文档 / OpenAPI
      </a>
    </aside>

    <div v-if="mobileNavOpen" class="nav-backdrop" @click="mobileNavOpen = false"></div>

    <div class="workspace">
      <header class="topbar">
        <div class="topbar-left">
          <button class="icon-button mobile-only" type="button" aria-label="打开导航" @click="mobileNavOpen = true">
            <el-icon><MenuIcon /></el-icon>
          </button>
          <div class="crumb">
            <span class="crumb-root">LiteTrain</span>
            <span class="crumb-slash">/</span>
            <span>{{ eyebrow }}</span>
            <span class="crumb-slash">/</span>
            <strong>{{ title }}</strong>
          </div>
        </div>

        <div class="topbar-right">
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
            {{ overview?.services.execution_backend === 'ray' ? 'RAY' : 'LOCAL' }}
          </span>
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
        <span>LiteTrain 2.0 · CPU-only YOLO training control plane</span>
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
