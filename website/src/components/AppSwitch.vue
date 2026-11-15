<script setup lang="ts">
defineProps<{ checked: boolean; label: string; disabled?: boolean; describedby?: string }>()
defineEmits<{ change: [checked: boolean] }>()
</script>

<template>
  <button type="button" class="app-switch" role="switch" :aria-checked="checked"
    :aria-label="label" :aria-describedby="describedby" :disabled="disabled"
    @click="$emit('change', !checked)"><span /></button>
</template>

<style scoped>
.app-switch { width: 44px; height: 26px; padding: 3px; flex-shrink: 0; border: 1px solid var(--line); border-radius: 20px; background: var(--input); cursor: pointer; }
.app-switch span { display: block; height: 18px; width: 18px; background: #9aaec5; border-radius: 50%; transition: transform .15s; }
.app-switch[aria-checked="true"] { background: var(--accent); border-color: var(--accent); }
.app-switch[aria-checked="true"] span { transform: translateX(18px); background: #09222b; }
.app-switch:focus-visible { outline: 2px solid var(--accent); outline-offset: 4px; }
.app-switch:disabled { cursor: wait; opacity: .55; }
</style>
