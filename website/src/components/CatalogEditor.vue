<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { api, errorText } from '../api'
import { useDraft } from '../drafts'
import type { Catalog, FormField } from '../collaboration'
import AppButton from './AppButton.vue'
import AppInput from './AppInput.vue'
import AppTextarea from './AppTextarea.vue'
import AppSelect from './AppSelect.vue'
import AppNotice from './AppNotice.vue'
import DraftStatus from './DraftStatus.vue'
import DiscussionMarkdown from './DiscussionMarkdown.vue'

const props = defineProps<{ repositoryId: number; kind: Catalog['kind']; kindLabel: string; entry?: Catalog }>()
const emit = defineEmits<{ saved: [entry: Catalog]; cancel: []; busy: [value: boolean] }>()
const form = ref<HTMLFormElement>(), busy = ref(false), error = ref(''), preview = ref(false)
const name = ref(props.entry?.name || ''), description = ref(props.entry?.description || '')
const color = ref(props.entry?.data.color || '#36c8c4'), due = ref(props.entry?.data.due_at?.slice(0, 10) || '')
const state = ref(props.entry?.data.state || 'open'), itemKind = ref(props.entry?.data.item_kind || 'issue')
const body = ref(props.entry?.data.body || '')
const fields = ref<FormField[]>(props.entry?.data.fields.map(field => ({ ...field, options: [...field.options] })) || [])
const draft = useDraft(() => `${props.repositoryId}:catalog:${props.kind}:${props.entry?.id || 'new'}`,
  { name, description, color, due, state, itemKind, body, fields })
const initial = JSON.stringify({ name: name.value, description: description.value, color: color.value,
  due: due.value, state: state.value, itemKind: itemKind.value, body: body.value, fields: fields.value })
const changed = computed(() => initial !== JSON.stringify({ name: name.value, description: description.value,
  color: color.value, due: due.value, state: state.value, itemKind: itemKind.value, body: body.value, fields: fields.value }))
