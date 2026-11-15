import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { api } from '../src/api'
import { auth } from '../src/auth'
import type { Catalog, MergePolicy } from '../src/collaboration'
import type { Repository } from '../src/repositories'
import CollaborationSettings from '../src/components/CollaborationSettings.vue'
import CatalogEditor from '../src/components/CatalogEditor.vue'
import MergePolicySettings from '../src/components/MergePolicySettings.vue'
import AppInput from '../src/components/AppInput.vue'
import AppTextarea from '../src/components/AppTextarea.vue'

vi.mock('../src/api', async original => ({ ...(await original<typeof import('../src/api')>()), api: vi.fn() }))
enableAutoUnmount(afterEach)
const repository = { id: 12, archived: false,
  permissions: { is_owner: true, is_admin: false, access: 'write' } } as Repository
const policy = (): MergePolicy => ({ methods: ['merge', 'squash', 'rebase'], required_approvals: 0,
  dismiss_stale_approvals: true, require_resolved_threads: false, required_checks: [] })
const entry = (kind: Catalog['kind'] = 'label', id = 1): Catalog => ({
  id, kind, name: `${kind}-${id}`, description: 'Description',
  data: { color: '#36c8c4', state: 'open', item_kind: 'issue', body: '', fields: [], due_at: null },
})
let catalog: Catalog[]
beforeEach(() => {
  localStorage.clear()
  vi.mocked(api).mockReset()
  auth.user = { id: 1 } as typeof auth.user
  catalog = [entry(), entry('type', 2), entry('milestone', 3), entry('template', 4)]
  vi.mocked(api).mockImplementation(async (path, method, data) => {
    if (path.endsWith('/merge-policy')) return method === 'PUT' ? data : policy()
    if (method === 'POST' || method === 'PUT') return { id: method === 'POST' ? 10 : Number(path.split('/').pop()), ...data as object }
    if (method === 'DELETE') return { ok: true }
    return catalog
  })
})
function button(wrapper: VueWrapper, text: string) {
  const found = wrapper.findAll('button').find(button => button.text() === text)
  if (!found) throw new Error(`Missing button ${text}`)
  return found
}
function input(wrapper: VueWrapper, label: string) {
  return wrapper.findAllComponents(AppInput).find(input => input.props('label') === label)!.get('input')
}
async function settings(repo = repository) {
  const wrapper = mount(CollaborationSettings, { props: { repository: repo }, attachTo: document.body })
  await flushPromises()
  return wrapper
}
async function tab(wrapper: VueWrapper, label: string) {
  await wrapper.findAll('[role="tab"]').find(tab => tab.text().startsWith(label))!.trigger('click')
}

it('opens creation on demand, edits in place, cancels without writes and restores focus', async () => {
  const wrapper = await settings()
  expect(wrapper.findComponent(CatalogEditor).exists()).toBe(false)
  await wrapper.get('[aria-label="编辑label-1"]').trigger('click')
  expect(wrapper.find('.catalog-entry').exists()).toBe(false)
  await input(wrapper, '名称').setValue('unsaved')
  await button(wrapper.findComponent(CatalogEditor), '取消').trigger('click')
  expect(wrapper.text()).toContain('label-1')
  expect(document.activeElement?.getAttribute('aria-label')).toBe('编辑label-1')
  expect(localStorage.length).toBe(0)
  expect(vi.mocked(api).mock.calls.every(([, method]) => !method)).toBe(true)
  await wrapper.get('[data-edit="new"]').trigger('click')
  await input(wrapper, '名称').setValue('New label')
  await wrapper.get('.catalog-edit-form').trigger('submit')
  await flushPromises()
  expect(wrapper.findComponent(CatalogEditor).exists()).toBe(false)
  expect(wrapper.text()).toContain('New label')
  expect(document.activeElement?.getAttribute('data-edit')).toBe('new')
  // A successful mutation uses its response, avoiding an ambiguous failed refetch / duplicate create.
  expect(vi.mocked(api).mock.calls.filter(([path, method]) => path.endsWith('/catalog') && !method)).toHaveLength(1)
})

