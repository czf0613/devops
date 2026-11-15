<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, useId, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { api, errorText, type Page } from '../api'
import type { WorkItem, Relation } from '../collaboration'
import { repositoryUrl, type Repository } from '../repositories'
import AppButton from './AppButton.vue'
import AppSelect from './AppSelect.vue'
import AppInput from './AppInput.vue'
import AppNotice from './AppNotice.vue'
import AppConfirm from './AppConfirm.vue'
const props = defineProps<{ item: WorkItem; repository: Repository; nested?: boolean; id?: string }>()
const emit = defineEmits<{ updated: [] }>()
const adding = ref(false),
  kind = ref('parent'),
  chosenRepo = ref(String(props.repository.id)),
  query = ref(''),
  target = ref(''),
  candidates = ref<WorkItem[]>([]),
  repos = ref<Repository[]>([props.repository]),
  repoPage = ref(1),
  repoTotal = ref(0)
const busy = ref(false),
  error = ref(''),
  expanded = ref<Record<number, { item: WorkItem; repository: Repository }>>({})
const confirmation = ref<InstanceType<typeof AppConfirm>>()
const root = ref<HTMLElement>(), panelId = useId(), searching = ref(false), searched = ref(false)
const expanding = ref<Record<number, boolean>>({})
let searchSequence = 0
onUnmounted(() => searchSequence++)
async function toggle() {
  if (busy.value) return
  adding.value = !adding.value
  searchSequence++
  searching.value = false
  error.value = ''
  if (adding.value) {
    chosenRepo.value = String(props.repository.id)
    query.value = ''
    candidates.value = []
    target.value = ''
    searched.value = false
    await nextTick()
    root.value?.querySelector<HTMLInputElement>('.relation-search input')?.focus()
  } else {
    await nextTick()
    root.value?.querySelector<HTMLElement>('.relation-toggle')?.focus()
  }
}
const children = computed(() =>
  props.item.relations.items.filter((r) => r.kind === 'parent' && r.source_id === props.item.id),
)
const others = computed(() =>
  props.item.relations.items.filter((r) => r.kind !== 'parent' || r.target_id === props.item.id),
)
const path = computed(() => `/repositories/${props.repository.id}/items/${props.item.number}`)
const options = computed(() =>
  props.item.kind === 'pull'
    ? [{ value: 'development', label: '关联 Issue' }]
    : [
        { value: 'parent', label: '添加已有子 Issue' },
        { value: 'move', label: '移动到父 Issue' },
        { value: 'blocks', label: '此 Issue 阻塞' },
        { value: 'duplicate', label: '重复于' },
        { value: 'development', label: '关联 PR' },
      ],
)
function label(relation: Relation) {
  return relation.kind === 'parent'
    ? '父 Issue'
    : relation.kind === 'blocks'
      ? relation.source_id === props.item.id
        ? '阻塞'
        : '被阻塞于'
      : relation.kind === 'duplicate'
        ? '重复关联'
        : '开发关联'
}
async function repositories(page = 1) {
  try {
    const result = await api<Page<Repository>>(`/repositories?page=${page}`)
    repos.value = page === 1 ? result.items : [...repos.value, ...result.items]
    repoPage.value = page
    repoTotal.value = result.total
  } catch (cause) {
    error.value = errorText(cause)
  }
}
watch(adding, (value) => {
  if (value) {
    kind.value = props.item.kind === 'pull' ? 'development' : 'parent'
    void repositories()
  }
})
async function search() {
  const current = ++searchSequence
  searching.value = true
  searched.value = false
  error.value = ''
  candidates.value = []
  target.value = ''
  try {
    const itemKind = kind.value === 'development' && props.item.kind === 'issue' ? 'pull' : 'issue'
    const result = (
      await api<Page<WorkItem>>(
        `/repositories/${chosenRepo.value}/items?${new URLSearchParams({ q: query.value, state: 'all', kind: itemKind })}`,
      )
    ).items.filter((item) => item.id !== props.item.id)
    if (current === searchSequence) { candidates.value = result; searched.value = true }
  } catch (cause) {
    if (current === searchSequence) error.value = errorText(cause)
  } finally {
    if (current === searchSequence) searching.value = false
  }
}
watch([chosenRepo, kind, query], () => {
  searchSequence++
  searching.value = false
  searched.value = false
  candidates.value = []
  target.value = ''
})
async function connect() {
  busy.value = true
  error.value = ''
  try {
    await api(
      `${path.value}/${kind.value === 'move' ? 'parent' : 'relations'}`,
      kind.value === 'move' ? 'PUT' : 'POST',
      {
        target_id: Number(target.value),
        kind: kind.value === 'move' ? 'parent' : kind.value,
        position: children.value.length,
      },
    )
    busy.value = false
    await toggle()
    emit('updated')
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
async function remove(relation: Relation) {
  if (!(await confirmation.value?.ask('解除关联不会删除或关闭 Issue。', '解除关联？'))) return
  try {
    await api(`${path.value}/relations/${relation.id}`, 'DELETE')
    emit('updated')
  } catch (cause) {
    error.value = errorText(cause)
  }
}
async function expand(relation: Relation) {
  if (expanding.value[relation.id]) return
  if (expanded.value[relation.id]) {
    delete expanded.value[relation.id]
    return
  }
  expanding.value[relation.id] = true
  error.value = ''
  try {
    const [item, repository] = await Promise.all([
      api<WorkItem>(`/repositories/${relation.item.repository_id}/items/${relation.item.number}`),
      api<Repository>(`/repositories/${relation.item.repository_id}`),
    ])
    expanded.value[relation.id] = { item, repository }
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    expanding.value[relation.id] = false
  }
}
async function reorder(index: number, delta: number) {
  const order = [...children.value]
  const entry = order.splice(index, 1)[0]!
  order.splice(index + delta, 0, entry)
  busy.value = true
  try {
    await api(`${path.value}/children/order`, 'PUT', { ids: order.map((r) => r.target_id) })
    emit('updated')
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
async function refreshChild(relation: Relation) {
  delete expanded.value[relation.id]
  await expand(relation)
  emit('updated')
}
</script>
<template>
  <section :id="id" ref="root" class="item-relations" :class="{ 'nested-subissues': nested }">
    <div v-if="!nested" class="heading-row">
      <h3>{{ item.kind === 'issue' ? '子 Issue 与关联' : '关联 Issue' }}</h3>
      <div v-if="item.permissions.can_manage" class="actions">
        <RouterLink
          v-if="item.kind === 'issue'"
          :to="{ path: `${repositoryUrl(repository)}/issue/new`, query: { parent: item.id } }"
          class="text-button"
          >＋ 创建子 Issue</RouterLink
        ><AppButton class="relation-toggle" variant="secondary" :aria-expanded="adding" :aria-controls="panelId" :disabled="busy" @click="toggle">{{ adding ? '关闭关联面板' : '关联已有记录' }}</AppButton>
      </div>
    </div>
    <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
    <div v-if="children.length && !nested" class="subissue-progress">
      <progress
        :value="item.relations.children_completed"
        :max="item.relations.children_total"
        aria-label="子 Issue 完成进度"
      /><span
        >{{ item.relations.children_completed }} / {{ item.relations.children_total }} 完成</span
      >
    </div>
    <div v-for="(relation, index) in children" :key="relation.id" class="subissue-row">
      <div class="subissue-summary">
          <button
            class="text-button subissue-toggle"
            :aria-expanded="Boolean(expanded[relation.id])"
            :aria-label="`${expanded[relation.id] ? '收起' : '展开'} ${relation.item.title}`"
            :aria-controls="`${panelId}-children-${relation.id}`"
            :disabled="expanding[relation.id]"
            @click="expand(relation)"
          >
            {{ expanded[relation.id] ? '▾' : '▸' }}</button
          ><span :class="`item-state-dot ${relation.item.state}`">{{
            relation.item.state === 'open' ? '◉' : '✓'
          }}</span
          ><RouterLink class="subissue-title" :to="relation.item.url"
            >{{ relation.item.title }}
            <span class="muted">#{{ relation.item.number }}</span></RouterLink
          >
        <div v-if="item.permissions.can_manage" class="subissue-actions">
          <button
            class="text-button"
            :disabled="index === 0 || busy"
            aria-label="上移子 Issue"
            @click="reorder(index, -1)"
          >
            ↑</button
          ><button
            class="text-button"
            :disabled="index === children.length - 1 || busy"
            aria-label="下移子 Issue"
            @click="reorder(index, 1)"
          >
            ↓</button
          ><button class="text-button" @click="remove(relation)">解除</button>
        </div>
      </div>
      <p v-if="expanding[relation.id]" class="subissue-empty" role="status">正在读取子 Issue…</p>
      <ItemRelations
        v-else-if="expanded[relation.id]"
        :id="`${panelId}-children-${relation.id}`"
        nested
        :item="expanded[relation.id]!.item"
        :repository="expanded[relation.id]!.repository"
        @updated="refreshChild(relation)"
      />
    </div>
    <div v-for="relation in nested ? [] : others" :key="relation.id" class="relation-row">
      <span class="muted">{{ label(relation) }}</span
      ><RouterLink :to="relation.item.url"
        >{{ relation.item.title }} #{{ relation.item.number }}</RouterLink
      ><button v-if="item.permissions.can_manage" class="text-button" @click="remove(relation)">
        解除
      </button>
    </div>
    <p v-if="nested && !children.length" class="subissue-empty">没有下一级子 Issue</p>
    <p v-else-if="!children.length && !others.length" class="muted">暂无关联</p>
    <form v-if="adding" :id="panelId" class="relation-form" @submit.prevent="connect" @keydown.esc.stop.prevent="toggle">
      <AppSelect v-model="kind" label="关联方式" :options="options" /><AppSelect
        v-model="chosenRepo"
        label="仓库"
        :options="repos.map((repo) => ({ value: String(repo.id), label: repo.full_name }))"
      /><AppButton
        v-if="repos.length < repoTotal"
        variant="secondary"
        @click="repositories(repoPage + 1)"
        >更多仓库</AppButton
      >
      <div class="relation-search">
        <AppInput v-model="query" label="查找记录" placeholder="输入标题关键词" :disabled="busy" @keydown.enter.stop.prevent="search" /><AppButton
          variant="secondary"
          :busy="searching" :disabled="busy"
          @click="search"
          >查找</AppButton
        >
      </div>
      <p v-if="searched && !candidates.length" class="muted" role="status">没有找到匹配的记录。</p>
      <AppSelect
        v-model="target"
        label="目标记录"
        :options="[
          { value: '', label: '请选择' },
          ...candidates.map((candidate) => ({
            value: String(candidate.id),
            label: `#${candidate.number} ${candidate.title}`,
          })),
        ]"
      /><div class="actions"><AppButton type="submit" :busy="busy" :disabled="!target || searching">保存关联</AppButton>
        <AppButton variant="secondary" :disabled="busy" @click="toggle">取消</AppButton></div>
    </form>
  </section>
  <AppConfirm ref="confirmation" />
</template>
