<script setup lang="ts">
import { useId } from 'vue'
defineOptions({ inheritAttrs: false })
withDefaults(defineProps<{ label?: string; hint?: string; type?: string }>(), { type: 'text' })
const [value, modifiers] = defineModel<string>({
  default: '',
  set: (value) => (modifiers.trim ? value.trim() : value),
})
const id = useId()
</script>
<template>
  <div class="form-field" :class="{ 'field-unlabelled': !label }">
    <label v-if="label" :for="id">{{ label }}</label>
    <div class="field-controls">
    <input
      :id="id"
      v-model="value"
      :type="type"
      :aria-describedby="hint ? `${id}-hint` : undefined"
      v-bind="$attrs"
    />
    <slot name="actions" />
    </div>
    <small v-if="hint" :id="`${id}-hint`">{{ hint }}</small>
  </div>
</template>
