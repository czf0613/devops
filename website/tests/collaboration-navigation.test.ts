import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { DOMWrapper, enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { api } from '../src/api'
import ItemRelations from '../src/components/ItemRelations.vue'
import IssueListView from '../src/views/IssueListView.vue'
import type { Repository } from '../src/repositories'
import type { Relation, WorkItem } from '../src/collaboration'

vi.mock('../src/api', async original => ({ ...(await original<typeof import('../src/api')>()), api: vi.fn() }))
enableAutoUnmount(afterEach)
beforeEach(() => { vi.mocked(api).mockReset() })
afterEach(() => { document.body.innerHTML = '' })
const repository = { id: 12, full_name: 'alice/project', name: 'project', owner: { id: 1, username: 'alice' },
  archived: false, permissions: { access: 'write', is_owner: true, is_admin: false } } as Repository
const item = (number: number) => ({
  id: number, number, repository_id: 12, kind: 'issue', title: `Issue ${number}`, state: 'open',
  url: `/repositories/alice/project/issue/${number}`, permissions: { can_manage: true },
  relations: { items: [], children_total: 0, children_completed: 0 },
}) as WorkItem
function relation(id: number, parent: WorkItem, child: WorkItem): Relation {
  return { id, kind: 'parent', source_id: parent.id, target_id: child.id, position: 0, item: child }
}
async function router(path: string) {
  const r = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/:pathMatch(.*)*', component: {} },
    { path: '/repositories/:owner/:name/issue', component: {}, meta: { repositoryTab: 'issue' } },
    { path: '/repositories/:owner/:name/pr', component: {}, meta: { repositoryTab: 'pr' } },
  ] })
  await r.push(path)
  return r
}

it('expands only descendants without repeating the relationship panel or parent backlinks', async () => {
  const parent = item(2), child = item(3), grandchild = item(4)
  const first = relation(20, parent, child), second = relation(21, child, grandchild)
  parent.relations = { items: [first], children_total: 1, children_completed: 0 }
  child.relations = { items: [{ ...first, item: parent }, second], children_total: 1, children_completed: 0 }
  grandchild.relations.items = [{ ...second, item: child }]
  vi.mocked(api).mockImplementation(async path => {
    if (path === '/repositories/12') return repository
    if (path.endsWith('/items/3')) return child
    if (path.endsWith('/items/4')) return grandchild
    throw new Error(`Unexpected request: ${path}`)
  })
  const r = await router(parent.url)
  const wrapper = mount(ItemRelations, { props: { item: parent, repository }, global: { plugins: [r] } })
  await wrapper.get('[aria-label="展开 Issue 3"]').trigger('click')
  await flushPromises()
  expect(wrapper.find(`#${wrapper.get('[aria-label="收起 Issue 3"]').attributes('aria-controls')}`).exists()).toBe(true)
  expect(wrapper.findAll('h3')).toHaveLength(1)
  expect(wrapper.findAll('.relation-toggle')).toHaveLength(1)
  expect(wrapper.findAll('.subissue-title').map(link => link.text())).toEqual(['Issue 3 #3', 'Issue 4 #4'])
  expect(wrapper.text()).not.toContain('父 Issue')
  await wrapper.get('[aria-label="展开 Issue 4"]').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('没有下一级子 Issue')
  await wrapper.get('[aria-label="收起 Issue 3"]').trigger('click')
  expect(wrapper.findAll('.subissue-title')).toHaveLength(1)
})

it('allows retry after an expansion fails and prevents duplicate requests while loading', async () => {
  const parent = item(2), child = item(3)
  parent.relations = { items: [relation(20, parent, child)], children_total: 1, children_completed: 0 }
  let reject!: (reason: Error) => void
  vi.mocked(api).mockImplementation(async path => path === '/repositories/12' ? repository : new Promise((_, fail) => { reject = fail }))
  const r = await router(parent.url)
  const wrapper = mount(ItemRelations, { props: { item: parent, repository }, global: { plugins: [r] } })
  const toggle = wrapper.get('[aria-label="展开 Issue 3"]')
  await toggle.trigger('click')
  await toggle.trigger('click')
  expect(api).toHaveBeenCalledTimes(2)
  expect(toggle.attributes('disabled')).toBeDefined()
  reject(new Error('offline'))
  await flushPromises()
  expect(wrapper.text()).toContain('offline')
  expect(toggle.attributes('disabled')).toBeUndefined()
  vi.mocked(api).mockImplementation(async path => path === '/repositories/12' ? repository : child)
  await toggle.trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('没有下一级子 Issue')
  expect(wrapper.text()).not.toContain('offline')
})

