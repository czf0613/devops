<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api, capabilities, errorText } from '../api'
import type { AuthResult } from '../api'
import { acceptLogin, auth } from '../auth'
import AppButton from '../components/AppButton.vue'
import AppInput from '../components/AppInput.vue'
import AppNotice from '../components/AppNotice.vue'
import EmailCodeField from '../components/EmailCodeField.vue'

const props = defineProps<{ mode: 'login' | 'register' }>()
const registering = computed(() => props.mode === 'register')
const form = reactive({ email: '', code: '', username: '', display_name: '', phone: '' })
const busy = ref(false),
  sending = ref(false),
  error = ref(''),
  message = ref('')
const registrationEnabled = ref(true)
const router = useRouter(),
  route = useRoute()
onMounted(async () => {
  try {
    registrationEnabled.value = (await capabilities()).registration_enabled
  } catch (cause) {
    error.value = errorText(cause)
  }
})
async function submit() {
  busy.value = true
  error.value = ''
  try {
    const body = registering.value
      ? { ...form, phone: form.phone || null }
      : { email: form.email, code: form.code }
    acceptLogin(await api<AuthResult>(`/auth/${props.mode}`, 'POST', body))
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    await router.replace(redirect.startsWith('/') && !redirect.startsWith('//') ? redirect : '/')
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="auth-intro">
    <div class="brand"><span class="brand-mark">D</span> DevOps</div>
    <div>
      <p class="eyebrow">YOUR TEAM, CONNECTED</p>
      <h1>从一个账号开始，<br />连接团队的每一天。</h1>
      <p>连接代码、仓库与团队，让开发协作井然有序。</p>
    </div>
    <small>DevOps · 从代码开始协作</small>
  </div>
  <main class="auth-form-wrap">
    <section class="auth-form">
      <p class="eyebrow">{{ registering ? 'CREATE ACCOUNT' : 'WELCOME BACK' }}</p>
      <h1>{{ registering ? '创建你的账号' : '登录工作空间' }}</h1>
      <p>
        {{ registering ? '验证邮箱后，即可成为普通成员。' : '使用邮箱验证码登录，无需登录密码。' }}
      </p>
      <AppNotice v-model="auth.notice" tone="success" />
      <AppNotice v-if="error || auth.error" tone="error">{{ error || auth.error }}</AppNotice>
      <AppNotice v-model="message" tone="success" />
      <AppNotice v-if="registering && !registrationEnabled">暂未开放注册，请联系管理员。</AppNotice>
      <form v-else @submit.prevent="submit">
        <AppInput
          v-model.trim="form.email"
          label="邮箱"
          type="email"
          autocomplete="email"
          placeholder="you@company.com"
          required
          maxlength="254"
        />
        <EmailCodeField
          v-model="form.code"
          v-model:sending="sending"
          :email="form.email"
          :purpose="mode"
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
        <template v-if="registering">
          <AppInput
            v-model.trim="form.display_name"
            label="姓名"
            autocomplete="name"
            maxlength="80"
            placeholder="你的姓名"
            required
          />
          <AppInput
            v-model.trim="form.username"
            label="用户名"
            autocomplete="username"
            pattern="[a-zA-Z][a-zA-Z0-9_.\-]{2,31}"
            minlength="3"
            maxlength="32"
            placeholder="设置全局唯一的用户名"
            required
            hint="系统内全局唯一的标识名称，3–32 位，以字母开头。"
          />
          <AppInput
            v-model="form.phone"
            label="手机号（选填）"
            type="tel"
            placeholder="+86.13912345678"
            autocomplete="tel"
          />
        </template>
        <AppButton type="submit" class="full" :busy="busy" :disabled="sending">{{
          registering ? '注册并登录' : '登录'
        }}</AppButton>
      </form>
      <p v-if="registering" class="auth-switch">
        已有账号？<RouterLink to="/login">去登录</RouterLink>
      </p>
      <p v-else-if="registrationEnabled" class="auth-switch">
        还没有账号？<RouterLink to="/register">注册账号</RouterLink>
      </p>
    </section>
  </main>
</template>
