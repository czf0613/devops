<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { api, dateText, errorText } from '../api'
import { auth } from '../auth'
import type { RegistryCredential } from '../registry'
import AppButton from './AppButton.vue'
import AppConfirm from './AppConfirm.vue'
import AppInput from './AppInput.vue'
import AppNotice from './AppNotice.vue'
import AppPanel from './AppPanel.vue'
import AppSelect from './AppSelect.vue'

const credentials = ref<RegistryCredential[]>([]), enabled = ref(false), host = ref(''), username = ref('')
const name = ref(''), access = ref('read'), days = ref('90'), token = ref('')
const acknowledgePermanent = ref(false)
const busy = ref(false), error = ref(''), message = ref(''), feedbackError = ref('')
const confirmation = ref<InstanceType<typeof AppConfirm>>()
let sequence = 0
onUnmounted(() => { sequence++; token.value = '' })
watch(() => auth.user?.id, () => {
  sequence++; token.value = ''; credentials.value = []; name.value = ''; access.value = 'read'; days.value = '90'; acknowledgePermanent.value = false; error.value = ''; message.value = ''; feedbackError.value = ''
  void load()
}, { immediate: true })
watch(days, () => { acknowledgePermanent.value = false })
async function load() {
  const current = ++sequence
  busy.value = true
  try {
    const data = await api<{ enabled: boolean; host: string; username: string; items: RegistryCredential[] }>('/me/registry-credentials')
    if (current !== sequence) return
    credentials.value = data.items; enabled.value = data.enabled; host.value = data.host; username.value = data.username
  } catch (cause) { if (current === sequence) error.value = errorText(cause) }
  finally { if (current === sequence) busy.value = false }
}
async function create() {
  if (busy.value || token.value || (days.value === 'permanent' && !acknowledgePermanent.value)) return
  const current = sequence
  busy.value = true; error.value = ''; message.value = ''
  try {
    const result = await api<RegistryCredential & { token: string }>('/me/registry-credentials', 'POST', {
      name: name.value, access: access.value, days: days.value === 'permanent' ? null : Number(days.value),
      ...(days.value === 'permanent' ? { acknowledge_permanent: true } : {}),
    })
    if (current !== sequence) return
    const { token: secret, ...metadata } = result
    credentials.value.unshift(metadata); token.value = secret; name.value = ''; acknowledgePermanent.value = false
  } catch (cause) { if (current === sequence) error.value = errorText(cause) }
  finally { if (current === sequence) busy.value = false }
}
async function copy(value: string) {
  const current = sequence
  message.value = ''; feedbackError.value = ''
  try { await navigator.clipboard.writeText(value); if (current === sequence) message.value = '已复制' }
  catch { if (current === sequence) feedbackError.value = '复制失败，请手动选择并复制。' }
}
async function revoke(item: RegistryCredential) {
  if (busy.value) return
  const current = sequence
  if (!await confirmation.value?.ask('使用此令牌的 Docker 客户端将在下一次请求被拒绝。', `撤销「${item.name}」？`) || current !== sequence) return
  busy.value = true; error.value = ''
  try {
    await api(`/me/registry-credentials/${item.id}`, 'DELETE')
    if (current === sequence) { credentials.value = credentials.value.filter(value => value.id !== item.id); token.value = '' }
  } catch (cause) { if (current === sequence) error.value = errorText(cause) }
  finally { if (current === sequence) busy.value = false }
}
</script>

