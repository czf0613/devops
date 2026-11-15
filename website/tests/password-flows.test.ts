import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { api, ApiError } from '../src/api'
import type { User } from '../src/api'
import { auth } from '../src/auth'
import EmailCodeField from '../src/components/EmailCodeField.vue'
import SecurityView from '../src/views/SecurityView.vue'
import UserDetailView from '../src/views/UserDetailView.vue'

vi.mock('../src/api', async (original) => ({
  ...(await original<typeof import('../src/api')>()),
  api: vi.fn(),
}))
enableAutoUnmount(afterEach)
afterEach(() => {
  vi.useRealTimers()
  document.body.innerHTML = ''
})
const member: User = {
  id: 2,
  username: 'test_member',
  display_name: '测试成员',
  email: 'member@example.test',
  phone: null,
  status: 'active',
  roles: ['member'],
  has_legacy_password: false,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
}
beforeEach(() => {
  vi.mocked(api).mockReset()
  auth.user = { ...member }
  auth.notice = ''
  auth.loaded = true
})

async function routerAt(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/security', component: { template: '<div />' } },
      { path: '/users', component: { template: '<div />' } },
      { path: '/users/:id', component: { template: '<div />' } },
      { path: '/login', component: { template: '<div />' } },
    ],
  })
  await router.push(path)
  await router.isReady()
  return router
}

describe('email verification field', () => {
  it('requests a purpose-bound code and enforces the server resend cooldown', async () => {
    vi.useFakeTimers()
    vi.mocked(api).mockResolvedValue({ message: '验证码已发送', retry_after: 60 })
    const wrapper = mount(EmailCodeField, {
      props: { email: member.email, purpose: 'legacy_password' },
    })
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(api).toHaveBeenCalledWith('/auth/email-codes', 'POST', {
      email: member.email,
      purpose: 'legacy_password',
    })
    expect(wrapper.emitted('sent')).toEqual([['验证码已发送']])
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    await wrapper.get('button').trigger('click')
    expect(api).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(60000)
    expect(wrapper.get('button').attributes('disabled')).toBeUndefined()
  })
  it('honors Retry-After even when the server rejects the send request', async () => {
    vi.useFakeTimers()
    vi.mocked(api).mockRejectedValue(new ApiError('稍后再试', 429, 120))
    const wrapper = mount(EmailCodeField, { props: { email: member.email, purpose: 'login' } })
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('error')).toEqual([['稍后再试']])
    expect(wrapper.get('button').text()).toContain('120 秒后重发')
    await vi.advanceTimersByTimeAsync(120000)
    expect(wrapper.get('button').attributes('disabled')).toBeUndefined()
  })
})

describe('self-service password changes', () => {
  it('requires a code, submits it with the password, updates the account and preserves login', async () => {
    const router = await routerAt('/security')
    const wrapper = mount(SecurityView, {
      global: { plugins: [router], stubs: { SessionList: true, GitAuthorizationList: true, RegistryCredentialList: true } },
    })
    await wrapper.get('input[type="password"]').setValue('compatible-secret')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
    await wrapper.get('form').trigger('submit')
    expect(api).not.toHaveBeenCalled()
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('123456')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeUndefined()
    vi.mocked(api).mockResolvedValue({
      message: '兼容密码已更新',
      user: { ...member, has_legacy_password: true },
    })
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api).toHaveBeenCalledWith('/me/legacy-password', 'PUT', {
      password: 'compatible-secret',
      code: '123456',
    })
    expect(auth.user?.has_legacy_password).toBe(true)
    expect(router.currentRoute.value.path).toBe('/security')
    expect(auth.notice).toBe('')
    expect(wrapper.get('input[type="password"]').element.value).toBe('')
    expect(wrapper.get('input[autocomplete="one-time-code"]').element.value).toBe('')
    expect(wrapper.get('[role="status"]').text()).toContain('兼容密码已更新')
  })
  it('preserves the existing password status and displays code validation errors', async () => {
    const router = await routerAt('/security')
    vi.mocked(api).mockRejectedValue(new ApiError('验证码错误或已过期', 400))
    const wrapper = mount(SecurityView, {
      global: { plugins: [router], stubs: { SessionList: true, GitAuthorizationList: true, RegistryCredentialList: true } },
    })
    await wrapper.get('input[type="password"]').setValue('compatible-secret')
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('999999')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toBe('验证码错误或已过期')
    expect(auth.user?.has_legacy_password).toBe(false)
    expect(router.currentRoute.value.path).toBe('/security')
  })
  it('clears only after email verification and confirmation, without requiring a new password', async () => {
    auth.user = { ...member, has_legacy_password: true }
    vi.spyOn(HTMLDialogElement.prototype, 'showModal').mockImplementation(function () {
      this.setAttribute('open', '')
    })
    vi.spyOn(HTMLDialogElement.prototype, 'close').mockImplementation(function () {
      this.removeAttribute('open')
    })
    const router = await routerAt('/security')
    const wrapper = mount(SecurityView, {
      attachTo: document.body,
      global: { plugins: [router], stubs: { SessionList: true, GitAuthorizationList: true, RegistryCredentialList: true } },
    })
    const clear = wrapper.findAll('button').find((button) => button.text() === '清除密码')!
    expect(clear.attributes('disabled')).toBeDefined()
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('123456')
    await clear.trigger('click')
    await nextTick()
    expect(api).not.toHaveBeenCalled()
    vi.mocked(api).mockResolvedValue({ message: '兼容密码已清除', user: member })
    document
      .querySelector('dialog[open]')!
      .querySelectorAll<HTMLButtonElement>('button')[1]!
      .click()
    await flushPromises()
    expect(api).toHaveBeenCalledWith('/me/legacy-password', 'PUT', {
      password: null,
      code: '123456',
    })
    expect(auth.user?.has_legacy_password).toBe(false)
    expect(router.currentRoute.value.path).toBe('/security')
  })
})

it('administrator detail offers clear only and sends only a null password after confirmation', async () => {
  auth.user = { ...member, id: 1, roles: ['admin'] }
  vi.spyOn(HTMLDialogElement.prototype, 'showModal').mockImplementation(function () {
    this.setAttribute('open', '')
  })
  vi.spyOn(HTMLDialogElement.prototype, 'close').mockImplementation(function () {
    this.removeAttribute('open')
  })
  vi.mocked(api).mockImplementation(async (path, method = 'GET') => {
    if (path === '/roles') return []
    if (method === 'PUT') return { message: '兼容密码已清除', user: member }
    return { ...member, has_legacy_password: true }
  })
  const router = await routerAt('/users/2')
  const wrapper = mount(UserDetailView, {
    attachTo: document.body,
    global: { plugins: [router], stubs: { SessionList: true, GitAuthorizationList: true, RegistryCredentialList: true } },
  })
  await flushPromises()
  expect(wrapper.find('input[type="password"]').exists()).toBe(false)
  expect(wrapper.text()).toContain('管理员只能清除兼容密码')
  vi.mocked(api).mockClear()
  const clear = wrapper.findAll('button').find((button) => button.text() === '清除兼容密码')!
  await clear.trigger('click')
  await nextTick()
  expect(api).not.toHaveBeenCalled()
  document.querySelector('dialog[open]')!.querySelectorAll<HTMLButtonElement>('button')[1]!.click()
  await flushPromises()
  expect(api).toHaveBeenCalledExactlyOnceWith('/users/2/legacy-password', 'PUT', { password: null })
  expect(clear.attributes('disabled')).toBeDefined()
  expect(auth.user?.roles).toEqual(['admin'])
  expect(router.currentRoute.value.path).toBe('/users/2')
})
