<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { api, errorText } from '../api'
import type { Catalog, WorkItem } from '../collaboration'
import type { RepositoryMember } from '../repositories'
import AppButton from './AppButton.vue'
import AppSelect from './AppSelect.vue'
import AppNotice from './AppNotice.vue'
const props = defineProps<{ item: WorkItem; catalog: Catalog[]; members: RepositoryMember[] }>()
type Field = 'assignees' | 'label' | 'type' | 'milestone'
const emit = defineEmits<{ updated: [] }>()
const labels: Record<Field, string> = { assignees: '负责人', label: '标签', type: '类型', milestone: '里程碑' }
const editing = ref<Field>(), values = ref<number[]>([]), single = ref(''), busy = ref(false), error = ref('')
const root = ref<HTMLElement>()
let version = 0, trigger: HTMLElement | null = null
const entries = computed(() => props.catalog.filter(c => c.kind === editing.value))
async function start(field: Field, event: Event) {
  trigger = event.currentTarget as HTMLElement
  editing.value = field
  version = props.item.version
  values.value = field === 'assignees' ? props.item.assignees.map(p => p.id)
    : props.item.catalog.filter(c => c.kind === field).map(c => c.id)
  single.value = String(values.value[0] || '')
  error.value = ''
  await nextTick()
  root.value?.querySelector<HTMLElement>('form input, form button[role="combobox"], form button')?.focus()
}
async function cancel() {
  if (busy.value) return
  const label = editing.value && labels[editing.value]
  editing.value = undefined
  error.value = ''
  await nextTick()
  if (trigger?.isConnected) trigger.focus()
  else root.value?.querySelector<HTMLElement>(`[aria-label="编辑${label}"]`)?.focus()
}
async function save() {
  if (busy.value || !editing.value) return
  busy.value = true
  error.value = ''
  try {
    const chosen = ['type', 'milestone'].includes(editing.value)
      ? single.value ? [Number(single.value)] : [] : values.value
    await api(`/repositories/${props.item.repository_id}/items/${props.item.number}`, 'PATCH', {
      version,
      ...(editing.value === 'assignees' ? { assignee_ids: chosen }
        : { catalog_ids: [...props.item.catalog.filter(c => c.kind !== editing.value).map(c => c.id), ...chosen] }),
    })
    busy.value = false
    await cancel()
    emit('updated')
  } catch (cause) { error.value = errorText(cause) }
  finally { busy.value = false }
}
</script>
<template>
  <div ref="root" class="sidebar-block inline-metadata">
    <section v-for="(label, field) in labels" :key="field" class="metadata-field">
      <div class="heading-row"><h3>{{ label }}</h3>
        <button v-if="item.permissions.can_manage && editing !== field" class="text-button"
          :disabled="busy || Boolean(editing)" :aria-label="`编辑${label}`" @click="start(field, $event)">编辑</button>
      </div>
      <form v-if="editing === field" @submit.prevent="save" @keydown.esc.stop.prevent="cancel">
        <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
        <div v-if="field === 'assignees' || field === 'label'" class="check-options">
          <label v-for="entry in field === 'assignees'
            ? members.filter(m => m.status === 'active').map(m => ({ id: m.user_id, name: m.display_name })) : entries" :key="entry.id">
            <input v-model="values" type="checkbox" :value="entry.id" :disabled="busy" />{{ entry.name }}
          </label>
          <p v-if="field === 'label' && !entries.length" class="muted">暂无标签，可在 Settings 中添加。</p>
        </div>
        <AppSelect v-else v-model="single" :label="label" hide-label :disabled="busy"
          :options="[{ value: '', label: '未指定' }, ...entries.map(c => ({ value: String(c.id), label: c.name }))]" />
        <div class="actions"><AppButton type="submit" :busy="busy">保存</AppButton>
          <AppButton variant="secondary" :disabled="busy" @click="cancel">取消</AppButton></div>
      </form>
      <p v-else-if="field === 'assignees'">{{ item.assignees.map(p => p.display_name).join('、') || '未指定' }}</p>
      <div v-else-if="field === 'label'" class="actions">
        <span v-for="entry in item.catalog.filter(c => c.kind === field)" :key="entry.id" class="issue-label" :style="{ borderColor: entry.data.color }">{{ entry.name }}</span>
        <span v-if="!item.catalog.some(c => c.kind === field)" class="muted">未指定</span>
      </div>
      <p v-else>{{ item.catalog.find(c => c.kind === field)?.name || '未指定' }}</p>
    </section>
  </div>
</template>
