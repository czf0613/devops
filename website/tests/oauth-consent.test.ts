import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { api } from '../src/api'
import OAuthConsentView from '../src/views/OAuthConsentView.vue'

vi.mock('../src/api', async (original) => ({
  ...(await original<typeof import('../src/api')>()),
  api: vi.fn(),
}))
enableAutoUnmount(afterEach)
beforeEach(() => { vi.mocked(api).mockReset() })
const requestId = 'r'.repeat(43)
const path = `/oauth/authorization-requests/${requestId}`
const details = {
  client_name: 'Git Credential Manager',
  scope: ['git:read', 'git:write'],
  redirect_uri: 'http://127.0.0.1:49152/',
}

async function page(id = requestId) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/oauth/consent', component: OAuthConsentView }],
  })
  await router.push(`/oauth/consent?request=${id}`)
  await router.isReady()
  const wrapper = mount(OAuthConsentView, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

it('shows current repository permission boundaries and scopes, with explicit confirmation and expired-request feedback', async () => {
  vi.mocked(api).mockResolvedValueOnce(details).mockRejectedValueOnce(new Error('授权已过期'))
  const wrapper = await page()
  expect(api).toHaveBeenCalledWith(path)
  expect(wrapper.text()).toContain('你有权限的仓库')
  expect(wrapper.text()).toContain('clone、fetch、pull')
  expect(wrapper.text()).toContain('push')
  expect(api).toHaveBeenCalledTimes(1)
  await wrapper.findAll('button')[0]!.trigger('click')
  await flushPromises()
  expect(api).toHaveBeenLastCalledWith(`${path}/decision`, 'POST', { approve: true })
  expect(wrapper.text()).toContain('授权已过期')
})

it('lets ordinary members approve or refuse without an administrator restriction', async () => {
  vi.mocked(api).mockResolvedValueOnce(details)
    .mockRejectedValueOnce(new Error('授权已过期'))
  const wrapper = await page()
  expect(wrapper.findAll('button')[0]!.attributes('disabled')).toBeUndefined()
  expect(wrapper.text()).not.toContain('系统管理员')
  await wrapper.findAll('button')[1]!.trigger('click')
  await flushPromises()
  expect(api).toHaveBeenLastCalledWith(`${path}/decision`, 'POST', { approve: false })
})

it('rejects a malformed request without querying or confirming it', async () => {
  const wrapper = await page('invalid')
  expect(api).not.toHaveBeenCalled()
  expect(wrapper.text()).toContain('请从客户端重新发起授权')
  expect(wrapper.findAll('button')).toHaveLength(0)
})

it('shows CLI API scopes without presenting them as Git credentials', async () => {
  vi.mocked(api).mockResolvedValueOnce({ ...details, client_name: 'DevOps CLI', scope: ['api:read'] })
  const wrapper = await page()
  expect(wrapper.text()).toContain('连接 DevOps CLI')
  expect(wrapper.text()).toContain('读取你有权限的仓库、Issue 和 Pull request')
  expect(wrapper.text()).not.toContain('clone、fetch、pull')
  expect(wrapper.text()).not.toContain('按你的仓库权限创建')
  expect(api).toHaveBeenCalledTimes(1)
})
