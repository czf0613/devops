import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { api } from '../src/api'
import { auth } from '../src/auth'
import type { Repository } from '../src/repositories'
import RepositoryCIView from '../src/views/RepositoryCIView.vue'
import CIRunView from '../src/views/CIRunView.vue'
import RepositoryCISettings from '../src/components/RepositoryCISettings.vue'
import CICommitStatus from '../src/components/CICommitStatus.vue'

vi.mock('../src/api', async original => ({ ...(await original<typeof import('../src/api')>()), api: vi.fn() }))
enableAutoUnmount(afterEach)
beforeEach(() => { vi.mocked(api).mockReset(); auth.user = null })
const repository = { id: 12, name: 'example', owner: { username: 'owner' }, archived: false, permissions: { is_owner: true, access: 'write' } } as Repository
const config = { enabled: true, can_run: true, can_configure: true, pools: ['default'], available_pools: ['default'] }
async function router() {
  const instance = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }] })
  await instance.push('/repositories/owner/example/ci/1')
  await instance.isReady()
  return instance
}
it('shows configuration only to owners and execution only to writers', async () => {
  vi.mocked(api).mockImplementation(async path => path.endsWith('/ci') ? { ...config, can_configure: false, can_run: false } : { items: [], total: 0 })
  const wrapper = mount(RepositoryCIView, { props: { repository }, global: { plugins: [await router()] } })
  await flushPromises()
  expect(wrapper.find('[role=switch]').exists()).toBe(false)
  expect(wrapper.find('.run-form').exists()).toBe(false)
  expect(wrapper.text()).toContain('还没有运行记录')
})
it('keeps the saved enable state when authorization update fails', async () => {
  vi.mocked(api).mockImplementation(async (path, method) => {
    if (method === 'PUT') throw new Error('资源池授权失败')
    return path.endsWith('/ci') ? config : { items: [], total: 0 }
  })
  const wrapper = mount(RepositoryCISettings, { props: { repository }, global: { plugins: [await router()] } })
  await flushPromises()
  await wrapper.get('[role=switch]').trigger('click')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/repositories/12/ci', 'PUT', { enabled: false, pools: ['default'] })
  expect(wrapper.get('[role=switch]').attributes('aria-checked')).toBe('true')
  expect(wrapper.text()).toContain('资源池授权失败')
})
it('discards stale responses after switching repositories', async () => {
  let resolve!: (value: unknown) => void
  vi.mocked(api).mockImplementation(async path => {
    if (path.includes('/12/') && path.includes('/runs')) return new Promise(done => { resolve = done })
    return path.endsWith('/ci') ? config : { items: [], total: 0 }
  })
  const wrapper = mount(RepositoryCIView, { props: { repository }, global: { plugins: [await router()] } })
  await flushPromises()
  await wrapper.setProps({ repository: { ...repository, id: 13 } })
  await flushPromises()
  resolve({ items: [{ id: 100, name: 'Old private run', state: 'success', ref: 'main', sha: 'abc', created_at: '2026-01-01' }], total: 1 })
  await flushPromises()
  expect(wrapper.text()).not.toContain('Old private run')
})
it('renders build output as text and offers artifacts under the repository ACL', async () => {
  const run = { id: 1, name: 'Test', state: 'success', ref: 'main', sha: 'a'.repeat(40), created_at: '2026-01-01', jobs: [{ id: 1, name: 'build', state: 'success', environment: { os: 'macos', pool: 'default' }, needs: [], attempts: [{ id: 5, worker: 'Mac', number: 1, state: 'success', steps: [], artifacts: [{ id: 7, name: 'build.txt', size: 12, expires_at: '2026-12-01' }] }] }] }
  vi.mocked(api).mockImplementation(async path => path.includes('/logs') ? { chunks: [{ text: '<img src=x onerror="evil()">' }], next: 1 } : run)
  const wrapper = mount(CIRunView, { props: { repository }, global: { plugins: [await router()] } })
  await flushPromises()
  expect(wrapper.get('.ci-log').text()).toContain('<img src=x')
  expect(wrapper.find('.ci-log img').exists()).toBe(false)
  expect(wrapper.get('.artifacts a').attributes('href')).toBe('/api/repositories/12/ci/artifacts/7')
})

it('navigates workflows and manually runs the selected workflow and branch', async () => {
  const workflow = { path: '.github/workflows/checks.yml', name: 'Checks', manual: true, inputs: {}, error: '' }
  vi.mocked(api).mockImplementation(async (path, method) => {
    if (method === 'POST') return { id: 9 }
    if (path.endsWith('/ci')) return config
    if (path.endsWith('/branches')) return { items: [{ name: 'main', commit: 'a'.repeat(40) }] }
    if (path.includes('/workflows?')) return { items: [workflow] }
    return { items: [], total: 0 }
  })
  const navigation = await router()
  await navigation.push('/repositories/owner/example/ci?workflow=.github/workflows/checks.yml')
  const wrapper = mount(RepositoryCIView, { props: { repository }, global: { plugins: [navigation] } })
  await flushPromises()
  expect(wrapper.find('[role=switch]').exists()).toBe(false)
  expect(wrapper.find('a[href$="/settings#ci"]').exists()).toBe(true)
  await wrapper.findAll('button').find(button => button.text() === '运行工作流')!.trigger('click')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/repositories/12/ci/runs', 'POST', { ref: 'main', workflow: workflow.path, inputs: {} })
  expect(navigation.currentRoute.value.path).toBe('/repositories/owner/example/ci/9')
})

it('shows exact commit checks, never reuses success after switching commits', async () => {
  let resolve!: (value: unknown) => void
  vi.mocked(api).mockImplementation(async path => path.endsWith('a'.repeat(40)) ? new Promise(done => { resolve = done }) : { state: 'none', items: [] })
  const wrapper = mount(CICommitStatus, { props: { repository, sha: 'a'.repeat(40) }, global: { plugins: [await router()] } })
  await flushPromises()
  await wrapper.setProps({ sha: 'b'.repeat(40) })
  await flushPromises()
  resolve({ state: 'success', items: [] })
  await flushPromises()
  expect(wrapper.text()).toContain('尚无 CI 检查')
  expect(wrapper.text()).not.toContain('CI 检查通过')
})
