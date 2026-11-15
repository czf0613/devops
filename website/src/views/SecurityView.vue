<script setup lang="ts">
import { ref } from 'vue'
import { api, errorText } from '../api'
import type { User } from '../api'
import { auth } from '../auth'
import AppBadge from '../components/AppBadge.vue'
import AppButton from '../components/AppButton.vue'
import AppConfirm from '../components/AppConfirm.vue'
import AppInput from '../components/AppInput.vue'
import AppNotice from '../components/AppNotice.vue'
import AppPanel from '../components/AppPanel.vue'
import EmailCodeField from '../components/EmailCodeField.vue'
import SessionList from '../components/SessionList.vue'
import GitAuthorizationList from '../components/GitAuthorizationList.vue'
import RegistryCredentialList from '../components/RegistryCredentialList.vue'

const password = ref(''),
  code = ref(''),
  busy = ref(false),
  sending = ref(false)
const error = ref(''),
  message = ref('')
const confirmation = ref<InstanceType<typeof AppConfirm>>()
async function save(clear = false) {
  if (busy.value || sending.value || !/^\d{6}$/.test(code.value)) return
  if (
    clear &&
    !(await confirmation.value?.ask(
      '清除后，旧系统将无法继续使用此兼容密码。邮箱登录和当前登录设备不受影响。',
      '清除兼容密码？',
    ))
  )
    return
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    const result = await api<{ message: string; user: User }>('/me/legacy-password', 'PUT', {
      password: clear ? null : password.value,
      code: code.value,
    })
    auth.user = result.user
    password.value = ''
    code.value = ''
    message.value = result.message
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page-heading">
    <p class="eyebrow">ACCOUNT SECURITY</p>
    <h1>账号安全</h1>
    <p>保护你的账号，管理旧系统凭据与登录设备。</p>
  </div>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  <AppNotice v-model="message" tone="success" />
  <div class="two-columns security-columns">
    <AppPanel
      class="compact-form"
      title="旧系统兼容密码"
      description="按需设置，供旧系统使用。本站始终使用邮箱验证码登录。"
    >
      <template #actions
        ><AppBadge :tone="auth.user?.has_legacy_password ? 'green' : undefined">{{
          auth.user?.has_legacy_password ? '已设置' : '未设置'
        }}</AppBadge></template
      >
      <form @submit.prevent="save()">
        <AppInput
          v-model="password"
          label="新兼容密码"
          type="password"
          autocomplete="new-password"
          required
          :disabled="busy"
          hint="8–72 个 UTF-8 字节，中文字符通常占 3 个字节。"
        />
        <AppInput
          :model-value="auth.user?.email ?? ''"
          label="验证邮箱"
          readonly
          hint="设置或清除密码前，需验证当前邮箱。操作完成后保持登录。"
        />
        <EmailCodeField
          v-model="code"
          v-model:sending="sending"
          :email="auth.user?.email ?? ''"
          purpose="legacy_password"
          :disabled="busy"
          @sent="
            (text) => {
              message = text
              error = ''
            }
          "
          @error="
            (text) => {
              error = text
              message = ''
            }
          "
        />
        <div class="actions">
          <AppButton
            type="submit"
            :busy="busy"
            :disabled="sending || code.length !== 6 || !password"
            >保存兼容密码</AppButton
          >
          <AppButton
            v-if="auth.user?.has_legacy_password"
            variant="danger"
            :disabled="busy || sending || code.length !== 6"
            @click="save(true)"
            >清除密码</AppButton
          >
        </div>
      </form>
    </AppPanel>
    <div class="security-sessions"><SessionList fill /></div>
  </div>
  <GitAuthorizationList />
  <RegistryCredentialList />
  <AppConfirm ref="confirmation" />
</template>

<style scoped>
.security-columns { align-items: stretch; margin-bottom: 24px; }
.security-columns > .panel { margin-bottom: 0; }
.security-sessions { position: relative; min-height: 520px; }
@media (max-width: 1100px) {
  .security-columns { gap: 24px; }
  .security-sessions { height: 520px; }
}
</style>
