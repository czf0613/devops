import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { api } from '../src/api'
import { auth } from '../src/auth'
import SystemStatusView from '../src/views/SystemStatusView.vue'

vi.mock('../src/api', async original => ({ ...(await original<typeof import('../src/api')>()), api: vi.fn() }))
enableAutoUnmount(afterEach)
beforeEach(() => {
  auth.user = { id: 1, email: 'admin@example.com', roles: ['admin'] } as NonNullable<typeof auth.user>
  vi.mocked(api).mockReset()
})
afterEach(() => {
  auth.user = null
  document.body.innerHTML = ''
})

const GiB = 1024 ** 3
const snapshot = {
  collected_at: '2026-11-17T14:00:00+00:00',
  host: {
    hostname: 'devops-host',
    system: 'Linux',
    release: '6.12.0',
    machine: 'x86_64',
    python: '3.14.7',
    uptime_seconds: 90061,
  },
  cpu: { usage_percent: 42.5, logical_cores: 2, load_average: [1, 0.5, 0.25] },
  memory: {
    total_bytes: 4 * GiB,
    used_bytes: 1.5 * GiB,
    available_bytes: 2.5 * GiB,
    usage_percent: 37.5,
    swap_total_bytes: 0,
    swap_used_bytes: 0,
  },
  disks: [
    { path: '/', label: '系统盘', total_bytes: 50 * GiB, used_bytes: 5 * GiB, free_bytes: 45 * GiB, usage_percent: 10 },
    { path: '/mnt/git-repos', label: 'Git 数据盘', total_bytes: 50 * GiB, used_bytes: 5 * GiB, free_bytes: 45 * GiB, usage_percent: 10 },
  ],
  storage: [{ path: '/mnt/git-repos', label: 'Git 仓库数据', bytes: 14 * 1024 ** 2, partial: false }],
}

it('shows CPU, memory, disk and data directory usage and refreshes on demand', async () => {
  vi.mocked(api).mockResolvedValue(snapshot)
  const wrapper = mount(SystemStatusView)
  await flushPromises()

  expect(api).toHaveBeenCalledWith('/admin/system')
  expect(wrapper.text()).toContain('42.5%')
  expect(wrapper.text()).toContain('37.5%')
  expect(wrapper.text()).toContain('1.5 GiB / 4.0 GiB')
  expect(wrapper.text()).toContain('Git 仓库数据')
  expect(wrapper.text()).toContain('14.0 MiB')
  expect(wrapper.text()).toContain('1 天 1 小时')
  expect(wrapper.findAll('[role="progressbar"]')).toHaveLength(5)

  await wrapper.get('button').trigger('click')
  await flushPromises()
  expect(api).toHaveBeenCalledTimes(2)
})

it('keeps the last snapshot when a refresh fails', async () => {
  vi.mocked(api).mockResolvedValueOnce(snapshot)
  const wrapper = mount(SystemStatusView)
  await flushPromises()
  vi.mocked(api).mockRejectedValueOnce(new Error('读取服务器状态失败'))
  await wrapper.get('button').trigger('click')
  await flushPromises()

  expect(wrapper.text()).toContain('读取服务器状态失败')
  expect(wrapper.text()).toContain('42.5%')
})