it('keeps drafts through category switches and remounts, isolates repositories and accounts', async () => {
  let wrapper = await settings()
  await tab(wrapper, 'Issue 类型')
  await wrapper.get('[data-edit="new"]').trigger('click')
  await input(wrapper, '名称').setValue('Type draft')
  await tab(wrapper, '标签')
  expect(wrapper.findComponent(CatalogEditor).exists()).toBe(false)
  wrapper.unmount()
  wrapper = await settings()
  await tab(wrapper, 'Issue 类型')
  await wrapper.get('[data-edit="new"]').trigger('click')
  expect(input(wrapper, '名称').element.value).toBe('Type draft')
  expect(wrapper.text()).toContain('已恢复草稿')
  wrapper.unmount()
  const other = await settings({ ...repository, id: 13 })
  await tab(other, 'Issue 类型')
  await other.get('[data-edit="new"]').trigger('click')
  expect(input(other, '名称').element.value).toBe('')
  auth.user = { id: 2 } as typeof auth.user
  const account = await settings()
  await tab(account, 'Issue 类型')
  await account.get('[data-edit="new"]').trigger('click')
  expect(input(account, '名称').element.value).toBe('')
})

it('keeps failed edits and prevents duplicate submissions or tab switches while saving', async () => {
  const wrapper = await settings()
  await wrapper.get('[aria-label="编辑label-1"]').trigger('click')
  await input(wrapper, '名称').setValue('Retry me')
  let reject!: (cause: Error) => void
  vi.mocked(api).mockImplementationOnce(() => new Promise((_, fail) => { reject = fail }))
  await wrapper.get('.catalog-edit-form').trigger('submit')
  await wrapper.get('.catalog-edit-form').trigger('submit')
  expect(vi.mocked(api).mock.calls.filter(([, method]) => method === 'PUT')).toHaveLength(1)
  expect(wrapper.get('[role="tab"]').attributes('disabled')).toBeDefined()
  expect(button(wrapper.findComponent(CatalogEditor), '取消').attributes('disabled')).toBeDefined()
  reject(new Error('Network unavailable'))
  await flushPromises()
  expect(input(wrapper, '名称').element.value).toBe('Retry me')
  expect(wrapper.text()).toContain('Network unavailable')
  expect(localStorage.length).toBe(1)
  await wrapper.get('.catalog-edit-form').trigger('submit')
  await flushPromises()
  expect(wrapper.text()).toContain('Retry me')
  expect(wrapper.findComponent(CatalogEditor).exists()).toBe(false)
  expect(localStorage.length).toBe(0)
})

it('reveals the editing action after a long form collapses without moving an already visible action', async () => {
  const wrapper = await settings()
  const rect = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ top: -200, bottom: -156 } as DOMRect)
  const scroll = vi.fn()
  Element.prototype.scrollIntoView = scroll
  await wrapper.get('[aria-label="编辑label-1"]').trigger('click')
  await button(wrapper.findComponent(CatalogEditor), '取消').trigger('click')
  expect(scroll).toHaveBeenCalledWith({ block: 'nearest' })
  scroll.mockClear()
  rect.mockReturnValue({ top: 200, bottom: 244 } as DOMRect)
  await wrapper.get('[aria-label="编辑label-1"]').trigger('click')
  await button(wrapper.findComponent(CatalogEditor), '取消').trigger('click')
  expect(scroll).not.toHaveBeenCalled()
  rect.mockRestore()
})

it('loads independently from policy, retries catalog loading and ignores old repository responses', async () => {
  let resolve!: (value: Catalog[]) => void
  vi.mocked(api).mockImplementation(async path => {
    if (path.endsWith('/merge-policy')) throw new Error('Policy unavailable')
    if (path.startsWith('/repositories/12')) return new Promise<Catalog[]>(done => { resolve = done })
    return [entry('label', 20)]
  })
  const wrapper = mount(CollaborationSettings, { props: { repository } })
  await wrapper.setProps({ repository: { ...repository, id: 13 } })
  await flushPromises()
  resolve([entry('label', 90)])
  await flushPromises()
  expect(wrapper.text()).toContain('label-20')
  expect(wrapper.text()).not.toContain('label-90')
  expect(wrapper.get('[data-edit="new"]').attributes('disabled')).toBeUndefined()
  expect(wrapper.findComponent(MergePolicySettings).find('form').exists()).toBe(false)
})

