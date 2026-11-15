<script setup lang="ts">
import { useId } from 'vue'
defineOptions({ inheritAttrs: false })
defineProps<{ label: string; hint?: string }>()
const value = defineModel<string>({ default: '' })
const id = useId()
</script>

<template>
  <div class="form-field">
    <label :for="id">{{ label }}</label>
    <textarea :id="id" v-model="value" :aria-describedby="hint ? `${id}-hint` : undefined" v-bind="$attrs" />
    <small v-if="hint" :id="`${id}-hint`">{{ hint }}</small>
  </div>
</template>

<style scoped>
textarea {
  width: 100%; min-height: 100px; resize: vertical; padding: 12px 14px;
  border: 1px solid var(--line); border-radius: 8px;
  background: var(--input); color: var(--text); font: inherit;
}
textarea:focus-visible { outline: 2px solid var(--accent, #59d6ec); outline-offset: 2px; }
</style>
