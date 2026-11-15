<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { auth } from '../auth'
import AppButton from '../components/AppButton.vue'
import AppNotice from '../components/AppNotice.vue'
import AppPanel from '../components/AppPanel.vue'
import { version } from '../../../cli/package.json'

const origin = window.location.origin
const download = `${origin}/res/devops-cli/${version}.tgz`
const install = `npm install --global ${download}`
const login = `devops auth login --hostname ${origin} --web`
const setup = `devops auth setup-git --hostname ${origin} --desktop`
const oauthConfigLines = [
  `git config --global credential.${origin}.provider generic`,
  `git config --global credential.${origin}.oauthClientId devops-gcm`,
  `git config --global credential.${origin}.oauthAuthorizeEndpoint /oauth/authorize`,
  `git config --global credential.${origin}.oauthTokenEndpoint /oauth/token`,
  `git config --global credential.${origin}.oauthRedirectUri http://127.0.0.1`,
  `git config --global credential.${origin}.oauthScopes 'git:read git:write'`,
  `git config --global credential.${origin}.oauthDefaultUserName oauth2`,
  `git config --global credential.${origin}.oauthUseClientAuthHeader false`,
]
const helperConfig = `git config --global credential.${origin}.helper manager`
const cliExamples = [
  { label: '登录状态', command: 'devops auth status' },
  { label: '仓库列表', command: 'devops repo list' },
  { label: 'Issue 列表', command: 'devops issue list -R owner/repo --json number,title,state' },
  { label: 'PR 列表', command: 'devops pr list -R owner/repo' },
  { label: 'CI 工作流', command: 'devops workflow list -R owner/repo' },
  { label: 'CI 运行', command: 'devops run list -R owner/repo --json databaseId,status,conclusion' },
  { label: 'PR 检查', command: 'devops pr checks 1 -R owner/repo --watch' },
  { label: 'CLI 帮助', command: 'devops --help' },
]
const registryHost = new URL(origin).host
const dockerLogin = computed(() => `docker login ${registryHost} -u ${auth.user?.username || 'YOUR_USERNAME'}`)
const notice = ref(''), copyError = ref('')
async function copy(value: string) {
  notice.value = ''; copyError.value = ''
  try { await navigator.clipboard.writeText(value); notice.value = '已复制' }
  catch { copyError.value = '请手动选中并复制命令' }
}
</script>
<template>
  <div class="page-heading"><p class="eyebrow">GET STARTED</p><h1>使用指南</h1><p>连接 Git 工具、使用 DevOps CLI，以及登录 Docker Registry。</p></div>
  <AppNotice v-model="notice" tone="success" placement="floating" />
  <AppNotice v-model="copyError" tone="error" placement="floating" />
  <div class="guide-grid">
    <AppPanel title="01 · Git 网页授权（GCM）">
      <p>DevOps 的 Git 只支持网页 OAuth 授权，没有用户名和密码可填。Git 客户端需要 GCM（Git Credential Manager）打开本站授权页；下面的配置不依赖 DevOps CLI。</p>
      <h3>GitHub Desktop</h3>
      <ol class="guide-steps">
        <li>macOS：<strong>GitHub Desktop → Settings / Preferences → Advanced</strong>。Windows：<strong>File → Options → Advanced</strong>。</li>
        <li>勾选 <strong>Use Git Credential Manager</strong>，然后保存。</li>
      </ol>
      <figure class="desktop-diagram" aria-label="示意图：打开 Advanced，勾选 Use Git Credential Manager，点击 Save">
        <div class="diagram-title"><span class="window-dots">● ● ●</span> GitHub Desktop · Settings</div>
        <div class="diagram-content">
          <div class="diagram-nav"><span>Accounts</span><span>Git</span><strong>Advanced <b>1</b></strong></div>
          <div class="diagram-main"><h3>Advanced</h3><div class="diagram-check"><span aria-hidden="true">✓</span> Use Git Credential Manager <b>2</b></div><p>使用 GCM 管理 Git 凭据</p><div class="diagram-save">Save <b>3</b></div></div>
        </div>
        <figcaption>简易示意图，选项位置可能随 Desktop 版本略有变化。</figcaption>
      </figure>
      <ol start="3" class="guide-steps"><li>在终端执行下面的「本站 OAuth 参数」一次，然后使用 Desktop 的 <strong>File → Clone Repository → URL</strong> 粘贴 HTTPS 克隆地址；首次拉取或推送时在打开的网页中登录并点击「允许访问」。</li></ol>
      <h3>本站 OAuth 参数</h3>
      <p>macOS、Windows 和 Linux 的终端都适用；域名按当前访问地址生成，逐条复制执行：</p>
      <div class="command-list config-list">
        <div v-for="(line, index) in oauthConfigLines" :key="line" class="command config-command">
          <pre tabindex="0"><code>{{ line }}</code></pre>
          <AppButton class="small" variant="secondary" :aria-label="`复制 Git OAuth 配置第 ${index + 1} 条`" @click="copy(line)">复制</AppButton>
        </div>
      </div>
      <p class="muted">如果客户端弹出用户名 / 密码框，说明这些参数缺失或不完整；DevOps 没有可填的 Git 密码，取消后在终端重新执行一次即可。</p>
      <h3>命令行 Git（可选）</h3>
      <p>终端里 clone / push 还需要 GCM 本身在 PATH 上。macOS 可执行 <code>brew install --cask git-credential-manager</code>，然后把本站 helper 指向 <code>manager</code>：</p>
      <div class="command"><pre tabindex="0"><code>{{ helperConfig }}</code></pre><AppButton class="small" variant="secondary" aria-label="复制 Git helper 配置命令" @click="copy(helperConfig)">复制</AppButton></div>
      <p class="muted">只有 GitHub Desktop 自带的 GCM 时，它不在终端 PATH 上；需要把 helper 指向该可执行文件，路径含空格时写成 <code>!"/Applications/GitHub Desktop.app/…/git-credential-manager"</code>。</p>
      <p>已安装 DevOps CLI 时，也可以用 <code>{{ setup }}</code> 自动写入上面的 OAuth 参数；它会保留 Desktop 的 helper 设置。</p>
      <p>DevOps 不使用网站密码作为 Git 密码。授权可在「账号安全 → Git / CLI 客户端授权」中撤销。</p>
      <a href="https://github.com/desktop/desktop/blob/release-3.6.6/docs/integrations/gitlab.md" target="_blank" rel="noopener noreferrer">GitHub Desktop 官方 GCM 设置说明 ↗</a>
    </AppPanel>
    <AppPanel title="02 · 安装 DevOps CLI">
      <p>适用于 macOS、Windows 和 Linux。先安装 <strong>Node.js 24 LTS</strong>（也支持 Node.js 22.12+），npm 随 Node.js 提供。</p>
      <div class="guide-links"><a href="https://nodejs.org/en/download" target="_blank" rel="noopener noreferrer">下载 Node.js ↗</a><a :href="download">下载 CLI {{ version }}</a><a :href="download + '.sha256'">SHA-256 校验和</a></div>
      <h3>1. 安装</h3>
      <div class="command"><pre tabindex="0"><code>{{ install }}</code></pre><AppButton class="small" variant="secondary" aria-label="复制 CLI 安装命令" @click="copy(install)">复制</AppButton></div>
      <p class="muted">安装包由本站直接提供，无需访问源码仓库。依赖从 npm 公共源下载。npm 全局目录没有写入权限时，使用 Node.js 版本管理器配置用户目录。</p>
      <h3>2. 网页登录</h3>
      <div class="command"><pre tabindex="0"><code>{{ login }}</code></pre><AppButton class="small" variant="secondary" aria-label="复制 CLI 登录命令" @click="copy(login)">复制</AppButton></div>
      <p>浏览器登录后确认授权。CLI 凭据默认保存在系统凭据库；无桌面环境的服务器可用 <code>--device</code>，在另一台设备的浏览器中确认设备代码。</p>
      <h3>3. 开始使用</h3>
      <div class="command-list"><div v-for="example in cliExamples" :key="example.command" class="command"><pre tabindex="0"><code>{{ example.command }}</code></pre><AppButton class="small" variant="secondary" :aria-label="`复制 ${example.label}命令`" @click="copy(example.command)">复制</AppButton></div></div>
      <p class="muted">在本地仓库中可根据 Git remote 识别仓库；也可用 <code>-R owner/repo</code> 指定。自建服务可在命令中增加 <code>--hostname https://example.com:8443</code>。</p>
      <p>CI 命令沿用 GitHub CLI 的 <code>workflow</code> / <code>run</code> 用法，可触发工作流、查看日志、等待检查、下载产物、取消或重跑。用 <code>devops workflow run --help</code> 或 <code>devops run --help</code> 查看参数。</p>
      <p>升级：使用新版本的安装地址再次执行 <code>npm install --global</code>。卸载：<code>npm uninstall --global @kevinc/devops-cli</code>。</p>
    </AppPanel>
    <AppPanel class="registry-guide" title="03 · Docker Registry 镜像仓库" description="使用原生 docker login 登录，一次登录即可访问你有权限的所有 repo。无需先登录 DevOps CLI。">
      <div class="registry-guide-grid">
        <div>
          <h3>1. 准备令牌</h3>
          <p>打开 <RouterLink to="/security">账号安全 → Docker 登录令牌</RouterLink>，填写名称、选择权限与有效期，然后创建令牌。需要推送时选择“读写”。</p>
          <p>令牌只显示一次，请立即保存。选择永久有效时须确认风险；不再使用或泄露的令牌应在账号安全中撤销。</p>
        </div>
        <div>
          <h3>2. Docker 登录</h3>
          <p>在终端执行以下命令：</p>
          <div class="command"><pre tabindex="0"><code>{{ dockerLogin }}</code></pre><AppButton variant="secondary" class="small" aria-label="复制 Docker 登录命令" @click="copy(dockerLogin)">复制</AppButton></div>
          <p>在 Password 提示中粘贴刚创建的 Docker 登录令牌。这里不使用邮箱验证码、Git 授权或兼容密码。</p>
        </div>
      </div>
    </AppPanel>
  </div>
