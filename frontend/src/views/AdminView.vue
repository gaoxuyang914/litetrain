<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Setting, UserFilled } from '@element-plus/icons-vue'

import { api } from '@/api/client'
import type { AuthUser, UserCreatePayload } from '@/types/auth'
import type { RuntimeSettings } from '@/types/task'

type Tab = 'accounts' | 'settings'

const activeTab = ref<Tab>('accounts')
const users = ref<AuthUser[]>([])
const loading = ref(true)
const usersLoading = ref(false)
const settingsLoading = ref(false)
const savingSettings = ref(false)
const createOpen = ref(false)
const createBusy = ref(false)
const createForm = reactive<UserCreatePayload>({
  username: '',
  password: '',
  role: 'viewer',
})
const runtimeSettings = ref<RuntimeSettings | null>(null)
const settingsForm = reactive({
  default_max_runtime_seconds: 1800,
  heartbeat_timeout_seconds: 25,
  default_max_retries: 3,
  default_cpu_cores: 4,
  user_cpu_quota: 8,
})

const roleOptions = [
  { value: 'admin', label: '管理员', caption: '全部权限，可进入后台' },
  { value: 'operator', label: '操作员', caption: '可提交、取消、管理资产' },
  { value: 'viewer', label: '只读成员', caption: '只能查看训练与日志' },
] as const

function roleLabel(role: string): string {
  return roleOptions.find((item) => item.value === role)?.label ?? role
}

function formatTime(value: string | null): string {
  if (!value) return '从未登录'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

async function loadUsers(silent = false) {
  if (!silent) usersLoading.value = true
  try {
    users.value = await api.listUsers()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '读取账号失败')
  } finally {
    usersLoading.value = false
  }
}

async function loadSettings() {
  settingsLoading.value = true
  try {
    const data = await api.getRuntimeSettings()
    runtimeSettings.value = data
    settingsForm.default_max_runtime_seconds = data.default_max_runtime_seconds
    settingsForm.heartbeat_timeout_seconds = data.heartbeat_timeout_seconds
    settingsForm.default_max_retries = data.default_max_retries
    settingsForm.default_cpu_cores = data.default_cpu_cores
    settingsForm.user_cpu_quota = data.user_cpu_quota
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '读取运行设置失败')
  } finally {
    settingsLoading.value = false
  }
}

async function loadAll() {
  loading.value = true
  await Promise.all([loadUsers(true), loadSettings()])
  loading.value = false
}

async function createAccount() {
  if (!createForm.username.trim() || createForm.password.length < 6) {
    ElMessage.warning('账号至少 3 位，密码至少 6 位')
    return
  }
  createBusy.value = true
  try {
    await api.createUser({
      username: createForm.username.trim(),
      password: createForm.password,
      role: createForm.role,
    })
    ElMessage.success(`已创建账号 ${createForm.username.trim()}`)
    createOpen.value = false
    createForm.username = ''
    createForm.password = ''
    createForm.role = 'viewer'
    await loadUsers()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '创建账号失败')
  } finally {
    createBusy.value = false
  }
}

async function toggleActive(user: any) {
  try {
    await api.updateUser(user.id, { active: !user.active })
    ElMessage.success(user.active ? `已停用 ${user.username}` : `已启用 ${user.username}`)
    await loadUsers()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '更新账号失败')
  }
}

async function changeRole(user: any, role: AuthUser['role']) {
  try {
    await api.updateUser(user.id, { role })
    ElMessage.success(`已将 ${user.username} 设为${roleLabel(role)}`)
    await loadUsers()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '更新角色失败')
  }
}

async function resetPassword(user: any) {
  try {
    const { value } = await ElMessageBox.prompt(`为 ${user.username} 设置新密码`, '重置密码', {
      inputType: 'password',
      inputPlaceholder: '至少 6 位',
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValidator: (input) => (input.trim().length >= 6 ? true : '密码至少 6 位'),
    })
    await api.updateUser(user.id, { password: value })
    ElMessage.success('密码已更新')
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(err instanceof Error ? err.message : '重置密码失败')
  }
}

async function saveSettings() {
  savingSettings.value = true
  try {
    const data = await api.updateRuntimeSettings({ ...settingsForm })
    runtimeSettings.value = data
    ElMessage.success('运行设置已保存，新任务将使用新的默认值')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存运行设置失败')
  } finally {
    savingSettings.value = false
  }
}

onMounted(loadAll)
</script>

