export interface Person {
  id: number
  username: string
  display_name: string
}
export interface FormField {
  id: string
  label: string
  type: 'input' | 'textarea' | 'select' | 'checkbox'
  required: boolean
  options: string[]
  description: string
}
export interface Catalog {
  id: number
  kind: 'label' | 'type' | 'milestone' | 'template'
  name: string
  description: string
  data: {
    color: string
    due_at?: string | null
    state: 'open' | 'closed'
    item_kind: 'issue' | 'pull'
    body: string
    fields: FormField[]
  }
}
export interface Reaction {
  emoji: string
  count: number
  mine: boolean
}
export interface ItemSummary {
  id: number
  repository_id: number
  number: number
  kind: 'issue' | 'pull'
  title: string
  state: 'open' | 'closed' | 'merged'
  close_reason: string | null
  url: string
}
export interface Relation {
  id: number
  kind: 'parent' | 'blocks' | 'duplicate' | 'development'
  source_id: number
  target_id: number
  position: number
  item: ItemSummary
}
export interface Backlink {
  id: number
  kind: string
  created_at: string
  source: ItemSummary | { kind: 'commit'; title: string; url: string }
  url: string
}
export interface WorkItem extends ItemSummary {
  body: string
  version: number
  author: Person
  created_at: string
  updated_at: string
  closed_at: string | null
  pinned: boolean
  locked: boolean
  assignees: Person[]
  catalog: Catalog[]
  comment_count: number
  permissions: { can_edit: boolean; can_manage: boolean; can_comment: boolean }
  reactions: Reaction[]
  subscription: { watching: boolean; email: boolean }
  relations: { items: Relation[]; children_total: number; children_completed: number }
  backlinks: Backlink[]
}
export interface Comment {
  id: number
  body: string
  version: number
  author: Person
  created_at: string
  updated_at: string
  pinned: boolean
  thread_id: number | null
  review_id: number | null
  pending: boolean
  url: string
  can_edit: boolean
  reactions: Reaction[]
}
export interface TimelineEntry {
  id: number
  action: string
  data: {
    related_item?: ItemSummary
    reason?: string
    fields?: string[]
    state?: string
    sha?: string
    method?: string
    name?: string
    status?: string
    thread_id?: number
  }
  created_at: string
  actor: Person | null
  comment?: Comment
}
export interface Reference {
  raw: string
  url: string
  title: string
  kind: string
  state?: string
  item_kind?: string
}
export interface Review {
  id: number
  author: Person
  body: string
  head_sha: string
  state: 'pending' | 'commented' | 'approved' | 'changes_requested' | 'dismissed'
  created_at: string
  submitted_at: string | null
}
export interface ReviewThread {
  id: number
  path: string
  side: 'left' | 'right'
  start_line: number
  end_line: number
  base_sha: string
  head_sha: string
  outdated: boolean
  resolved: boolean
  resolved_by: Person | null
  comments: Comment[]
}
export type MergeMethod = 'merge' | 'squash' | 'rebase'
export interface MergePolicy {
  methods: MergeMethod[]
  required_approvals: number
  dismiss_stale_approvals: boolean
  require_resolved_threads: boolean
  required_checks: string[]
}
export interface MergeCheck {
  status: 'checking' | 'clean' | 'conflict' | 'error'
  conflicts: string[]
  error: string | null
}
export interface Pull {
  id: number
  base_ref: string
  head_ref: string
  base_sha: string
  head_sha: string
  draft: boolean
  merged_sha: string | null
  merge_method: string | null
  reviewers: Person[]
  reviews: Review[]
  policy: MergePolicy
  can_merge: boolean
  approvals: number
  required_approvals: number
  unresolved_threads: number
  reasons: string[]
  checks: {
    id: number
    name: string
    status: string
    details: string
    external_url: string | null
    head_sha: string
  }[]
  checks_configured: boolean
  merge_checks: Partial<Record<MergeMethod, MergeCheck>>
  base_missing: boolean
  head_missing: boolean
  merge_intent: {
    id: number
    state: string
    error: string | null
    result_sha: string | null
  } | null
}
export interface Commit {
  sha: string
  author: string
  date: string
  title: string
}
export interface DiffFile {
  path: string
  old_path: string
  status: string
  old_sha: string
  new_sha: string
  viewed?: boolean
  additions: number
  deletions: number
  binary: boolean
}
export interface Comparison {
  base_sha: string
  head_sha: string
  merge_base: string
  files: DiffFile[]
  total: number
  additions: number
  deletions: number
  page: number
  page_size: number
  commits: Commit[]
  commits_truncated: boolean
  path?: string
  patch?: string
}
export const emojiLabels: Record<string, string> = {
  '+1': '👍',
  '-1': '👎',
  laugh: '😄',
  hooray: '🎉',
  confused: '😕',
  heart: '❤️',
  rocket: '🚀',
  eyes: '👀',
}
export const stateLabel = (item: ItemSummary) =>
  item.state === 'merged'
    ? '已合并'
    : item.state === 'open'
      ? '开放'
      : item.close_reason === 'not_planned'
        ? '不计划处理'
        : item.close_reason === 'duplicate'
          ? '重复'
          : '已关闭'
export const methodLabels: Record<MergeMethod, string> = {
  merge: '创建合并提交',
  squash: 'Squash 合并',
  rebase: 'Rebase 合并',
}
export async function scrollAnchor(hash: string) {
  if (!hash) return
  let id: string
  try {
    id = decodeURIComponent(hash.slice(1))
  } catch {
    return
  }
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
  document.getElementById(id)?.scrollIntoView({ block: 'start' })
}
