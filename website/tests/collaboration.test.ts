import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { RouterView, createMemoryHistory, createRouter } from 'vue-router'
import { renderDiscussion } from '../src/discussion-markdown'
import DiscussionMarkdown from '../src/components/DiscussionMarkdown.vue'
import DiffViewer from '../src/components/DiffViewer.vue'
import NewItemView from '../src/views/NewItemView.vue'
import AppSelect from '../src/components/AppSelect.vue'
import RepositoryDetailView from '../src/views/RepositoryDetailView.vue'
import { api } from '../src/api'
import type { Comparison, Pull, Reference } from '../src/collaboration'
import type { Repository } from '../src/repositories'
vi.mock('../src/api', async (original) => ({
  ...(await original<typeof import('../src/api')>()),
  api: vi.fn(),
}))
vi.mock('../src/auth', () => ({
  auth: { user: { id: 1, roles: ['member'] }, loaded: true },
  loadUser: vi.fn(),
}))
import realRouter from '../src/router'

enableAutoUnmount(afterEach)
beforeEach(() => {
  vi.mocked(api).mockReset()
  Element.prototype.scrollIntoView = vi.fn()
})
const target: Reference = {
  raw: '#1',
  url: '/repositories/alice/project/issue/1',
  title: 'Target <img onerror=bad>',
  kind: 'item',
  state: 'open',
}
function render(source: string, editable = false) {
  const host = document.createElement('div')
  host.innerHTML = renderDiscussion(source, [target], editable)
  return host
}
const repository = {
  id: 12,
  name: 'project',
  full_name: 'alice/project',
  owner: { id: 1, username: 'alice' },
  default_branch: 'main',
  archived: false,
  permissions: { is_owner: true, access: 'write', is_admin: false },
} as Repository
async function router(path: string, meta = {}) {
  const value = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/repositories/:owner/:name/:rest(.*)', component: { template: '<div />' }, meta },
    ],
  })
  await value.push(path)
  await value.isReady()
  return value
}

it('uses direct paths for repository tabs and PR sections with no tab query', () => {
  for (const [path, tab] of [
    ['code', 'code'],
    ['settings', 'settings'],
    ['issue', 'issue'],
    ['issue/1', 'issue'],
    ['pr', 'pr'],
    ['pr/2', 'pr'],
    ['pr/2/changes', 'pr'],
    ['pr/2/commits', 'pr'],
    ['pr/2/checks', 'pr'],
  ]) {
    const resolved = realRouter.resolve(`/repositories/alice/project/${path}`)
    expect(resolved.meta.repositoryTab).toBe(tab)
    expect(resolved.query).toEqual({})
    expect(resolved.matched).toHaveLength(2)
  }
  expect(realRouter.resolve('/repositories/alice/project/issue/123#issuecomment-12').hash).toBe(
    '#issuecomment-12',
  )
  expect(realRouter.resolve('/repositories/alice/project/pr/new').meta.repositoryTab).toBe('pr')
})

it('preserves scroll for local query updates and PR sections, but resets for a different record', async () => {
  const resolve = (path: string) => realRouter.resolve(`/repositories/alice/project/${path}`)
  const scroll = realRouter.options.scrollBehavior!
  for (const [from, to] of [
    ['pr/new', 'pr/new?file=b.txt'], ['pr/2/changes?file=a.txt', 'pr/2/changes?file=b.txt'],
    ['issue?q=first', 'issue?q=second'], ['pr/2', 'pr/2/changes'], ['pr/2/changes', 'pr/2/checks'],
  ]) expect(await scroll(resolve(to!), resolve(from!), null)).toBe(false)
  expect(await scroll(resolve('pr/3'), resolve('pr/2'), null)).toEqual({ top: 0 })
  expect(await scroll(resolve('issue/new?parent=2'), resolve('issue/new?parent=1'), null)).toEqual({ top: 0 })
  expect(await scroll(resolve('pr/2/changes#diff-file-right-1'), resolve('pr/2'), null)).toBe(false)
  expect(await scroll(resolve('issue?page=2'), resolve('issue'), { left: 0, top: 750 })).toEqual({ left: 0, top: 750 })
})

