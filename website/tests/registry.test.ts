import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount, RouterLinkStub } from '@vue/test-utils'
import { auth } from '../src/auth'
import { api } from '../src/api'
import type { Repository } from '../src/repositories'
import RegistryCredentialList from '../src/components/RegistryCredentialList.vue'
import AppSelect from '../src/components/AppSelect.vue'
import { confirmWithCode } from './admin-helpers'
import RegistryAdminView from '../src/views/RegistryAdminView.vue'
import RepositoryRegistryView from '../src/views/RepositoryRegistryView.vue'

vi.mock('../src/api', async original => ({ ...(await original<typeof import('../src/api')>()), api: vi.fn() }))
enableAutoUnmount(afterEach)
beforeEach(() => { vi.mocked(api).mockReset(); auth.user = null })
afterEach(() => { document.body.innerHTML = '' })
const credential = { id: 1, name: 'Laptop', access: 'read', expires_at: '2099-01-01T00:00:00Z', last_used_at: null }
const overview = { enabled: true, host: 'registry.example.com', username: 'owner', items: [{ id: 3, name: 'team', repository_id: 12, enabled: true, version: 1 }] }
const repository = { id: 12, archived: false, permissions: { is_owner: true, is_admin: true, access: 'write' } } as Repository

it('creates a user-wide token, displays it only until dismissed, and confirms revocation', async () => {
  vi.mocked(api).mockResolvedValueOnce({ ...overview, items: [] })
  const wrapper = mount(RegistryCredentialList, { attachTo: document.body })
  await flushPromises()
  expect(wrapper.text()).toContain('docker login registry.example.com -u owner')
  await wrapper.get('input').setValue('Laptop')
  vi.mocked(api).mockResolvedValueOnce({ ...credential, token: 'dr_synthetic' })
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/me/registry-credentials', 'POST', { name: 'Laptop', access: 'read', days: 90 })
  expect(wrapper.get('.secret-token').text()).toBe('dr_synthetic')
  await wrapper.findAll('button').find(button => button.text() === '已保存，关闭')!.trigger('click')
  expect(wrapper.text()).not.toContain('dr_synthetic')
  await wrapper.get('.session-list button').trigger('click')
  expect(document.querySelector('dialog[open]')?.textContent).toContain('下一次请求被拒绝')
  vi.mocked(api).mockResolvedValueOnce(undefined)
  document.querySelectorAll<HTMLButtonElement>('dialog[open] button')[1]!.click()
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/me/registry-credentials/1', 'DELETE')
  expect(wrapper.find('.session-list').exists()).toBe(false)
})

it('discards token responses after the account changes', async () => {
  vi.mocked(api).mockResolvedValueOnce({ ...overview, items: [] })
  const wrapper = mount(RegistryCredentialList, {})
  await flushPromises()
  let resolve!: (value: unknown) => void
  vi.mocked(api).mockImplementationOnce(() => new Promise(done => { resolve = done }))
  await wrapper.get('input').setValue('Laptop')
  await wrapper.get('form').trigger('submit')
  vi.mocked(api).mockResolvedValueOnce({ ...overview, items: [] })
  auth.user = { ...auth.user, id: 998 } as NonNullable<typeof auth.user>
  await flushPromises()
  resolve({ ...credential, token: 'dr_stale' })
  await flushPromises()
  expect(wrapper.text()).not.toContain('dr_stale')
  expect(wrapper.text()).not.toContain('Laptop')
})

