<script setup lang="ts">
import { onUnmounted, ref, useId } from 'vue'
import { api, ApiError, errorText } from '../api'
import AppButton from './AppButton.vue'
const props = defineProps<{
  email: string
  purpose: 'login' | 'register' | 'change_email' | 'legacy_password' | 'admin_operation' | 'repository_operation'
  disabled?: boolean
}>()
const code = defineModel<string>({ default: '' })
const sending = defineModel<boolean>('sending', { default: false })
const emit = defineEmits<{ sent: [message: string]; error: [message: string] }>()
const id = useId(),
  countdown = ref(0)
let deadline = 0
const timer = window.setInterval(() => {
  countdown.value = Math.max(0, Math.ceil((deadline - Date.now()) / 1000))
}, 1000)
onUnmounted(() => window.clearInterval(timer))
function cooldown(seconds: number) {
  deadline = Date.now() + seconds * 1000
  countdown.value = seconds
}
async function send() {
  if (sending.value || countdown.value || props.disabled || !props.email) return
  sending.value = true
  try {
    const result = await api<{ message: string; retry_after: number }>(
      '/auth/email-codes',
      'POST',
      { email: props.email, purpose: props.purpose },
    )
    cooldown(result.retry_after)
    emit('sent', result.message)
  } catch (cause) {
    if (cause instanceof ApiError && cause.retryAfter) cooldown(cause.retryAfter)
    emit('error', errorText(cause))
  } finally {
    sending.value = false
  }
}
</script>
<template>
  <div class="form-field">
    <label :for="id">邮箱验证码</label>
    <div class="input-action">
      <input
        :id="id"
        v-model.trim="code"
        inputmode="numeric"
        pattern="[0-9]{6}"
        maxlength="6"
        required
        autocomplete="one-time-code"
        placeholder="输入 6 位验证码"
        :disabled="disabled"
      />
      <AppButton
        variant="secondary"
        :busy="sending"
        :disabled="disabled || countdown > 0 || !email"
        @click="send"
      >
        {{ countdown > 0 ? `${countdown} 秒后重发` : '获取验证码' }}
      </AppButton>
    </div>
  </div>
</template>
