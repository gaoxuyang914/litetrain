import { computed, ref } from 'vue'

import { api, AUTH_TOKEN_KEY, AUTH_USER_KEY } from '@/api/client'
import type { AuthUser } from '@/types/auth'

const token = ref(localStorage.getItem(AUTH_TOKEN_KEY))
const user = ref<AuthUser | null>(readStoredUser())

function readStoredUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(AUTH_USER_KEY)
    return raw ? (JSON.parse(raw) as AuthUser) : null
  } catch {
    return null
  }
}

function persist(nextToken: string, nextUser: AuthUser): void {
  token.value = nextToken
  user.value = nextUser
  localStorage.setItem(AUTH_TOKEN_KEY, nextToken)
  localStorage.setItem(AUTH_USER_KEY, JSON.stringify(nextUser))
}

function clear(): void {
  token.value = null
  user.value = null
  localStorage.removeItem(AUTH_TOKEN_KEY)
  localStorage.removeItem(AUTH_USER_KEY)
}

export function useAuth() {
  const isAuthenticated = computed(() => Boolean(token.value))
  const isAdmin = computed(() => user.value?.permissions.includes('admin.console') ?? false)

  async function login(username: string, password: string): Promise<AuthUser> {
    const response = await api.login(username, password)
    persist(response.token, response.user)
    return response.user
  }

  async function ensureUser(force = false): Promise<AuthUser | null> {
    if (!token.value) return null
    if (user.value && !force) return user.value
    try {
      const current = await api.getMe()
      user.value = current
      localStorage.setItem(AUTH_USER_KEY, JSON.stringify(current))
      return current
    } catch {
      clear()
      return null
    }
  }

  function hasPermission(permission: string): boolean {
    return user.value?.permissions.includes(permission) ?? false
  }

  async function logout(): Promise<void> {
    try {
      await api.logout()
    } catch {
      // The local session is cleared even if the server session already expired.
    } finally {
      clear()
    }
  }

  return {
    token,
    user,
    isAuthenticated,
    isAdmin,
    login,
    ensureUser,
    logout,
    hasPermission,
  }
}