<template>
  <div class="page admin-page" v-loading="loading">
    <div class="page-head">
      <div class="page-heading">
        <span class="eyebrow">PLATFORM CONTROL</span>
        <h1 class="page-title">后台管理</h1>
        <p class="page-subtitle">管理训练平台账号和运行策略。运行配置只在这里修改。</p>
      </div>
      <div class="page-actions">
        <el-button class="page-icon-button" :icon="Refresh" title="刷新" :loading="loading" @click="loadAll" />
      </div>
    </div>

    <section class="panel admin-panel">
      <div class="admin-tabs">
        <button
          type="button"
          class="admin-tab"
          :class="{ active: activeTab === 'accounts' }"
          @click="activeTab = 'accounts'"
        >
          <el-icon><UserFilled /></el-icon>
          <span>
            <strong>账号管理</strong>
            <small>账号、角色与启停</small>
          </span>
        </button>
        <button
          type="button"
          class="admin-tab"
          :class="{ active: activeTab === 'settings' }"
          @click="activeTab = 'settings'"
        >
          <el-icon><Setting /></el-icon>
          <span>
            <strong>运行设置</strong>
            <small>超时、心跳、重试与配额</small>
          </span>
        </button>
        <div class="admin-tab-meta">
          <span class="tag-soft"><span class="dot"></span>仅管理员可见</span>
        </div>
      </div>

      <template v-if="activeTab === 'accounts'">
        <div class="admin-toolbar">
          <div>
            <strong>平台账号</strong>
            <span>管理员、操作员和只读成员共 {{ users.length }} 个</span>
          </div>
          <el-button type="primary" :icon="Plus" @click="createOpen = true">创建账号</el-button>
        </div>
        <el-table :data="users" row-key="id" class="admin-table" v-loading="usersLoading">
          <el-table-column label="账号" min-width="170">
            <template #default="{ row }">
              <div class="account-cell">
                <span class="account-avatar">{{ row.username.slice(0, 1).toUpperCase() }}</span>
                <div>
                  <strong>{{ row.username }}</strong>
                  <small>{{ row.id }}</small>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="角色" width="150">
            <template #default="{ row }">
              <el-select
                :model-value="row.role"
                size="small"
                class="role-select"
                @change="(value: string) => changeRole(row, value as AuthUser['role'])"
              >
                <el-option v-for="option in roleOptions" :key="option.value" :label="option.label" :value="option.value" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <span class="account-status" :class="{ off: !row.active }">
                <i></i>{{ row.active ? '已启用' : '已停用' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="最近登录" width="170">
            <template #default="{ row }">
              <span class="mono account-time">{{ formatTime(row.last_login_at) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="190" align="right">
            <template #default="{ row }">
              <el-button text size="small" @click="resetPassword(row)">重置密码</el-button>
              <el-button text size="small" :type="row.active ? 'danger' : 'primary'" @click="toggleActive(row)">
                {{ row.active ? '停用' : '启用' }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>

      <template v-else>
        <div class="admin-toolbar">
          <div>
            <strong>运行策略</strong>
            <span>修改后写入 SQLite，调度器和新任务立即使用新配置</span>
          </div>
          <el-button type="primary" :loading="savingSettings" @click="saveSettings">保存设置</el-button>
        </div>
        <div class="admin-settings" v-loading="settingsLoading">
          <div class="admin-setting-row">
            <div>
              <strong>任务超时上限</strong>
              <span>同时作为新任务默认值；提交页不能超过该值</span>
            </div>
            <div class="admin-setting-control">
              <el-input-number
                v-model="settingsForm.default_max_runtime_seconds"
                :min="runtimeSettings?.limits.default_max_runtime_seconds?.[0] ?? 10"
                :max="runtimeSettings?.limits.default_max_runtime_seconds?.[1] ?? 86400"
                :step="60"
                controls-position="right"
              />
              <span>秒</span>
            </div>
          </div>
          <div class="admin-setting-row">
            <div>
              <strong>僵死判定阈值</strong>
              <span>超过该时间没有 worker 心跳，任务会被终止并回收资源</span>
            </div>
            <div class="admin-setting-control">
              <el-input-number
                v-model="settingsForm.heartbeat_timeout_seconds"
                :min="5"
                :max="600"
                :step="5"
                controls-position="right"
              />
              <span>秒</span>
            </div>
          </div>
          <div class="admin-setting-row">
            <div>
              <strong>失败重试上限</strong>
              <span>同时作为新任务默认值；超过该值的重试配置会被拒绝</span>
            </div>
            <div class="admin-setting-control">
              <el-input-number v-model="settingsForm.default_max_retries" :min="0" :max="5" controls-position="right" />
              <span>次</span>
            </div>
          </div>
          <div class="admin-setting-row">
            <div>
              <strong>默认 CPU 核数</strong>
              <span>新任务表单的默认资源占用</span>
            </div>
            <div class="admin-setting-control">
              <el-input-number
                v-model="settingsForm.default_cpu_cores"
                :min="1"
                :max="runtimeSettings?.limits.default_cpu_cores?.[1] ?? 8"
                controls-position="right"
              />
              <span>核</span>
            </div>
          </div>
          <div class="admin-setting-row">
            <div>
              <strong>单用户 CPU 配额</strong>
              <span>不能低于当前已分配的 CPU；新任务和抢占都受此限制</span>
            </div>
            <div class="admin-setting-control">
              <el-input-number
                v-model="settingsForm.user_cpu_quota"
                :min="1"
                :max="runtimeSettings?.total_cpus ?? 8"
                controls-position="right"
              />
              <span>核</span>
            </div>
          </div>
        </div>
        <p class="admin-note">当前提交页上限：{{ Math.round(settingsForm.default_max_runtime_seconds / 60) }} 分钟 / {{ settingsForm.default_max_retries }} 次重试。任务提交页会同时受这里的超时和重试上限约束。</p>
      </template>
    </section>

    <el-dialog v-model="createOpen" title="创建平台账号" width="460px" align-center>
      <div class="create-form">
        <label>
          <span>账号</span>
          <el-input v-model="createForm.username" placeholder="至少 3 位" autocomplete="off" />
        </label>
        <label>
          <span>初始密码</span>
          <el-input v-model="createForm.password" type="password" show-password placeholder="至少 6 位" autocomplete="new-password" />
        </label>
        <label>
          <span>角色</span>
          <el-select v-model="createForm.role" style="width: 100%">
            <el-option v-for="option in roleOptions" :key="option.value" :label="option.label" :value="option.value">
              <div class="role-option">
                <strong>{{ option.label }}</strong>
                <small>{{ option.caption }}</small>
              </div>
            </el-option>
          </el-select>
        </label>
      </div>
      <template #footer>
        <el-button @click="createOpen = false">取消</el-button>
        <el-button type="primary" :loading="createBusy" @click="createAccount">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.admin-page {
  gap: 18px;
}

.admin-panel {
  overflow: hidden;
}

.admin-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--border);
}

.admin-tab {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 9px 13px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--text-muted);
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.admin-tab:hover {
  background: var(--panel-soft);
}

.admin-tab.active {
  border-color: var(--accent-line);
  background: var(--accent-soft);
  color: var(--accent);
}

.admin-tab > .el-icon {
  font-size: 16px;
}

.admin-tab span {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.admin-tab strong {
  color: inherit;
  font-size: 13px;
  font-weight: 600;
}

.admin-tab small {
  color: var(--text-muted);
  font-size: 10px;
}

.admin-tab-meta {
  margin-left: auto;
}

.admin-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 17px 18px;
}

.admin-toolbar > div {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.admin-toolbar strong {
  color: var(--text);
  font-size: 15px;
  font-weight: 600;
}

.admin-toolbar span {
  color: var(--text-muted);
  font-size: 12px;
}

.admin-table {
  border-top: 1px solid var(--border);
}

.account-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.account-avatar {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  flex: none;
  border: 1px solid var(--accent-line);
  border-radius: 8px;
  background: var(--accent-soft);
  color: var(--accent);
  font-family: var(--mono);
  font-size: 12px;
}

.account-cell > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.account-cell strong {
  color: var(--text);
  font-size: 13px;
  font-weight: 600;
}

.account-cell small {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 10px;
}

.account-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--success);
  font-size: 12px;
}

.account-status i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}

.account-status.off {
  color: var(--text-muted);
}

.account-time {
  color: var(--text-muted);
  font-size: 11px;
}

.role-select {
  width: 120px;
}

.admin-settings {
  border-top: 1px solid var(--border);
}

.admin-setting-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 22px;
  padding: 14px 18px;
}

