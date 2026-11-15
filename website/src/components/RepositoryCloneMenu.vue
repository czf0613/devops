<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import AppButton from './AppButton.vue'
import AppIcon from './AppIcon.vue'

const props = defineProps<{ cloneUrl: string }>()
const id = useId(), root = ref<HTMLElement>()
const open = ref(false), copied = ref(false), error = ref('')
let copyTimer: ReturnType<typeof setTimeout> | undefined

function clearFeedback() {
  clearTimeout(copyTimer)
  copied.value = false
  error.value = ''
}
function close(restoreFocus = false) {
  open.value = false
  clearFeedback()
  if (restoreFocus) root.value?.querySelector<HTMLButtonElement>('.clone-trigger')?.focus()
}
function outside(event: Event) {
  if (open.value && !root.value?.contains(event.target as Node)) close()
}
function keydown(event: KeyboardEvent) {
  // Safari does not focus buttons on mouse clicks, so Escape must also work from body.
  if (open.value && event.key === 'Escape') {
    event.preventDefault()
    close(true)
  }
}
async function copy() {
  clearFeedback()
  const url = props.cloneUrl
  try {
    await navigator.clipboard.writeText(url)
    if (!open.value || url !== props.cloneUrl) return
    copied.value = true
    copyTimer = setTimeout(() => { copied.value = false }, 2000)
  } catch {
    if (open.value && url === props.cloneUrl) error.value = '复制失败，请选中地址手动复制'
  }
}
watch(() => props.cloneUrl, () => close())
onMounted(() => {
  document.addEventListener('pointerdown', outside)
  document.addEventListener('focusin', outside)
  document.addEventListener('keydown', keydown)
})
onBeforeUnmount(() => {
  close()
  document.removeEventListener('pointerdown', outside)
  document.removeEventListener('focusin', outside)
  document.removeEventListener('keydown', keydown)
})
</script>

<template>
  <div ref="root" class="clone-menu">
    <AppButton class="clone-trigger" :aria-expanded="open" :aria-controls="id" @click="open ? close() : (open = true)">
      <AppIcon name="code" :size="16" />HTTPS 克隆<AppIcon name="chevron" :size="14" />
    </AppButton>
    <section v-if="open" :id="id" class="clone-popup" aria-label="克隆仓库">
      <header class="clone-heading">
        <strong><AppIcon name="repo" :size="17" />克隆仓库</strong>
        <AppButton variant="ghost" class="clone-close" aria-label="关闭克隆面板" @click="close(true)"><AppIcon name="close" :size="16" /></AppButton>
      </header>
      <div class="clone-body">
        <div class="clone-label"><span>HTTPS</span><span v-if="copied" class="copy-feedback" aria-hidden="true">已复制</span></div>
        <div class="clone-address">
          <code tabindex="0" aria-label="HTTPS 克隆地址">{{ cloneUrl }}</code>
          <AppButton variant="secondary" class="clone-copy" aria-label="复制克隆地址" :title="copied ? '已复制' : '复制地址'" @click="copy"><AppIcon :name="copied ? 'check' : 'copy'" :size="17" /></AppButton>
        </div>
        <span class="sr-only" role="status">{{ copied ? '克隆地址已复制' : '' }}</span>
        <p v-if="error" class="clone-error" role="alert">{{ error }}</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.clone-menu { position: relative; }
.clone-popup { position: absolute; right: 0; top: calc(100% + 8px); z-index: 20; width: min(420px, calc(100vw - 48px)); border: 1px solid var(--line); background: #101c2b; box-shadow: 0 12px 32px #0006; border-radius: 10px; overflow: hidden; }
.clone-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 10px 12px 10px 16px; border-bottom: 1px solid var(--line); }
.clone-heading strong { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.clone-heading strong .app-icon { color: var(--muted); }
.clone-close { min-height: 28px; width: 28px; padding: 4px; }
.clone-body { padding: 14px 16px 16px; }
.clone-label { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; color: var(--muted); font-size: 11px; font-weight: 600; }
.copy-feedback { color: var(--accent); }
.clone-address { display: flex; align-items: stretch; min-width: 0; border: 1px solid var(--line); border-radius: 7px; background: var(--input); }
.clone-address code { flex: 1; min-width: 0; padding: 11px 12px; font-size: 12px; line-height: 1.65; overflow-wrap: anywhere; user-select: all; }
.clone-copy { flex: 0 0 42px; min-height: 40px; padding: 8px; border: 0; border-left: 1px solid var(--line); border-radius: 0 6px 6px 0; }
.clone-error { margin: 10px 0 0; color: #ffb4bc; font-size: 12px; line-height: 1.6; }
</style>
