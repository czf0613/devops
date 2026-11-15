import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { defineComponent, ref } from 'vue'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { RouterView, createMemoryHistory, createRouter } from 'vue-router'
import { api } from '../src/api'
import { auth } from '../src/auth'
import { useDraft } from '../src/drafts'
import ItemDetailView from '../src/views/ItemDetailView.vue'
import NewItemView from '../src/views/NewItemView.vue'
import InlineMetadata from '../src/components/InlineMetadata.vue'
import ItemRelations from '../src/components/ItemRelations.vue'
import AppSelect from '../src/components/AppSelect.vue'
import CommentCard from '../src/components/CommentCard.vue'
import DiffViewer from '../src/components/DiffViewer.vue'
import RepositoryDetailView from '../src/views/RepositoryDetailView.vue'
import NotificationsView from '../src/views/NotificationsView.vue'
import type { Repository } from '../src/repositories'
import type { WorkItem, Comment, Comparison, Pull } from '../src/collaboration'
vi.mock('../src/api', async original => ({ ...(await original<typeof import('../src/api')>()), api: vi.fn() }))
enableAutoUnmount(afterEach)
beforeEach(() => {
  localStorage.clear()
  vi.mocked(api).mockReset()
  auth.user = { id: 1 } as typeof auth.user
  Element.prototype.scrollIntoView = vi.fn()
})
const repository = { id: 12, full_name: 'alice/project', name: 'project', default_branch: 'main',
  owner: { id: 1, username: 'alice' }, archived: false, permissions: { access: 'write', is_owner: true, is_admin: false } } as Repository
const fixture = () => ({
  id: 5, number: 1, repository_id: 12, kind: 'issue', title: 'Original title', body: 'Original body', version: 1,
  state: 'open', url: '/repositories/alice/project/issue/1', author: { id: 1, username: 'alice', display_name: 'Alice' },
  created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
  catalog: [], assignees: [], reactions: [], backlinks: [], comment_count: 0,
  subscription: { watching: true, email: false }, permissions: { can_edit: true, can_manage: true, can_comment: true },
  relations: { items: [], children_total: 0, children_completed: 0 },
}) as WorkItem
async function router(url = '/repositories/alice/project/issue/1') {
  const r = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/:pathMatch(.*)*', component: {} },
    { path: '/repositories/:owner/:name/issue/new', component: {}, meta: { repositoryTab: 'issue' } },
    { path: '/repositories/:owner/:name/issue/:number', component: {}, meta: { repositoryTab: 'issue' } },
  ] })
  await r.push(url)
  return r
}
function mockDetail(item = fixture()) {
  vi.mocked(api).mockImplementation(async (path, method, data) => {
    if (path.endsWith('/items/1') && method === 'PATCH') {
      Object.assign(item, data, { version: item.version + 1 })
      return { ...item }
    }
    if (path.endsWith('/items/1')) return { ...item }
    if (path.includes('/timeline')) return { items: [], total: 0, page: 1, page_size: 50, pinned_comments: [] }
    if (path.endsWith('/resolve')) return []
    return []
  })
  return item
}
const DraftHarness = defineComponent({
  props: { scope: { type: String, default: '12:new:issue' } },
  setup(props) {
    const text = ref('')
    const draft = useDraft(() => props.scope, { text })
    draft.restore()
    return { text, draft }
  },
  template: '<input v-model="text" /><button @click="text = \'\'; draft.clear()">Submit</button><p>{{ draft.message.value }}</p>',
})

it('restores unsent drafts across remounts, isolates accounts and scopes, and clears only after success', async () => {
  const first = mount(DraftHarness)
  await first.get('input').setValue('Do not lose this')
  first.unmount()
  const second = mount(DraftHarness)
  expect(second.get('input').element.value).toBe('Do not lose this')
  expect(second.text()).toContain('已恢复草稿')
  const other = mount(DraftHarness, { props: { scope: '13:new:issue' } })
  expect(other.get('input').element.value).toBe('')
  auth.user = { id: 2 } as typeof auth.user
  const account = mount(DraftHarness)
  expect(account.get('input').element.value).toBe('')
  auth.user = { id: 1 } as typeof auth.user
  await second.get('button').trigger('click')
  second.unmount()
  expect(mount(DraftHarness).get('input').element.value).toBe('')
})

