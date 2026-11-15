import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { api } from '../src/api'
import { auth } from '../src/auth'
import { confirmWithCode, dialogButton } from './admin-helpers'
import RegistryGCPanel from '../src/components/RegistryGCPanel.vue'

vi.mock('../src/api', async original => ({ ...(await original<typeof import('../src/api')>()), api: vi.fn() }))
enableAutoUnmount(afterEach)
beforeEach(() => { vi.mocked(api).mockReset(); auth.user = null })
afterEach(() => { vi.useRealTimers(); document.body.innerHTML = '' })
const empty = { worker_available: true, active: null, total: 0, items: [] }
const run = { id: 1, state: 'running', phase: 'preview', created_at: '2026-10-05T12:00:00Z', finished_at: null, before_bytes: null, after_bytes: null, summary: {}, error: null }

it('requires an explicit manual-window confirmation before creating GC and blocks duplicate launches', async () => {
  vi.mocked(api).mockResolvedValue(empty)
  const wrapper = mount(RegistryGCPanel, { props: { enabled: true }, attachTo: document.body })
  await flushPromises()
  const start = wrapper.findAll('button').find(button => button.text() === '回收空间')!
  await start.trigger('click')
  const dialog = document.querySelector('dialog[open]')!
  expect(dialog.textContent).toContain('系统不会自动进入只读状态')
  expect(dialog.textContent).toContain('只通过 digest 使用的无标签历史版本也可能被删除')
  expect(api).not.toHaveBeenCalledWith('/admin/registry/gc', 'POST', expect.anything())
  dialogButton('取消').click()
  await flushPromises()
  expect(api).toHaveBeenCalledTimes(1)
  await start.trigger('click')
  vi.mocked(api).mockImplementation(async (_, method) => method === 'POST' ? run : { ...empty, active: run, items: [run], total: 1 })
  await confirmWithCode()
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/admin/registry/gc', 'POST', { maintenance_confirmed: true, code: '123456' })
  expect(start.attributes('disabled')).toBeDefined()
  expect(wrapper.text()).toContain('正在预检')
  expect(wrapper.text()).toContain('离开页面不会终止任务')
})

it('refreshes an active run through completion and reports measured storage reduction', async () => {
  vi.useFakeTimers()
  vi.mocked(api).mockResolvedValue({ ...empty, active: run, total: 1, items: [run] })
  const wrapper = mount(RegistryGCPanel, { props: { enabled: true } })
  await flushPromises()
  vi.mocked(api).mockResolvedValue({ ...empty, total: 1, items: [{ ...run, state: 'succeeded', before_bytes: 2097152, after_bytes: 1048576, summary: { eligible_blobs: 3, eligible_manifests: 2 } }] })
  await vi.advanceTimersByTimeAsync(3000)
  await flushPromises()
  expect(wrapper.text()).toContain('存储占用减少 1.00 MiB')
  expect(wrapper.text()).toContain('已处理：3 个数据块、2 个无标签清单')
  expect(wrapper.text()).not.toContain('请继续保持停止写入')
  const count = vi.mocked(api).mock.calls.length
  await vi.advanceTimersByTimeAsync(3000)
  expect(api).toHaveBeenCalledTimes(count)
})

it('shows submission errors and discards stale history when the account changes', async () => {
  vi.mocked(api).mockResolvedValue(empty)
  const wrapper = mount(RegistryGCPanel, { props: { enabled: true }, attachTo: document.body })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '回收空间')!.trigger('click')
  vi.mocked(api).mockRejectedValueOnce(new Error('已有空间回收任务'))
  await confirmWithCode()
  await flushPromises()
  expect(document.querySelector('dialog[open]')?.textContent).toContain('已有空间回收任务')
  dialogButton('取消').click(); await flushPromises()
  let resolve!: (data: unknown) => void
  vi.mocked(api).mockImplementationOnce(() => new Promise(done => { resolve = done }))
  await wrapper.findAll('button').find(button => button.text() === '刷新回收记录')!.trigger('click')
  vi.mocked(api).mockResolvedValue({ ...empty, worker_available: false })
  auth.user = { id: 22 } as NonNullable<typeof auth.user>
  await flushPromises()
  resolve({ ...empty, items: [{ ...run, error: 'stale account data' }] })
  await flushPromises()
  expect(wrapper.text()).not.toContain('stale account data')
  expect(wrapper.text()).toContain('空间回收进程未就绪')
})
