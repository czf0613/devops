import { confirmWithCode } from './admin-helpers'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { api } from '../src/api'
import { auth } from '../src/auth'
import type { Repository, RepositoryMember } from '../src/repositories'
import RepositoryMemberForm from '../src/components/RepositoryMemberForm.vue'
import RepositorySettings from '../src/components/RepositorySettings.vue'
import RepositoryDetailView from '../src/views/RepositoryDetailView.vue'
import RepositoryCodeView from '../src/views/RepositoryCodeView.vue'
import RepositorySettingsView from '../src/views/RepositorySettingsView.vue'
import RepositoriesView from '../src/views/RepositoriesView.vue'
import AppSelect from '../src/components/AppSelect.vue'
import RepositoryCode from '../src/components/RepositoryCode.vue'
import RepositoryProtection from '../src/components/RepositoryProtection.vue'

vi.mock('../src/api', async original => ({
  ...(await original<typeof import('../src/api')>()), api: vi.fn(),
}))
enableAutoUnmount(afterEach)
afterEach(() => { document.body.innerHTML = '' })
beforeEach(() => { vi.mocked(api).mockReset(); auth.user = null })

const repository: Repository = {
  id: 12, name: 'example', description: 'An engineering project', full_name: 'owner/example', archived: false,
  protect_main: false, protect_push: false,
  owner: { id: 1, username: 'owner', display_name: '拥有者' }, clone_path: '/git/owner/example.git',
  default_branch: 'main', member_count: 3, created_at: '', updated_at: '',
  permissions: { is_owner: false, is_admin: false, can_manage_members: false, access: 'read', role: '开发人员' },
}
const members: RepositoryMember[] = [
  { user_id: 1, username: 'owner', display_name: '拥有者', status: 'active', access: 'write', role: '', is_owner: true, is_admin: false },
  { user_id: 2, username: 'manager', display_name: '管理员', status: 'active', access: 'read', role: '开发人员', is_owner: false, is_admin: true },
  { user_id: 3, username: 'reader', display_name: '成员', status: 'active', access: 'read', role: '测试人员', is_owner: false, is_admin: false },
]