it('clears an erased draft and reports unavailable browser storage without blocking typing', async () => {
  const wrapper = mount(DraftHarness)
  await wrapper.get('input').setValue('draft')
  await wrapper.get('input').setValue('')
  wrapper.unmount()
  expect(localStorage.length).toBe(0)
  const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
  const limited = mount(DraftHarness)
  await limited.get('input').setValue('still here')
  expect(limited.get('input').element.value).toBe('still here')
  expect(limited.text()).toContain('无法保存草稿')
  spy.mockRestore()
})

it('does not restore expired or invalid drafts', () => {
  localStorage.setItem('devops:discussion-draft:v1:1:12:new:issue', JSON.stringify({
    savedAt: Date.now() - 31 * 86400000, value: { text: 'expired' },
  }))
  expect(mount(DraftHarness).get('input').element.value).toBe('')
  localStorage.setItem('devops:discussion-draft:v1:1:12:new:issue', JSON.stringify({
    savedAt: Date.now(), value: { text: 42 },
  }))
  expect(mount(DraftHarness).get('input').element.value).toBe('')
})

it('edits title and body in place, restores after refresh, and discards cancelled edits', async () => {
  mockDetail()
  const r = await router()
  const wrapper = mount(ItemDetailView, { props: { repository }, global: { plugins: [r] }, attachTo: document.body })
  await flushPromises()
  await wrapper.get('[aria-label="编辑标题"]').trigger('click')
  expect(wrapper.find('h1').exists()).toBe(false)
  expect(wrapper.get('#issue-body').text()).toContain('Original body')
  await wrapper.get('.title-edit input').setValue('Saved local title')
  await wrapper.get('[aria-label="编辑正文"]').trigger('click')
  await wrapper.get('#issue-body textarea').setValue('Saved local body')
  expect(wrapper.find('#issue-body .discussion-markdown').exists()).toBe(false)
  wrapper.unmount()
  await r.push('/repositories/alice/project/issue/1')
  const restored = mount(ItemDetailView, { props: { repository }, global: { plugins: [r] }, attachTo: document.body })
  await flushPromises()
  expect(restored.get('.title-edit input').element.value).toBe('Saved local title')
  expect(restored.get('#issue-body textarea').element.value).toBe('Saved local body')
  await restored.get('.title-edit').trigger('keydown', { key: 'Escape' })
  expect(restored.get('h1').text()).toContain('Original title')
  await restored.get('#issue-body form').trigger('keydown', { key: 'Escape' })
  expect(restored.get('#issue-body').text()).toContain('Original body')
  expect(vi.mocked(api).mock.calls.filter(c => c[1] === 'PATCH')).toHaveLength(0)
  expect(document.activeElement?.getAttribute('aria-label')).toBe('编辑正文')
})

it('can save independent title and body edits without making its own unchanged field stale', async () => {
  mockDetail()
  const r = await router()
  const wrapper = mount(ItemDetailView, { props: { repository }, global: { plugins: [r] } })
  await flushPromises()
  await wrapper.get('[aria-label="编辑标题"]').trigger('click')
  await wrapper.get('.title-edit input').setValue('New title')
  await wrapper.get('[aria-label="编辑正文"]').trigger('click')
  await wrapper.get('#issue-body textarea').setValue('New body')
  await wrapper.get('.title-edit').trigger('submit')
  await flushPromises()
  await wrapper.get('#issue-body form').trigger('submit')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/repositories/12/items/1', 'PATCH', { version: 2, body: 'New body' })
  expect(wrapper.find('#issue-body textarea').exists()).toBe(false)
  expect(wrapper.get('#issue-body').text()).toContain('New body')
})