function mockList() {
  vi.mocked(api).mockImplementation(async path => {
    if (path.endsWith('/catalog')) return [{ id: 10, kind: 'label', name: 'Bug' }]
    if (path.endsWith('/members')) return [
      { user_id: 1, username: 'alice', display_name: 'Alice' },
      { user_id: 2, username: 'bob', display_name: 'Bob' },
    ]
    if (path.endsWith('/saved-filters')) return [{ id: 9, name: 'My bugs', query: { q: 'saved search', label: '10', state: 'closed' } }]
    if (path.includes('/items?')) return { items: [], total: 0, page: 1, page_size: 25, counts: {} }
    throw new Error(`Unexpected request: ${path}`)
  })
}
it.each(['issue', 'pr'])('filters %s from the compact menu, preserves other conditions and clears individual chips', async kind => {
  mockList()
  const r = await router(`/repositories/alice/project/${kind}?q=original&state=closed&page=2`)
  const wrapper = mount(IssueListView, { props: { repository }, global: { plugins: [r] }, attachTo: document.body })
  await flushPromises()
  expect(wrapper.get('details').element.open).toBe(false)
  const author = wrapper.get('.issue-filter-menus button')
  await author.trigger('click')
  const input = new DOMWrapper(document.querySelector<HTMLInputElement>('[aria-label="搜索作者"]')!)
  await input.setValue('@bob')
  await input.trigger('keydown', { key: 'Enter' })
  await flushPromises()
  expect(r.currentRoute.value.query).toEqual({ q: 'original', state: 'closed', author: '2' })
  const lastRequest = vi.mocked(api).mock.calls.filter(([path]) => path.includes('/items?')).at(-1)![0]
  const query = new URLSearchParams(lastRequest.split('?')[1])
  expect(query.get('kind')).toBe(kind === 'pr' ? 'pull' : 'issue')
  expect(query.get('author')).toBe('2')
  expect(wrapper.get('[aria-label="移除作者筛选"]').text()).toContain('Bob')
  await wrapper.get('[aria-label="移除作者筛选"]').trigger('click')
  await flushPromises()
  expect(r.currentRoute.value.query).toEqual({ q: 'original', state: 'closed' })
  await wrapper.get('[aria-label="移除搜索筛选"]').trigger('click')
  await flushPromises()
  expect(wrapper.get('.issue-search input').element.value).toBe('')
  r.back()
  await flushPromises()
  expect(wrapper.get('.issue-search input').element.value).toBe('original')
})

it('applies saved filters, keeps search in sync, and dismisses secondary filters with focus restored', async () => {
  mockList()
  const r = await router('/repositories/alice/project/issue')
  const wrapper = mount(IssueListView, { props: { repository }, global: { plugins: [r] }, attachTo: document.body })
  await flushPromises()
  const details = wrapper.get('details')
  await wrapper.get('summary').trigger('click')
  expect(details.element.open).toBe(true)
  await wrapper.get('.saved-filter-row button').trigger('click')
  await flushPromises()
  expect(details.element.open).toBe(false)
  expect(document.activeElement).toBe(wrapper.get('summary').element)
  expect(wrapper.get('.issue-search input').element.value).toBe('saved search')
  expect(wrapper.get('[aria-label="移除标签筛选"]').text()).toContain('Bug')
  await wrapper.get('summary').trigger('click')
  await wrapper.get('input[type="date"]').setValue('2026-10-03')
  await flushPromises()
  expect(r.currentRoute.value.query.updated_after).toBe('2026-10-03T00:00:00Z')
  await details.trigger('keydown', { key: 'Escape' })
  expect(details.element.open).toBe(false)
  expect(document.activeElement).toBe(wrapper.get('summary').element)
  await wrapper.get('.active-issue-filters > .text-button').trigger('click')
  await flushPromises()
  expect(r.currentRoute.value.query).toEqual({ state: 'closed' })
})