async function detail(permissions = repository.permissions, initialPath = '/repositories/owner/example?tab=settings', description = repository.description) {
  vi.mocked(api).mockImplementation(async path => {
    if (path === '/repositories/12' || path === '/repositories/by-name/owner/example') return { ...repository, permissions, description }
    if (path === '/repositories/12/catalog') return []
    if (path === '/repositories/12/merge-policy') return { methods: ['merge','squash','rebase'], required_approvals: 0, dismiss_stale_approvals: true, require_resolved_threads: true, required_checks: [] }
    if (path === '/repositories/12/members') return members
    if (path === '/repositories/12/branches') return { items: [{ name: 'main', commit: 'a'.repeat(40) }, { name: 'feature/code', commit: 'b'.repeat(40) }], truncated: false }
    if (path.startsWith('/repositories/12/contents?')) {
      const params = new URLSearchParams(path.split('?')[1])
      if (params.get('path') === 'src/example.ts') return { type: 'blob', ref: params.get('ref'), path: 'src/example.ts', commit: 'b'.repeat(40), size: 20, content: '<img src=x onerror=alert(1)>', binary: false, too_large: false, lfs: null }
      return { type: 'tree', ref: params.get('ref'), path: '', commit: 'a'.repeat(40), readme: { name: 'README.md', path: 'README.md', content: '# Project ' + params.get('ref') + '\n\n**Welcome**' }, entries: [{ name: 'src', path: 'src', type: 'tree', oid: 'a'.repeat(40), size: null }], total: 1, page: 1, page_size: 200 }
    }
    throw new Error(`unexpected request: ${path}`)
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/repositories/:id', component: RepositoryDetailView },
    { path: '/repositories/:owner/:name', component: RepositoryDetailView, children: [{path:'issue',component:{template:'<div />'}},{path:'pr',component:{template:'<div />'}},{path:'code',component:RepositoryCodeView,meta:{repositoryTab:'code'}},{path:'settings',component:RepositorySettingsView,meta:{repositoryTab:'settings'}}] },
    { path: '/repositories', component: RepositoriesView },
  ] })
  await router.push(initialPath)
  await router.isReady()
  const wrapper = mount({ template: '<RouterView />' }, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

it('shows clone access and members without management controls for a read-only developer', async () => {
  const wrapper = await detail()
  expect(wrapper.text()).toContain('owner/example')
  expect(wrapper.text()).toContain('只读')
  expect(wrapper.text()).not.toContain('添加成员')
  expect(wrapper.find('[aria-label="编辑 @reader"]').exists()).toBe(false)
  expect(wrapper.findComponent(RepositoryMemberForm).exists()).toBe(false)
  expect(wrapper.get('code[aria-label="HTTPS 克隆地址"]').text()).toContain('/git/owner/example.git')
  expect(wrapper.findComponent(RepositorySettings).exists()).toBe(false)
})

it('opens Code first, keeps settings separate, and preserves branch/file links on navigation', async () => {
  const wrapper = await detail(repository.permissions, '/repositories/owner/example')
  expect(wrapper.get('.repo-tabs a').text()).toBe('Code')
  expect(wrapper.findComponent(RepositoryCode).exists()).toBe(true)
  expect(wrapper.find('.repo-members').exists()).toBe(false)
  expect(wrapper.get('[aria-label="仓库代码"]').text()).toContain('src')
  expect(wrapper.get('[aria-label="README 内容"] h1').text()).toBe('Project main')
  expect(wrapper.get('[aria-label="README 内容"] strong').text()).toBe('Welcome')
  expect(wrapper.text()).not.toContain('使用 Git LFS 管理大文件')
  await choose(wrapper, '分支', 'feature/code')
  expect(wrapper.vm.$route.query.ref).toBe('feature/code')
  expect(wrapper.get('[aria-label="README 内容"] h1').text()).toBe('Project feature/code')
  await wrapper.vm.$router.push({ query: { ref: 'feature/code', path: 'src/example.ts' } })
  await flushPromises()
  expect(wrapper.get('[aria-label="文件内容"]').text()).toContain('<img src=x onerror=alert(1)>')
  expect(wrapper.find('[aria-label="文件内容"] img').exists()).toBe(false)
  await wrapper.findAll('.repo-tabs a').find(link => link.text() === 'Settings')!.trigger('click')
  await flushPromises()
  expect(wrapper.findComponent(RepositoryCode).exists()).toBe(false)
  expect(wrapper.find('.repo-members').exists()).toBe(true)
  expect(wrapper.vm.$route.query.path).toBe('src/example.ts')
  await wrapper.get('.repo-tabs a').trigger('click')
  await flushPromises()
  expect(wrapper.get('[aria-label="文件内容"]').text()).toContain('<img')
})

it('shows a useful empty repository state', async () => {
  const wrapper = await detail(repository.permissions, '/repositories/owner/example')
  vi.mocked(api).mockResolvedValueOnce({ items: [], truncated: false })
  await wrapper.findAll('button').find(button => button.text() === '刷新')!.trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('仓库还是空的')
  expect(wrapper.text()).toContain('git clone')
  expect(wrapper.get('[role="combobox"]').text()).toBe('main')
  expect(wrapper.get('[role="combobox"]').attributes('disabled')).toBeDefined()
})

it('only renders owner protection switches and keeps the last saved value on failure', async () => {
  const reader = await detail()
  expect(reader.find('[role="switch"]').exists()).toBe(false)
  const manager = await detail({ ...repository.permissions, is_admin: true, can_manage_members: true })
  expect(manager.find('[role="switch"]').exists()).toBe(false)
  const owner = await detail({ ...repository.permissions, is_owner: true, can_manage_members: true })
  const panel = owner.getComponent(RepositoryProtection)
  const main = panel.get('[aria-label="分支保护"]')
  vi.mocked(api).mockRejectedValueOnce(new Error('保存失败'))
  await main.trigger('click')
  await flushPromises()
  expect(main.attributes('aria-checked')).toBe('false')
  expect(panel.text()).toContain('保存失败')
  vi.mocked(api).mockResolvedValueOnce({ ...repository, protect_main: true, permissions: { ...repository.permissions, is_owner: true } })
  await main.trigger('click')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/repositories/12/protection', 'PUT', { protect_main: true, protect_push: false })
  expect(main.attributes('aria-checked')).toBe('true')
})

it('lets a repo administrator edit ordinary members but not owners or administrators', async () => {
  const wrapper = await detail({ ...repository.permissions, is_admin: true, can_manage_members: true })
  expect(wrapper.find('[aria-label="编辑 @owner"]').exists()).toBe(false)
  expect(wrapper.find('[aria-label="编辑 @manager"]').exists()).toBe(false)
  expect(wrapper.find('[aria-label="移除 @manager"]').exists()).toBe(false)
  expect(wrapper.find('[aria-label="移除 @owner"]').exists()).toBe(false)
  await wrapper.get('[aria-label="编辑 @reader"]').trigger('click')
  const form = wrapper.getComponent(RepositoryMemberForm)
  expect(form.props('allowAdmin')).toBe(false)
  expect(form.text()).not.toContain('管理权限')
  vi.mocked(api).mockResolvedValueOnce({})
  await form.get('form').trigger('submit')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/repositories/12/members/3', 'PATCH', { access: 'read', role: '测试人员' })
})

it('allows the owner to manage repo administrators and assign the administrator flag', async () => {
  const wrapper = await detail({ ...repository.permissions, is_owner: true, can_manage_members: true, access: 'write' })
  expect(wrapper.find('[aria-label="编辑 @owner"]').exists()).toBe(false)
  await wrapper.get('[aria-label="编辑 @manager"]').trigger('click')
  const form = wrapper.getComponent(RepositoryMemberForm)
  expect(form.props('allowAdmin')).toBe(true)
  expect(form.text()).toContain('管理权限')
  await choose(form, '仓库读写权限', '读写 · clone / pull / push')
  await choose(form, '岗位角色', '产品经理')
  await choose(form, '管理权限', '普通成员')
  vi.mocked(api).mockResolvedValueOnce({})
  await form.get('form').trigger('submit')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/repositories/12/members/2', 'PATCH', { access: 'write', role: '产品经理', is_admin: false })
  expect(wrapper.findComponent(RepositorySettings).exists()).toBe(true)
})

async function choose(wrapper: VueWrapper, label: string, option: string) {
  const select = wrapper.findAllComponents(AppSelect).find(component => component.props('label') === label)!
  await select.get('[role="combobox"]').trigger('click')
  const item = [...document.querySelectorAll<HTMLElement>('[role="option"]')].find(element => element.textContent?.trim() === option)!
  item.click()
  await flushPromises()
}

it('keeps job role independent from Git access and omits admin changes for a manager', async () => {
  const wrapper = mount(RepositoryMemberForm, { props: {
    value: { access: 'read', role: '开发人员', is_admin: false }, allowAdmin: false, busy: false,
  } })
  await choose(wrapper, '岗位角色', '测试人员')
  await wrapper.get('form').trigger('submit')
  expect(wrapper.emitted('submit')).toEqual([[{ access: 'read', role: '测试人员' }]])
})

it('preserves custom roles and supports switching between presets, custom input and no role', async () => {
  const wrapper = mount(RepositoryMemberForm, { props: {
    value: { access: 'write', role: '技术负责人', is_admin: true }, allowAdmin: true, busy: false,
  } })
  expect(wrapper.get('input').element.value).toBe('技术负责人')
  await wrapper.get('form').trigger('submit')
  expect(wrapper.emitted('submit')?.at(-1)).toEqual([{ access: 'write', role: '技术负责人', is_admin: true }])
  await choose(wrapper, '岗位角色', '开发人员')
  expect(wrapper.find('input').exists()).toBe(false)
  await choose(wrapper, '岗位角色', '自定义岗位…')
  expect(wrapper.get('input').element.value).toBe('技术负责人')
  await wrapper.get('input').setValue('  架构师  ')
  await wrapper.get('form').trigger('submit')
  expect(wrapper.emitted('submit')?.at(-1)).toEqual([{ access: 'write', role: '架构师', is_admin: true }])
  await choose(wrapper, '岗位角色', '未指定')
  await wrapper.get('form').trigger('submit')
  expect(wrapper.emitted('submit')?.at(-1)).toEqual([{ access: 'write', role: '', is_admin: true }])
})

it.each([
  { is_owner: false, is_admin: true, target: 'reader', id: 3 },
  { is_owner: true, is_admin: false, target: 'manager', id: 2 },
])('removes permitted members only after confirmation: $target', async ({ is_owner, is_admin, target, id }) => {
  const permissions = { ...repository.permissions, is_owner, is_admin, can_manage_members: true }
  const wrapper = await detail(permissions)
  await wrapper.get(`[aria-label="移除 @${target}"]`).trigger('click')
  confirmDialog(false)
  await flushPromises()
  expect(api).not.toHaveBeenCalledWith(`/repositories/12/members/${id}`, 'DELETE')
  await wrapper.get(`[aria-label="移除 @${target}"]`).trigger('click')
  vi.mocked(api).mockResolvedValueOnce({}).mockResolvedValueOnce({ ...repository, permissions })
    .mockResolvedValueOnce(members.filter(member => member.user_id !== id))
  confirmDialog()
  await flushPromises()
  expect(api).toHaveBeenCalledWith(`/repositories/12/members/${id}`, 'DELETE')
  expect(wrapper.get('.repo-members table').text()).not.toContain(`@${target}`)
  expect(wrapper.text()).toContain('成员已移除')
})

it('keeps long descriptions in Settings and out of the repository header', async () => {
  const description = '1234567890'.repeat(200)
  const wrapper = await detail(repository.permissions, '/repositories/owner/example', description)
  expect(wrapper.text()).not.toContain(description)
  expect(wrapper.get('.repository-heading').text()).toContain('owner/example')
  await wrapper.findAll('.repo-tabs a').find(link => link.text() === 'Settings')!.trigger('click')
  await flushPromises()
  expect(wrapper.get('.repo-description').text()).toBe(description)
  expect(wrapper.find('.description-toggle').exists()).toBe(false)
})

it('creates a repository without accepting owner or storage fields from the UI', async () => {
  vi.mocked(api).mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 20 })
    .mockResolvedValueOnce(repository)
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/repositories', component: RepositoriesView },
    { path: '/repositories/:owner/:name', component: { template: '<div />' } },
  ] })
  await router.push('/repositories')
  await router.isReady()
  const wrapper = mount(RepositoriesView, { global: { plugins: [router] } })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text().includes('创建仓库'))!.trigger('click')
  await wrapper.get('input[required]').setValue('example')
  await wrapper.get('textarea').setValue('A project')
  await wrapper.get('form.compact-form').trigger('submit')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/repositories', 'POST', { name: 'example', description: 'A project' })
  expect(router.currentRoute.value.path).toBe('/repositories/owner/example')
})

