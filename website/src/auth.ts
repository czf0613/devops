import { reactive } from 'vue'
import { api, ApiError, setCsrf } from './api'
import type { AuthResult, User } from './api'

export const auth = reactive({ user: null as User | null, loaded: false, error: '', notice: '', retryPath: '' })

export async function loadUser() {
  try {
    auth.user = await api<User>('/me')
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      auth.user = null
      setCsrf('')
    } else {
      auth.error = error instanceof Error ? error.message : '无法读取登录状态'
      return false
    }
  }
  auth.error = ''
  auth.loaded = true
  auth.retryPath = ''
  return true
}

export function acceptLogin(result: AuthResult) {
  auth.user = result.user
  auth.loaded = true
  auth.error = ''
  auth.notice = ''
  auth.retryPath = ''
  setCsrf(result.csrf_token)
}

export function forgetLogin(notice = '') {
  auth.user = null
  auth.loaded = true
  auth.error = auth.retryPath = ''
  auth.notice = notice
  setCsrf('')
}