it('preserves repository child drafts across query changes and redirects same-repository legacy roots', async () => {
  vi.mocked(api).mockResolvedValue(repository)
  const r = createRouter({
    history: createMemoryHistory(),
    routes: [{
      path: '/repositories/:owner/:name',
      component: RepositoryDetailView,
      children: [
        {
          path: 'pr/new',
          component: { data: () => ({ draft: '' }), template: '<input v-model="draft" />' },
          meta: { repositoryTab: 'pr' },
        },
        { path: 'settings', component: { template: '<p>Settings content</p>' } },
      ],
    }],
  })
  await r.push('/repositories/alice/project/pr/new')
  await r.isReady()
  const wrapper = mount(RouterView, { global: { plugins: [r] } })
  await flushPromises()
  await wrapper.get('input').setValue('Keep this draft')
  await r.replace({ query: { file: 'docs/example.md' }, hash: '#line-1' })
  await flushPromises()
  expect(wrapper.get('input').element.value).toBe('Keep this draft')
  expect(api).toHaveBeenCalledTimes(1)
  await r.push('/repositories/alice/project?tab=settings')
  await flushPromises()
  expect(r.currentRoute.value.path).toBe('/repositories/alice/project/settings')
  expect(r.currentRoute.value.query).toEqual({})
  expect(wrapper.text()).toContain('Settings content')
})

it('autolinks prose references while preserving escaped text, code and existing links', () => {
  const host = render('#1 and `#1` and \\#1 and [#1](https://example.org)\n\n```\n#1\n```')
  expect(host.querySelectorAll('.discussion-reference')).toHaveLength(1)
  expect(host.querySelector('a')?.getAttribute('href')).toBe(target.url)
  expect(host.querySelectorAll('code')).toHaveLength(2)
  expect(host.querySelector('a[target="_blank"]')?.getAttribute('rel')).toBe('noopener noreferrer')
  expect(host.querySelector('img')).toBeNull()
})

it('sanitizes raw HTML, protocols and embedded content; private raster attachments remain images', () => {
  const host = render(
    '<script>bad()</script>\n\n<img src=x onerror=bad()>\n\n[bad](javascript:alert%281%29)\n\n![svg](data:image/svg+xml,bad)\n\n![attachment](/api/attachments/1)',
  )
  expect(host.querySelector('script,svg,iframe,[onerror]')).toBeNull()
  expect(host.querySelectorAll('img')).toHaveLength(1)
  expect(host.querySelector('img')?.getAttribute('src')).toBe('/api/attachments/1')
  expect([...host.querySelectorAll('a')].some((link) => link.href.startsWith('javascript:'))).toBe(
    false,
  )
})

it('maps editable task lists to their actual source lines, excluding prose and code', () => {
  const host = render('[ ] prose\n\n- [ ] Open\n- [x] Done\n\n```\n- [ ] code\n```', true)
  const inputs = [...host.querySelectorAll('input')]
  expect(inputs).toHaveLength(2)
  expect(inputs.map((input) => input.dataset.taskLine)).toEqual(['2', '3'])
  expect(inputs[0]?.disabled).toBe(false)
  expect(inputs[1]?.checked).toBe(true)
  expect(render('- [ ] readonly').querySelector('input')?.disabled).toBe(true)
})

it('ignores stale reference lookups after the body changes and toggles only the selected task', async () => {
  let stale!: (value: unknown) => void
  vi.mocked(api)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          stale = resolve
        }),
    )
    .mockResolvedValue([])
  const r = await router('/repositories/alice/project/issue/1')
  const wrapper = mount(DiscussionMarkdown, {
    props: { repositoryId: 12, source: '#1', itemId: 4, editable: true },
    global: { plugins: [r] },
  })
  await wrapper.setProps({ source: '- [ ] New\n- [x] Existing' })
  await flushPromises()
  stale([target])
  await flushPromises()
  expect(wrapper.find('.discussion-reference').exists()).toBe(false)
  await wrapper.findAll('input')[0]!.setValue(true)
  expect(wrapper.emitted('update')?.[0]?.[0]).toBe('- [x] New\n- [x] Existing')
  expect(api).toHaveBeenCalledWith(
    '/repositories/12/markdown/resolve',
    'POST',
    expect.objectContaining({ item_id: 4 }),
  )
})

