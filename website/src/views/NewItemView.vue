<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api, errorText } from '../api'
import { repositoryUrl, type Repository, type RepositoryMember } from '../repositories'
import type { Catalog, Comparison, WorkItem } from '../collaboration'
import AppButton from '../components/AppButton.vue'
import AppInput from '../components/AppInput.vue'
import AppTextarea from '../components/AppTextarea.vue'
import AppSelect from '../components/AppSelect.vue'
import AppNotice from '../components/AppNotice.vue'
import AppPanel from '../components/AppPanel.vue'
import DiscussionEditor from '../components/DiscussionEditor.vue'
import ItemMetadataFields from '../components/ItemMetadataFields.vue'
import DiffViewer from '../components/DiffViewer.vue'
import DraftStatus from '../components/DraftStatus.vue'
import AppConfirm from '../components/AppConfirm.vue'
import { useDraft } from '../drafts'
const props = defineProps<{ repository: Repository }>()
const route = useRoute(),
  router = useRouter()
const isPull = computed(() => route.meta.repositoryTab === 'pr'),
  path = computed(() => `/repositories/${props.repository.id}`)
const title = ref(''),
  body = ref(''),
  error = ref(''),
  busy = ref(false),
  loaded = ref(false),
  draft = ref(false)
const catalog = ref<Catalog[]>([]),
  members = ref<RepositoryMember[]>([]),
  selected = ref<number[]>([]),
  assignees = ref<number[]>([]),
  reviewers = ref<number[]>([])
const templateId = ref(''),
  fields = ref<Record<string, string | boolean>>({})
const template = computed(() =>
  catalog.value.find((entry) => entry.id === Number(templateId.value)),
)
const branches = ref<{ name: string; commit: string }[]>([]),
  base = ref(props.repository.default_branch),
  head = ref(String(route.query.head || '')),
  comparison = ref<Comparison>(),
  comparing = ref(false)
