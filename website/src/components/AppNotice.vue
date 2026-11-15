<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'

const props = withDefaults(defineProps<{
  tone?: 'error' | 'success' | 'info'
  duration?: number
  placement?: 'inline' | 'floating'
}>(), { tone: 'info', placement: 'inline' })
const message = defineModel<string>()
const emit = defineEmits<{ dismiss: [] }>()
const visible = ref(true)
const host = ref<HTMLElement>()
watch(() => props.placement, placement => {
  if (placement !== 'floating') return
  let target = document.getElementById('app-notice-stack')
  if (!target) {
    target = document.createElement('div')
    target.id = 'app-notice-stack'
    target.className = 'app-notice-stack'
    document.body.append(target)
  }
  host.value = target
}, { immediate: true })
let timer: ReturnType<typeof setTimeout> | undefined
function dismiss() {
  clearTimeout(timer)
  visible.value = false
  if (message.value !== undefined) message.value = ''
  emit('dismiss')
}
watch(() => [message.value, props.tone, props.duration], () => {
  clearTimeout(timer)
  visible.value = true
  const duration = props.duration ?? (props.tone === 'success' ? 5000 : 0)
  if (duration > 0 && message.value !== '') timer = setTimeout(dismiss, duration)
}, { immediate: true })
onBeforeUnmount(() => clearTimeout(timer))
</script>
<template>
  <Teleport :to="host || 'body'" :disabled="placement === 'inline'">
  <div v-if="visible && message !== ''" class="message app-notice" :class="[tone, { 'notice-floating': placement === 'floating' }]" :role="tone === 'error' ? 'alert' : 'status'">
    <div><slot>{{ message }}</slot></div>
    <button v-if="placement === 'floating' || (tone === 'success' && duration !== 0)" type="button" aria-label="关闭提示" @click="dismiss"><AppIcon name="close" :size="16" /></button>
  </div>
  </Teleport>
</template>
<style scoped>
.app-notice { display: flex; align-items: flex-start; gap: 16px; }
.app-notice > div { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.app-notice button { display: inline-flex; padding: 2px; border: 0; border-radius: 4px; background: transparent; color: inherit; }
.app-notice button:hover { background: #ffffff12; }
.notice-floating { margin: 0; pointer-events: auto; box-shadow: 0 8px 28px #0006; }
</style>
