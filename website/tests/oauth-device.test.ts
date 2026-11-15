import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { api } from '../src/api'
import OAuthDeviceView from '../src/views/OAuthDeviceView.vue'

vi.mock('../src/api', async (original) => ({
  ...(await original<typeof import('../src/api')>()), api: vi.fn(),
}))
enableAutoUnmount(afterEach)
beforeEach(() => { vi.mocked(api).mockReset() })

async function page() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/oauth/device', component: OAuthDeviceView }],
  })
  await router.push('/oauth/device?user_code=ABCD-EFGH')
  await router.isReady()
  const wrapper = mount(OAuthDeviceView, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

it.each([true, false])('requires code review before an explicit device decision: %s', async approve => {
  vi.mocked(api).mockResolvedValueOnce({ client_name: 'DevOps CLI', scope: ['api:read'] })
    .mockResolvedValueOnce({ ok: true })
  const wrapper = await page()
  expect(api).not.toHaveBeenCalled()
  expect(wrapper.get('input').element.value).toBe('ABCD-EFGH')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/oauth/device-requests/ABCD-EFGH')
  expect(wrapper.text()).toContain('不要输入别人发给你的设备代码')
  expect(wrapper.text()).not.toContain('按你的仓库权限创建')
  expect(wrapper.find('input').exists()).toBe(false)
  await wrapper.findAll('button')[approve ? 0 : 1]!.trigger('click')
  await flushPromises()
  expect(api).toHaveBeenLastCalledWith('/oauth/device-requests/ABCD-EFGH/decision', 'POST', { approve })
  expect(wrapper.text()).toContain('已处理，请返回终端查看结果')
  expect(wrapper.findAll('button')).toHaveLength(0)
})

it('never offers approval for an invalid or expired code', async () => {
  vi.mocked(api).mockRejectedValueOnce(new Error('设备码不存在或已过期'))
  const wrapper = await page()
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(wrapper.text()).toContain('设备码不存在或已过期')
  expect(wrapper.text()).not.toContain('允许访问')
  expect(api).toHaveBeenCalledTimes(1)
})