function mockRegistry() {
  vi.mocked(api).mockImplementation(async path => {
    if (path.endsWith('/registry')) return overview
    if (path.endsWith('/candidates')) return []
    if (path.includes('/images?')) return { items: [{ name: 'app', reference: 'registry.example.com/team/app' }], total: 1 }
    if (path.includes('/tags?')) return { items: [{ tag: 'latest', digest: 'sha256:' + 'a'.repeat(64), platforms: ['linux/amd64', 'linux/arm64'] }], total: 1 }
    if (path === '/me/registry-credentials') return { ...overview, items: [] }
    throw new Error(path)
  })
}
it('allows members to browse images without namespace or delete controls', async () => {
  mockRegistry()
  const wrapper = mount(RepositoryRegistryView, { global: { stubs: { RouterLink: RouterLinkStub } }, props: { repository: { ...repository, permissions: { ...repository.permissions, is_owner: false } } } })
  await flushPromises()
  expect(wrapper.text()).not.toContain('创建并绑定')
  expect(wrapper.findComponent(RegistryCredentialList).exists()).toBe(false)
  expect(api).not.toHaveBeenCalledWith('/me/registry-credentials')
  await wrapper.get('.image-list button').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('linux/amd64、linux/arm64')
  expect(wrapper.text()).toContain('复制拉取命令')
  expect(wrapper.text()).not.toContain('删除版本')
  expect(wrapper.find('.namespace-list button').exists()).toBe(false)
})
it('requires confirmation describing all-tag deletion before an owner deletes a digest', async () => {
  mockRegistry()
  const wrapper = mount(RepositoryRegistryView, { global: { stubs: { RouterLink: RouterLinkStub } }, props: { repository }, attachTo: document.body })
  await flushPromises()
  await wrapper.get('.image-list button').trigger('click')
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '删除版本')!.trigger('click')
  expect(api).not.toHaveBeenCalledWith(expect.anything(), 'DELETE', expect.anything())
  expect(document.querySelector('dialog[open]')?.textContent).toContain('其他页的同 digest 标签也会删除')
  const original = vi.mocked(api).getMockImplementation()!
  vi.mocked(api).mockImplementation(async (path, method, body) => method === 'DELETE' ? undefined : original(path, method, body))
  document.querySelectorAll<HTMLButtonElement>('dialog[open] button')[1]!.click()
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/repositories/12/registry/namespaces/3/manifest?image=app', 'DELETE', { digest: 'sha256:' + 'a'.repeat(64) })
})

it('copies a pull command using floating feedback outside the repository layout', async () => {
  mockRegistry()
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
  const wrapper = mount(RepositoryRegistryView, { props: { repository }, global: { stubs: { RouterLink: RouterLinkStub } }, attachTo: document.body })
  await flushPromises()
  await wrapper.get('.image-list button').trigger('click')
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '复制拉取命令')!.trigger('click')
  await flushPromises()
  expect(writeText).toHaveBeenCalledWith('docker pull registry.example.com/team/app:latest')
  expect(wrapper.find('.notice-floating').exists()).toBe(false)
  expect(document.querySelector('.app-notice-stack [role="status"]')?.textContent).toContain('已复制按标签拉取的命令')
  writeText.mockRejectedValueOnce(new Error('Clipboard unavailable'))
  await wrapper.findAll('button').find(button => button.text() === '复制拉取命令')!.trigger('click')
  await flushPromises()
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  expect(document.querySelector('.app-notice-stack [role="alert"]')?.textContent).toContain('复制失败')
})

it('lists orphan namespaces and confirms the destination and access change before a versioned admin bind', async () => {
  const ns = { ...overview.items[0], enabled: false, bound: false, version: 7, repository: { id: 12, full_name: 'owner/old', deleted: true, archived: false } }
  vi.mocked(api).mockImplementation(async path => {
    if (path.startsWith('/admin/registry/repositories?')) return { items: [{ id: 22, full_name: 'other/target' }], total: 1 }
    if (path.startsWith('/admin/registry?')) return { ...overview, items: [ns], total: 1 }
    return undefined
  })
  const wrapper = mount(RegistryAdminView, { attachTo: document.body, global: { stubs: { RegistryGCPanel: true } } })
  await flushPromises()
  expect(wrapper.text()).toContain('原 repo 已删除')
  await wrapper.findAll('button').find(button => button.text() === '绑定 repo')!.trigger('click')
  await flushPromises()
  await wrapper.get('.binding-editor [role="combobox"]').trigger('click')
  document.querySelectorAll<HTMLElement>('[role="option"]')[1]!.click()
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '确认绑定')!.trigger('click')
  expect(document.querySelector('dialog[open]')?.textContent).toContain('other/target')
  expect(document.querySelector('dialog[open]')?.textContent).toContain('下一次请求立即生效')
  expect(api).not.toHaveBeenCalledWith(expect.anything(), 'PUT', expect.anything())
  await confirmWithCode()
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/admin/registry/namespaces/3/binding', 'PUT', { repository_id: 22, expected_version: 7, code: '123456' })
  expect(wrapper.find('.binding-editor').exists()).toBe(false)
})

