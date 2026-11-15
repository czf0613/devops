<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, errorText } from '../api'
import type { User } from '../api'
import { auth, forgetLogin } from '../auth'
import AppButton from '../components/AppButton.vue'
import AppInput from '../components/AppInput.vue'
import AppNotice from '../components/AppNotice.vue'
import AppPanel from '../components/AppPanel.vue'
import EmailCodeField from '../components/EmailCodeField.vue'

const form = reactive({
  username: auth.user?.username ?? '',
  display_name: auth.user?.display_name ?? '',
  phone: auth.user?.phone ?? '',
})
const email = ref(''),
  code = ref(''),
  busy = ref(false),
  sending = ref(false),
  error = ref(''),
  message = ref('')
const router = useRouter()
async function save() {
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    auth.user = await api<User>('/me', 'PATCH', { ...form, phone: form.phone || null })
    message.value = '个人资料已保存'
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
async function changeEmail() {
  busy.value = true
  error.value = ''
  try {
    await api('/me/email', 'POST', { email: email.value, code: code.value })
    forgetLogin('邮箱已更换，请使用新邮箱登录')
    await router.replace('/login')
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page-heading">
    <p class="eyebrow">MY PROFILE</p>
    <h1>个人资料</h1>
    <p>维护你的姓名、用户名和联系信息。</p>
  </div>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  <AppNotice v-model="message" tone="success" />
  <div class="two-columns">
    <AppPanel title="基本信息">
      <form @submit.prevent="save">
        <AppInput
          v-model.trim="form.display_name"
          label="姓名"
          required
          maxlength="80"
          autocomplete="name"
        />
        <AppInput
          v-model.trim="form.username"
          label="用户名"
          required
          minlength="3"
          maxlength="32"
          autocomplete="username"
          hint="系统内全局唯一的标识名称，3–32 位，以字母开头。"
        />
        <AppInput
          v-model="form.phone"
          label="手机号（选填）"
          type="tel"
          placeholder="+86.13912345678"
        />
        <AppInput :model-value="auth.user?.email ?? ''" label="当前邮箱" readonly />
        <AppButton type="submit" :busy="busy">保存资料</AppButton>
      </form>
    </AppPanel>
    <AppPanel title="更换邮箱" description="邮箱用于登录与联系。验证新邮箱后，所有设备会退出登录。">
      <form @submit.prevent="changeEmail">
        <AppInput
          v-model.trim="email"
          label="新邮箱"
          type="email"
          required
          placeholder="new@company.com"
        />
        <EmailCodeField
          v-model="code"
          v-model:sending="sending"
          :email="email"
          purpose="change_email"
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
        <AppButton
          type="submit"
          variant="secondary"
          :busy="busy"
          :disabled="sending || code.length !== 6"
          >验证并更换</AppButton
        >
      </form>
    </AppPanel>
  </div>
</template>
