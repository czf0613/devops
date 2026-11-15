<script setup lang="ts">
import { nextTick, onUnmounted, ref, useId, watch } from 'vue'
import { errorText } from '../api'
import { auth } from '../auth'
import AppButton from './AppButton.vue'
import AppIcon from './AppIcon.vue'
import AppNotice from './AppNotice.vue'
import EmailCodeField from './EmailCodeField.vue'
const dialog = ref<HTMLDialogElement>(),
  message = ref(''),
  title = ref('确认操作'),
  acceptLabel = ref('确认操作'),
  id = useId()
let resolve: ((accepted: boolean) => void) | undefined
let previousFocus: HTMLElement | null = null
let execute: ((code: string) => Promise<void>) | undefined
let generation = 0
const verification = ref(false), code = ref(''), error = ref(''), sent = ref(''), busy = ref(false), sending = ref(false)
const purpose = ref<'admin_operation' | 'repository_operation'>('admin_operation')
async function open(text: string, heading: string, label: string, operation?: (code: string) => Promise<void>) {
  generation++
  resolve?.(false)
  execute = operation
  verification.value = !!operation
  code.value = error.value = sent.value = ''
  busy.value = sending.value = false
  message.value = text
  title.value = heading
  acceptLabel.value = label
  previousFocus = document.activeElement as HTMLElement | null
  await nextTick()
  dialog.value?.showModal()
  return new Promise<boolean>((done) => {
    resolve = done
  })
}
function ask(text: string, heading = '确认操作', label = '确认操作') {
  return open(text, heading, label)
}
function askVerified(text: string, heading: string, label: string, operation: (code: string) => Promise<void>, verificationPurpose: 'admin_operation' | 'repository_operation' = 'admin_operation') {
  purpose.value = verificationPurpose
  return open(text, heading, label, operation)
}
function finish(accepted: boolean) {
  generation++
  resolve?.(accepted)
  resolve = undefined
  execute = undefined
  code.value = ''
  dialog.value?.close()
  previousFocus?.focus({ preventScroll: true })
}
function cancel() { if (!busy.value && !sending.value) finish(false) }
async function accept() {
  if (busy.value || sending.value) return
  if (!execute) { finish(true); return }
  if (!/^[0-9]{6}$/.test(code.value)) return
  const current = generation
  busy.value = true; error.value = ''
  try {
    await execute(code.value)
    if (current === generation) finish(true)
  } catch (cause) { if (current === generation) error.value = errorText(cause) }
  finally { if (current === generation || !resolve) busy.value = false }
}
watch(() => [auth.user?.id, auth.user?.email, auth.user?.roles?.join(',')], () => {
  if (verification.value && resolve) finish(false)
})
onUnmounted(() => {
  generation++
  resolve?.(false)
  dialog.value?.close()
})
defineExpose({ ask, askVerified })
</script>
<template>
  <Teleport to="body">
    <dialog
      ref="dialog"
      class="confirm-dialog"
      :aria-labelledby="`${id}-title`"
      :aria-describedby="`${id}-message`"
      @cancel.prevent="cancel"
      @click="$event.target === dialog && cancel()"
    >
      <form class="confirm-content" @submit.prevent="accept">
        <span class="dialog-symbol"><AppIcon name="shield" :size="24" /></span>
        <h2 :id="`${id}-title`">{{ title }}</h2>
        <p :id="`${id}-message`" class="confirm-message">{{ message }}</p>
        <div v-if="verification" class="confirm-verification">
          <p>验证{{ purpose === 'admin_operation' ? '当前管理员' : '当前账号' }}邮箱：<strong>{{ auth.user?.email }}</strong></p>
          <EmailCodeField :key="`${purpose}-${auth.user?.id}-${auth.user?.email}`" v-model="code" v-model:sending="sending" :email="auth.user?.email ?? ''" :purpose="purpose" :disabled="busy" @sent="sent = $event; error = ''" @error="error = $event" />
          <small>每次操作都需要验证，验证码成功使用一次后失效。</small>
          <AppNotice v-if="sent && !error">{{ sent }}</AppNotice>
          <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
        </div>
        <div class="actions">
          <AppButton variant="secondary" autofocus :disabled="busy || sending" @click="cancel">取消</AppButton>
          <AppButton type="submit" variant="danger" :busy="busy" :disabled="sending || (verification && !/^[0-9]{6}$/.test(code))">{{ acceptLabel }}</AppButton>
        </div>
      </form>
    </dialog>
  </Teleport>
</template>

<style scoped>
.confirm-verification { min-width: 0; overflow-wrap: anywhere; }
.confirm-verification p { margin: 0 0 12px; }
.confirm-verification .form-field { margin: 0 0 8px; }
.confirm-verification small { color: var(--muted); }
</style>
