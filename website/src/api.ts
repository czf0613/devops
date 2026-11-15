export interface User {
  id: number
  username: string
  display_name: string
  email: string
  phone: string | null
  status: 'active' | 'disabled'
  roles: string[]
  has_legacy_password: boolean
  created_at: string
  updated_at: string
}

export interface Session {
  id: number
  user_id: number
  created_at: string
  expires_at: string
  last_seen_at: string
  revoked_at: string | null
  revoke_reason: string | null
  login_ip: string | null
  user_agent: string | null
  current: boolean
  active: boolean
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}
export interface AuthResult {
  user: User
  csrf_token: string
}
export interface Capabilities {
  csrf_token: string
  registration_enabled: boolean
}
export interface Role {
  code: string
  name: string
  description: string
  assignable: boolean
}
export interface AuditEvent {
  id: number
  created_at: string
  actor_user_id: number | null
  target_type: string | null
  target_id: number | null
  action: string
  result: string
  request_id: string
  details: Record<string, unknown>
}

export class ApiError extends Error {
  status: number
  retryAfter: number
  constructor(message: string, status: number, retryAfter = 0) {
    super(message)
    this.status = status
    this.retryAfter = retryAfter
  }
}

let csrfToken = ''
let csrfPending: Promise<Capabilities> | null = null
export function setCsrf(value: string) {
  csrfToken = value
}

export async function capabilities(): Promise<Capabilities> {
  if (!csrfPending) {
    csrfPending = api<Capabilities>('/auth/csrf')
      .then((data) => {
        csrfToken = data.csrf_token
        return data
      })
      .finally(() => {
        csrfPending = null
      })
  }
  return csrfPending
}

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  if (method !== 'GET') {
    if (!csrfToken) await capabilities()
    headers['X-CSRF-Token'] = csrfToken
  }
  if (body !== undefined) headers['Content-Type'] = body instanceof Blob ? 'application/octet-stream' : 'application/json'
  let response: Response
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers,
      credentials: 'same-origin',
      cache: 'no-store',
      ...(body !== undefined ? { body: body instanceof Blob ? body : JSON.stringify(body) } : {}),
      signal: AbortSignal.timeout(20000),
    })
  } catch {
    throw new ApiError('服务连接失败，请检查网络或稍后重试', 0)
  }
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/login' && path !== '/me') {
      window.dispatchEvent(new Event('session-expired'))
    }
    if (response.status === 403) csrfToken = ''
    const message =
      typeof data.detail === 'string'
        ? data.detail
        : Array.isArray(data.detail)
          ? data.detail.map((error: { msg: string }) => error.msg).join('；')
          : '请求失败，请稍后重试'
    throw new ApiError(message, response.status, Number(response.headers.get('Retry-After')) || 0)
  }
  return data as T
}

export const errorText = (error: unknown) =>
  error instanceof Error ? error.message : '操作失败，请重试'
export const dateText = (value: string) =>
  new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