it('keeps the original edit version when restoring a draft after another person edited the item', async () => {
  localStorage.setItem('devops:discussion-draft:v1:1:12:item:1:body', JSON.stringify({
    savedAt: Date.now(), value: { body: 'My old draft', version: 1 },
  }))
  const item = fixture()
  item.version = 2
  mockDetail(item)
  const r = await router()
  const wrapper = mount(ItemDetailView, { props: { repository }, global: { plugins: [r] } })
  await flushPromises()
  vi.mocked(api).mockRejectedValueOnce(new Error('内容已更新'))
  await wrapper.get('#issue-body form').trigger('submit')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/repositories/12/items/1', 'PATCH', { version: 1, body: 'My old draft' })
  expect(wrapper.get('#issue-body textarea').element.value).toBe('My old draft')
  expect(wrapper.text()).toContain('内容已更新')
})

it('preserves a failed comment and clears a successful comment even if timeline refresh fails', async () => {
  mockDetail()
  const r = await router()
  const wrapper = mount(ItemDetailView, { props: { repository }, global: { plugins: [r] } })
  await flushPromises()
  await wrapper.get('.new-comment textarea').setValue('Comment draft')
  vi.mocked(api).mockRejectedValueOnce(new Error('offline'))
  await wrapper.get('.new-comment').trigger('submit')
  await flushPromises()
  expect(wrapper.get('.new-comment textarea').element.value).toBe('Comment draft')
  vi.mocked(api).mockResolvedValueOnce({}).mockRejectedValueOnce(new Error('reload failed'))
  await wrapper.get('.new-comment').trigger('submit')
  await flushPromises()
  expect(wrapper.get('.new-comment textarea').element.value).toBe('')
  expect(localStorage.getItem('devops:discussion-draft:v1:1:12:item:1:comment')).toBeNull()
})

it('replaces only the edited metadata field, supports cancel, and saves only that field', async () => {
  const r = await router()
  vi.mocked(api).mockResolvedValue({})
  const wrapper = mount(InlineMetadata, { props: {
    item: fixture(), catalog: [], members: [{ user_id: 1, display_name: 'Alice', username: 'alice', status: 'active' }] as never,
  }, global: { plugins: [r] }, attachTo: document.body })
  await wrapper.get('[aria-label="编辑负责人"]').trigger('click')
  await wrapper.get('input').setValue(true)
  await wrapper.get('form').trigger('keydown', { key: 'Escape' })
  expect(wrapper.find('form').exists()).toBe(false)
  expect(document.activeElement?.getAttribute('aria-label')).toBe('编辑负责人')
  expect(api).not.toHaveBeenCalled()
  await wrapper.get('[aria-label="编辑负责人"]').trigger('click')
  expect(wrapper.get('input').element.checked).toBe(false)
  await wrapper.get('input').setValue(true)
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/repositories/12/items/1', 'PATCH', { version: 1, assignee_ids: [1] })
  expect(wrapper.find('form').exists()).toBe(false)
  expect(wrapper.emitted('updated')).toHaveLength(1)
})

it('cancels relation selection and ignores a search response for a previous repository', async () => {
  const r = await router()
  let resolve!: (value: unknown) => void
  vi.mocked(api).mockImplementation(async path => {
    if (path.startsWith('/repositories?page')) return { items: [repository], total: 1 }
    return new Promise(done => { resolve = done })
  })
  const wrapper = mount(ItemRelations, { props: { item: fixture(), repository }, global: { plugins: [r] }, attachTo: document.body })
  await wrapper.get('.relation-toggle').trigger('click')
  await flushPromises()
  expect(document.activeElement).toBe(wrapper.get('.relation-search input').element)
  await wrapper.get('.relation-search input').trigger('keydown', { key: 'Enter' })
  wrapper.findAllComponents(AppSelect)[1]!.vm.$emit('update:modelValue', '99')
  await flushPromises()
  resolve({ items: [{ ...fixture(), id: 6, title: 'Stale result' }] })
  await flushPromises()
  expect(JSON.stringify(wrapper.findAllComponents(AppSelect)[2]!.props('options'))).not.toContain('Stale result')
  await wrapper.get('.relation-form').trigger('keydown', { key: 'Escape' })
  expect(wrapper.find('.relation-form').exists()).toBe(false)
  expect(document.activeElement).toBe(wrapper.get('.relation-toggle').element)
})

