<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, useId, watch } from 'vue'
import AppIcon from './AppIcon.vue'

export interface SelectOption {
  value: string
  label: string
  disabled?: boolean
}
const props = defineProps<{
  options: SelectOption[]
  label: string
  hideLabel?: boolean
  disabled?: boolean
  compact?: boolean
  searchable?: boolean
  active?: boolean
}>()
const value = defineModel<string>({ default: '' })
const emit = defineEmits<{ change: [value: string] }>()
const id = useId(),
  trigger = ref<HTMLButtonElement>(),
  popup = ref<HTMLElement>(),
  optionList = ref<HTMLElement>(),
  searchInput = ref<HTMLInputElement>()
const open = ref(false),
  highlighted = ref(-1),
  query = ref('')
const placement = ref({ left: '0px', top: '0px', width: '0px', maxHeight: '280px' })
const selected = computed(() => props.options.find((option) => option.value === value.value))
const visibleOptions = computed(() => props.options.filter(option =>
  !props.searchable || `${option.label} ${option.value}`.toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase()),
))
const available = computed(() =>
  visibleOptions.value
    .map((option, index) => (option.disabled ? -1 : index))
    .filter((index) => index >= 0),
)
let search = '',
  lastTyped = 0

function position() {
  if (!open.value || !trigger.value) return
  const rect = trigger.value.getBoundingClientRect()
  const below = window.innerHeight - rect.bottom - 16,
    above = rect.top - 16
  const up = below < 160 && above > below
  const height = Math.min(280, Math.max(64, up ? above : below))
  const width = Math.min(Math.max(rect.width, props.searchable ? 280 : 160), window.innerWidth - 24)
  const actualHeight = Math.min(height, Math.max(1, visibleOptions.value.length) * 42 + (props.searchable ? 106 : 12))
  placement.value = {
    left: `${Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))}px`,
    top: `${up ? rect.top - actualHeight - 6 : rect.bottom + 6}px`,
    width: `${width}px`,
    maxHeight: `${height}px`,
  }
}
function show() {
  if (props.disabled || !props.options.length) return
  query.value = ''
  if (!available.value.length) return
  highlighted.value = visibleOptions.value.findIndex(
    (option) => option.value === value.value && !option.disabled,
  )
  if (highlighted.value < 0) highlighted.value = available.value[0] ?? -1
  open.value = true
  trigger.value?.focus()
  position()
  void reveal()
  if (props.searchable) void nextTick(() => searchInput.value?.focus())
}
async function reveal() {
  await nextTick()
  const container = props.searchable ? optionList.value : popup.value
  const option = container?.querySelectorAll<HTMLElement>('[role="option"]')[highlighted.value]
  if (option && container) {
    if (option.offsetTop < container.scrollTop) container.scrollTop = option.offsetTop
    else if (
      option.offsetTop + option.offsetHeight >
      container.scrollTop + container.clientHeight
    ) {
      container.scrollTop = option.offsetTop + option.offsetHeight - container.clientHeight
    }
  }
}
function choose(index: number) {
  const option = visibleOptions.value[index]
  if (!option || option.disabled || props.disabled) return
  value.value = option.value
  emit('change', option.value)
  open.value = false
  trigger.value?.focus()
}
function close() { open.value = false; trigger.value?.focus() }
function searchKeydown(event: KeyboardEvent) {
  if (event.isComposing) return
  if (event.key === 'Escape') {
    event.preventDefault()
    event.stopPropagation()
    close()
  } else if (event.key === 'Enter') {
    event.preventDefault()
    choose(highlighted.value)
  } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    const current = available.value.indexOf(highlighted.value)
    highlighted.value = available.value[Math.max(0, Math.min(available.value.length - 1,
      current + (event.key === 'ArrowDown' ? 1 : -1)))] ?? -1
    void reveal()
  }
}
function focusout(event: FocusEvent) {
  const target = event.relatedTarget as Node | null
  if (!trigger.value?.contains(target) && !popup.value?.contains(target)) open.value = false
}
function keydown(event: KeyboardEvent) {
  if (props.disabled) return
  if (event.key === 'Escape') {
    if (open.value) {
      open.value = false
      event.preventDefault()
      event.stopPropagation()
    }
    return
  }
  if (event.key === 'Tab') {
    if (open.value) choose(highlighted.value)
    return
  }
  if (['Enter', ' '].includes(event.key)) {
    event.preventDefault()
    if (open.value) choose(highlighted.value)
    else show()
    return
  }
  if (['ArrowDown', 'ArrowUp', 'Home', 'End', 'PageDown', 'PageUp'].includes(event.key)) {
    event.preventDefault()
    const wasOpen = open.value
    if (!wasOpen) show()
    const indexes = available.value
    const current = indexes.indexOf(highlighted.value)
    if (event.key === 'Home') highlighted.value = indexes[0] ?? -1
    else if (event.key === 'End') highlighted.value = indexes.at(-1) ?? -1
    else if (wasOpen) {
      const delta = (event.key.includes('Down') ? 1 : -1) * (event.key.startsWith('Page') ? 10 : 1)
      highlighted.value = indexes[Math.max(0, Math.min(indexes.length - 1, current + delta))] ?? -1
    }
    void reveal()
  } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault()
    if (!open.value) show()
    search =
      Date.now() - lastTyped < 700 ? search + event.key.toLowerCase() : event.key.toLowerCase()
    lastTyped = Date.now()
    const query = [...search].every((character) => character === search[0]) ? search[0]! : search
    const indexes = [
      ...available.value.filter((index) => index > highlighted.value),
      ...available.value.filter((index) => index <= highlighted.value),
    ]
    const match = indexes.find((index) =>
      visibleOptions.value[index]?.label.toLowerCase().startsWith(query),
    )
    if (match !== undefined) highlighted.value = match
    void reveal()
  }
}
function outside(event: PointerEvent) {
  if (
    open.value &&
    !trigger.value?.contains(event.target as Node) &&
    !popup.value?.contains(event.target as Node)
  )
    open.value = false
}
watch(
  () => props.disabled,
  (disabled) => {
    if (disabled) open.value = false
  },
)
watch(
  () => props.options,
  () => {
    if (open.value) {
      highlighted.value = visibleOptions.value.findIndex(
        option => option.value === value.value && !option.disabled,
      )
      if (highlighted.value < 0) highlighted.value = available.value[0] ?? -1
      position()
      void reveal()
    }
  },
)
watch(query, () => {
  highlighted.value = available.value[0] ?? -1
  position()
  void reveal()
}, { flush: 'sync' })
onMounted(() => {
  document.addEventListener('pointerdown', outside)
  window.addEventListener('resize', position)
  window.addEventListener('scroll', position, true)
})
onUnmounted(() => {
  document.removeEventListener('pointerdown', outside)
  window.removeEventListener('resize', position)
  window.removeEventListener('scroll', position, true)
})
</script>
<template>
  <div class="form-field app-select" :class="{ 'field-unlabelled': hideLabel || compact, 'app-select-compact': compact }">
    <label :id="`${id}-label`" :for="id" :class="{ 'sr-only': hideLabel || compact }">{{ label }}</label>
    <div class="field-controls">
    <button
      :id="id"
      ref="trigger"
      type="button"
      :role="searchable ? 'button' : 'combobox'"
      class="select-trigger"
      :class="{ expanded: open, 'filter-active': active }"
      :disabled="disabled"
      :aria-expanded="open"
      :aria-haspopup="searchable ? 'dialog' : 'listbox'"
      :aria-labelledby="`${id}-label`"
      :aria-controls="open ? `${id}-list` : undefined"
      :aria-activedescendant="!searchable && open && highlighted >= 0 ? `${id}-${highlighted}` : undefined"
      @click="open ? (open = false) : show()"
      @keydown="keydown"
      @blur="focusout"
    >
      <span>{{ compact ? label : selected?.label || '请选择' }}</span
      ><AppIcon name="chevron" :size="16" />
    </button>
    <slot name="actions" />
    </div>
    <Teleport to="body">
      <div
        v-if="open"
        :id="`${id}-list`"
        ref="popup"
        :role="searchable ? 'dialog' : 'listbox'"
        :aria-labelledby="`${id}-label`"
        class="select-popup" :class="{ 'select-search-panel': searchable }"
        :style="placement"
        @focusout="focusout"
        @keydown.esc.stop.prevent="close"
      >
        <div v-if="searchable" class="select-search-heading">
          <div><strong>筛选{{ label }}</strong><button type="button" class="text-button" :aria-label="`关闭${label}筛选`" @click="close"><AppIcon name="close" :size="14" /></button></div>
          <input ref="searchInput" v-model="query" type="search" :aria-label="`搜索${label}`" :placeholder="`搜索${label}…`" role="combobox" aria-autocomplete="list" aria-expanded="true" :aria-controls="`${id}-options`" :aria-activedescendant="highlighted >= 0 ? `${id}-${highlighted}` : undefined" @keydown="searchKeydown" />
        </div>
        <div ref="optionList" :id="`${id}-options`" :role="searchable ? 'listbox' : undefined" :aria-label="searchable ? label : undefined" :class="{ 'select-search-options': searchable }" :style="searchable ? { maxHeight: `${Math.max(60, parseFloat(placement.maxHeight) - 100)}px` } : undefined">
        <div
          v-for="(option, index) in visibleOptions"
          :id="`${id}-${index}`"
          :key="option.value"
          role="option"
          :aria-selected="value === option.value"
          :aria-disabled="option.disabled || undefined"
          class="select-option"
          :class="{
            highlighted: highlighted === index,
            selected: value === option.value,
            disabled: option.disabled,
          }"
          @pointermove="!option.disabled && (highlighted = index)"
          @mousedown.prevent
          @click="choose(index)"
        >
          <span>{{ option.label }}</span
          ><AppIcon v-if="value === option.value" name="check" :size="16" />
        </div>
        <p v-if="!visibleOptions.length" class="select-empty" role="status">没有匹配项</p>
        </div>
      </div>
    </Teleport>
  </div>
</template>
