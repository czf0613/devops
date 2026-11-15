import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { api } from '../src/api'
import RepositoryActivity from '../src/components/RepositoryActivity.vue'
import GitAuthorizationList from '../src/components/GitAuthorizationList.vue'
import RepositoriesView from '../src/views/RepositoriesView.vue'

vi.mock('../src/api', async original => ({
  ...(await original<typeof import('../src/api')>()), api: vi.fn(),
}))
enableAutoUnmount(afterEach)
beforeEach(() => { vi.mocked(api).mockReset() })
afterEach(() => { document.body.innerHTML = '' })
const activity = {
  weeks: [{ start: '2026-09-14', commits: 0 }, { start: '2026-09-21', commits: 2 }, { start: '2026-09-28', commits: 5 }],
  total: 7, truncated: false, commit_limit: 10000,
}

it('renders real weekly totals, distinguishes empty activity from failure, and marks limited data', async () => {
  vi.mocked(api).mockResolvedValueOnce(activity)
  const wrapper = mount(RepositoryActivity, { props: { repositoryId: 12 } })
  await flushPromises()
  expect(wrapper.get('svg').attributes('aria-label')).toBe('近 12 周 7 次提交')
  expect(wrapper.get('figure').attributes('title')).toContain('共享提交只计一次')
  expect(wrapper.findAll('circle title').map(title => title.text())).toContain('2026-09-21 当周：2 次提交')
  vi.mocked(api).mockResolvedValueOnce({ ...activity, total: 0, weeks: activity.weeks.map(week => ({ ...week, commits: 0 })) })
  await wrapper.setProps({ repositoryId: 13 })
  await flushPromises()
  expect(wrapper.get('figcaption').text()).toBe('近 12 周 0 次提交')
  vi.mocked(api).mockRejectedValueOnce(new Error('storage unavailable'))
  await wrapper.setProps({ repositoryId: 14 })
  await flushPromises()
  expect(wrapper.find('svg').exists()).toBe(false)
  expect(wrapper.text()).toContain('提交趋势暂不可用')
  vi.mocked(api).mockResolvedValueOnce({ ...activity, truncated: true })
  await wrapper.setProps({ repositoryId: 15 })
  await flushPromises()
  expect(wrapper.get('figcaption').text()).toBe('近 12 周至少 7 次提交')
})

it('never replaces a different repository chart with a late response', async () => {
  let resolve!: (value: unknown) => void
  vi.mocked(api).mockImplementationOnce(() => new Promise(done => { resolve = done }))
  const wrapper = mount(RepositoryActivity, { props: { repositoryId: 12 } })
  vi.mocked(api).mockResolvedValueOnce({ ...activity, total: 20 })
  await wrapper.setProps({ repositoryId: 13 })
  await flushPromises()
  resolve(activity)
  await flushPromises()
  expect(wrapper.get('figcaption').text()).toBe('近 12 周 20 次提交')
})

it('keeps repository metadata and long descriptions alongside an independent activity chart', async () => {
  const description = '1234567890'.repeat(200)
  vi.mocked(api).mockImplementation(async path => {
    if (path.startsWith('/repositories?')) return { items: [{
      id: 12, name: 'example', full_name: 'owner/example', owner: { username: 'owner' }, description,
      archived: true, member_count: 4, created_at: '2026-09-28T00:00:00Z',
      permissions: { is_owner: false, is_admin: true, access: 'read', role: '开发人员' },
    }], page: 1, total: 1 }
    if (path === '/repositories/12/activity') return activity
    throw new Error(path)
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/repositories', component: RepositoriesView }] })
  await router.push('/repositories')
  await router.isReady()
  const wrapper = mount(RepositoriesView, { global: { plugins: [router] } })
  await flushPromises()
  const row = wrapper.get('[aria-label="工程仓库列表"] li')
  expect(row.get('.repository-description').attributes('title')).toBe(description)
  expect(row.get('a').attributes('href')).toBe('/repositories/owner/example')
  expect(row.text()).toContain('仓库管理员')
  expect(row.text()).toContain('只读')
  expect(row.text()).toContain('4 位成员')
  expect(row.text()).toContain('已归档')
  expect(row.get('figcaption').text()).toContain('7 次提交')
})

const grant = { id: 1, client_name: 'Active client', created_at: '2026-01-01T00:00:00Z', expires_at: '2099-01-01T00:00:00Z', revoked_at: null }
it('hides revoked authorizations, retains expiry feedback, and removes a grant after confirmed revocation', async () => {
  const expired = { ...grant, id: 2, client_name: 'Expired client', expires_at: '2000-01-01T00:00:00Z' }
  vi.mocked(api).mockResolvedValueOnce([grant, expired, { ...grant, id: 3, client_name: 'Revoked client', revoked_at: '2026-01-02T00:00:00Z' }])
  const wrapper = mount(GitAuthorizationList, { attachTo: document.body })
  await flushPromises()
  expect(wrapper.text()).not.toContain('Revoked client')
  expect(wrapper.text()).toContain('已过期')
  expect(wrapper.findAll('.session-list button')).toHaveLength(1)
  await wrapper.get('.session-list button').trigger('click')
  vi.mocked(api).mockResolvedValueOnce({ message: 'Git 授权已撤销' }).mockResolvedValueOnce([expired])
  document.querySelectorAll<HTMLButtonElement>('dialog[open] button')[1]!.click()
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/me/git-authorizations/1', 'DELETE')
  expect(wrapper.text()).not.toContain('Active client')
  expect(wrapper.text()).toContain('Expired client')
})

it('shows an empty authorization list after revocations without deleting history in the UI', async () => {
  vi.mocked(api).mockResolvedValueOnce([{ ...grant, revoked_at: '2026-01-02T00:00:00Z' }])
  const wrapper = mount(GitAuthorizationList)
  await flushPromises()
  expect(wrapper.text()).toContain('暂无未撤销的客户端授权')
  expect(wrapper.find('.session-list').exists()).toBe(false)
})
