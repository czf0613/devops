import { afterEach, describe, expect, it, vi } from 'vitest'
import { DOMWrapper, enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import AppSelect from '../src/components/AppSelect.vue'
import AppConfirm from '../src/components/AppConfirm.vue'
import SidebarNav from '../src/components/SidebarNav.vue'

enableAutoUnmount(afterEach)
afterEach(() => {
  document.body.innerHTML = ''
})
const options = [
  { value: 'active', label: '正常' },
  { value: 'pending', label: '待开通', disabled: true },
  { value: 'disabled', label: '停用' },
]
function select() {
  return mount(AppSelect, {
    attachTo: document.body,
    props: { label: '账号状态', modelValue: 'active', options },
  })
}
function option(index: number) {
  return document.querySelectorAll<HTMLElement>('[role="option"]')[index]!
}

describe('custom select', () => {
  it('searches compact filters, preserves search on option refresh, and returns focus after choosing', async () => {
    const wrapper = mount(AppSelect, { attachTo: document.body, props: {
      label: '作者', compact: true, searchable: true, modelValue: '1', options: [
        { value: '', label: '所有作者' }, { value: '1', label: 'Alice (@alice)' },
        { value: '2', label: 'Bob (@bob)' },
      ],
    } })
    const trigger = wrapper.get('button')
    expect(trigger.text()).toBe('作者')
    await trigger.trigger('click')
    const input = new DOMWrapper(document.querySelector<HTMLInputElement>('[aria-label="搜索作者"]')!)
    expect(document.activeElement).toBe(input.element)
    await input.setValue('@bob')
    expect(document.querySelectorAll('[role="option"]')).toHaveLength(1)
    await wrapper.setProps({ options: [...wrapper.props('options'), { value: '3', label: 'Carol (@carol)' }] })
    expect(input.element.value).toBe('@bob')
    await input.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('change')).toEqual([['2']])
    expect(document.querySelector('[role="dialog"]')).toBeNull()
    expect(document.activeElement).toBe(trigger.element)
    await wrapper.setProps({ modelValue: '2' })
    await trigger.trigger('click')
    const reopened = document.querySelector<HTMLInputElement>('[aria-label="搜索作者"]')!
    expect(reopened.value).toBe('')
    expect(document.getElementById(reopened.getAttribute('aria-activedescendant')!)?.textContent).toContain('Bob')
  })

  it('dismisses empty filter searches with Escape or outside focus without changing the value', async () => {
    const wrapper = mount(AppSelect, { attachTo: document.body, props: {
      label: '标签', compact: true, searchable: true, options, modelValue: 'active',
    } })
    const trigger = wrapper.get('button')
    await trigger.trigger('click')
    const input = new DOMWrapper(document.querySelector<HTMLInputElement>('[aria-label="搜索标签"]')!)
    await input.setValue('not found')
    expect(document.querySelector('[role="status"]')?.textContent).toBe('没有匹配项')
    await input.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('change')).toBeUndefined()
    await input.trigger('keydown', { key: 'Escape' })
    expect(document.activeElement).toBe(trigger.element)
    expect(trigger.attributes('aria-expanded')).toBe('false')
    await trigger.trigger('click')
    const outside = document.createElement('button')
    document.body.append(outside)
    outside.focus()
    await nextTick()
    expect(trigger.attributes('aria-expanded')).toBe('false')
    expect(wrapper.emitted('change')).toBeUndefined()
  })

  it('renders a custom popup and commits mouse selection', async () => {
    const wrapper = select()
    const trigger = wrapper.get('[role="combobox"]')
    await trigger.trigger('click')
    expect(trigger.attributes('aria-expanded')).toBe('true')
    expect(document.querySelector('select')).toBeNull()
    expect(document.querySelector('[role="listbox"]')?.parentElement).toBe(document.body)
    expect(option(0).getAttribute('aria-selected')).toBe('true')
    option(1).click()
    await nextTick()
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    option(2).click()
    await nextTick()
    expect(wrapper.emitted('update:modelValue')).toEqual([['disabled']])
    expect(trigger.attributes('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(trigger.element)
  })

  it('skips disabled options, cancels with Escape, and commits on Enter or Tab', async () => {
    const wrapper = select()
    const trigger = wrapper.get('[role="combobox"]')
    await trigger.trigger('keydown', { key: 'ArrowDown' })
    await trigger.trigger('keydown', { key: 'ArrowDown' })
    expect(trigger.attributes('aria-activedescendant')).toBe(option(2).id)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    await trigger.trigger('keydown', { key: 'Escape' })
    expect(trigger.attributes('aria-expanded')).toBe('false')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    await trigger.trigger('keydown', { key: 'End' })
    await trigger.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('update:modelValue')).toEqual([['disabled']])
    await trigger.trigger('keydown', { key: 'Home' })
    const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
    trigger.element.dispatchEvent(tab)
    await nextTick()
    expect(tab.defaultPrevented).toBe(false)
    expect(wrapper.emitted('change')).toEqual([['disabled'], ['active']])
  })

  it('supports type-ahead, outside dismissal, and disabled state', async () => {
    const wrapper = select()
    const trigger = wrapper.get('[role="combobox"]')
    await trigger.trigger('keydown', { key: '停' })
    expect(trigger.attributes('aria-activedescendant')).toBe(option(2).id)
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await nextTick()
    expect(document.querySelector('[role="listbox"]')).toBeNull()
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    await trigger.trigger('click')
    await wrapper.setProps({ disabled: true })
    expect(document.querySelector('[role="listbox"]')).toBeNull()
    expect(trigger.attributes('disabled')).toBeDefined()
  })

  it('positions above a trigger near the viewport edge and keeps keyboard options visible', async () => {
    const wrapper = select()
    const trigger = wrapper.get('[role="combobox"]')
    vi.spyOn(trigger.element, 'getBoundingClientRect').mockReturnValue({
      left: 10,
      top: 700,
      bottom: 744,
      width: 220,
      height: 44,
      right: 230,
      x: 10,
      y: 700,
      toJSON() {},
    })
    await wrapper.setProps({
      options: Array.from({ length: 25 }, (_, index) => ({
        value: String(index),
        label: `选项 ${index}`,
      })),
    })
    await trigger.trigger('click')
    const popup = document.querySelector<HTMLElement>('[role="listbox"]')!
    expect(parseFloat(popup.style.top)).toBeLessThan(700)
    Object.defineProperty(popup, 'clientHeight', { value: 250 })
    Object.defineProperty(option(24), 'offsetTop', { value: 1000 })
    Object.defineProperty(option(24), 'offsetHeight', { value: 42 })
    await trigger.trigger('keydown', { key: 'End' })
    await nextTick()
    expect(popup.scrollTop).toBe(792)
  })
})

