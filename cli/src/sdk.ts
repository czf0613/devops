/** Raw DevOps API SDK. CLI gh-compatible projections live in output.ts. */
import type {
  CIConfig,
  WorkflowCatalog,
  CIRun,
  CIRunDetail,
  CITrigger,
  CILogs,
} from './ci-types.js'
export type * from './ci-types.js'
export const DEFAULT_HOST = 'https://devops.kevinc.ltd'

export function normalizeHost(input = DEFAULT_HOST): string {
  const url = new URL(input.includes('://') ? input : `https://${input}`)
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/')
    throw new Error('Host must be an origin, e.g. https://example.com:8443')
  if (
    url.protocol !== 'https:' &&
    !(url.protocol === 'http:' && ['127.0.0.1', '[::1]', 'localhost'].includes(url.hostname))
  )
    throw new Error('HTTPS is required (HTTP is allowed only on loopback)')
  return url.origin
}

export class APIError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
    public requestId: string | null,
  ) {
    super(
      `HTTP ${status}: ${typeof detail === 'string' ? detail : JSON.stringify(detail)}${requestId ? ` (request ${requestId})` : ''}`,
    )
    this.name = 'APIError'
  }
}
export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}
export interface Person {
  id: number
  username: string
  display_name: string
}
export interface Catalog {
  id: number
  kind: string
  name: string
  description: string
  data: Record<string, unknown>
}
export interface Repository {
  id: number
  name: string
  full_name: string
  description: string
  clone_path: string
  archived: boolean
  default_branch: string
  owner: Person
  created_at: string
  updated_at: string
  permissions: { is_owner: boolean; is_admin: boolean; access: string; role: string }
}
export interface Item {
  id: number
  number: number
  kind: 'issue' | 'pull'
  title: string
  body?: string
  state: string
  version: number
  url: string
  author: Person
  assignees: Person[]
  catalog: Catalog[]
  created_at: string
  updated_at: string
  closed_at: string | null
  locked: boolean
}
export interface Pull {
  base_ref: string
  head_ref: string
  base_sha: string
  head_sha: string
  draft: boolean
  merged_sha: string | null
  reviewers: Person[]
  reviews: unknown[]
  reasons: string[]
  checks: PullCheck[]
  policy: { required_checks: string[] }
  merge_checks: Record<string, unknown>
  merge_intent: MergeIntent | null
}
export interface PullCheck {
  id: number
  name: string
  status: 'queued' | 'running' | 'success' | 'failure' | 'cancelled' | 'skipped'
  details: string
  external_url: string | null
  head_sha: string
}
export interface MergeIntent {
  id: number
  state: string
  result_sha?: string | null
  error?: string | null
}
export interface CreateItem {
  title: string
  body?: string
  assignee_ids?: number[]
  catalog_ids?: number[]
}
export interface CreatePull extends CreateItem {
  head_ref: string
  base_ref?: string
  draft?: boolean
  reviewer_ids?: number[]
}
export interface UpdateItem extends Partial<CreateItem> {
  version: number
  state?: 'open' | 'closed'
  close_reason?: string
  locked?: boolean
}
export type TokenProvider = string | (() => string | Promise<string>)

