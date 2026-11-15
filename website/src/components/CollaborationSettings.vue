<script setup lang="ts">
import { computed, nextTick, ref, useId, watch } from 'vue'
import { api, errorText } from '../api'
import type { Repository } from '../repositories'
import type { Catalog } from '../collaboration'
import AppPanel from './AppPanel.vue'
import AppButton from './AppButton.vue'
import AppNotice from './AppNotice.vue'
import AppConfirm from './AppConfirm.vue'
import CatalogEditor from './CatalogEditor.vue'
import MergePolicySettings from './MergePolicySettings.vue'

const props = defineProps<{ repository: Repository }>()
const catalog = ref<Catalog[]>([]), kind = ref<Catalog['kind']>('label')
const error = ref(''), notice = ref(''), loading = ref(true), busy = ref(false)
const editing = ref<number | 'new'>(), root = ref<HTMLElement>()
const confirmation = ref<InstanceType<typeof AppConfirm>>()
const id = useId()
const kindLabels = { label: '标签', type: 'Issue 类型', milestone: '里程碑', template: '模板' }
const kinds = Object.keys(kindLabels) as Catalog['kind'][]
const entries = computed(() => catalog.value.filter(c => c.kind === kind.value))
const canManage = computed(() => !props.repository.archived &&
  (props.repository.permissions.is_owner || props.repository.permissions.is_admin ||
    props.repository.permissions.access === 'write'))
let sequence = 0
async function load() {
  const current = ++sequence
  loading.value = true
  error.value = ''
  try {
    const result = await api<Catalog[]>(`/repositories/${props.repository.id}/catalog`)
    if (current === sequence) catalog.value = result
  } catch (cause) { if (current === sequence) error.value = errorText(cause) }
  finally { if (current === sequence) loading.value = false }
}
watch(() => props.repository.id, () => {
  editing.value = undefined
  catalog.value = []
  kind.value = 'label'
  busy.value = false
  notice.value = ''
  void load()
}, { immediate: true })
function selectKind(value: Catalog['kind']) {
  if (busy.value || kind.value === value) return
  editing.value = undefined // Unmounting keeps the draft; Cancel explicitly discards it.
  kind.value = value
}
function tabKey(event: KeyboardEvent, value: Catalog['kind']) {
  let index = kinds.indexOf(value)
  if (event.key === 'ArrowRight') index = (index + 1) % kinds.length
  else if (event.key === 'ArrowLeft') index = (index + kinds.length - 1) % kinds.length
  else if (event.key === 'Home') index = 0
  else if (event.key === 'End') index = kinds.length - 1
  else return
  event.preventDefault()
  if (busy.value) return
  selectKind(kinds[index]!)
  void nextTick(() => root.value?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.focus())
}
async function closeEditor(target = editing.value) {
  editing.value = undefined
  await nextTick()
  focusAction(target)
}
function focusAction(target: number | 'new' | undefined) {
  const button = root.value?.querySelector<HTMLElement>(`[data-edit="${target}"]`)
  if (!button) return
  button.focus({ preventScroll: true })
  const row = button.closest<HTMLElement>('.catalog-entry, .catalog-toolbar') || button
  const rect = row.getBoundingClientRect()
  // Collapsing a long template can leave the viewport far below its original row.
  if (rect.top < 0 || rect.bottom > window.innerHeight) row.scrollIntoView({ block: 'nearest' })
}
async function saved(entry: Catalog) {
  const index = catalog.value.findIndex(c => c.id === entry.id)
  if (index < 0) catalog.value.push(entry)
  else catalog.value[index] = entry
  notice.value = `${kindLabels[entry.kind]}已保存`
  await closeEditor()
}
async function remove(entry: Catalog) {
  if (busy.value || !canManage.value) return
  const repositoryId = props.repository.id
  const message = entry.kind === 'template'
    ? `删除“${entry.name}”模板？已创建的 Issue 和 PR 不受影响。`
    : `删除“${entry.name}”并从已有 Issue 和 PR 中移除此${kindLabels[entry.kind]}。`
  if (!(await confirmation.value?.ask(message, `删除${kindLabels[entry.kind]}？`))) return
  if (props.repository.id !== repositoryId || !canManage.value) return
  busy.value = true
  error.value = ''
  try {
    await api(`/repositories/${repositoryId}/catalog/${entry.id}`, 'DELETE')
    if (props.repository.id !== repositoryId) return
    catalog.value = catalog.value.filter(c => c.id !== entry.id)
    notice.value = `${kindLabels[entry.kind]}已删除`
    busy.value = false
    await nextTick()
    focusAction('new')
  } catch (cause) { if (props.repository.id === repositoryId) error.value = errorText(cause) }
  finally { if (props.repository.id === repositoryId) busy.value = false }
}
</script>