describe('role-aware sidebar', () => {
  async function nav(roles: string[], path = '/') {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<div />' } },
        { path: '/repositories', component: { template: '<div />' } },
        { path: '/repositories/:owner/:name', component: { template: '<div />' } },
        { path: '/notifications', component: { template: '<div />' } },
        { path: '/guide', component: { template: '<div />' } },
        { path: '/profile', component: { template: '<div />' } },
        { path: '/security', component: { template: '<div />' } },
        { path: '/users', component: { template: '<div />' }, meta: { admin: true } },
        { path: '/users/:id', component: { template: '<div />' }, meta: { admin: true } },
        { path: '/audit', component: { template: '<div />' }, meta: { admin: true } },
        { path: '/admin/registry', component: { template: '<div />' }, meta: { admin: true } },
      ],
    })
    await router.push(path)
    await router.isReady()
    return {
      wrapper: mount(SidebarNav, { props: { roles }, global: { plugins: [router] } }),
      router,
    }
  }
  it('excludes all administrator navigation for members and responds to role changes', async () => {
    const { wrapper } = await nav(['member'])
    expect(wrapper.findAll('a').map((link) => link.text())).toEqual([
      '工作台',
      '工程仓库',
      '镜像管理',
      '通知',
      '使用指南',
      '个人资料',
      '账号安全',
    ])
    expect(wrapper.find('[aria-controls="admin-navigation"]').exists()).toBe(false)
    await wrapper.setProps({ roles: ['member', 'admin'] })
    await wrapper.get('[aria-controls="admin-navigation"]').trigger('click')
    expect(wrapper.get('#admin-navigation').text()).toContain('人员管理')
    await wrapper.setProps({ roles: ['member'] })
    expect(wrapper.find('.nav-group').exists()).toBe(false)
  })
  it('opens the second-level group on administrator pages and allows collapsing', async () => {
    const { wrapper, router } = await nav(['admin'], '/users')
    expect(wrapper.get('[aria-controls="admin-navigation"]').attributes('aria-expanded')).toBe('true')
    expect(wrapper.findAll('.nav-children a').length).toBe(5)
    expect(wrapper.get('a[aria-label="构建机"]').attributes('href')).toBe('/admin/ci/workers')
    expect(wrapper.get('a[aria-label="仓库管理"]').attributes('href')).toBe('/admin/repositories')
    expect(wrapper.get('a[aria-label="Registry 管理"]').attributes('href')).toBe('/admin/registry')
    await wrapper.get('[aria-controls="admin-navigation"]').trigger('click')
    expect(wrapper.find('.nav-children').exists()).toBe(false)
    await router.push('/audit')
    await flushPromises()
    expect(wrapper.get('[aria-controls="admin-navigation"]').attributes('aria-expanded')).toBe('true')
    await router.push('/users/2')
    await flushPromises()
    expect(wrapper.get('a[href="/users"]').classes()).toContain('is-active')
    expect(wrapper.get('a[href="/users"]').attributes('aria-current')).toBe('location')
  })
  it('nests repositories under the workbench and opens the group for a repository URL', async () => {
    const { wrapper, router } = await nav(['member'], '/repositories/owner/project')
    expect(wrapper.get('#workspace-navigation a').text()).toBe('工程仓库')
    expect(wrapper.get('#workspace-navigation a').classes()).toContain('is-active')
    expect(wrapper.get('a[href="/"]').text()).toBe('工作台')
    await wrapper.get('[aria-controls="workspace-navigation"]').trigger('click')
    expect(wrapper.find('#workspace-navigation').exists()).toBe(false)
    await router.push('/')
    await flushPromises()
    expect(wrapper.get('[aria-controls="workspace-navigation"]').attributes('aria-expanded')).toBe('true')
  })
})

it('custom confirmation resolves cancel/accept and restores focus', async () => {
  vi.spyOn(HTMLDialogElement.prototype, 'showModal').mockImplementation(function () {
    this.setAttribute('open', '')
  })
  vi.spyOn(HTMLDialogElement.prototype, 'close').mockImplementation(function () {
    this.removeAttribute('open')
  })
  const opener = document.createElement('button')
  document.body.append(opener)
  opener.focus()
  const wrapper = mount(AppConfirm, { attachTo: document.body })
  const canceled = wrapper.vm.ask('清除后不能恢复', '清除密码？')
  await nextTick()
  const dialog = document.querySelector('dialog')!
  expect(dialog.textContent).toContain('清除密码？')
  dialog.dispatchEvent(new Event('cancel', { cancelable: true }))
  expect(await canceled).toBe(false)
  expect(document.activeElement).toBe(opener)
  const accepted = wrapper.vm.ask('确认操作')
  await nextTick()
  dialog.querySelectorAll<HTMLButtonElement>('button')[1]!.click()
  expect(await accepted).toBe(true)
  expect(dialog.hasAttribute('open')).toBe(false)
})