.admin-setting-row + .admin-setting-row {
  border-top: 1px solid var(--border);
}

.admin-setting-row > div:first-child {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.admin-setting-row strong {
  color: var(--text);
  font-size: 13px;
  font-weight: 600;
}

.admin-setting-row span {
  color: var(--text-muted);
  font-size: 12px;
}

.admin-setting-control {
  display: flex;
  align-items: center;
  gap: 7px;
  flex: none;
}

.admin-setting-control :deep(.el-input-number) {
  width: 150px;
}

.admin-note {
  margin: 0;
  padding: 13px 18px;
  border-top: 1px solid var(--border);
  color: var(--text-muted);
  font-size: 12px;
}

.create-form {
  display: flex;
  flex-direction: column;
  gap: 15px;
}

.create-form label {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.create-form label > span {
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
}

.role-option {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 3px 0;
}

.role-option strong {
  color: var(--text);
  font-size: 13px;
}

.role-option small {
  color: var(--text-muted);
  font-size: 11px;
}

@media (max-width: 760px) {
  .admin-tabs {
    flex-wrap: wrap;
  }

  .admin-tab-meta {
    width: 100%;
    margin-left: 0;
  }

  .admin-setting-row {
    align-items: flex-start;
    flex-direction: column;
    gap: 9px;
  }

  .admin-setting-control {
    width: 100%;
  }

  .admin-setting-control :deep(.el-input-number) {
    flex: 1;
    width: auto;
  }
}
</style>
