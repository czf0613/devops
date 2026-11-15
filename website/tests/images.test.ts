import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { api } from '../src/api'
import { auth } from '../src/auth'
import ImagesView from '../src/views/ImagesView.vue'
import RegistryImages from '../src/components/RegistryImages.vue'
import RegistryNamespaceCreate from '../src/components/RegistryNamespaceCreate.vue'
import type { Repository } from '../src/repositories'

vi.mock('../src/api', async original => ({ ...(await original<typeof import('../src/api')>()), api: vi.fn() }))
enableAutoUnmount(afterEach)
const repository = { id: 12, full_name: 'owner/project', name: 'project', owner: { username: 'owner' }, archived: false, permissions: { is_owner: false, access: 'read' } } as Repository
const namespace = { id: 3, name: 'team', repository_id: 12, enabled: true, version: 1, repository }
const overview = { enabled: true, host: 'registry.example.com', items: [namespace], total: 1, page_size: 20 }
beforeEach(() => {
  auth.user = { id: 2, email: 'reader@example.com', roles: ['member'] } as NonNullable<typeof auth.user>
  vi.mocked(api).mockReset()
  vi.mocked(api).mockImplementation(async path => {
    if (path.startsWith('/me/registry/namespaces?')) return overview
    if (path.startsWith('/me/registry/repositories?')) return { items: [{ ...repository, permissions: { is_owner: true } }], total: 1 }
    if (path.includes('/images?')) return { items: [{ name: 'nested/app', reference: 'registry.example.com/team/nested/app' }], total: 1 }
    if (path.includes('/tags?')) return { items: [{ tag: 'v1', digest: 'sha256:' + 'a'.repeat(64), platforms: ['linux/amd64', 'linux/arm64'] }], total: 1 }
    return namespace
  })
})
afterEach(() => { document.body.innerHTML = '' })
async function view(path = '/images') {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/images', component: ImagesView }] })
  await router.push(path); await router.isReady()
  const wrapper = mount(ImagesView, { attachTo: document.body, global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}
it('uses the same namespace image browser and member permissions as a repo tab', async () => {
  const { wrapper } = await view()
  expect(wrapper.text()).toContain('owner/project')
  await wrapper.get('.my-namespaces button').trigger('click'); await flushPromises()
  expect(wrapper.findComponent(RegistryImages).exists()).toBe(true)
  await wrapper.get('.image-list button').trigger('click'); await flushPromises()
  expect(api).toHaveBeenCalledWith('/repositories/12/registry/namespaces/3/tags?image=nested%2Fapp&page=1')
  expect(wrapper.text()).toContain('linux/amd64、linux/arm64')
  expect(wrapper.text()).not.toContain('删除版本')
  expect(wrapper.get('.namespace-context a').attributes('href')).toBe('/repositories/owner/project/registry')
})
it('creates a namespace only after selecting a permitted repository using the shared form', async () => {
  const { wrapper } = await view()
  await wrapper.get('[aria-controls="create-namespace"]').trigger('click'); await flushPromises()
  expect(wrapper.findComponent(RegistryNamespaceCreate).exists()).toBe(false)
  await wrapper.get('#create-namespace [role="combobox"]').trigger('click')
  document.querySelectorAll<HTMLElement>('[role="option"]')[1]!.click(); await flushPromises()
  expect(wrapper.findComponent(RegistryNamespaceCreate).exists()).toBe(true)
  await wrapper.get('.namespace-create input').setValue('new-team')
  await wrapper.get('.namespace-create').trigger('submit'); await flushPromises()
  expect(api).toHaveBeenCalledWith('/repositories/12/registry/namespaces', 'POST', { name: 'new-team' })
  expect(wrapper.find('#create-namespace').exists()).toBe(false)
})
it('keeps filters in the URL and discards pending data and selections on an account change', async () => {
  const { wrapper, router } = await view('/images?q=team&state=bound&page=2')
  expect(api).toHaveBeenCalledWith('/me/registry/namespaces?q=team&state=bound&page=2')
  await wrapper.get('.my-namespaces button').trigger('click'); await flushPromises()
  let resolve!: (value: unknown) => void
  vi.mocked(api).mockImplementationOnce(() => new Promise(done => { resolve = done }))
  await wrapper.findAll('button').find(button => button.text() === '刷新')!.trigger('click')
  vi.mocked(api).mockResolvedValue({ ...overview, items: [], total: 0 })
  auth.user = { id: 99 } as NonNullable<typeof auth.user>
  await flushPromises(); resolve(overview); await flushPromises()
  expect(wrapper.findComponent(RegistryImages).exists()).toBe(false)
  expect(wrapper.text()).not.toContain('owner/project')
  await router.push('/images?q=next'); await flushPromises()
  expect(wrapper.get('.namespace-filters input').element.value).toBe('next')
})
it('does not load images from unbound namespaces and offers owners a versioned rebind', async () => {
  const unbound = { ...namespace, enabled: false, repository: { ...repository, permissions: { is_owner: true } } }
  const fallback = vi.mocked(api).getMockImplementation()!
  vi.mocked(api).mockImplementation(async (path, ...args) => path.startsWith('/me/registry/namespaces?') ? { ...overview, items: [unbound] } : fallback(path, ...args))
  const { wrapper } = await view()
  await wrapper.get('.my-namespaces button').trigger('click'); await flushPromises()
  expect(wrapper.findComponent(RegistryImages).exists()).toBe(false)
  expect(api).not.toHaveBeenCalledWith(expect.stringContaining('/images?'))
  await wrapper.findAll('button').find(button => button.text() === '重新绑定到原仓库')!.trigger('click'); await flushPromises()
  expect(api).toHaveBeenCalledWith('/repositories/12/registry/namespaces/3', 'PUT', { enabled: true, expected_version: 1 })
})
