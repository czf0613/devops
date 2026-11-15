import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { api } from '../src/api'
import { auth } from '../src/auth'
import AppConfirm from '../src/components/AppConfirm.vue'
import RepositoryMemberForm from '../src/components/RepositoryMemberForm.vue'
import RepositoryAdminDetailView from '../src/views/RepositoryAdminDetailView.vue'
import RepositoryAdminView from '../src/views/RepositoryAdminView.vue'
import { confirmWithCode, dialogButton } from './admin-helpers'

vi.mock('../src/api', async original => ({ ...(await original<typeof import('../src/api')>()), api: vi.fn() }))
enableAutoUnmount(afterEach)
beforeEach(() => {
  vi.mocked(api).mockReset()
  auth.user = { id: 90, email: 'admin@example.com', roles: ['admin'] } as NonNullable<typeof auth.user>
})
afterEach(() => { document.body.innerHTML = '' })
const owner = { id: 1, username: 'owner', display_name: '拥有者' }
const member = { user_id: 2, username: 'reader', display_name: '成员', status: 'active', access: 'read', role: '', is_admin: false, is_owner: false }
const repo = { id: 8, name: 'private', full_name: 'owner/private', owner, archived: false, deleted_at: null, created_at: '2026-10-05T00:00:00Z', updated_at: '2026-10-05T00:00:00Z', member_count: 2, members: [{ ...member, user_id: 1, username: 'owner', is_owner: true, access: 'write' }, member] }
async function routerAt(path = '/admin/repositories/8') {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/admin/repositories', component: RepositoryAdminView }, { path: '/admin/repositories/:id', component: RepositoryAdminDetailView }] })
  await router.push(path); await router.isReady()
  return router
}

it('sends an admin-purpose code to the signed-in email and consumes it in one confirmed action', async () => {
  vi.mocked(api).mockResolvedValue({ message: '验证码已发送', retry_after: 60 })
  const wrapper = mount(AppConfirm, { attachTo: document.body })
  const execute = vi.fn().mockResolvedValue(undefined)
  const result = wrapper.vm.askVerified('修改成员权限', '管理员操作', '验证并执行', execute)
  await flushPromises()
  expect(dialogButton('验证并执行').disabled).toBe(true)
  dialogButton('获取验证码').click(); await flushPromises()
  expect(api).toHaveBeenCalledWith('/auth/email-codes', 'POST', { email: 'admin@example.com', purpose: 'admin_operation' })
  expect(execute).not.toHaveBeenCalled()
  await confirmWithCode()
  expect(execute).toHaveBeenCalledExactlyOnceWith('123456')
  expect(await result).toBe(true)
  expect(document.querySelector('dialog[open]')).toBeNull()
})

it('keeps validation errors in the confirmation and clears pending verification on an account change', async () => {
  const wrapper = mount(AppConfirm, { attachTo: document.body })
  const execute = vi.fn().mockRejectedValue(new Error('验证码错误或已失效'))
  const result = wrapper.vm.askVerified('删除 namespace', '删除', '验证并执行', execute)
  await flushPromises(); await confirmWithCode()
  expect(document.querySelector('dialog[open] [role="alert"]')?.textContent).toContain('验证码错误')
  auth.user = { id: 91, email: 'other@example.com', roles: ['admin'] } as NonNullable<typeof auth.user>
  await flushPromises()
  expect(await result).toBe(false)
  expect(document.querySelector('dialog[open]')).toBeNull()
  expect(document.querySelector<HTMLInputElement>('dialog input')!.value).toBe('')
})

it('lists global metadata with a management link and filters deleted repositories', async () => {
  vi.mocked(api).mockResolvedValue({ items: [repo], total: 1 })
  const router = await routerAt('/admin/repositories')
  const wrapper = mount(RepositoryAdminView, { global: { plugins: [router] } })
  await flushPromises()
  expect(wrapper.find('a').attributes('href')).toBe('/admin/repositories/8')
  expect(wrapper.text()).toContain('每次修改都需要验证当前管理员邮箱')
  await wrapper.get('[role="combobox"]').trigger('click')
  document.querySelectorAll<HTMLElement>('[role="option"]')[2]!.click()
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(api).toHaveBeenLastCalledWith('/admin/repositories?q=&state=deleted&page=1')
})