<template>
  <div ref="root">
    <AppPanel title="Issue 与 PR 设置" class="collaboration-settings">
      <AppNotice v-model="notice" tone="success" />
      <div class="catalog-toolbar">
        <div class="catalog-kind-tabs" role="tablist" aria-label="Issue 与 PR 设置分类">
          <button v-for="key in kinds" :id="`${id}-${key}`" :key="key" type="button" role="tab"
            :aria-selected="kind === key" :aria-controls="`${id}-catalog`" :tabindex="kind === key ? 0 : -1"
            :disabled="busy" @click="selectKind(key)" @keydown="tabKey($event, key)">
            {{ kindLabels[key] }}<span class="catalog-count">{{ catalog.filter(c => c.kind === key).length }}</span>
          </button>
        </div>
        <AppButton v-if="canManage" data-edit="new" :disabled="loading || busy || Boolean(error) || editing !== undefined"
          :aria-expanded="editing === 'new'" :aria-controls="`${id}-new`" @click="editing = 'new'">
          新增{{ kind === 'type' ? ' Issue 类型' : kindLabels[kind] }}
        </AppButton>
      </div>
      <div :id="`${id}-catalog`" role="tabpanel" :aria-labelledby="`${id}-${kind}`" :aria-busy="loading">
        <AppNotice v-if="error" tone="error">{{ error }}
          <AppButton variant="secondary" :disabled="busy" @click="load">重新加载</AppButton>
        </AppNotice>
        <p v-if="loading" class="catalog-empty muted">正在加载…</p>
        <template v-else>
          <CatalogEditor v-if="editing === 'new' && canManage" :id="`${id}-new`" :key="`${repository.id}:${kind}:new`"
            :repository-id="repository.id" :kind="kind" :kind-label="kindLabels[kind]"
            @busy="busy = $event" @saved="saved" @cancel="closeEditor" />
          <div class="catalog-list">
            <template v-for="entry in entries" :key="entry.id">
              <CatalogEditor v-if="editing === entry.id && canManage" :repository-id="repository.id"
                :entry="entry" :kind="kind" :kind-label="kindLabels[kind]"
                @busy="busy = $event" @saved="saved" @cancel="closeEditor" />
              <div v-else class="catalog-entry">
                <div class="catalog-entry-content">
                  <strong class="catalog-entry-name">
                    <span v-if="kind === 'label' || kind === 'type'" class="catalog-color-dot" :style="{ background: entry.data.color }" />
                    {{ entry.name }}
                    <span v-if="kind === 'milestone'" class="catalog-meta">{{ entry.data.state === 'closed' ? '已关闭' : '开放' }}</span>
                    <span v-if="kind === 'template'" class="catalog-meta">{{ entry.data.item_kind === 'pull' ? 'PR' : 'Issue' }}</span>
                  </strong>
                  <p v-if="entry.description">{{ entry.description }}</p>
                  <small v-if="kind === 'milestone'">{{ entry.data.due_at ? `截止 ${entry.data.due_at.slice(0, 10)}` : '未设截止日期' }}</small>
                  <small v-if="kind === 'template'">{{ entry.data.fields.length ? `${entry.data.fields.length} 个表单字段` : 'Markdown 模板' }}</small>
                </div>
                <div v-if="canManage" class="actions">
                  <AppButton variant="ghost" :data-edit="entry.id" :aria-label="`编辑${entry.name}`"
                    :disabled="busy || editing !== undefined" @click="editing = entry.id">编辑</AppButton>
                  <AppButton variant="ghost" class="catalog-delete" :aria-label="`删除${entry.name}`"
                    :disabled="busy || editing !== undefined" @click="remove(entry)">删除</AppButton>
                </div>
              </div>
            </template>
          </div>
          <p v-if="!entries.length && editing !== 'new' && !error" class="catalog-empty muted">
            暂无{{ kind === 'type' ? ' Issue 类型' : kindLabels[kind] }}
          </p>
        </template>
      </div>
    </AppPanel>
    <MergePolicySettings v-if="repository.permissions.is_owner" :key="repository.id" :repository="repository" />
    <AppConfirm ref="confirmation" />
  </div>
</template>

<style scoped>
.catalog-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 24px; margin-bottom: 16px; }
.catalog-kind-tabs { display: flex; align-self: stretch; gap: 8px; border-bottom: 1px solid var(--line); }
.catalog-kind-tabs button { display: flex; align-items: center; gap: 8px; padding: 10px 14px; background: none; border: 0; border-bottom: 2px solid transparent; color: var(--muted); font: inherit; cursor: pointer; white-space: nowrap; }
.catalog-kind-tabs button[aria-selected='true'] { color: var(--text); border-bottom-color: var(--accent); }
.catalog-kind-tabs button:disabled { cursor: default; opacity: .6; }
.catalog-count { padding: 0 7px; border-radius: 10px; background: var(--input); font-size: 12px; color: var(--muted); }
.catalog-toolbar > .button { flex-shrink: 0; }
.catalog-entry { display: flex; align-items: center; justify-content: space-between; gap: 24px; padding: 16px 0; border-bottom: 1px solid var(--line); }
.catalog-entry:last-child { border-bottom: 0; }
.catalog-entry-content { min-width: 0; overflow-wrap: anywhere; }
.catalog-entry-name { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.catalog-color-dot { width: 12px; height: 12px; flex-shrink: 0; border-radius: 50%; border: 1px solid #ffffff40; }
.catalog-entry-content p { margin: 5px 0 0; color: var(--muted); white-space: pre-wrap; }
.catalog-entry-content small { display: block; margin-top: 5px; }
.catalog-entry .actions { flex-shrink: 0; gap: 4px; }
.catalog-meta { font-size: 12px; font-weight: 400; padding: 1px 8px; border: 1px solid var(--line); border-radius: 20px; color: var(--muted); }
.catalog-delete { color: var(--red); }
.catalog-empty { margin: 0; padding: 28px 0; text-align: center; }
.catalog-entry, .catalog-toolbar { scroll-margin-block: 24px; }
</style>