it('closes an open relation dropdown before Escape cancels the surrounding form', async () => {
  const r = await router()
  vi.mocked(api).mockResolvedValue({ items: [repository], total: 1 })
  const wrapper = mount(ItemRelations, { props: { item: fixture(), repository }, global: { plugins: [r] }, attachTo: document.body })
  await wrapper.get('.relation-toggle').trigger('click')
  await flushPromises()
  const select = wrapper.findComponent(AppSelect).get('[role="combobox"]')
  await select.trigger('click')
  expect(select.attributes('aria-expanded')).toBe('true')
  await select.trigger('keydown', { key: 'Escape' })
  expect(select.attributes('aria-expanded')).toBe('false')
  expect(wrapper.find('.relation-form').exists()).toBe(true)
  await select.trigger('keydown', { key: 'Escape' })
  expect(wrapper.find('.relation-form').exists()).toBe(false)
  expect(document.activeElement).toBe(wrapper.get('.relation-toggle').element)
})

it('restores new issue template fields and parent-specific drafts without replacing the typed body', async () => {
  const template = { id: 8, kind: 'template', name: 'Report', data: { item_kind: 'issue', body: 'Default', fields: [{ id: 'steps', label: 'Steps', type: 'textarea' }] } }
  vi.mocked(api).mockImplementation(async path => path.endsWith('/catalog') ? [template] : [])
  const r = await router('/repositories/alice/project/issue/new?parent=5')
  const first = mount(NewItemView, { props: { repository }, global: { plugins: [r] } })
  await flushPromises()
  first.findComponent(AppSelect).vm.$emit('update:modelValue', '8')
  await flushPromises()
  await first.get('input[placeholder="简要描述需要处理的内容"]').setValue('Child')
  await first.get('.discussion-editor textarea').setValue('Typed description')
  await first.get('.template-field textarea').setValue('Reproduction steps')
  first.unmount()
  await r.push('/repositories/alice/project/issue/new?parent=5')
  const second = mount(NewItemView, { props: { repository }, global: { plugins: [r] } })
  await flushPromises()
  expect(second.get('.discussion-editor textarea').element.value).toBe('Typed description')
  expect(second.get('.template-field textarea').element.value).toBe('Reproduction steps')
  await r.push('/repositories/alice/project/issue/new?parent=9')
  const different = mount(NewItemView, { props: { repository }, global: { plugins: [r] } })
  await flushPromises()
  expect(different.get('.discussion-editor textarea').element.value).toBe('')
})

it('closes the comment action menu and cancels the in-place comment edit without sending data', async () => {
  vi.mocked(api).mockResolvedValue([])
  const comment = { id: 19, body: 'Comment', version: 1, author: fixture().author, url: '/comment',
    can_edit: true, reactions: [], pending: false, created_at: fixture().created_at } as Comment
  const r = await router()
  const wrapper = mount(CommentCard, { props: { comment, item: fixture(), repository }, global: { plugins: [r] } })
  await flushPromises()
  wrapper.get('details').element.setAttribute('open', '')
  await wrapper.get('.comment-menu button').trigger('click')
  expect(wrapper.get('details').attributes('open')).toBeUndefined()
  await wrapper.get('textarea').setValue('Cancelled comment')
  await wrapper.get('form').trigger('keydown', { key: 'Escape' })
  expect(wrapper.find('textarea').exists()).toBe(false)
  expect(wrapper.text()).toContain('Comment')
  expect(vi.mocked(api).mock.calls.some(call => call[1] === 'PATCH')).toBe(false)
})