it('hides mutation controls for read-only and archived repositories', async () => {
  const read = await settings({ ...repository, permissions: { ...repository.permissions, is_owner: false, access: 'read' } })
  expect(read.find('[data-edit]').exists()).toBe(false)
  expect(read.findComponent(MergePolicySettings).exists()).toBe(false)
  const archived = await settings({ ...repository, archived: true })
  expect(archived.find('[data-edit]').exists()).toBe(false)
  expect(archived.findComponent(MergePolicySettings).get('input').attributes('disabled')).toBeDefined()
  expect(archived.findComponent(MergePolicySettings).find('button[type="submit"]').exists()).toBe(false)
})

it('supports keyboard tabs, Escape to cancel and deletion confirmation', async () => {
  const wrapper = await settings()
  await wrapper.get('[role="tab"]').trigger('keydown', { key: 'ArrowRight' })
  expect(wrapper.get('[role="tab"][aria-selected="true"]').text()).toContain('Issue 类型')
  expect(document.activeElement?.getAttribute('role')).toBe('tab')
  await wrapper.get('[data-edit="new"]').trigger('click')
  await wrapper.get('.catalog-edit-form').trigger('keydown', { key: 'Escape' })
  expect(wrapper.findComponent(CatalogEditor).exists()).toBe(false)
  await wrapper.get('[aria-label="删除type-2"]').trigger('click')
  await flushPromises()
  const dialog = document.querySelector<HTMLDialogElement>('dialog[open]')!
  expect(dialog.textContent).toContain('type-2')
  dialog.querySelector<HTMLButtonElement>('button.secondary')!.click()
  await flushPromises()
  expect(wrapper.text()).toContain('type-2')
  await wrapper.get('[aria-label="删除type-2"]').trigger('click')
  await flushPromises()
  document.querySelector<HTMLButtonElement>('dialog[open] button.danger')!.click()
  await flushPromises()
  expect(wrapper.text()).not.toContain('type-2')
  expect(api).toHaveBeenCalledWith('/repositories/12/catalog/2', 'DELETE')
})

it('preserves milestone time when editing other fields and clears due date explicitly', async () => {
  const milestone = entry('milestone', 3)
  milestone.data.due_at = '2026-10-08T18:00:00+08:00'
  let wrapper = mount(CatalogEditor, { props: { repositoryId: 12, kind: 'milestone', kindLabel: '里程碑', entry: milestone } })
  await input(wrapper, '名称').setValue('Updated milestone')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(api).toHaveBeenLastCalledWith('/repositories/12/catalog/3', 'PUT', expect.objectContaining({ data: expect.objectContaining({ due_at: milestone.data.due_at }) }))
  wrapper.unmount()
  wrapper = mount(CatalogEditor, { props: { repositoryId: 12, kind: 'milestone', kindLabel: '里程碑', entry: milestone } })
  await input(wrapper, '截止日期（可选）').setValue('')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(api).toHaveBeenLastCalledWith('/repositories/12/catalog/3', 'PUT', expect.objectContaining({ data: expect.objectContaining({ due_at: null }) }))
})