it('creates a child Issue through the shared API and navigates to its permanent path', async () => {
  const r = await router('/repositories/alice/project/issue/new?parent=5', {
    repositoryTab: 'issue',
  })
  vi.mocked(api).mockImplementation(async (path, method) => {
    if (path.endsWith('/catalog') || path.endsWith('/members')) return []
    if (path === '/repositories/12/issues' && method === 'POST')
      return { url: '/repositories/alice/project/issue/6' }
    return []
  })
  const wrapper = mount(NewItemView, { props: { repository }, global: { plugins: [r] } })
  await flushPromises()
  await wrapper.get('input[required]').setValue('Child')
  await wrapper.get('.new-item-form').trigger('submit')
  await flushPromises()
  expect(api).toHaveBeenCalledWith(
    '/repositories/12/issues',
    'POST',
    expect.objectContaining({ title: 'Child', parent_id: 5 }),
  )
  expect(r.currentRoute.value.path).toBe('/repositories/alice/project/issue/6')
})

it('waits for an existing base branch and clears an in-flight comparison when both branches match', async () => {
  const r = await router('/repositories/alice/project/pr/new', { repositoryTab: 'pr' })
  let finish!: (value: unknown) => void
  vi.mocked(api).mockImplementation(async (path) => {
    if (path.endsWith('/branches'))
      return {
        items: [
          { name: 'feature', commit: 'b'.repeat(40) },
          { name: 'test', commit: 'a'.repeat(40) },
        ],
      }
    if (path.includes('/compare?'))
      return new Promise((resolve) => {
        finish = resolve
      })
    return []
  })
  const wrapper = mount(NewItemView, { props: { repository }, global: { plugins: [r] } })
  await flushPromises()
  const controls = wrapper.findAllComponents(AppSelect)
  const base = controls.find((control) => control.props('label') === '目标分支')!
  const head = controls.find((control) => control.props('label') === '源分支')!
  expect(base.props('modelValue')).toBe('')
  expect(api).not.toHaveBeenCalledWith(expect.stringContaining('/compare?'))
  base.vm.$emit('update:modelValue', 'test')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/repositories/12/compare?base=test&head=feature')
  expect(vi.mocked(api).mock.calls.filter(([path]) => path.includes('/compare?'))).toHaveLength(1)
  expect(wrapper.text()).toContain('正在比较提交')
  head.vm.$emit('update:modelValue', 'test')
  await flushPromises()
  expect(wrapper.text()).not.toContain('正在比较提交')
  finish({ commits: [], files: [], total: 0 })
  await flushPromises()
  expect(wrapper.findComponent(DiffViewer).exists()).toBe(false)
})

it('loads only the selected diff and sends the revision actually displayed for line comments', async () => {
  const r = await router('/repositories/alice/project/pr/1/changes?file=a.txt', {
    repositoryTab: 'pr',
  })
  const comparison = {
    base_sha: 'a'.repeat(40),
    head_sha: 'b'.repeat(40),
    merge_base: 'a'.repeat(40),
    files: [
      {
        path: 'a.txt',
        old_path: 'a.txt',
        status: 'M',
        old_sha: 'a',
        new_sha: 'b',
        additions: 1,
        deletions: 1,
        binary: false,
      },
    ],
    total: 1,
    additions: 1,
    deletions: 1,
    page: 1,
    page_size: 100,
    commits: [],
    commits_truncated: false,
  } as Comparison
  const pull = { base_sha: comparison.base_sha, head_sha: comparison.head_sha } as Pull
  vi.mocked(api).mockImplementation(async (path) =>
    path.includes('/diff?') ? { ...comparison, patch: '@@ -1 +1 @@\n-old\n+new\n' } : {},
  )
  const wrapper = mount(DiffViewer, {
    props: { repository, comparison, pull, number: 1, canComment: true },
    global: { plugins: [r] },
  })
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/repositories/12/pulls/1/diff?path=a.txt&page=1')
  await wrapper.get('tr.add .diff-line button').trigger('click')
  await flushPromises()
  await wrapper.get('textarea').setValue('Comment on shown revision')
  await wrapper.setProps({ pull: { ...pull, head_sha: 'c'.repeat(40) } })
  await wrapper.get('.line-comment-form').trigger('submit')
  await flushPromises()
  expect(api).toHaveBeenCalledWith(
    '/repositories/12/pulls/1/threads',
    'POST',
    expect.objectContaining({
      base_sha: comparison.base_sha,
      head_sha: comparison.head_sha,
      start_line: 1,
      end_line: 1,
    }),
  )
  expect(r.currentRoute.value.hash).toContain('right-1')
})