it('edits members with the shared form and sends the captured revision only after code verification', async () => {
  vi.mocked(api).mockResolvedValue(repo)
  const router = await routerAt()
  const wrapper = mount(RepositoryAdminDetailView, { attachTo: document.body, global: { plugins: [router] } })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '编辑')!.trigger('click')
  await wrapper.getComponent(RepositoryMemberForm).findAll('button').find(button => button.text() === '取消编辑')!.trigger('click')
  expect(wrapper.findComponent(RepositoryMemberForm).exists()).toBe(false)
  expect(api).toHaveBeenCalledTimes(1)
  expect(document.querySelector('dialog[open]')).toBeNull()
  await wrapper.findAll('button').find(button => button.text() === '编辑')!.trigger('click')
  wrapper.getComponent(RepositoryMemberForm).vm.$emit('submit', { access: 'write', role: '运维人员', is_admin: true })
  await flushPromises()
  expect(document.querySelector('dialog[open]')?.textContent).toContain('仓库管理员')
  expect(api).not.toHaveBeenCalledWith(expect.anything(), 'PATCH', expect.anything())
  await confirmWithCode()
  expect(api).toHaveBeenCalledWith('/admin/repositories/8/members/2', 'PATCH', { access: 'write', role: '运维人员', is_admin: true, expected_updated_at: repo.updated_at, code: '123456' })
  expect(wrapper.findComponent(RepositoryMemberForm).exists()).toBe(false)
})

it('requires a fresh confirmation for each archive or delete and leaves state unchanged on conflicts', async () => {
  vi.mocked(api).mockResolvedValue(repo)
  const router = await routerAt()
  const wrapper = mount(RepositoryAdminDetailView, { attachTo: document.body, global: { plugins: [router] } })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '强制归档')!.trigger('click')
  dialogButton('取消').click(); await flushPromises()
  expect(api).toHaveBeenCalledTimes(1)
  await wrapper.findAll('button').find(button => button.text() === '强制删除')!.trigger('click')
  expect(dialogButton('验证并执行').disabled).toBe(true)
  vi.mocked(api).mockRejectedValueOnce(new Error('仓库或成员已被其他操作修改'))
  await confirmWithCode()
  expect(document.querySelector('dialog[open]')?.textContent).toContain('已被其他操作修改')
  expect(wrapper.text()).not.toContain('此仓库已删除')
  expect(api).toHaveBeenCalledWith('/admin/repositories/8', 'DELETE', { confirm_name: repo.full_name, expected_updated_at: repo.updated_at, code: '123456' })
})

it('selects a new owner and explicitly describes preserved membership before transfer', async () => {
  vi.mocked(api).mockImplementation(async path => path.includes('/candidates?') ? [{ id: 2, username: 'reader', display_name: '成员' }] : repo)
  const router = await routerAt()
  const wrapper = mount(RepositoryAdminDetailView, { attachTo: document.body, global: { plugins: [router] } })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '更换拥有者')!.trigger('click')
  await wrapper.get('input').setValue('reader')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '选择')!.trigger('click')
  await wrapper.findAll('button').find(button => button.text() === '确认移交')!.trigger('click')
  expect(document.querySelector('dialog[open]')?.textContent).toContain('原拥有者保留读写和仓库管理员权限')
  await confirmWithCode()
  expect(api).toHaveBeenCalledWith('/admin/repositories/8/transfer', 'POST', { new_owner_id: 2, confirm_name: repo.full_name, expected_updated_at: repo.updated_at, code: '123456' })
})

