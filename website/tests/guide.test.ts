import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount, RouterLinkStub } from '@vue/test-utils'
import { auth } from '../src/auth'
import GuideView from '../src/views/GuideView.vue'
import { version } from '../../cli/package.json'

vi.mock('../../cli/package.json', () => ({ version: '9.8.7' }))

enableAutoUnmount(afterEach)
beforeEach(() => { auth.user = { id: 1, username: 'guide_user' } as NonNullable<typeof auth.user> })
afterEach(() => { document.body.innerHTML = '' })

it('explains Registry token preparation and login with the current username and floating copy feedback', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
  const wrapper = mount(GuideView, { attachTo: document.body, global: { stubs: { RouterLink: RouterLinkStub } } })
  expect(wrapper.text()).toContain('无需先登录 DevOps CLI')
  expect(wrapper.get('a[href$="/res/devops-cli/9.8.7.tgz"]').text()).toBe('下载 CLI 9.8.7')
  expect(wrapper.findAllComponents(RouterLinkStub).map(link => link.props('to'))).toEqual(['/security'])
  expect(wrapper.get('.registry-guide').findAll('h3').map(heading => heading.text())).toEqual(['1. 准备令牌', '2. Docker 登录'])
  expect(wrapper.get('.registry-guide').findAll('.command')).toHaveLength(1)
  const host = window.location.host
  await wrapper.get('[aria-label="复制 Docker 登录命令"]').trigger('click'); await flushPromises()
  expect(writeText).toHaveBeenLastCalledWith(`docker login ${host} -u guide_user`)
  expect(wrapper.find('.notice-floating').exists()).toBe(false)
  expect(document.querySelector('.app-notice-stack [role="status"]')?.textContent).toContain('已复制')
  auth.user = { id: 2, username: 'next_user' } as NonNullable<typeof auth.user>
  await flushPromises()
  await wrapper.get('[aria-label="复制 Docker 登录命令"]').trigger('click')
  expect(writeText).toHaveBeenLastCalledWith(`docker login ${host} -u next_user`)
  writeText.mockRejectedValueOnce(new Error('clipboard'))
  await wrapper.get('[aria-label="复制 Docker 登录命令"]').trigger('click'); await flushPromises()
  expect(document.querySelector('.app-notice-stack [role="alert"]')?.textContent).toContain('手动选中并复制')
})

it('copies each complete command separately without combining lines or adding line breaks', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
  const wrapper = mount(GuideView, { global: { stubs: { RouterLink: RouterLinkStub } } })
  for (const command of wrapper.findAll('.command')) await command.get('button').trigger('click')
  const origin = window.location.origin
  const oauthConfig = [
    `git config --global credential.${origin}.provider generic`,
    `git config --global credential.${origin}.oauthClientId devops-gcm`,
    `git config --global credential.${origin}.oauthAuthorizeEndpoint /oauth/authorize`,
    `git config --global credential.${origin}.oauthTokenEndpoint /oauth/token`,
    `git config --global credential.${origin}.oauthRedirectUri http://127.0.0.1`,
    `git config --global credential.${origin}.oauthScopes 'git:read git:write'`,
    `git config --global credential.${origin}.oauthDefaultUserName oauth2`,
    `git config --global credential.${origin}.oauthUseClientAuthHeader false`,
  ].join('\n')
  expect(writeText.mock.calls.map(([value]) => value)).toEqual([
    oauthConfig,
    `git config --global credential.${origin}.helper manager`,
    `npm install --global ${window.location.origin}/res/devops-cli/${version}.tgz`,
    `devops auth login --hostname ${window.location.origin} --web`,
    'devops auth status',
    'devops repo list',
    'devops issue list -R owner/repo --json number,title,state',
    'devops pr list -R owner/repo',
    'devops workflow list -R owner/repo',
    'devops run list -R owner/repo --json databaseId,status,conclusion',
    'devops pr checks 1 -R owner/repo --watch',
    'devops --help',
    `docker login ${window.location.host} -u guide_user`,
  ])
})