it('keeps line comments through polling and refresh and clears after publishing', async () => {
  const comparison = {
    base_sha: 'a'.repeat(40), head_sha: 'b'.repeat(40), merge_base: 'a'.repeat(40),
    files: [{ path: 'a.txt', status: 'M', additions: 1, deletions: 1 }],
    total: 1, page: 1, page_size: 100, commits: [], additions: 1, deletions: 1,
  } as Comparison
  const pull = { base_sha: comparison.base_sha, head_sha: comparison.head_sha } as Pull
  vi.mocked(api).mockImplementation(async path => path.includes('/diff?')
    ? { ...comparison, patch: '@@ -1 +1 @@\n-old\n+new\n' } : {})
  const r = await router('/repositories/alice/project/pr/2/changes')
  const props = { repository, comparison, pull, number: 2, canComment: true }
  const first = mount(DiffViewer, { props, global: { plugins: [r] } })
  await flushPromises()
  await first.get('tr.add .diff-line button').trigger('click')
  await first.get('textarea').setValue('Line draft')
  await first.setProps({ comparison: { ...comparison } })
  await flushPromises()
  expect(first.get('textarea').element.value).toBe('Line draft')
  first.unmount()
  await r.push('/repositories/alice/project/pr/2/changes')
  const restored = mount(DiffViewer, { props, global: { plugins: [r] } })
  await flushPromises()
  expect(restored.get('textarea').element.value).toBe('Line draft')
  await restored.get('.line-comment-form').trigger('submit')
  await flushPromises()
  expect(restored.find('textarea').exists()).toBe(false)
  restored.unmount()
  await r.push('/repositories/alice/project/pr/2/changes')
  const submitted = mount(DiffViewer, { props, global: { plugins: [r] } })
  await flushPromises()
  expect(submitted.find('textarea').exists()).toBe(false)
})

it('keeps restored review text bound to its original commit until the reviewer acknowledges new changes', async () => {
  const item = { ...fixture(), kind: 'pull', url: '/repositories/alice/project/pr/1' } as WorkItem
  const comparison = { base_sha: 'a', head_sha: 'new-head', files: [], total: 0, commits: [], page: 1, page_size: 100 } as unknown as Comparison
  const pull = {
    base_sha: 'a', head_sha: 'new-head', base_ref: 'main', head_ref: 'feature', reviewers: [], reviews: [],
    checks: [], merge_checks: { merge: { status: 'clean' } }, policy: { methods: ['merge'] }, reasons: [], can_merge: true,
  } as unknown as Pull
  localStorage.setItem('devops:discussion-draft:v1:1:12:item:1:review', JSON.stringify({
    savedAt: Date.now(), value: { body: 'Review written before the push', state: 'commented', head: 'old-head' },
  }))
  vi.mocked(api).mockImplementation(async path => {
    if (path.endsWith('/items/1')) return item
    if (path.endsWith('/pulls/1')) return pull
    if (path.endsWith('/diff')) return comparison
    if (path.includes('/timeline')) return { items: [], total: 0, page: 1, page_size: 50, pinned_comments: [] }
    if (path.endsWith('/branches')) return { items: [] }
    return []
  })
  const r = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/repositories/:owner/:name/pr/:number', component: {}, meta: { repositoryTab: 'pr' } },
    { path: '/:rest(.*)*', component: {} },
  ] })
  await r.push('/repositories/alice/project/pr/1')
  const wrapper = mount(ItemDetailView, { props: { repository }, global: { plugins: [r] } })
  await flushPromises()
  expect(wrapper.get('.review-form textarea').element.value).toBe('Review written before the push')
  expect(wrapper.get('.review-form button[type="submit"]').attributes('disabled')).toBeDefined()
  expect(wrapper.get('.review-form').text()).toContain('源分支已有新提交')
  await wrapper.get('.review-form .text-button').trigger('click')
  await wrapper.get('.review-form').trigger('submit')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/repositories/12/pulls/1/reviews', 'POST', {
    head_sha: 'new-head', state: 'commented', body: 'Review written before the push',
  })
  expect(wrapper.get('.review-form textarea').element.value).toBe('')
})

