import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { api, type User } from '../src/api'
import { auth } from '../src/auth'
import type { DashboardItem } from '../src/dashboard'
import { recentRepositoryIds, rememberRepository } from '../src/recent-repositories'
import DashboardItems from '../src/components/DashboardItems.vue'
import RecentRepositories from '../src/components/RecentRepositories.vue'
import HomeView from '../src/views/HomeView.vue'
import RepositoryDetailView from '../src/views/RepositoryDetailView.vue'
import RepositoriesView from '../src/views/RepositoriesView.vue'
import type { Repository } from '../src/repositories'

vi.mock('../src/api', async original => ({ ...(await original<typeof import('../src/api')>()), api: vi.fn() }))
enableAutoUnmount(afterEach)
const user = (id = 1) => ({ id, username: `user${id}`, display_name: `User ${id}`, roles: ['member'] }) as User
const repository = { id: 7, full_name: 'alice/project', name: 'project', owner: { username: 'alice' }, url: '/repositories/alice/project', archived: false }
const item = (title: string, kind: 'issue' | 'pull' = 'issue') => ({
  id: 1, number: 3, repository_id: 7, repository, title, kind, state: 'open', close_reason: null,
  url: `${repository.url}/${kind === 'pull' ? 'pr' : 'issue'}/3`, author: user(), updated_at: new Date().toISOString(), draft: false,
}) as DashboardItem
const page = (items: DashboardItem[] = [], total = items.length) => ({ items, total, page: 1, page_size: 5 })
beforeEach(() => { vi.mocked(api).mockReset(); auth.user = user(); auth.loaded = true; localStorage.clear() })
afterEach(() => { auth.user = null; document.body.innerHTML = ''; vi.useRealTimers() })
async function router(path = '/') {
  const r = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/:pathMatch(.*)*', component: {} },
    { path: '/repositories/:owner/:name/code', component: {}, meta: { repositoryTab: 'code' } },
  ] })
  await r.push(path)
  return r
}

it('loads sections independently, offers a local retry, and does not mark notifications read', async () => {
  let failed = true
  vi.mocked(api).mockImplementation(async path => {
    if (path.includes('kind=pull')) { if (failed) throw new Error('PR 暂时无法读取'); return page([item('需要审阅', 'pull')]) }
    if (path.includes('kind=issue')) return page([item('修复搜索')])
    if (path.includes('/activity')) return { items: [] }
    if (path.includes('/notifications')) return { ...page(), unread_count: 0 }
    throw new Error(`Unexpected ${path}`)
  })
  const r = await router()
  const wrapper = mount(HomeView, { global: { plugins: [r] } })
  await flushPromises()
  expect(wrapper.get('[aria-label="Pull requests"]').text()).toContain('PR 暂时无法读取')
  expect(wrapper.get('[aria-label="Issues"]').text()).toContain('修复搜索')
  expect(wrapper.get('[aria-label="未读通知"]').text()).toContain('没有未读通知')
  expect(wrapper.get('[aria-label="最近动态"]').text()).toContain('还没有协作动态')
  expect(wrapper.get('a[href="/repositories?create=1"]').text()).toBe('新建仓库')
  failed = false
  await wrapper.get('[aria-label="Pull requests"] .error-message button').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('需要审阅')
  expect(vi.mocked(api).mock.calls).toHaveLength(5)
  expect(vi.mocked(api).mock.calls.every(call => call.length === 1)).toBe(true)
})

it('keeps filters in history and ignores a response from an older selection', async () => {
  let resolveOld!: (value: unknown) => void
  let delayed = true
  vi.mocked(api).mockImplementation(path => {
    if (path.includes('scope=related') && delayed) return new Promise(resolve => { resolveOld = resolve })
    return Promise.resolve(page([item(path.includes('scope=authored') ? '我创建的事项' : '相关事项')]))
  })
  const r = await router('/?pr_scope=review_requested')
  const wrapper = mount(DashboardItems, { props: { kind: 'issue' }, global: { plugins: [r] } })
  await wrapper.get('.item-filters button:last-child').trigger('click')
  await flushPromises()
  expect(r.currentRoute.value.query).toEqual({ pr_scope: 'review_requested', issue_scope: 'authored' })
  expect(wrapper.text()).toContain('我创建的事项')
  resolveOld(page([item('旧请求内容')]))
  await flushPromises()
  expect(wrapper.text()).not.toContain('旧请求内容')
  expect(wrapper.get('.view-all').attributes('href')).toBe('/work/issues?scope=authored')
  delayed = false
  r.back()
  await flushPromises()
  expect(wrapper.get('.item-filters button').attributes('aria-pressed')).toBe('true')
  expect(wrapper.text()).toContain('相关事项')
})