export class DevOpsClient {
  readonly origin: string
  constructor(options: {
    hostname?: string
    token: TokenProvider
    timeout?: number
    fetch?: typeof fetch
  }) {
    this.origin = normalizeHost(options.hostname)
    this.options = options
  }
  private options: { token: TokenProvider; timeout?: number; fetch?: typeof fetch }
  private async send(
    endpoint: string,
    options: {
      method?: string
      body?: unknown
      signal?: AbortSignal
      accept?: string
      timeout?: number
    } = {},
  ): Promise<Response> {
    if (!endpoint.startsWith('/api/') || endpoint.includes('\\') || endpoint.includes('#'))
      throw new Error('API endpoint must start with /api/')
    const url = new URL(endpoint, this.origin)
    if (url.origin !== this.origin || !url.pathname.startsWith('/api/'))
      throw new Error('Cross-origin API request refused')
    const token =
      typeof this.options.token === 'function'
        ? await this.options.token()
        : this.options.token
    const response = await (this.options.fetch ?? fetch)(url, {
      method: options.method ?? 'GET',
      redirect: 'manual',
      headers: {
        Accept: options.accept ?? 'application/json',
        Authorization: `Bearer ${token}`,
        ...(options.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal
        ? AbortSignal.any([
            options.signal,
            AbortSignal.timeout(this.options.timeout ?? options.timeout ?? 30000),
          ])
        : AbortSignal.timeout(this.options.timeout ?? options.timeout ?? 30000),
    })
    if (response.ok) return response
    const text = await response.text()
    let data: any
    try {
      data = text ? JSON.parse(text) : null
    } catch {
      data = text
    }
    throw new APIError(
      response.status,
      data?.detail ?? data,
      response.headers.get('x-request-id'),
    )
  }
  async request<T = unknown>(
    endpoint: string,
    options: { method?: string; body?: unknown; signal?: AbortSignal } = {},
  ): Promise<T> {
    const response = await this.send(endpoint, options)
    const text = await response.text()
    try {
      return (text ? JSON.parse(text) : null) as T
    } catch {
      return text as T
    }
  }
  async *pages<T>(endpoint: string): AsyncGenerator<Page<T>> {
    const url = new URL(endpoint, this.origin)
    for (let page = Number(url.searchParams.get('page') || 1); page <= 100000; page++) {
      url.searchParams.set('page', String(page))
      const result = await this.request<Page<T>>(url.pathname + url.search)
      if (
        !Array.isArray(result.items) ||
        !Number.isSafeInteger(result.page_size) ||
        result.page_size <= 0 ||
        result.page !== page
      )
        throw new Error('Endpoint does not use DevOps page pagination')
      yield result
      if (!result.items.length || page * result.page_size >= result.total) return
    }
    throw new Error('Pagination exceeded server page limit')
  }
  async list<T>(endpoint: string, limit = 30): Promise<T[]> {
    if (!Number.isSafeInteger(limit) || limit < 1)
      throw new Error('Limit must be a positive integer')
    const rows: T[] = []
    for await (const page of this.pages<T>(endpoint)) {
      rows.push(...page.items)
      if (rows.length >= limit) break
    }
    return rows.slice(0, limit)
  }
  me() {
    return this.request<{ id: number; login: string; name: string; scopes: string[] }>(
      '/api/cli/me',
    )
  }
  repository(owner: string, name: string) {
    return this.request<Repository>(
      `/api/repositories/by-name/${encodeURIComponent(owner)}/${encodeURIComponent(name)}`,
    )
  }
  createRepository(name: string, description = '') {
    return this.request<Repository>('/api/repositories', {
      method: 'POST',
      body: { name, description },
    })
  }
  item(repo: number, number: number) {
    return this.request<Item>(`/api/repositories/${repo}/items/${number}`)
  }
  createIssue(repo: number, body: CreateItem) {
    return this.request<Item>(`/api/repositories/${repo}/issues`, { method: 'POST', body })
  }
  updateItem(repo: number, number: number, body: UpdateItem) {
    return this.request<Item>(`/api/repositories/${repo}/items/${number}`, {
      method: 'PATCH',
      body,
    })
  }
  pull(repo: number, number: number) {
    return this.request<Pull>(`/api/repositories/${repo}/pulls/${number}`)
  }
  createPull(repo: number, body: CreatePull) {
    return this.request<Item>(`/api/repositories/${repo}/pulls`, { method: 'POST', body })
  }
  comment(repo: number, number: number, body: string) {
    return this.request<{ url: string }>(`/api/repositories/${repo}/items/${number}/comments`, {
      method: 'POST',
      body: { body },
    })
  }
  merge(
    repo: number,
    number: number,
    body: {
      base_sha: string
      head_sha: string
      method?: 'merge' | 'squash' | 'rebase'
      delete_branch?: boolean
    },
  ) {
    return this.request<MergeIntent>(`/api/repositories/${repo}/pulls/${number}/merge`, {
      method: 'POST',
      body,
    })
  }
  mergeStatus(repo: number, number: number, intent: number) {
    return this.request<MergeIntent>(`/api/repositories/${repo}/pulls/${number}/merges/${intent}`)
  }
  ciConfig(repo: number) {
    return this.request<CIConfig>(`/api/repositories/${repo}/ci`)
  }
  workflows(repo: number, ref: string) {
    return this.request<WorkflowCatalog>(
      `/api/repositories/${repo}/ci/workflows?${new URLSearchParams({ ref })}`,
    )
  }
  workflowSource(repo: number, path: string, ref: string) {
    return this.request<{ type: string; content: string | null; commit: string }>(
      `/api/repositories/${repo}/contents?${new URLSearchParams({ path, ref })}`,
    )
  }
  runs(repo: number, options: { workflow?: string; state?: string; limit?: number } = {}) {
    const query = new URLSearchParams()
    if (options.workflow) query.set('workflow', options.workflow)
    if (options.state) query.set('state', options.state)
    return this.list<CIRun>(
      `/api/repositories/${repo}/ci/runs?${query}`,
      options.limit ?? 20,
    )
  }
  run(repo: number, run: number) {
    return this.request<CIRunDetail>(`/api/repositories/${repo}/ci/runs/${run}`)
  }
  runWorkflow(repo: number, body: CITrigger) {
    return this.request<CIRunDetail>(`/api/repositories/${repo}/ci/runs`, {
      method: 'POST',
      body,
    })
  }
  cancelRun(repo: number, run: number) {
    return this.request<{ ok: boolean }>(
      `/api/repositories/${repo}/ci/runs/${run}/cancel`,
      { method: 'POST' },
    )
  }
  rerun(repo: number, run: number) {
    return this.request<CIRunDetail>(`/api/repositories/${repo}/ci/runs/${run}/rerun`, {
      method: 'POST',
    })
  }
  runLogs(repo: number, attempt: number, after = 0) {
    return this.request<CILogs>(
      `/api/repositories/${repo}/ci/attempts/${attempt}/logs?after=${after}`,
    )
  }
  /** Stream the response body; callers must consume/cancel it and verify artifact size/hash. */
  downloadArtifact(repo: number, artifact: number, signal?: AbortSignal) {
    return this.send(`/api/repositories/${repo}/ci/artifacts/${artifact}`, {
      accept: 'application/octet-stream',
      signal,
      timeout: 300000,
    })
  }
}