const twoFiles = () => ({
  base_sha: 'a'.repeat(40), head_sha: 'b'.repeat(40), merge_base: 'a'.repeat(40),
  files: ['a.txt', 'b.txt'].map(path => ({ path, status: 'A', additions: 1, deletions: 0 })),
  total: 2, page: 1, page_size: 100, commits: [], additions: 2, deletions: 0,
}) as Comparison

it('switches new PR diff files without rebuilding the form or losing body and title', async () => {
  const comparison = twoFiles()
  vi.mocked(api).mockImplementation(async path => {
    if (path.includes('/by-name/')) return repository
    if (path.endsWith('/branches')) return { items: [{ name: 'main' }, { name: 'feature' }] }
    if (path.includes('/compare?')) return { ...comparison, patch: '@@ -0,0 +1 @@\n+added\n' }
    return []
  })
  const r = createRouter({ history: createMemoryHistory(), routes: [{
    path: '/repositories/:owner/:name', component: RepositoryDetailView,
    children: [{ path: 'pr/new', component: NewItemView, meta: { repositoryTab: 'pr' } }],
  }] })
  await r.push('/repositories/alice/project/pr/new?head=feature')
  const wrapper = mount(RouterView, { global: { plugins: [r] } })
  await flushPromises()
  const editor = wrapper.get('.discussion-editor textarea').element
  await wrapper.get('.new-item-form input').setValue('Keep title')
  await wrapper.get('.discussion-editor textarea').setValue('Keep body')
  await wrapper.findAll('.diff-tree > button')[1]!.trigger('click')
  await flushPromises()
  expect(r.currentRoute.value.query).toEqual({ head: 'feature', file: 'b.txt' })
  expect(wrapper.get('.discussion-editor textarea').element).toBe(editor)
  expect(wrapper.get('.discussion-editor textarea').element.value).toBe('Keep body')
  expect(wrapper.get('.new-item-form input').element.value).toBe('Keep title')
  await r.replace({ query: { head: 'feature', file: 'a.txt' } })
  await flushPromises()
  expect(wrapper.get('.diff-tree > button.active').text()).toContain('a.txt')
  expect(vi.mocked(api).mock.calls.filter(([path]) => path.endsWith('/branches'))).toHaveLength(1)
  expect(vi.mocked(api).mock.calls.some(([, method]) => method === 'PATCH')).toBe(false)
})

it('preserves in-place PR edits and diff line drafts when navigating between PR sections', async () => {
  const item = { ...fixture(), kind: 'pull', url: '/repositories/alice/project/pr/1' }
  const comparison = twoFiles()
  const pull = { ...comparison, base_ref: 'main', head_ref: 'feature', reviewers: [], reviews: [], checks: [],
    merge_checks: { merge: { status: 'clean' } }, policy: { methods: ['merge'] }, reasons: [], can_merge: true }
  vi.mocked(api).mockImplementation(async path => {
    if (path.includes('/by-name/')) return repository
    if (path.endsWith('/items/1')) return item
    if (path.endsWith('/pulls/1')) return pull
    if (path.includes('/diff')) return { ...comparison, patch: '@@ -0,0 +1 @@\n+added\n' }
    if (path.includes('/timeline')) return { items: [], total: 0, page: 1, page_size: 50, pinned_comments: [] }
    if (path.endsWith('/branches')) return { items: [] }
    return []
  })
  const r = createRouter({ history: createMemoryHistory(), routes: [{
    path: '/repositories/:owner/:name', component: RepositoryDetailView,
    children: [
      { path: 'pr/:number', component: ItemDetailView, meta: { repositoryTab: 'pr' } },
      { path: 'pr/:number/:section', component: ItemDetailView, meta: { repositoryTab: 'pr' } },
    ],
  }] })
  await r.push('/repositories/alice/project/pr/1')
  const wrapper = mount(RouterView, { global: { plugins: [r] } })
  await flushPromises()
  await wrapper.get('[aria-label="编辑正文"]').trigger('click')
  const editor = wrapper.get('#issue-body textarea').element
  await wrapper.get('#issue-body textarea').setValue('Unsent PR body')
  await wrapper.get('.pull-tabs a[href$="changes"]').trigger('click')
  await flushPromises()
  await wrapper.get('tr.add .diff-line button').trigger('click')
  await flushPromises()
  await wrapper.get('.line-comment-form textarea').setValue('Unsent line comment')
  const before = vi.mocked(api).mock.calls.filter(([path]) => path.includes('/timeline')).length
  await r.push('/repositories/alice/project/pr/1/checks?file=a.txt')
  await r.push('/repositories/alice/project/pr/1?file=a.txt')
  await flushPromises()
  expect(wrapper.get('#issue-body textarea').element).toBe(editor)
  expect(wrapper.get('#issue-body textarea').element.value).toBe('Unsent PR body')
  expect(wrapper.get('.item-layout').isVisible()).toBe(true)
  await r.push('/repositories/alice/project/pr/1/changes?file=a.txt')
  await flushPromises()
  expect(wrapper.get('.line-comment-form textarea').element.value).toBe('Unsent line comment')
  expect(wrapper.get('.diff-viewer').isVisible()).toBe(true)
  expect(vi.mocked(api).mock.calls.filter(([path]) => path.includes('/timeline'))).toHaveLength(before)
})