it('restores full list search, status and pagination and forces pending reviews to open', async () => {
  vi.mocked(api).mockResolvedValue(page([item('匹配结果')], 60))
  const r = await router('/work/issues?scope=assigned&state=closed&q=hello&page=2')
  const wrapper = mount(DashboardItems, { props: { kind: 'issue', full: true }, global: { plugins: [r] } })
  await flushPromises()
  expect(vi.mocked(api).mock.lastCall?.[0]).toContain('scope=assigned&state=closed&q=hello&page=2&page_size=25')
  expect((wrapper.get('input').element as HTMLInputElement).value).toBe('hello')
  await wrapper.get('.pager button:last-child').trigger('click')
  await flushPromises()
  expect(r.currentRoute.value.query.page).toBe('3')
  await wrapper.get('input').setValue('new title')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(r.currentRoute.value.query.page).toBeUndefined()
  expect(r.currentRoute.value.query.q).toBe('new title')
  r.back()
  await flushPromises()
  expect((wrapper.get('input').element as HTMLInputElement).value).toBe('hello')
  await r.push('/work/pulls?scope=review_requested&state=closed&page=1.5')
  await wrapper.setProps({ kind: 'pull' })
  await flushPromises()
  expect(vi.mocked(api).mock.lastCall?.[0]).toContain('scope=review_requested&state=open&q=&page=1')
})

it('clears visible data on account change and ignores the old account response', async () => {
  let resolveOld!: (value: unknown) => void
  vi.mocked(api).mockImplementation(() => auth.user?.id === 1
    ? new Promise(resolve => { resolveOld = resolve }) : Promise.resolve(page([item('第二个账号的事项')])))
  const r = await router()
  const wrapper = mount(DashboardItems, { props: { kind: 'issue' }, global: { plugins: [r] } })
  auth.user = user(2)
  await flushPromises()
  resolveOld(page([item('第一个账号的事项')]))
  await flushPromises()
  expect(wrapper.text()).toContain('第二个账号的事项')
  expect(wrapper.text()).not.toContain('第一个账号的事项')
  auth.user = null
  await flushPromises()
  expect(wrapper.text()).not.toContain('第二个账号的事项')
})

it('revalidates on focus and clears former results after access is removed', async () => {
  vi.mocked(api).mockResolvedValueOnce(page([item('旧仓库事项')])).mockResolvedValue(page())
  const r = await router()
  const wrapper = mount(DashboardItems, { props: { kind: 'issue' }, global: { plugins: [r] } })
  await flushPromises()
  expect(wrapper.text()).toContain('旧仓库事项')
  window.dispatchEvent(new Event('focus'))
  await flushPromises()
  expect(wrapper.text()).not.toContain('旧仓库事项')
  expect(wrapper.text()).toContain('暂时没有与你相关的开放 Issue')
})

it('stores only bounded per-account IDs and tolerates invalid or unavailable storage', () => {
  for (let i = 1; i <= 25; i++) rememberRepository(1, i)
  rememberRepository(1, 10)
  rememberRepository(2, 100)
  expect(recentRepositoryIds(1)).toHaveLength(20)
  expect(recentRepositoryIds(1).slice(0, 2)).toEqual([10, 25])
  expect(recentRepositoryIds(2)).toEqual([100])
  localStorage.setItem('devops.recent-repositories.3', '[1,1,"name",-1,null,2.5]')
  expect(recentRepositoryIds(3)).toEqual([1])
  const broken = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('disabled') })
  expect(recentRepositoryIds(1)).toEqual([])
  broken.mockRestore()
})

it('shows server-authorized recent repositories only and debounces repository search', async () => {
  rememberRepository(1, 999)
  rememberRepository(1, 7)
  vi.mocked(api).mockResolvedValue({ items: [repository] })
  const r = await router()
  const wrapper = mount(RecentRepositories, { props: { userId: 1 }, global: { plugins: [r] } })
  await flushPromises()
  expect(vi.mocked(api).mock.lastCall?.[0]).toContain('recent=7&recent=999')
  expect(wrapper.findAll('li')).toHaveLength(1)
  expect(wrapper.text()).toContain('alice/project')
  vi.useFakeTimers()
  await wrapper.get('input').setValue('ali')
  await wrapper.get('input').setValue('alice/project')
  expect(vi.mocked(api).mock.calls).toHaveLength(1)
  await vi.advanceTimersByTimeAsync(250)
  await flushPromises()
  expect(vi.mocked(api).mock.lastCall?.[0]).toContain('q=alice%2Fproject')
  expect(vi.mocked(api).mock.calls).toHaveLength(2)
})

it('remembers only successfully loaded repositories and the create link opens the existing form', async () => {
  const detail = { ...repository, permissions: { access: 'write', is_owner: true, is_admin: false } } as unknown as Repository
  vi.mocked(api).mockResolvedValue(detail)
  const r = await router('/repositories/alice/project/code')
  const wrapper = mount(RepositoryDetailView, { global: { plugins: [r] } })
  await flushPromises()
  expect(recentRepositoryIds(1)).toEqual([7])
  vi.mocked(api).mockRejectedValueOnce(new Error('无权访问'))
  await r.push('/repositories/hidden/private/code')
  await flushPromises()
  expect(recentRepositoryIds(1)).toEqual([7])
  wrapper.unmount()
  vi.mocked(api).mockResolvedValue(page())
  await r.push('/repositories?create=1')
  const list = mount(RepositoriesView, { global: { plugins: [r] } })
  await flushPromises()
  expect(list.text()).toContain('创建私有仓库')
  expect(list.find('input[placeholder="例如：devops-platform"]').exists()).toBe(true)
})