it('resolves the owner/name URL and redirects saved numeric links to the canonical URL', async () => {
  const named = await detail()
  expect(api).toHaveBeenCalledWith('/repositories/by-name/owner/example')
  expect(named.vm.$router.currentRoute.value.path).toBe('/repositories/owner/example/settings')
  const legacy = await detail(repository.permissions, '/repositories/12')
  expect(legacy.vm.$router.currentRoute.value.path).toBe('/repositories/owner/example/code')
})

it('keeps adding and editing members in one panel and clears the completed add form', async () => {
  const wrapper = await detail({ ...repository.permissions, is_owner: true, can_manage_members: true })
  const panel = wrapper.get('.repo-members')
  expect(panel.find('[aria-label="搜索可添加的同事"]').exists()).toBe(false)
  await panel.get('[aria-controls="add-repository-member"]').trigger('click')
  expect(panel.get('table').text()).toContain('@reader')
  await panel.get('input').setValue('newuser')
  vi.mocked(api).mockResolvedValueOnce([{ id: 4, username: 'newuser', display_name: '新同事' }])
  await panel.get('form.repo-search').trigger('submit')
  await flushPromises()
  await panel.get('.repo-candidates button').trigger('click')
  vi.mocked(api).mockResolvedValueOnce({})
  await panel.getComponent(RepositoryMemberForm).get('form').trigger('submit')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/repositories/12/members', 'POST', { user_id: 4, access: 'read', role: '', is_admin: false })
  expect(panel.find('.repo-add-member').exists()).toBe(false)
})