it('keeps diff height while loading, ignores stale patches, and keeps revision-specific drafts', async () => {
  const comparison = twoFiles(), pending = new Map<string, (value: unknown) => void>()
  let defer = false
  vi.mocked(api).mockImplementation(async path => {
    if (defer) return new Promise(resolve => { pending.set(new URLSearchParams(path.split('?')[1]).get('path')!, resolve) })
    return { ...comparison, patch: '@@ -0,0 +1 @@\n+file a\n' }
  })
  const r = await router('/repositories/alice/project/pr/2/changes')
  const wrapper = mount(DiffViewer, { props: { repository, comparison, number: 2,
    pull: { ...comparison } as Pull, canComment: true }, global: { plugins: [r] } })
  await flushPromises()
  const region = wrapper.get('[aria-busy]')
  vi.spyOn(region.element, 'getBoundingClientRect').mockReturnValue({ height: 480 } as DOMRect)
  await wrapper.get('tr.add .diff-line button').trigger('click')
  await flushPromises()
  expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled()
  await wrapper.get('textarea').setValue('A draft')
  defer = true
  await wrapper.findAll('.diff-tree > button')[1]!.trigger('click')
  await flushPromises()
  expect(region.attributes('style')).toContain('min-height: 480px')
  await wrapper.findAll('.diff-tree > button')[0]!.trigger('click')
  await flushPromises()
  pending.get('a.txt')!({ ...comparison, patch: '@@ -0,0 +1 @@\n+current a\n' })
  await flushPromises()
  pending.get('b.txt')!({ ...comparison, patch: '@@ -0,0 +1 @@\n+stale b\n' })
  await flushPromises()
  expect(wrapper.text()).toContain('current a')
  expect(wrapper.text()).not.toContain('stale b')
  expect(wrapper.get('textarea').element.value).toBe('A draft')
  expect(region.attributes('aria-busy')).toBe('false')
  expect(region.attributes('style')).not.toContain('480px')
})

it('ignores late notification responses after changing the unread filter', async () => {
  let first!: (value: unknown) => void
  const empty = { items: [], total: 0, page: 1, page_size: 30, unread_count: 0 }
  vi.mocked(api).mockImplementation(async path => path.endsWith('unread=true')
    ? new Promise(resolve => { first = resolve }) : empty)
  const r = await router('/notifications')
  const wrapper = mount(NotificationsView, { global: { plugins: [r] } })
  await wrapper.findAll('header button')[1]!.trigger('click')
  await flushPromises()
  first({ ...empty, unread_count: 99 })
  await flushPromises()
  expect(wrapper.get('h1').text()).toContain('0 条未读')
  expect(wrapper.findAll('header button')[1]!.attributes('aria-pressed')).toBe('true')
})
