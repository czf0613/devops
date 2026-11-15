import { afterEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { defineComponent, nextTick, ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import AppNotice from '../src/components/AppNotice.vue'
import App from '../src/App.vue'
import { renderMarkdown } from '../src/markdown'

enableAutoUnmount(afterEach)
afterEach(() => { vi.useRealTimers(); localStorage.clear() })

it('expires success notices, clears their model, and can show the same message again', async () => {
  vi.useFakeTimers()
  const wrapper = mount(defineComponent({ components: { AppNotice }, setup: () => ({ text: ref('已保存') }), template: '<AppNotice v-model="text" tone="success" />' }))
  await vi.advanceTimersByTimeAsync(4999)
  expect(wrapper.text()).toBe('已保存')
  await vi.advanceTimersByTimeAsync(1)
  expect(wrapper.find('[role="status"]').exists()).toBe(false)
  expect(wrapper.vm.text).toBe('')
  wrapper.vm.text = '已保存'
  await nextTick()
  expect(wrapper.text()).toBe('已保存')
  await wrapper.get('[aria-label="关闭提示"]').trigger('click')
  expect(wrapper.vm.text).toBe('')
  expect(wrapper.find('[role="status"]').exists()).toBe(false)
})
it('restarts on a new message and cancels timers on unmount', async () => {
  vi.useFakeTimers()
  const wrapper = mount(AppNotice, { props: { tone: 'success', modelValue: '已保存' } })
  await vi.advanceTimersByTimeAsync(4000)
  await wrapper.setProps({ modelValue: '已复制' })
  await vi.advanceTimersByTimeAsync(4000)
  expect(wrapper.text()).toBe('已复制')
  wrapper.unmount()
  expect(vi.getTimerCount()).toBe(0)
})
it('keeps errors, information, and explicitly persistent feedback visible', async () => {
  vi.useFakeTimers()
  const error = mount(AppNotice, { props: { tone: 'error' }, slots: { default: '保存失败' } })
  const info = mount(AppNotice, { slots: { default: '已归档' } })
  const ongoing = mount(AppNotice, { props: { tone: 'success', duration: 0 }, slots: { default: '正在返回客户端' } })
  await vi.advanceTimersByTimeAsync(30000)
  expect(error.get('[role="alert"]').text()).toBe('保存失败')
  expect(info.text()).toBe('已归档')
  expect(ongoing.text()).toBe('正在返回客户端')
})

it('puts floating notices in a shared overlay without adding content to their page', async () => {
  vi.useFakeTimers()
  const first = mount(AppNotice, { props: { modelValue: '已复制', tone: 'success', placement: 'floating' } })
  const second = mount(AppNotice, { props: { modelValue: '复制失败', tone: 'error', placement: 'floating' } })
  expect(first.find('.app-notice').exists()).toBe(false)
  const host = document.getElementById('app-notice-stack')!
  expect(host.querySelectorAll('.app-notice').length).toBe(2)
  expect(host.textContent).toContain('已复制')
  await vi.advanceTimersByTimeAsync(5000)
  expect(host.textContent).not.toContain('已复制')
  expect(host.textContent).toContain('复制失败')
  second.unmount()
  expect(host.querySelector('.app-notice')).toBeNull()
  first.unmount()
  host.remove()
})

const context = { repositoryId: 12, repositoryPath: '/repositories/owner/example', branch: 'feature/readme', filePath: 'docs/README.md' }
function rendered(source: string) {
  const host = document.createElement('div')
  host.innerHTML = renderMarkdown(source, context)
  return host
}
it('renders headings, lists, code and tables while escaping raw HTML and dangerous URLs', () => {
  const host = rendered('# Overview\n\n**Strong** and ~~old~~\n\n- One\n- Two\n\n```html\n<script>alert(1)</script>\n```\n\n| Name | Value |\n| --- | --- |\n| A | B |\n\n<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n\n[bad](javascript:alert%281%29)\n[encoded](java&#x73;cript:alert%281%29)\n![bad](data:image/svg+xml,bad)\n')
  expect(host.querySelector('h1')?.textContent).toBe('Overview')
  expect(host.querySelectorAll('li').length).toBe(2)
  expect(host.querySelector('s')?.textContent).toBe('old')
  expect(host.querySelector('table td')?.textContent).toBe('A')
  expect(host.querySelector('pre code')?.textContent).toContain('<script>')
  expect(host.querySelector('script,iframe,svg,img,[onerror]')).toBeNull()
  expect([...host.querySelectorAll('a')].some(a => a.href.startsWith('javascript:'))).toBe(false)
})
it('resolves relative links and images inside the same repository and selected branch', () => {
  const host = rendered('[Guide](./中文%20说明.md)\n\n[Root](../README.md)\n\n![diagram](./assets/图.png)\n\n[External](https://example.com)\n\n# Hello\n[Jump](#hello)')
  const guide = new URL(host.querySelector('a')!.getAttribute('href')!, 'https://devops.test')
  expect(guide.pathname).toBe(context.repositoryPath)
  expect(guide.searchParams.get('ref')).toBe('feature/readme')
  expect(guide.searchParams.get('path')).toBe('docs/中文 说明.md')
  const folder = rendered('[Folder](./assets/)').querySelector('a')!
  expect(new URL(folder.getAttribute('href')!, 'https://devops.test').searchParams.get('path')).toBe('docs/assets')
  const root = new URL(host.querySelectorAll('a')[1]!.getAttribute('href')!, 'https://devops.test')
  expect(root.searchParams.get('path')).toBe('README.md')
  const image = new URL(host.querySelector('img')!.getAttribute('src')!, 'https://devops.test')
  expect(image.pathname).toBe('/api/repositories/12/image')
  expect(image.searchParams.get('path')).toBe('docs/assets/图.png')
  expect(image.searchParams.get('ref')).toBe('feature/readme')
  expect(host.querySelector('a[target="_blank"]')?.getAttribute('rel')).toBe('noopener noreferrer')
  expect(host.querySelector('h1')?.id).toBe('readme-hello')
  expect(host.querySelector('a[href="#readme-hello"]')).not.toBeNull()
})

it('collapses the whole sidebar, keeps repository navigation accessible and remembers the choice', async () => {
  const router = createRouter({ history: createMemoryHistory(), routes: ['/', '/repositories', '/profile', '/security', '/status'].map(path => ({ path, component: { template: '<div>Page</div>' } })) })
  await router.push('/repositories')
  await router.isReady()
  const wrapper = mount(App, { global: { plugins: [router] } })
  await wrapper.get('[aria-label="收起侧边栏"]').trigger('click')
  expect(wrapper.get('.workspace').classes()).toContain('sidebar-collapsed')
  expect(wrapper.get('[aria-controls="workspace-sidebar"]').attributes('aria-expanded')).toBe('false')
  expect(wrapper.get('a[aria-label="工程仓库"]').attributes('title')).toBe('工程仓库')
  expect(localStorage.getItem('devops.sidebar.collapsed')).toBe('true')
  await wrapper.get('a[aria-label="个人资料"]').trigger('click')
  await flushPromises()
  expect(wrapper.get('.workspace').classes()).toContain('sidebar-collapsed')
  wrapper.unmount()
  const restored = mount(App, { global: { plugins: [router] } })
  expect(restored.get('.workspace').classes()).toContain('sidebar-collapsed')
  await restored.get('[aria-label="展开侧边栏"]').trigger('click')
  expect(localStorage.getItem('devops.sidebar.collapsed')).toBe('false')
})