function settings() {
  return mount(RepositorySettings, { attachTo: document.body, props: { repository: {
    ...repository, permissions: { ...repository.permissions, is_owner: true, can_manage_members: true },
  } } })
}
function confirmDialog(accept = true) {
  document.querySelectorAll<HTMLButtonElement>('dialog[open] button')[accept ? 1 : 0]!.click()
}

it('requires exact repository name and final confirmation before deletion', async () => {
  const wrapper = settings()
  await wrapper.get('[aria-controls="delete-repository"]').trigger('click')
  const form = wrapper.get('#delete-repository')
  await form.get('input').setValue('example')
  expect(form.get('button').attributes('disabled')).toBeDefined()
  await form.get('input').setValue('owner/example')
  await form.trigger('submit')
  expect(api).not.toHaveBeenCalled()
  confirmDialog(false)
  await flushPromises()
  expect(wrapper.emitted('deleted')).toBeUndefined()
  await form.trigger('submit')
  vi.mocked(api).mockResolvedValueOnce({})
  await confirmWithCode()
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/repositories/12', 'DELETE', { confirm_name: 'owner/example', code: '123456' })
  expect(wrapper.emitted('deleted')).toHaveLength(1)
})

it('archives and unarchives with confirmation and emits the server permissions', async () => {
  const wrapper = settings()
  const archived = { ...repository, archived: true, permissions: { ...repository.permissions, is_owner: true, access: 'read' as const } }
  await wrapper.get('.setting-row button').trigger('click')
  vi.mocked(api).mockResolvedValueOnce(archived)
  await confirmWithCode()
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/repositories/12/archive', 'PUT', { archived: true, code: '123456' })
  expect(wrapper.emitted('updated')?.[0]?.[0]).toEqual(archived)
  await wrapper.setProps({ repository: archived })
  await wrapper.get('.setting-row button').trigger('click')
  vi.mocked(api).mockResolvedValueOnce(repository)
  await confirmWithCode()
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/repositories/12/archive', 'PUT', { archived: false, code: '123456' })
})