<template>
  <AppPanel title="Docker 登录令牌" description="令牌属于你的账号，一次登录即可访问你有权限的所有 repo；成员权限调整立即生效。">
    <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
    <AppNotice v-model="message" tone="success" placement="floating" />
    <AppNotice v-model="feedbackError" tone="error" placement="floating" />
    <template v-if="enabled">
      <div class="login-command"><code>docker login {{ host }} -u {{ username }}</code><AppButton variant="secondary" class="small" @click="copy(`docker login ${host} -u ${username}`)">复制登录命令</AppButton></div>
      <p class="muted">在 Password 提示中粘贴专用令牌。无需在不同项目之间切换令牌。读写上限不会赋予你在 repo 中没有的权限。</p>
      <div v-if="token" class="token-result">
        <strong>令牌仅显示一次，请现在保存</strong>
        <code class="secret-token">{{ token }}</code>
        <div class="actions"><AppButton variant="secondary" @click="copy(token)">复制令牌</AppButton><AppButton @click="token = ''">已保存，关闭</AppButton></div>
      </div>
      <form v-else class="credential-form" @submit.prevent="create">
        <AppInput v-model="name" label="令牌名称" placeholder="例如：开发电脑、CI 构建" :maxlength="80" required :disabled="busy" />
        <AppSelect v-model="access" label="权限上限" :options="[{ value: 'read', label: '只读 · pull' }, { value: 'write', label: '读写 · pull / push' }]" :disabled="busy" />
        <AppSelect v-model="days" label="有效期" :options="[{ value: '30', label: '30 天' }, { value: '90', label: '90 天' }, { value: '365', label: '365 天' }, { value: 'permanent', label: '永久有效' }]" :disabled="busy" />
        <AppButton type="submit" :busy="busy" :disabled="!name.trim() || (days === 'permanent' && !acknowledgePermanent)">创建令牌</AppButton>
        <div v-if="days === 'permanent'" class="permanent-warning">
          <p role="alert"><strong>永久令牌不会自动过期。</strong>如果泄露，他人可持续使用你的当前镜像权限，直到你主动撤销令牌。建议优先选择有期限的令牌，并将令牌保存在凭据管理器或 CI 密钥中。</p>
          <label class="checkbox-label"><input v-model="acknowledgePermanent" type="checkbox" :disabled="busy"><span>我已了解风险，仍要创建永久令牌</span></label>
          <p class="muted">永久有效仅表示没有到期时间；撤销令牌、停用账号或收回仓库权限仍会立即限制后续请求。</p>
        </div>
      </form>
    </template>
    <p v-else-if="!busy" class="muted">镜像仓库尚未启用。</p>
    <ul v-if="credentials.length" class="session-list">
      <li v-for="item in credentials" :key="item.id">
        <div class="session-main"><strong>{{ item.name }}</strong><p>{{ item.access === 'read' ? '只读' : '读写' }} · 有权限的所有 repo</p><small><template v-if="item.expires_at">{{ new Date(item.expires_at).getTime() <= Date.now() ? '已过期' : '到期' }} {{ dateText(item.expires_at) }}</template><template v-else>永久有效</template> · 最近使用 {{ item.last_used_at ? dateText(item.last_used_at) : '尚未使用' }}</small></div>
        <AppButton variant="danger" class="small" :disabled="busy" @click="revoke(item)">撤销令牌</AppButton>
      </li>
    </ul>
    <p v-else-if="!busy" class="muted">暂无令牌</p>
  </AppPanel>
  <AppConfirm ref="confirmation" />
</template>

<style scoped>
.credential-form { display: grid; grid-template-columns: minmax(160px, 2fr) 1fr 1fr auto; align-items: end; gap: 16px; margin: 24px 0; }
.credential-form :deep(.form-field) { margin: 0; }
.credential-form > .button { min-height: 44px; }
.permanent-warning { grid-column: 1 / -1; padding: 16px 20px; border: 1px solid var(--red); border-radius: 10px; background: #ff969f0a; }
.permanent-warning p { margin: 0 0 12px; }
.permanent-warning p:last-child { margin: 12px 0 0; }
.permanent-warning strong { color: var(--red); }
.permanent-warning .checkbox-label { align-items: center; margin: 0; }
.login-command { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; }
.login-command code, .secret-token { overflow-wrap: anywhere; }
.token-result { padding: 20px; background: var(--surface-hover); border: 1px solid var(--line); border-radius: 10px; margin: 20px 0; }
.secret-token { display: block; user-select: all; margin: 16px 0; }
@media (max-width: 900px) { .credential-form { grid-template-columns: 1fr 1fr; } }
</style>