it('hides all mutation controls on a deleted repository and discards old page responses', async () => {
  vi.mocked(api).mockResolvedValue({ ...repo, deleted_at: '2026-10-05T03:00:00Z' })
  const router = await routerAt()
  const wrapper = mount(RepositoryAdminDetailView, { global: { plugins: [router] } })
  await flushPromises()
  expect(wrapper.text()).toContain('此仓库已删除')
  expect(wrapper.text()).not.toContain('强制删除')
  let resolve!: (value: unknown) => void
  vi.mocked(api).mockImplementationOnce(() => new Promise(done => { resolve = done }))
  await wrapper.findAll('button').find(button => button.text() === '刷新仓库状态')!.trigger('click')
  vi.mocked(api).mockResolvedValue({ ...repo, id: 9, full_name: 'owner/next' })
  await router.push('/admin/repositories/9'); await flushPromises()
  resolve(repo); await flushPromises()
  expect(wrapper.text()).toContain('owner/next')
  expect(wrapper.text()).not.toContain('owner/private')
})

it('opens the member editor inside the member panel before the table and can cancel it in place', async () => {
  vi.mocked(api).mockResolvedValue(repo)
  const wrapper = mount(RepositoryAdminDetailView, { global: { plugins: [await routerAt()] } })
  await flushPromises()
  const button = wrapper.get('[aria-controls="admin-add-member"]')
  await button.trigger('click')
  const editor = wrapper.get('#admin-add-member').element
  const table = wrapper.get('.table-scroll').element
  expect(editor.parentElement).toBe(table.parentElement)
  expect(editor.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(button.attributes('aria-expanded')).toBe('true')
  await button.trigger('click')
  expect(wrapper.find('#admin-add-member').exists()).toBe(false)
})


it('explains an owner-only repository and exposes transfer next to the owner', async () => {
  vi.mocked(api).mockResolvedValue({ ...repo, members: repo.members.slice(0, 1), member_count: 1 })
  const wrapper = mount(RepositoryAdminDetailView, { attachTo: document.body, global: { plugins: [await routerAt()] } })
  await flushPromises()
  expect(wrapper.text()).toContain('当前只有拥有者')
  expect(wrapper.text()).toContain('请先更换拥有者，再编辑原拥有者的成员设置')
  const ownerRow = wrapper.get('tbody tr')
  expect(ownerRow.findAll('td')[1]!.text()).toContain('更换拥有者')
  expect(ownerRow.findAll('button').map(button => button.text())).toEqual(['更换拥有者'])
  await ownerRow.get('button').trigger('click'); await flushPromises()
  expect(wrapper.get('#admin-transfer input').element).toBe(document.activeElement)
  expect(api).not.toHaveBeenCalledWith(expect.anything(), 'PATCH', expect.anything())
})

it('adds a member from an owner-only repository and reveals the editable permission row', async () => {
  vi.mocked(api).mockImplementation(async (path, method) => {
    if (path.includes('/candidates?')) return [{ id: 2, username: 'reader', display_name: '成员' }]
    if (method === 'POST') return repo
    return { ...repo, members: repo.members.slice(0, 1), member_count: 1 }
  })
  const wrapper = mount(RepositoryAdminDetailView, { attachTo: document.body, global: { plugins: [await routerAt()] } })
  await flushPromises()
  await wrapper.get('[aria-controls="admin-add-member"]').trigger('click')
  await wrapper.get('#admin-add-member input').setValue('reader')
  await wrapper.get('#admin-add-member form').trigger('submit'); await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '选择')!.trigger('click')
  wrapper.findComponent(RepositoryMemberForm).vm.$emit('submit', { access: 'read', role: '测试人员', is_admin: false })
  await flushPromises()
  expect(api).not.toHaveBeenCalledWith('/admin/repositories/8/members', 'POST', expect.anything())
  await confirmWithCode()
  expect(api).toHaveBeenCalledWith('/admin/repositories/8/members', 'POST', { user_id: 2, access: 'read', role: '测试人员', is_admin: false, expected_updated_at: repo.updated_at, code: '123456' })
  expect(wrapper.text()).not.toContain('当前只有拥有者')
  const memberRow = wrapper.findAll('tbody tr').find(row => row.text().includes('@reader'))!
  expect(memberRow.findAll('td')[1]!.findAll('button').map(button => button.text())).toEqual(['编辑', '移除'])
  await memberRow.findAll('button')[0]!.trigger('click')
  expect(wrapper.findComponent(RepositoryMemberForm).props('allowAdmin')).toBe(true)
  expect(wrapper.findComponent(RepositoryMemberForm).props('value')).toMatchObject(member)
})
