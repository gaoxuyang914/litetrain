<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Lock, User } from '@element-plus/icons-vue'

import { useAuth } from '@/composables/useAuth'

const route = useRoute()
const router = useRouter()
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
    await auth.login(username.value.trim(), password.value)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : ''
    if (redirect && redirect.startsWith('/')) {
      await router.replace(redirect)
    } else {
      await router.replace({ name: 'overview' })
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '登录失败，请检查账号和密码'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-visual">
      <div class="login-brand">
        <span class="brand-mark" aria-hidden="true">
          <i></i><i></i><i></i><i></i>
        </span>
        <div>
          <strong>LiteTrain</strong>
          <span>CPU 训练平台</span>
        </div>
      </div>
      <div class="login-visual-copy">
        <span class="login-kicker">PRIVATE WORKSPACE</span>
        <h1>训练任务，<br />交给平台管理。</h1>
        <p>任务、资源、日志和模型产物都在同一个受控工作区里。登录后继续。</p>
      </div>
      <div class="login-signal">
        <span class="signal-dot"></span>
        <span>FastAPI · SQLite · Ray</span>
      </div>
    </section>

    <section class="login-panel">
      <div class="login-form">
        <span class="login-eyebrow">欢迎回来</span>
        <h2>登录 LiteTrain</h2>
        <p class="login-sub">使用平台账号进入训练工作区；管理员可继续进入后台管理。</p>

        <form class="login-fields" @submit.prevent="submit">
          <label>
            <span>账号</span>
            <el-input
              v-model="username"
              size="large"
              autocomplete="username"
              placeholder="输入账号"
              :prefix-icon="User"
              @keyup.enter="submit"
            />
          </label>
          <label>
            <span>密码</span>
            <el-input
              v-model="password"
              size="large"
              type="password"
              show-password
              autocomplete="current-password"
              placeholder="输入密码"
              :prefix-icon="Lock"
              @keyup.enter="submit"
            />
          </label>
          <p v-if="error" class="login-error">{{ error }}</p>
          <el-button class="login-submit" type="primary" size="large" :loading="loading" @click="submit">
            登录
          </el-button>
        </form>

        <p class="login-hint">
          首次运行默认管理员 <code>admin</code> / <code>admin123</code>；生产环境请通过
          <code>LITETRAIN_BOOTSTRAP_ADMIN_PASSWORD</code> 覆盖。
        </p>
      </div>
    </section>
  </main>
</template>

<style scoped>
.login-page {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(420px, 0.9fr);
  min-height: 100vh;
  background: var(--bg);
}

.login-visual {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  min-height: 100vh;
  padding: 44px clamp(32px, 6vw, 96px);
  background:
    radial-gradient(620px 460px at 20% 15%, var(--accent-soft), transparent 68%),
    linear-gradient(145deg, var(--panel-soft), var(--bg));
  border-right: 1px solid var(--border);
}

.login-brand {
  display: flex;
  align-items: center;
  gap: 11px;
}

.brand-mark {
  position: relative;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 3px;
  width: 38px;
  height: 38px;
  padding: 5px;
  border-radius: 9px;
  background: var(--panel);
  border: 1px solid var(--border);
}

.brand-mark i {
  display: block;
  border-radius: 2px;
  background: var(--accent);
}

.brand-mark i:nth-child(2) {
  opacity: 0.55;
}

.brand-mark i:nth-child(3) {
  opacity: 0.28;
}

.brand-mark i:nth-child(4) {
  opacity: 0.8;
}

.login-brand strong {
  display: block;
  color: var(--text);
  font-family: var(--font-display);
  font-size: 19px;
  letter-spacing: -0.3px;
}

.login-brand div > span {
  display: block;
  margin-top: 1px;
  color: var(--text-muted);
  font-size: 12px;
}

.login-visual-copy {
  max-width: 560px;
  padding: 56px 0;
}

.login-kicker {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 11px;
}

.login-visual h1 {
  margin: 18px 0 20px;
  color: var(--text);
  font-family: var(--font-display);
  font-size: clamp(38px, 5vw, 64px);
  font-weight: 500;
  letter-spacing: -2px;
  line-height: 1.14;
}

.login-visual-copy p {
  max-width: 430px;
  color: var(--text-secondary);
  font-size: 16px;
  line-height: 1.8;
}

.login-signal {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 11px;
}

.signal-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--success);
  box-shadow: 0 0 0 4px var(--success-soft);
}

.login-panel {
  display: grid;
  place-items: center;
  padding: 32px;
  background: var(--bg);
}

.login-form {
  width: min(360px, 100%);
}

.login-eyebrow {
  color: var(--accent);
  font-family: var(--mono);
  font-size: 11px;
}

.login-form h2 {
  margin: 8px 0 6px;
  color: var(--text);
  font-family: var(--font-display);
  font-size: 30px;
  font-weight: 600;
  letter-spacing: -0.6px;
}

.login-sub {
  margin: 0 0 28px;
  color: var(--text-muted);
  font-size: 13px;
  line-height: 1.7;
}

.login-fields {
  display: flex;
  flex-direction: column;
  gap: 15px;
}

.login-fields label {
  display: flex;
  flex-direction: column;
  gap: 7px;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
}

.login-fields :deep(.el-input__wrapper) {
  min-height: 46px;
  border-radius: 9px;
}

.login-error {
  margin: -2px 0 0;
  color: var(--red);
  font-size: 12px;
}

.login-submit {
  width: 100%;
  min-height: 46px;
  margin-top: 4px;
  border-radius: 9px;
  font-size: 15px;
}

.login-hint {
  margin: 26px 0 0;
  padding-top: 16px;
  border-top: 1px solid var(--border);
  color: var(--text-muted);
  font-size: 11px;
  line-height: 1.8;
}

.login-hint code {
  padding: 1px 4px;
  border-radius: 4px;
  background: var(--panel-soft);
  color: var(--text-secondary);
  font-family: var(--mono);
}

@media (max-width: 900px) {
  .login-page {
    grid-template-columns: minmax(0, 1fr);
  }

  .login-visual {
    min-height: auto;
    padding: 28px 24px 30px;
    border-right: 0;
    border-bottom: 1px solid var(--border);
  }

  .login-visual-copy {
    padding: 32px 0 16px;
  }

  .login-visual h1 {
    font-size: 34px;
    letter-spacing: -1px;
  }

  .login-visual-copy p,
  .login-signal {
    display: none;
  }

  .login-panel {
    padding: 32px 24px 48px;
    place-items: start center;
  }
}
</style>