it('keeps template field IDs unique after removals, supports ordering and validates empty options', async () => {
  const wrapper = mount(CatalogEditor, { props: { repositoryId: 12, kind: 'template', kindLabel: '模板' } })
  await input(wrapper, '名称').setValue('Bug report')
  await button(wrapper, '添加字段').trigger('click')
  await button(wrapper, '添加字段').trigger('click')
  await wrapper.get('[aria-label="移除字段 1"]').trigger('click')
  await button(wrapper, '添加字段').trigger('click')
  const cards = () => wrapper.findAll('.template-field-card')
  await cards()[0]!.get('input').setValue('Steps')
  await cards()[1]!.get('input').setValue('Environment')
  await wrapper.get('[aria-label="上移字段 2"]').trigger('click')
  expect(cards()[0]!.get('input').element.value).toBe('Environment')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  const data = vi.mocked(api).mock.calls.at(-1)![2] as Catalog
  expect(data.data.fields.map(field => field.id)).toEqual(['field_1', 'field_2'])
  expect(data.data.fields.map(field => field.label)).toEqual(['Environment', 'Steps'])
  const invalid = entry('template')
  invalid.data.fields = [{ id: 'choice', label: 'Choice', type: 'select', options: [], required: false, description: '' }]
  const select = mount(CatalogEditor, { props: { repositoryId: 12, kind: 'template', kindLabel: '模板', entry: invalid } })
  await input(select, '名称').setValue('Select form')
  vi.mocked(api).mockClear()
  await select.get('form').trigger('submit')
  expect(select.text()).toContain('单选字段需要 1–30 个选项')
  expect(api).not.toHaveBeenCalled()
  await select.findAllComponents(AppTextarea).find(c => c.props('label') === '选项，每行一个')!.get('textarea').setValue('Alpha\n\nBeta\n')
  await select.get('form').trigger('submit')
  await flushPromises()
  expect(api).toHaveBeenLastCalledWith('/repositories/12/catalog/1', 'PUT', expect.objectContaining({ data: expect.objectContaining({ fields: [expect.objectContaining({ options: ['Alpha', 'Beta'] })] }) }))
})

it('does not allow saving default policy when initial load fails', async () => {
  vi.mocked(api).mockRejectedValueOnce(new Error('Offline'))
  const wrapper = mount(MergePolicySettings, { props: { repository } })
  await flushPromises()
  expect(wrapper.find('form').exists()).toBe(false)
  expect(wrapper.text()).toContain('Offline')
  await button(wrapper, '重新加载').trigger('click')
  await flushPromises()
  expect(button(wrapper, '保存合并规则').attributes('disabled')).toBeDefined()
  expect(wrapper.get('input[type="number"]').element.value).toBe('0')
})

it('restores policy drafts, rejects empty merge methods, cancels, and persists normalized values', async () => {
  let wrapper = mount(MergePolicySettings, { props: { repository } })
  await flushPromises()
  await wrapper.get('input[type="number"]').setValue('2')
  wrapper.unmount()
  wrapper = mount(MergePolicySettings, { props: { repository } })
  await flushPromises()
  expect(wrapper.get('input[type="number"]').element.value).toBe('2')
  expect(wrapper.text()).toContain('已恢复草稿')
  await button(wrapper, '取消修改').trigger('click')
  expect(wrapper.get('input[type="number"]').element.value).toBe('0')
  expect(localStorage.length).toBe(0)
  for (const checkbox of wrapper.findAll('input[type="checkbox"]').slice(0, 3)) await checkbox.setValue(false)
  expect(wrapper.text()).toContain('至少保留一种合并方式')
  expect(button(wrapper, '保存合并规则').attributes('disabled')).toBeDefined()
  await wrapper.get('input[value="squash"]').setValue(true)
  await wrapper.get('input[type="number"]').setValue('1')
  await wrapper.get('textarea').setValue(' lint \n\nbuild\nlint')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(api).toHaveBeenLastCalledWith('/repositories/12/merge-policy', 'PUT', {
    ...policy(), methods: ['squash'], required_approvals: 1, required_checks: ['lint', 'build'],
  })
  expect(wrapper.text()).toContain('PR 合并规则已保存')
  expect(localStorage.length).toBe(0)
  expect(button(wrapper, '保存合并规则').attributes('disabled')).toBeDefined()
})

it('keeps policy edits on failure and cancels back to the server values', async () => {
  const wrapper = mount(MergePolicySettings, { props: { repository }, attachTo: document.body })
  await flushPromises()
  await wrapper.get('input[type="number"]').setValue('2')
  vi.mocked(api).mockRejectedValueOnce(new Error('Cannot save'))
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(wrapper.get('input[type="number"]').element.value).toBe('2')
  expect(wrapper.text()).toContain('Cannot save')
  expect(localStorage.length).toBe(1)
  await button(wrapper, '取消修改').trigger('click')
  expect(wrapper.get('input[type="number"]').element.value).toBe('0')
  expect(document.activeElement).toBe(wrapper.get('input[type="number"]').element)
  expect(localStorage.length).toBe(0)
})
