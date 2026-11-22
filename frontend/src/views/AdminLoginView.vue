<script setup lang="ts">
import { computed, ref } from 'vue'
import { Lock, User } from '@element-plus/icons-vue'

import { useAuth } from '@/composables/useAuth'

const emit = defineEmits<{ authenticated: [] }>()
const auth = useAuth()
const username = ref('admin')
const password = ref('')
const loading = ref(false)
const error = ref('')
const canSubmit = computed(() => username.value.trim().length > 0 && password.value.length > 0)

async function submit() {
  if (!canSubmit.value || loading.value) return
  error.value = ''
  loading.value = true
  try {
    const user = await auth.login(username.value.trim(), password.value, 'admin')
    if (!user.permissions.includes('admin.console')) {
      await auth.logout()
      error.value = '该账号没有后台管理权限'
      return
    }
    emit('authenticated')
  } catch (err) {
    error.value = err instanceof Error ? err.message : '登录失败，请检查账号和密码'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="admin-login">
    <section class="admin-login-card">
      <div class="admin-login-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></div>
      <span class="admin-login-kicker">LiteTrain · PLATFORM CONTROL</span>
      <h1>后台管理登录</h1>
      <p>后台使用独立端口和独立登录 session，与训练平台的登录状态互不影响。</p>
      <form class="admin-login-fields" @submit.prevent="submit">
        <label>
          <span>管理员账号</span>
          <el-input v-model="username" :prefix-icon="User" autocomplete="username" @keyup.enter="submit" />
        </label>
        <label>
          <span>密码</span>
          <el-input
            v-model="password"
            type="password"
            show-password
            :prefix-icon="Lock"
            autocomplete="current-password"
            @keyup.enter="submit"
          />
        </label>
        <p v-if="error" class="admin-login-error">{{ error }}</p>
        <el-button type="primary" size="large" :loading="loading" @click="submit">进入后台</el-button>
      </form>
      <small>默认管理员：admin / admin123</small>
    </section>
  </main>
</template>

<style scoped>
.admin-login {
  display: grid;
  place-items: center;
  min-height: 100vh;
  padding: 24px;
  background:
    radial-gradient(500px 340px at 50% 0%, var(--accent-soft), transparent 70%),
    var(--bg);
}

.admin-login-card {
  width: min(420px, 100%);
  padding: 34px;
  border: 1px solid var(--border);
  border-radius: 16px;
  background: var(--panel);
  box-shadow: var(--shadow-lg);
}

.admin-login-mark {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 3px;
  width: 34px;
  height: 34px;
  padding: 5px;
  margin-bottom: 20px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel-soft);
}

.admin-login-mark i {
  border-radius: 2px;
  background: var(--accent);
}

.admin-login-mark i:nth-child(2) { opacity: 0.55; }
.admin-login-mark i:nth-child(3) { opacity: 0.28; }
.admin-login-mark i:nth-child(4) { opacity: 0.8; }

.admin-login-kicker {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 10px;
}

.admin-login-card h1 {
  margin: 10px 0 8px;
  color: var(--text);
  font-family: var(--font-display);
  font-size: 28px;
  font-weight: 600;
  letter-spacing: -0.6px;
}

.admin-login-card > p {
  margin: 0 0 24px;
  color: var(--text-muted);
  font-size: 13px;
  line-height: 1.7;
}

.admin-login-fields {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.admin-login-fields label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 13px;
}

.admin-login-fields :deep(.el-input__wrapper) {
  min-height: 44px;
}

.admin-login-error {
  margin: 0;
  color: var(--red);
  font-size: 12px;
}

.admin-login-fields .el-button {
  min-height: 44px;
  margin-top: 4px;
}

.admin-login-card small {
  display: block;
  margin-top: 20px;
  color: var(--text-muted);
  font-size: 11px;
}
</style>