const canManage = computed(
  () =>
    props.repository.permissions.access === 'write' ||
    props.repository.permissions.is_admin ||
    props.repository.permissions.is_owner,
)
let compareSequence = 0
let restoring = false
const confirmation = ref<InstanceType<typeof AppConfirm>>()
const savedDraft = useDraft(
  () => `${props.repository.id}:new:${isPull.value ? 'pull' : 'issue'}:${String(route.query.parent || '')}:${String(route.query.source || '')}`,
  { title, body, selected, assignees, reviewers, templateId, fields, base, head, draft },
  () => loaded.value && !restoring,
)
async function discardDraft() {
  if (!(await confirmation.value?.ask('清除这个新建表单中暂存的内容？', '清除草稿？'))) return
  title.value = body.value = templateId.value = ''
  fields.value = {}
  selected.value = assignees.value = reviewers.value = []
  draft.value = false
  await nextTick()
  savedDraft.clear()
}
watch(templateId, () => {
  if (restoring) return
  body.value = template.value?.data.body || ''
  fields.value = Object.fromEntries(
    (template.value?.data.fields || []).map((field) => [
      field.id,
      field.type === 'checkbox' ? false : '',
    ]),
  )
})
async function compare() {
  const current = ++compareSequence
  comparison.value = undefined
  comparing.value = false
  error.value = ''
  if (!base.value || !head.value || base.value === head.value) return
  comparing.value = true
  try {
    const data = await api<Comparison>(
      `${path.value}/compare?${new URLSearchParams({ base: base.value, head: head.value })}`,
    )
    if (current === compareSequence) comparison.value = data
  } catch (cause) {
    if (current === compareSequence) error.value = errorText(cause)
  } finally {
    if (current === compareSequence) comparing.value = false
  }
}
watch([base, head, loaded], () => {
  if (loaded.value && isPull.value) void compare()
})
async function submit() {
  busy.value = true
  error.value = ''
  try {
    const common = {
      title: title.value,
      body: body.value,
      ...(canManage.value ? { assignee_ids: assignees.value, catalog_ids: selected.value } : {}),
      ...(templateId.value ? { template_id: Number(templateId.value), fields: fields.value } : {}),
    }
    const result = await api<WorkItem>(
      `${path.value}/${isPull.value ? 'pulls' : 'issues'}`,
      'POST',
      isPull.value
        ? {
            ...common,
            base_ref: base.value,
            head_ref: head.value,
            draft: draft.value,
            reviewer_ids: reviewers.value,
          }
        : { ...common, ...(route.query.parent ? { parent_id: Number(route.query.parent) } : {}) },
    )
    savedDraft.clear()
    await router.push(result.url)
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
onMounted(async () => {
  try {
    ;[catalog.value, members.value] = await Promise.all([
      api<Catalog[]>(`${path.value}/catalog`),
      api<RepositoryMember[]>(`${path.value}/members`),
    ])
    if (route.query.source && String(route.query.source).startsWith('/repositories/'))
      body.value = `相关内容：[查看原始位置](${String(route.query.source)})\n\n`
    restoring = true
    savedDraft.restore()
    await nextTick()
    restoring = false
    if (isPull.value) {
      branches.value = (await api<{ items: typeof branches.value }>(`${path.value}/branches`)).items
      if (!branches.value.some((branch) => branch.name === base.value)) base.value = ''
      if (!branches.value.some((branch) => branch.name === head.value)) head.value = ''
      if (!head.value)
        head.value = branches.value.find((branch) => branch.name !== base.value)?.name || ''
    }
    loaded.value = true
  } catch (cause) {
    error.value = errorText(cause)
  }
})
</script>
<template>
  <div class="heading-row">
    <h2>{{ isPull ? '创建 Pull request' : route.query.parent ? '创建子 Issue' : '新建 Issue' }}</h2>
    <RouterLink :to="`${repositoryUrl(repository)}/${isPull ? 'pr' : 'issue'}`"
      >返回列表</RouterLink
    >
  </div>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  <p v-else-if="!loaded" class="muted" role="status">正在读取表单…</p>
  <AppPanel v-if="isPull" title="比较分支"
    ><div class="branch-compare">
      <AppSelect
        v-model="base"
        label="目标分支" :disabled="busy || !loaded"
        :options="branches.map((b) => ({ value: b.name, label: b.name }))"
      /><span>←</span
      ><AppSelect
        v-model="head"
        label="源分支" :disabled="busy || !loaded"
        :options="branches.map((b) => ({ value: b.name, label: b.name }))"
      />
    </div>
    <p v-if="comparing" class="muted">正在比较提交…</p>
    <p v-else-if="comparison" class="muted">
      {{ comparison.commits.length }} 个提交 · {{ comparison.total }} 个文件
    </p></AppPanel
  >
  <form class="new-item-form" @submit.prevent="submit">
    <div class="item-main">
      <AppSelect
        v-if="
          catalog.some(
            (c) => c.kind === 'template' && c.data.item_kind === (isPull ? 'pull' : 'issue'),
          )
        "
        v-model="templateId"
        label="模板"
        :options="[
          { value: '', label: '空白模板' },
          ...catalog
            .filter(
              (c) => c.kind === 'template' && c.data.item_kind === (isPull ? 'pull' : 'issue'),
            )
            .map((c) => ({ value: String(c.id), label: c.name })),
        ]"
        :disabled="busy || !loaded"
      /><AppInput
        v-model="title"
        label="标题"
        required
        maxlength="256"
        :disabled="busy || !loaded"
        placeholder="简要描述需要处理的内容"
      />
      <DiscussionEditor v-model="body" :repository-id="repository.id" :disabled="busy || !loaded" />
      <DraftStatus :message="savedDraft.message.value" />
      <div v-for="field in template?.data.fields || []" :key="field.id" class="template-field">
        <label v-if="field.type === 'checkbox'" class="check-label"
          ><input
            v-model="fields[field.id]"
            type="checkbox"
            :required="field.required"
            :disabled="busy || !loaded"
          />{{ field.label }}</label
        >
        <AppSelect
          v-else-if="field.type === 'select'"
          :model-value="String(fields[field.id] || '')"
          :label="field.label + (field.required ? ' *' : '')"
          :options="[
            { value: '', label: '请选择' },
            ...field.options.map((value) => ({ value, label: value })),
          ]"
          @change="fields[field.id] = $event"
        />
        <AppTextarea
          v-else-if="field.type === 'textarea'"
          :model-value="String(fields[field.id] || '')"
          :label="field.label"
          :required="field.required"
          :disabled="busy || !loaded"
          @update:model-value="fields[field.id] = $event"
        />
        <AppInput
          v-else
          :model-value="String(fields[field.id] || '')"
          :label="field.label"
          :required="field.required"
          :disabled="busy || !loaded"
          @update:model-value="fields[field.id] = $event"
        /><small>{{ field.description }}</small>
      </div>
      <label v-if="isPull" class="check-label"
        ><input v-model="draft" type="checkbox" :disabled="busy || !loaded" />先创建为草稿</label
      >
      <div class="actions">
        <AppButton
          type="submit"
          :busy="busy"
          :disabled="
            !loaded || repository.archived || (isPull && (!comparison?.commits.length || comparing))
          "
          >{{ isPull ? (draft ? '创建草稿 PR' : '创建 PR') : '创建 Issue' }}</AppButton
        ><RouterLink :to="`${repositoryUrl(repository)}/${isPull ? 'pr' : 'issue'}`"
          >取消</RouterLink
        >
        <button v-if="savedDraft.saved.value" type="button" class="text-button" :disabled="busy || !loaded" @click="discardDraft">清除草稿</button>
      </div>
    </div>
    <aside class="item-sidebar">
      <ItemMetadataFields
        v-if="canManage"
        v-model:assignees="assignees"
        v-model:selected="selected"
        :catalog="catalog"
        :members="members"
        :disabled="busy || !loaded"
      />
      <fieldset v-if="isPull">
        <legend>审阅者</legend>
        <label v-for="member in members" :key="member.user_id" class="check-label"
          ><input v-model="reviewers" type="checkbox" :value="member.user_id" :disabled="busy || !loaded" />{{
            member.display_name
          }}</label
        >
      </fieldset>
    </aside>
  </form>
  <DiffViewer
    v-if="isPull && comparison"
    :key="`${base}:${head}`"
    :repository="repository"
    :comparison="comparison"
    :compare-refs="{ base, head }"
  />
  <AppConfirm ref="confirmation" />
</template>
