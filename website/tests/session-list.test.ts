import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { api } from '../src/api'
import type { Session } from '../src/api'
import { forgetLogin } from '../src/auth'
import SessionList from '../src/components/SessionList.vue'

vi.mock('../src/api', async original => ({
  ...(await original<typeof import('../src/api')>()), api: vi.fn(),
}))
vi.mock('../src/auth', async original => ({ ...(await original<typeof import('../src/auth')>()), forgetLogin: vi.fn() }))
enableAutoUnmount(afterEach)
beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(forgetLogin).mockReset() })
afterEach(() => { document.body.innerHTML = '' })

const device = (id: number, current = false): Session => ({
  id, user_id: 1, created_at: '2026-10-01T00:00:00Z', expires_at: '2099-01-01T00:00:00Z',
  last_seen_at: '2026-10-03T00:00:00Z', revoked_at: null, revoke_reason: null,
  login_ip: `192.0.2.${id}`, user_agent: `Device ${id}`, current, active: true,
})
const page = (items: Session[], total = items.length, number = 1) => ({
  items, total, page: number, page_size: 20,
})
async function render(userId?: number) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/security', component: { template: '<div />' } },
    { path: '/login', component: { template: '<div />' } },
  ] })
  await router.push('/security')
  await router.isReady()
  const wrapper = mount(SessionList, { props: { userId }, attachTo: document.body, global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}
async function confirm() {
  document.querySelectorAll<HTMLButtonElement>('dialog[open] button')[1]!.click()
  await flushPromises()
}

it('returns to the last available page when revoking its final device', async () => {
  const firstPage = Array.from({ length: 20 }, (_, i) => device(i + 2))
  vi.mocked(api).mockResolvedValueOnce(page(firstPage, 21))
  const { wrapper } = await render()
  vi.mocked(api).mockResolvedValueOnce(page([device(1)], 21, 2))
  await wrapper.findAll('.pager button')[1]!.trigger('click')
  await flushPromises()
  expect(wrapper.get('.pager').text()).toContain('第 2 / 2 页')
  await wrapper.get('.session-list button').trigger('click')
  vi.mocked(api).mockResolvedValueOnce({ message: '会话已退出' })
    .mockResolvedValueOnce(page([], 20, 2)).mockResolvedValueOnce(page(firstPage))
  await confirm()
  expect(api).toHaveBeenCalledWith('/me/sessions/1', 'DELETE')
  expect(wrapper.findAll('.session-list li')).toHaveLength(20)
  expect(wrapper.get('.pager').text()).toContain('共 20 条 · 第 1 / 1 页')
  expect(wrapper.findAll('.session-list strong').map(ip => ip.text())).not.toContain('192.0.2.1')
})

it('keeps a successfully revoked device hidden even if reloading fails', async () => {
  vi.mocked(api).mockResolvedValueOnce(page([device(1), device(2)]))
  const { wrapper } = await render()
  await wrapper.findAll('.session-list button')[0]!.trigger('click')
  vi.mocked(api).mockResolvedValueOnce({ message: '会话已退出' })
    .mockRejectedValueOnce(new Error('连接失败'))
  await confirm()
  expect(wrapper.findAll('.session-list li')).toHaveLength(1)
  expect(wrapper.get('.session-list strong').text()).toBe('192.0.2.2')
  expect(wrapper.text()).toContain('连接失败')
  expect(wrapper.get('.pager').text()).toContain('共 1 条')
})

it('shows no devices after an administrator revokes all target sessions', async () => {
  vi.mocked(api).mockResolvedValueOnce(page([device(1)]))
  const { wrapper, router } = await render(1)
  await wrapper.get('.section-heading button').trigger('click')
  vi.mocked(api).mockResolvedValueOnce({ message: '会话已退出' }).mockResolvedValueOnce(page([]))
  await confirm()
  expect(api).toHaveBeenCalledWith('/users/1/sessions', 'DELETE')
  expect(wrapper.text()).toContain('暂无有效登录设备')
  expect(wrapper.get('.section-heading button').attributes('disabled')).toBeDefined()
  expect(router.currentRoute.value.path).toBe('/security')
  expect(forgetLogin).not.toHaveBeenCalled()
})

it('still returns to login when revoking the current device', async () => {
  vi.mocked(api).mockResolvedValueOnce(page([device(1, true)]))
  const { wrapper, router } = await render()
  await wrapper.get('.session-list button').trigger('click')
  vi.mocked(api).mockResolvedValueOnce({ message: '会话已退出' })
  await confirm()
  expect(forgetLogin).toHaveBeenCalledWith('会话已退出，请重新登录')
  expect(router.currentRoute.value.path).toBe('/login')
})