it('transfers to a selected user, surfaces conflicts, and switches to the new owner URL on success', async () => {
  const wrapper = await detail({ ...repository.permissions, is_owner: true, can_manage_members: true })
  const settingsPanel = wrapper.getComponent(RepositorySettings)
  await settingsPanel.get('[aria-controls="transfer-repository"]').trigger('click')
  await settingsPanel.get('.candidate-search input').setValue('reader')
  vi.mocked(api).mockResolvedValueOnce([{ id: 3, username: 'reader', display_name: '成员' }])
  await settingsPanel.get('.candidate-search').trigger('submit')
  await flushPromises()
  await settingsPanel.get('.candidate-list button').trigger('click')
  const form = settingsPanel.findAll('form')[1]!
  await form.get('input').setValue('owner/example')
  await form.trigger('submit')
  vi.mocked(api).mockRejectedValueOnce(new Error('新拥有者已有同名仓库'))
  await confirmWithCode()
  await flushPromises()
  expect(document.querySelector('dialog[open]')?.textContent).toContain('新拥有者已有同名仓库')
  const transferred = { ...repository, full_name: 'reader/example',
    owner: { id: 3, username: 'reader', display_name: '成员' }, clone_path: '/git/reader/example.git',
    permissions: { ...repository.permissions, is_admin: true, can_manage_members: true, access: 'write' },
  }
  const fallback = vi.mocked(api).getMockImplementation()!
  vi.mocked(api).mockImplementation(async (path, ...args) => path === '/repositories/by-name/reader/example' || (path === '/repositories/12' && !args[0]) ? transferred : fallback(path, ...args))
  vi.mocked(api).mockResolvedValueOnce(transferred)
  await confirmWithCode()
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/repositories/12/transfer', 'POST', { new_owner_id: 3, confirm_name: 'owner/example', code: '123456' })
  expect(wrapper.vm.$router.currentRoute.value.path).toBe('/repositories/reader/example/settings')
  expect(wrapper.get('h1').text()).toBe('reader/example')
  expect(wrapper.findComponent(RepositorySettings).exists()).toBe(false)
})

it('uses the signed-in account email and repository-only purpose, cancelling when the account changes', async () => {
  auth.user = { id: 1, email: 'owner@example.com', roles: ['member'] } as NonNullable<typeof auth.user>
  const wrapper = settings()
  await wrapper.get('.setting-row button').trigger('click')
  const dialog = document.querySelector<HTMLDialogElement>('dialog[open]')!
  expect(dialog.querySelector<HTMLButtonElement>('button[type="submit"]')!.disabled).toBe(true)
  vi.mocked(api).mockResolvedValueOnce({ message: '验证码已发送', retry_after: 60 })
  const send = [...dialog.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent?.trim() === '获取验证码')!
  send.click(); await flushPromises()
  expect(api).toHaveBeenCalledWith('/auth/email-codes', 'POST', { email: 'owner@example.com', purpose: 'repository_operation' })
  auth.user = { id: 9, email: 'another@example.com', roles: ['member'] } as NonNullable<typeof auth.user>
  await flushPromises()
  expect(document.querySelector('dialog[open]')).toBeNull()
  expect(api).not.toHaveBeenCalledWith(expect.anything(), 'PUT', expect.anything())
})
