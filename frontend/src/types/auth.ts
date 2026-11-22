export interface AuthUser {
  id: string
  username: string
  role: 'admin' | 'operator' | 'viewer'
  permissions: string[]
  active: boolean
  created_at: string
  last_login_at: string | null
}

export interface LoginResponse {
  token: string
  expires_at: string
  user: AuthUser
}

export interface UserCreatePayload {
  username: string
  password: string
  role: 'admin' | 'operator' | 'viewer'
}

export interface UserUpdatePayload {
  role?: 'admin' | 'operator' | 'viewer'
  active?: boolean
  password?: string
}
