<script setup lang="ts">
withDefaults(
  defineProps<{
    variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
    type?: 'button' | 'submit' | 'reset'
    busy?: boolean
    disabled?: boolean
  }>(),
  { variant: 'primary', type: 'button' },
)
</script>
<template>
  <button
    :type="type"
    class="button"
    :class="variant"
    :disabled="disabled || busy"
    :aria-busy="busy || undefined"
  >
    <span v-if="busy" class="spinner" aria-hidden="true"></span><slot />
  </button>
</template>
