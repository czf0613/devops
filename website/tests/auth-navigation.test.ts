import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { api, ApiError, type User } from '../src/api'
import { auth, forgetLogin, loadUser } from '../src/auth'
import router from '../src/router'
import App from '../src/App.vue'

vi.mock('../src/api', async original => ({ ...(await original<typeof import('../src/api')>()), api: vi.fn() }))
enableAutoUnmount(afterEach)
const user = { id: 1, display_name: 'Alice', roles: ['member'] } as User
beforeEach(async () => {
  Object.assign(auth, { user: null, loaded: false, error: '', notice: '', retryPath: '' })
  vi.mocked(api).mockReset().mockImplementation(async path => path === '/me' ? user
    : { items: [], total: 0, page: 1, page_size: 30, unread_count: 0 })
  window.scrollTo = vi.fn()
  await router.replace('/notifications')
  vi.mocked(api).mockClear()
})

it('keeps the known session and loaded state on network errors, but clears them on 401', async () => {
  vi.mocked(api).mockRejectedValueOnce(new ApiError('offline', 0))
  expect(await loadUser()).toBe(false)
  expect(auth.user).toEqual(user)
  expect(auth.loaded).toBe(true)
  expect(auth.error).toBe('offline')
  vi.mocked(api).mockRejectedValueOnce(new ApiError('expired', 401))
  expect(await loadUser()).toBe(true)
  expect(auth.user).toBeNull()
  expect(auth.error).toBe('')
})

it('does not treat an unavailable initial session check as a confirmed login state', async () => {
  auth.loaded = false
  auth.user = null
  vi.mocked(api).mockRejectedValueOnce(new ApiError('unavailable', 502))
  expect(await loadUser()).toBe(false)
  expect(auth.loaded).toBe(false)
  expect(auth.user).toBeNull()
})

it('does not refetch the session for file selection or PR section changes', async () => {
  await router.push('/repositories/alice/project/pr/1/changes')
  vi.mocked(api).mockClear()
  await router.replace({ query: { file: 'b.txt' } })
  await router.push('/repositories/alice/project/pr/1/checks?file=b.txt')
  expect(api).not.toHaveBeenCalled()
  expect(auth.user).toEqual(user)
  await router.push('/repositories/alice/project/pr/2')
  expect(api).toHaveBeenCalledWith('/me')
})

it('cancels navigation on a transient failure and retries the full destination without remounting the current page', async () => {
  const wrapper = mount(App, { global: { plugins: [router] } })
  await flushPromises()
  const heading = wrapper.get('.main h1').element
  vi.mocked(api).mockRejectedValueOnce(new ApiError('temporarily unavailable', 503))
  await router.push('/profile?from=notifications#profile')
  await flushPromises()
  expect(router.currentRoute.value.path).toBe('/notifications')
  expect(wrapper.get('.main h1').element).toBe(heading)
  expect(wrapper.text()).toContain('temporarily unavailable')
  expect(auth.retryPath).toBe('/profile?from=notifications#profile')
  const retry = wrapper.findAll('button').find(button => button.text() === '重试')!
  await retry.trigger('click')
  await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/profile?from=notifications#profile'))
  expect(auth.error).toBe('')
})

it('still redirects expired sessions and denies admin routes to ordinary members', async () => {
  await router.push('/users')
  expect(router.currentRoute.value.path).toBe('/')
  vi.mocked(api).mockRejectedValue(new ApiError('expired', 401))
  await router.push('/profile')
  expect(router.currentRoute.value.path).toBe('/login')
  expect(router.currentRoute.value.query.redirect).toBe('/profile')
  expect(auth.user).toBeNull()
})

it('reaches login after a confirmed logout even when the service is temporarily unavailable', async () => {
  forgetLogin('已退出登录')
  vi.mocked(api).mockRejectedValue(new ApiError('offline', 0))
  await router.replace('/login')
  expect(router.currentRoute.value.path).toBe('/login')
  expect(api).not.toHaveBeenCalled()
})

it('hides protected page content until the initial session check succeeds', async () => {
  auth.loaded = false
  auth.user = null
  auth.error = 'temporarily unavailable'
  auth.retryPath = '/notifications'
  const wrapper = mount(App, { global: { plugins: [router] } })
  await flushPromises()
  expect(wrapper.find('.notification-list').exists()).toBe(false)
  expect(api).not.toHaveBeenCalled()
  await wrapper.findAll('button').find(button => button.text() === '重试')!.trigger('click')
  await vi.waitFor(() => expect(wrapper.find('.notification-list').exists()).toBe(true))
  expect(auth.user).toEqual(user)
})