it('keeps the last namespace state when a concurrent admin unbind is rejected', async () => {
  const ns = { ...overview.items[0], bound: true, repository: { id: 12, full_name: 'owner/source', deleted: false, archived: false } }
  vi.mocked(api).mockImplementation(async (path, method) => {
    if (method === 'PUT') throw new Error('namespace 已被其他操作修改，请刷新后重试')
    if (path.startsWith('/admin/registry?')) return { ...overview, items: [ns], total: 1 }
    return undefined
  })
  const wrapper = mount(RegistryAdminView, { attachTo: document.body, global: { stubs: { RegistryGCPanel: true } } })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '解绑')!.trigger('click')
  await confirmWithCode()
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/admin/registry/namespaces/3/binding', 'PUT', { repository_id: null, expected_version: 1, code: '123456' })
  expect(document.querySelector('dialog[open]')?.textContent).toContain('namespace 已被其他操作修改')
  expect(wrapper.text()).toContain('当前 repo：owner/source')
  expect(document.querySelector('.notice-floating')).toBeNull()
})

it('deletes an unbound namespace only after explaining retention and verifying email', async () => {
  const ns = { ...overview.items[0], enabled: false, bound: false, version: 7, repository: { id: 12, full_name: 'owner/old', deleted: true, archived: false } }
  let deleted = false
  vi.mocked(api).mockImplementation(async (path, method) => {
    if (method === 'DELETE') { deleted = true; return { message: 'namespace 已删除' } }
    if (path.startsWith('/admin/registry?')) return { ...overview, items: deleted ? [] : [ns], total: deleted ? 0 : 1 }
    return undefined
  })
  const wrapper = mount(RegistryAdminView, { attachTo: document.body, global: { stubs: { RegistryGCPanel: true } } })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '删除 namespace')!.trigger('click')
  expect(document.querySelector('dialog[open]')?.textContent).toContain('名称继续保留')
  expect(api).not.toHaveBeenCalledWith(expect.anything(), 'DELETE', expect.anything())
  await confirmWithCode()
  expect(api).toHaveBeenCalledWith('/admin/registry/namespaces/3', 'DELETE', { expected_version: 7, confirm_name: 'team', code: '123456' })
  expect(wrapper.text()).toContain('没有符合条件的 namespace')
})


it('warns before permanent-token creation, requires acknowledgement, and resets it when expiry or account changes', async () => {
  vi.mocked(api).mockResolvedValue({ ...overview, items: [] })
  const wrapper = mount(RegistryCredentialList, { attachTo: document.body })
  await flushPromises()
  await wrapper.get('input').setValue('CI')
  const expiry = wrapper.findAllComponents(AppSelect).find(field => field.props('label') === '有效期')!
  expiry.vm.$emit('update:modelValue', 'permanent'); await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toContain('永久令牌不会自动过期')
  expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
  await wrapper.get('form').trigger('submit')
  expect(api).not.toHaveBeenCalledWith('/me/registry-credentials', 'POST', expect.anything())
  await wrapper.get('input[type="checkbox"]').setValue(true)
  expiry.vm.$emit('update:modelValue', '30'); await flushPromises()
  expect(wrapper.find('.permanent-warning').exists()).toBe(false)
  expiry.vm.$emit('update:modelValue', 'permanent'); await flushPromises()
  expect(wrapper.get<HTMLInputElement>('input[type="checkbox"]').element.checked).toBe(false)
  await wrapper.get('input[type="checkbox"]').setValue(true)
  vi.mocked(api).mockResolvedValueOnce({ ...credential, name: 'CI', expires_at: null, token: 'dr_synthetic_permanent' })
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(api).toHaveBeenCalledWith('/me/registry-credentials', 'POST', { name: 'CI', access: 'read', days: null, acknowledge_permanent: true })
  expect(wrapper.get('.session-list').text()).toContain('永久有效')
  expect(wrapper.get('.session-list').text()).not.toMatch(/已过期|1970|Invalid Date/)
  await wrapper.findAll('button').find(button => button.text() === '已保存，关闭')!.trigger('click')
  expect(wrapper.get<HTMLInputElement>('input[type="checkbox"]').element.checked).toBe(false)
  await wrapper.get('input[type="checkbox"]').setValue(true)
  auth.user = { id: 88 } as NonNullable<typeof auth.user>
  await flushPromises()
  expect(wrapper.findAllComponents(AppSelect).find(field => field.props('label') === '有效期')!.props('modelValue')).toBe('90')
  expect(wrapper.find('.permanent-warning').exists()).toBe(false)
  expect(wrapper.text()).not.toContain('dr_synthetic_permanent')
})
