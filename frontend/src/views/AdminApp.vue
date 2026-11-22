<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { SwitchButton } from '@element-plus/icons-vue'

import { useAuth } from '@/composables/useAuth'
import AdminLoginView from '@/views/AdminLoginView.vue'
import AdminView from '@/views/AdminView.vue'

const auth = useAuth()
const checking = ref(true)
const userName = computed(() => auth.user.value?.username ?? '管理员')

async function load() {
  checking.value = true
  const user = await auth.ensureUser(true)
  if (user && !user.permissions.includes('admin.console')) {
    await auth.logout()
  }
  checking.value = false
}

async function logout() {
  await auth.logout()
}

onMounted(load)
</script>

<template>
  <div v-if="checking" class="admin-loading">正在验证后台权限…</div>
  <AdminLoginView v-else-if="!auth.user.value" @authenticated="load" />
  <div v-else class="admin-shell">
    <header class="admin-shell-head">
      <div class="admin-shell-brand">
        <span class="admin-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
        <div>
          <strong>LiteTrain 后台</strong>
          <small>独立管理端口 · 独立登录 session</small>
        </div>
      </div>
      <div class="admin-shell-user">
        <span>{{ userName }}</span>
        <button type="button" title="退出后台" aria-label="退出后台" @click="logout">
          <el-icon><SwitchButton /></el-icon>
        </button>
      </div>
    </header>
    <main class="admin-shell-content">
      <AdminView />
    </main>
  </div>
</template>

<style scoped>
.admin-loading {
  display: grid;
  place-items: center;
  min-height: 100vh;
  color: var(--text-muted);
  background: var(--bg);
  font-size: 13px;
}

.admin-shell {
  min-height: 100vh;
  background: var(--bg);
}

.admin-shell-head {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  height: 64px;
  padding: 0 28px;
  border-bottom: 1px solid var(--border);
  background: color-mix(in srgb, var(--panel) 92%, transparent);
  backdrop-filter: blur(14px);
}

.admin-shell-brand {
  display: flex;
  align-items: center;
  gap: 10px;
}

.admin-mark {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 2px;
  width: 30px;
  height: 30px;
  padding: 4px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: var(--panel-soft);
}

.admin-mark i {
  border-radius: 2px;
  background: var(--accent);
}

.admin-mark i:nth-child(2) { opacity: 0.55; }
.admin-mark i:nth-child(3) { opacity: 0.28; }
.admin-mark i:nth-child(4) { opacity: 0.8; }

.admin-shell-brand div {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.admin-shell-brand strong {
  color: var(--text);
  font-family: var(--font-display);
  font-size: 15px;
  letter-spacing: -0.2px;
}

.admin-shell-brand small {
  color: var(--text-muted);
  font-size: 10px;
}

.admin-shell-user {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--text-secondary);
  font-size: 12px;
}

.admin-shell-user button {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel-soft);
  color: var(--text-muted);
  cursor: pointer;
}

.admin-shell-user button:hover {
  border-color: var(--accent-line);
  color: var(--accent);
}

.admin-shell-content {
  width: min(1240px, 100%);
  margin: 0 auto;
  padding: 28px;
}

.admin-shell-content :deep(.page) {
  gap: 18px;
}

@media (max-width: 600px) {
  .admin-shell-head {
    padding: 0 16px;
  }

  .admin-shell-content {
    padding: 18px 16px;
  }

  .admin-shell-brand small {
    display: none;
  }
}
</style>