const validColor = computed(() => /^#[0-9a-f]{6}$/i.test(color.value))
const fieldTypes = [
  { value: 'input', label: '单行文本' }, { value: 'textarea', label: '多行文本' },
  { value: 'select', label: '单选' }, { value: 'checkbox', label: '确认框' },
]
onMounted(() => {
  draft.restore()
  void nextTick(() => form.value?.querySelector<HTMLInputElement>('input')?.focus({ preventScroll: true }))
})
function cancel() {
  if (busy.value) return
  draft.clear()
  emit('cancel')
}
async function addField() {
  const ids = new Set(fields.value.map(field => field.id))
  let index = 1
  while (ids.has(`field_${index}`)) index++
  fields.value.push({ id: `field_${index}`, label: '', type: 'textarea', required: false, options: [], description: '' })
  await nextTick()
  const cards = form.value?.querySelectorAll<HTMLElement>('.template-field-card')
  cards?.[cards.length - 1]?.querySelector<HTMLInputElement>('input')?.focus()
}
async function removeField(index: number) {
  fields.value.splice(index, 1)
  await nextTick()
  const cards = form.value?.querySelectorAll<HTMLElement>('.template-field-card')
  if (cards?.length) cards[Math.min(index, cards.length - 1)]?.querySelector<HTMLInputElement>('input')?.focus()
  else form.value?.querySelector<HTMLElement>('[data-add-field]')?.focus()
}
function moveField(index: number, direction: number) {
  const other = index + direction
  if (other < 0 || other >= fields.value.length) return
  const field = fields.value.splice(index, 1)[0]!
  fields.value.splice(other, 0, field)
}
async function save() {
  if (busy.value || !name.value.trim() || !changed.value) return
  error.value = ''
  if ((props.kind === 'label' || props.kind === 'type') && !validColor.value) {
    error.value = '请输入六位十六进制颜色，例如 #36c8c4。'
    return
  }
  if (props.kind === 'template') {
    if (new Set(fields.value.map(field => field.id)).size !== fields.value.length) {
      error.value = '表单字段标识不能重复。'
      return
    }
    const invalid = fields.value.findIndex(field => !field.label.trim() ||
      !/^[a-z][a-z0-9_]{0,49}$/.test(field.id) ||
      (field.type === 'select' && (!field.options.some(option => option.trim()) ||
        field.options.filter(option => option.trim()).length > 30 || field.options.some(option => option.trim().length > 256))))
    if (invalid >= 0) {
      error.value = `请检查第 ${invalid + 1} 个字段的标题、标识和选项；单选字段需要 1–30 个选项。`
      return
    }
  }
  busy.value = true
  emit('busy', true)
  try {
    const entry = await api<Catalog>(`/repositories/${props.repositoryId}/catalog${props.entry ? `/${props.entry.id}` : ''}`,
      props.entry ? 'PUT' : 'POST', {
        kind: props.kind, name: name.value.trim(), description: description.value,
        data: { color: color.value, due_at: due.value ? (due.value === props.entry?.data.due_at?.slice(0, 10)
          ? props.entry.data.due_at : `${due.value}T00:00:00Z`) : null,
          state: state.value, item_kind: itemKind.value, body: body.value,
          fields: fields.value.map(field => ({ ...field, options: field.options.map(option => option.trim()).filter(Boolean) })) },
      })
    draft.clear()
    emit('saved', entry)
  } catch (cause) { error.value = errorText(cause) }
  finally { busy.value = false; emit('busy', false) }
}
</script>

<template>
  <form ref="form" class="catalog-edit-form" :aria-label="`${entry ? '编辑' : '新增'}${kindLabel}`"
    @submit.prevent="save" @keydown.esc.stop.prevent="cancel">
    <div class="catalog-edit-heading">
      <h3>{{ entry ? '编辑' : '新增' }}{{ kind === 'type' ? ' Issue 类型' : kindLabel }}</h3>
      <AppButton variant="ghost" :disabled="busy" @click="cancel">取消</AppButton>
    </div>
    <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
    <fieldset class="catalog-edit-fields" :disabled="busy">
      <div class="catalog-primary-fields">
        <AppInput v-model="name" label="名称" required maxlength="80" :disabled="busy" />
        <div v-if="kind === 'label' || kind === 'type'" class="catalog-color-control">
          <AppInput :model-value="validColor ? color : '#36c8c4'" label="颜色" type="color" :disabled="busy" @update:model-value="color = $event" />
          <AppInput v-model="color" label="颜色值" pattern="#[0-9a-fA-F]{6}" maxlength="7" required :disabled="busy" />
          <span class="catalog-preview" :style="{ borderColor: validColor ? color : undefined }"><i :style="{ background: validColor ? color : undefined }" />{{ name || '预览' }}</span>
        </div>
        <AppSelect v-if="kind === 'template'" v-model="itemKind" label="适用类型" :disabled="busy"
          :options="[{ value: 'issue', label: 'Issue' }, { value: 'pull', label: 'Pull request' }]" />
      </div>
      <AppTextarea v-model="description" label="说明（可选）" maxlength="2000" rows="2" :disabled="busy" class="catalog-description" />
      <div v-if="kind === 'milestone'" class="milestone-fields">
        <AppInput v-model="due" label="截止日期（可选）" type="date" :disabled="busy" />
        <AppSelect v-model="state" label="状态" :disabled="busy" :options="[{ value: 'open', label: '开放' }, { value: 'closed', label: '已关闭' }]" />
      </div>
      <template v-if="kind === 'template'">
        <div class="template-body-heading">
          <h4>默认正文</h4>
          <div class="actions">
            <AppButton variant="ghost" :aria-pressed="!preview" :disabled="busy" @click="preview = false">编写</AppButton>
            <AppButton variant="ghost" :aria-pressed="preview" :disabled="busy" @click="preview = true">预览</AppButton>
          </div>
        </div>
        <div v-if="preview" class="template-body-preview">
          <DiscussionMarkdown v-if="body" :source="body" :repository-id="repositoryId" />
          <p v-else class="muted">暂无内容</p>
        </div>
        <AppTextarea v-else v-model="body" label="默认 Markdown 正文" rows="5" maxlength="65536" :disabled="busy" />
        <div class="template-body-heading">
          <h4>表单字段 <span class="muted">{{ fields.length }} / 30</span></h4>
          <AppButton variant="secondary" data-add-field :disabled="busy || fields.length >= 30" @click="addField">添加字段</AppButton>
        </div>
        <p v-if="!fields.length" class="muted template-fields-empty">可直接使用 Markdown 正文，或添加结构化字段。</p>
        <section v-for="(field, index) in fields" :key="index" class="template-field-card" :aria-label="`表单字段 ${index + 1}`">
          <div class="template-body-heading">
            <strong>字段 {{ index + 1 }}</strong>
            <div class="actions">
              <AppButton variant="ghost" :disabled="busy || index === 0" :aria-label="`上移字段 ${index + 1}`" @click="moveField(index, -1)">↑</AppButton>
              <AppButton variant="ghost" :disabled="busy || index === fields.length - 1" :aria-label="`下移字段 ${index + 1}`" @click="moveField(index, 1)">↓</AppButton>
              <AppButton variant="ghost" :disabled="busy" :aria-label="`移除字段 ${index + 1}`" @click="removeField(index)">移除</AppButton>
            </div>
          </div>
          <div class="template-field-grid">
            <AppInput v-model="field.label" label="标题" required maxlength="256" :disabled="busy" />
            <AppSelect v-model="field.type" label="控件" :options="fieldTypes" :disabled="busy" />
            <AppInput v-model="field.id" label="字段标识" required pattern="[a-z][a-z0-9_]{0,49}" maxlength="50" :disabled="busy" />
          </div>
          <AppInput v-model="field.description" label="提示（可选）" maxlength="2000" :disabled="busy" />
          <AppTextarea v-if="field.type === 'select'" :model-value="field.options.join('\n')" label="选项，每行一个" rows="3" required :disabled="busy"
            @update:model-value="field.options = $event.split('\n')" />
          <label class="check-label"><input v-model="field.required" type="checkbox" :disabled="busy" />必填</label>
        </section>
      </template>
    </fieldset>
    <div class="catalog-edit-footer">
      <div class="actions">
        <AppButton type="submit" :busy="busy" :disabled="!name.trim() || !changed">{{ entry ? '保存更改' : '创建' }}</AppButton>
        <AppButton variant="secondary" :disabled="busy" @click="cancel">取消</AppButton>
      </div>
      <DraftStatus :message="draft.message.value" />
    </div>
  </form>
</template>

<style scoped>
.catalog-edit-form { border: 1px solid var(--line); border-radius: 8px; background: var(--input); padding: 18px 20px; margin: 12px 0; }
.catalog-edit-heading, .template-body-heading, .catalog-edit-footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.catalog-edit-heading { margin-bottom: 16px; }
.catalog-edit-heading h3, .template-body-heading h4 { margin: 0; }
.catalog-edit-fields { margin: 0; padding: 0; border: 0; min-width: 0; }
.catalog-primary-fields { display: grid; grid-template-columns: minmax(200px, 1fr) minmax(300px, .65fr); gap: 20px; align-items: end; }
.catalog-primary-fields > :only-child { grid-column: 1 / -1; }
.catalog-primary-fields > .form-field, .catalog-color-control .form-field, .milestone-fields .form-field, .template-field-grid .form-field { margin: 0; }
.catalog-color-control { display: grid; grid-template-columns: 44px 115px minmax(80px, 1fr); gap: 10px; align-items: end; }
.catalog-color-control :deep(input[type='color']) { width: 44px; height: 44px; min-height: 44px; padding: 5px; cursor: pointer; }
.catalog-preview { display: flex; align-items: center; gap: 6px; min-width: 0; max-width: 240px; overflow: hidden; white-space: nowrap; border: 1px solid var(--line); border-radius: 20px; padding: 4px 10px; margin-bottom: 7px; font-size: 12px; }
.catalog-preview i { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
.catalog-description :deep(textarea) { min-height: 76px; }
.milestone-fields { display: grid; grid-template-columns: 240px 200px; gap: 20px; }
.template-body-heading { margin: 16px 0 10px; }
.template-body-heading .actions { gap: 4px; }
.template-body-heading [aria-pressed='true'] { color: var(--accent); background: var(--surface); }
.template-body-preview { min-height: 160px; border: 1px solid var(--line); border-radius: 8px; padding: 16px; }
.template-fields-empty { margin: 10px 0; font-size: 13px; }
.template-field-card { border: 1px solid var(--line); background: var(--surface); border-radius: 8px; padding: 14px 16px; margin: 12px 0; }
.template-field-card .template-body-heading { margin: 0 0 12px; }
.template-field-grid { display: grid; grid-template-columns: 1.5fr 1fr 1fr; gap: 14px; }
.template-field-card .check-label { margin-bottom: 0; }
.catalog-edit-footer { border-top: 1px solid var(--line); margin-top: 18px; padding-top: 16px; }
.catalog-edit-footer :deep(.draft-status) { margin: 0; }
</style>