</template>
<style scoped>
.guide-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; align-items: stretch; }
.guide-grid > .panel { min-width: 0; margin-bottom: 0; }
.registry-guide { grid-column: 1 / -1; }
.registry-guide-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 32px; }
.registry-guide-grid > div { min-width: 0; }
.registry-guide-grid h3:first-child { margin-top: 0; }
.guide-grid p, .guide-steps { line-height: 1.8; }
.guide-steps { padding-left: 22px; }
.guide-steps li { padding-bottom: 10px; }
.guide-grid a { color: var(--accent); text-decoration: underline; text-underline-offset: 3px; }
.guide-grid h3 { margin-top: 24px; }
.guide-links { display: flex; flex-wrap: wrap; gap: 16px; margin: 20px 0; }
.command { display: flex; gap: 12px; align-items: center; background: var(--input); padding: 14px; border-radius: 10px; }
.command pre { flex: 1; min-width: 0; max-width: none; margin: 0; padding: 0; border: 0; background: transparent; white-space: pre; overflow-x: auto; line-height: 1.7; }
.command > .button { flex-shrink: 0; }
.command-list { display: grid; gap: 8px; }
.config-command pre { white-space: pre-wrap; overflow-wrap: anywhere; }
.desktop-diagram { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; margin: 20px 0; }
.diagram-title { background: #eff2f7; padding: 12px; font-size: 12px; color: #536078; }
.window-dots { color: #a8b4c6; padding-right: 12px; }
.diagram-content { display: flex; min-height: 176px; }
.diagram-nav { width: 100px; flex-shrink: 0; background: #f8f9fc; padding: 16px 8px; display: flex; flex-direction: column; gap: 12px; font-size: 12px; color: #536078; }
.diagram-nav strong { color: #235ac7; background: #e8efff; padding: 7px 4px; border-radius: 5px; }
.diagram-main { padding: 4px 18px 16px; flex: 1; font-size: 12px; background: #fff; color: #263650; }
.diagram-main h3 { margin: 14px 0; }
.diagram-check { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.diagram-check > span { background: #316bd5; color: white; border-radius: 3px; padding: 1px 4px; }
.diagram-main p { color: #536078; }
.desktop-diagram b { display: inline-grid; place-items: center; background: #e8efff; color: #235ac7; width: 18px; height: 18px; border-radius: 50%; font-size: 11px; }
.diagram-save { width: fit-content; margin-left: auto; background: #316bd5; color: white; padding: 6px 18px; border-radius: 5px; }
figcaption { padding: 10px 12px; font-size: 12px; color: #68758b; background: #f8f9fc; }
@media (max-width: 1100px) { .guide-grid { grid-template-columns: 1fr; } }
@media (max-width: 520px) { .command { flex-direction: column; } .diagram-nav { width: 84px; } }
</style>
