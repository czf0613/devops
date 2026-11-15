<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api, dateText, errorText, type Page } from '../api'
import { repositoryUrl, type Repository, type RepositoryMember } from '../repositories'
import { stateLabel, type Catalog, type WorkItem } from '../collaboration'
import AppButton from '../components/AppButton.vue'
import AppInput from '../components/AppInput.vue'
import AppSelect from '../components/AppSelect.vue'
import AppNotice from '../components/AppNotice.vue'
import AppBadge from '../components/AppBadge.vue'
import AppIcon from '../components/AppIcon.vue'
import AppConfirm from '../components/AppConfirm.vue'
import Pager from '../components/Pager.vue'
import ItemMetadataFields from '../components/ItemMetadataFields.vue'
const props = defineProps<{ repository: Repository }>()
const route = useRoute(),
  router = useRouter()
const isPull = computed(() => route.meta.repositoryTab === 'pr')
const base = computed(() => `/repositories/${props.repository.id}`)
const page = ref<Page<WorkItem> & { counts: Record<string, number> }>({
  items: [],
  total: 0,
  page: 1,
  page_size: 25,
  counts: {},
})
const catalog = ref<Catalog[]>([]),
  members = ref<RepositoryMember[]>([]),
  filters = ref<{ id: number; name: string; query: Record<string, string> }[]>([])
const error = ref(''),
  busy = ref(false),
  notice = ref(''),
  search = ref(String(route.query.q || '')),
  savedName = ref('')
const selected = ref<number[]>([]),
  bulkAssignees = ref<number[]>([]),
  bulkCatalog = ref<number[]>([]),
  editMetadata = ref(false)
const confirmation = ref<InstanceType<typeof AppConfirm>>()
const canManage = computed(
  () =>
    !props.repository.archived &&
    (props.repository.permissions.access === 'write' || props.repository.permissions.is_admin),
)
const moreFilters = ref<HTMLDetailsElement>()
const sortOptions = [
  { value: 'updated', label: '最近更新' }, { value: 'created', label: '最新创建' },
  { value: 'oldest', label: '最早创建' }, { value: 'comments', label: '最多评论' },
]
const filterMenus = computed(() => [
  { key: 'author', label: '作者', options: [{ value: '', label: '所有作者' }, ...members.value.map(m => ({ value: String(m.user_id), label: `${m.display_name} (@${m.username})` }))] },
  ...(['label', 'type', 'milestone'] as const).map(kind => ({
    key: kind, label: { label: '标签', type: '类型', milestone: '里程碑' }[kind],
    options: [{ value: '', label: '全部' }, ...catalog.value.filter(c => c.kind === kind).map(c => ({ value: String(c.id), label: c.name }))],
  })),
  { key: 'assignee', label: '负责人', options: [{ value: '', label: '所有负责人' }, ...members.value.map(m => ({ value: String(m.user_id), label: `${m.display_name} (@${m.username})` }))] },
])
const activeFilters = computed(() => {
  const entries: { key: string; label: string; value: string }[] = []
  if (route.query.q) entries.push({ key: 'q', label: '搜索', value: String(route.query.q) })
  for (const menu of filterMenus.value) {
    const value = String(route.query[menu.key] || '')
    if (value) entries.push({ key: menu.key, label: menu.label, value: menu.options.find(option => option.value === value)?.label || value })
  }
  if (route.query.updated_after) entries.push({ key: 'updated_after', label: '更新于', value: String(route.query.updated_after).slice(0, 10) + ' 起' })
  if (route.query.sort && route.query.sort !== 'updated') entries.push({ key: 'sort', label: '排序', value: sortOptions.find(option => option.value === route.query.sort)?.label || String(route.query.sort) })
  return entries
})
function closeMore(restoreFocus = false) {
  if (moreFilters.value) moreFilters.value.open = false
  if (restoreFocus) moreFilters.value?.querySelector('summary')?.focus()
}
function dismissMore(event: Event) {
  if (!moreFilters.value?.contains(event.target as Node)) closeMore()
}
function applySaved(query: Record<string, string>) { closeMore(true); void router.push({ query }) }
function clearFilters() { void router.push({ query: { state: route.query.state } }) }
let sequence = 0
async function load() {
  const current = ++sequence
  busy.value = true
  error.value = ''
  selected.value = []
  try {
    const query = new URLSearchParams({
      ...Object.fromEntries(
        Object.entries(route.query)
          .filter(([, value]) => value)
          .map(([key, value]) => [key, String(value)]),
      ),
      kind: isPull.value ? 'pull' : 'issue',
    })
    const result = await api<typeof page.value>(`${base.value}/items?${query}`)
    if (current === sequence) page.value = result
  } catch (cause) {
    if (current === sequence) error.value = errorText(cause)
  } finally {
    if (current === sequence) busy.value = false
  }
}
function filter(key: string, value: string) {
  void router.push({ query: { ...route.query, [key]: value || undefined, page: undefined } })
}
async function bulk(values: Record<string, unknown>) {
  if (!selected.value.length) return
  if (
    !(await confirmation.value?.ask(`将更新选中的 ${selected.value.length} 条记录。`, '批量更新？'))
  )
    return
  busy.value = true
  try {
    await api(`${base.value}/items/bulk`, 'POST', { numbers: selected.value, ...values })
    await load()
    notice.value = '已更新选中的记录'
    editMetadata.value = false
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
async function saveFilter() {
  try {
    await api(`${base.value}/saved-filters`, 'POST', {
      name: savedName.value,
      query: Object.fromEntries(
        Object.entries(route.query)
          .filter(([key, value]) => key !== 'page' && value)
          .map(([key, value]) => [key, String(value)]),
      ),
    })
    filters.value = await api(`${base.value}/saved-filters`)
    savedName.value = ''
    notice.value = '筛选已保存'
  } catch (cause) {
    error.value = errorText(cause)
  }
}
async function removeFilter(id: number) {
  try {
    await api(`${base.value}/saved-filters/${id}`, 'DELETE')
    filters.value = filters.value.filter((entry) => entry.id !== id)
  } catch (cause) {
    error.value = errorText(cause)
  }
}
watch(() => route.query.q, value => { search.value = String(value || '') })
watch(() => route.fullPath, load, { immediate: true })
onUnmounted(() => { sequence++; document.removeEventListener('pointerdown', dismissMore) })
onMounted(async () => {
  document.addEventListener('pointerdown', dismissMore)
  try {
    ;[catalog.value, members.value, filters.value] = await Promise.all([
      api<Catalog[]>(`${base.value}/catalog`),
      api<RepositoryMember[]>(`${base.value}/members`),
      api<typeof filters.value>(`${base.value}/saved-filters`),
    ])
  } catch (cause) {
    error.value = errorText(cause)
  }
})
</script>
<template>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice
  ><AppNotice v-model="notice" tone="success" />
  <div class="issue-search-row">
    <form class="issue-search" @submit.prevent="filter('q', search)">
      <AppInput v-model="search" aria-label="搜索标题或正文" placeholder="搜索标题或正文…" maxlength="256" />
      <AppButton type="submit" variant="secondary" :busy="busy" aria-label="搜索"><AppIcon name="search" :size="17" /></AppButton>
    </form>
    <details ref="moreFilters" class="issue-more-filters" @keydown.esc.stop.prevent="closeMore(true)" @focusout="($event.relatedTarget && !moreFilters?.contains($event.relatedTarget as Node)) && closeMore()">
      <summary class="text-button">更多筛选<AppIcon name="chevron" :size="14" /></summary>
      <div class="issue-filter-extras">
        <div class="heading-row"><strong>更多筛选</strong><button type="button" class="text-button" aria-label="关闭更多筛选" @click="closeMore(true)"><AppIcon name="close" :size="14" /></button></div>
        <AppInput :model-value="String(route.query.updated_after || '').slice(0, 10)" type="date" label="更新于此日期之后" @update:model-value="filter('updated_after', $event ? `${$event}T00:00:00Z` : '')" />
        <button v-if="route.query.updated_after" type="button" class="text-button" @click="filter('updated_after', '')">不限更新日期</button>
        <div class="saved-filters">
          <h3>保存的筛选</h3>
          <p v-if="!filters.length" class="muted">暂无保存的筛选</p>
          <div v-for="entry in filters" :key="entry.id" class="saved-filter-row">
            <button type="button" class="text-button" @click="applySaved(entry.query)">{{ entry.name }}</button>
            <button type="button" class="text-button" :aria-label="`删除筛选 ${entry.name}`" @click="removeFilter(entry.id)">×</button>
          </div>
          <form class="actions" @submit.prevent="saveFilter">
            <AppInput v-model="savedName" aria-label="筛选名称" placeholder="为当前筛选命名" required maxlength="80" />
            <AppButton type="submit" variant="secondary" :disabled="!savedName.trim()">保存</AppButton>
          </form>
        </div>
      </div>
    </details>
    <RouterLink
      v-if="!repository.archived && (!isPull || repository.permissions.access === 'write')"
      class="button primary issue-create-button"
      :to="`${repositoryUrl(repository)}/${isPull ? 'pr/new' : 'issue/new'}`"
      >{{ isPull ? '创建 PR' : '新建 Issue' }}</RouterLink
    >
  </div>
  <div v-if="activeFilters.length" class="active-issue-filters" aria-label="已选筛选条件">
    <button v-for="entry in activeFilters" :key="entry.key" type="button" class="issue-filter-chip" :aria-label="`移除${entry.label}筛选`" @click="filter(entry.key, '')">
      <span>{{ entry.label }}：{{ entry.value }}</span><AppIcon name="close" :size="12" />
    </button>
    <button type="button" class="text-button" @click="clearFilters">清除筛选</button>
  </div>
  <section class="issue-list" :aria-busy="busy">
    <div class="issue-list-heading">
      <div class="issue-state-filters">
        <input
          v-if="canManage"
          type="checkbox"
          aria-label="选择本页所有记录"
          :checked="page.items.length > 0 && selected.length === page.items.length"
          @change="
            selected =
              selected.length === page.items.length ? [] : page.items.map((item) => item.number)
          "
        /><button
          class="text-button"
          :class="{ active: !route.query.state || route.query.state === 'open' }"
          @click="filter('state', 'open')"
        >
          ◉ {{ page.counts.open || 0 }} 开放</button
        ><button
          class="text-button"
          :class="{ active: route.query.state === 'closed' }"
          @click="filter('state', 'closed')"
        >
          ✓ {{ (page.counts.closed || 0) + (page.counts.merged || 0) }} 已关闭
        </button>
      </div>
      <div class="issue-filter-menus" aria-label="列表筛选">
        <AppSelect v-for="menu in filterMenus" :key="menu.key" compact searchable :label="menu.label" :options="menu.options" :model-value="String(route.query[menu.key] || '')" :active="Boolean(route.query[menu.key])" @change="filter(menu.key, $event)" />
        <AppSelect compact :label="sortOptions.find(option => option.value === (route.query.sort || 'updated'))?.label || '排序'" :options="sortOptions" :model-value="String(route.query.sort || 'updated')" :active="Boolean(route.query.sort && route.query.sort !== 'updated')" @change="filter('sort', $event)" />
      </div>
    </div>
    <div v-if="canManage && selected.length" class="bulk-actions">
      <span class="muted">已选择 {{ selected.length }} 条</span>
      <AppButton
        variant="secondary"
        :disabled="busy"
        @click="bulk({ state: 'closed', close_reason: 'completed' })"
        >关闭</AppButton
      ><AppButton variant="secondary" :disabled="busy" @click="bulk({ state: 'open' })"
        >重新打开</AppButton
      ><AppButton variant="secondary" @click="editMetadata = !editMetadata">编辑元数据</AppButton
      ><template v-if="editMetadata"
        ><ItemMetadataFields
          v-model:assignees="bulkAssignees"
          v-model:selected="bulkCatalog"
          :catalog="catalog"
          :members="members"
        /><AppButton
          :disabled="busy"
          @click="bulk({ assignee_ids: bulkAssignees, catalog_ids: bulkCatalog })"
          >应用所选元数据</AppButton
        ></template
      >
    </div>
    <div v-for="item in page.items" :key="item.id" class="issue-list-row">
      <input
        v-if="canManage"
        v-model="selected"
        type="checkbox"
        :value="item.number"
        :aria-label="`选择 #${item.number}`"
      /><span class="item-state-dot" :class="item.state">{{
        item.state === 'open' ? '◉' : item.state === 'merged' ? '⑂' : '✓'
      }}</span>
      <div class="issue-list-main">
        <div class="actions">
          <RouterLink class="issue-title-link" :to="item.url">{{ item.title }}</RouterLink
          ><AppBadge v-if="item.pinned">置顶</AppBadge
          ><span
            v-for="label in item.catalog.filter((c) => c.kind === 'label')"
            :key="label.id"
            class="issue-label"
            :style="{ borderColor: label.data.color }"
            >{{ label.name }}</span
          >
        </div>
        <small
          >#{{ item.number }} · {{ stateLabel(item) }} · {{ item.author.display_name }} 创建 ·
          {{ dateText(item.updated_at) }} 更新</small
        >
      </div>
      <span class="muted" :title="item.assignees.map((p) => p.display_name).join('、')">{{
        item.assignees.map((p) => p.username).join('、')
      }}</span
      ><RouterLink :to="item.url" class="muted" :aria-label="`${item.comment_count} 条评论`"
        >☏ {{ item.comment_count }}</RouterLink
      >
    </div>
    <div v-if="!page.items.length" class="collaboration-empty">
      {{ busy ? '正在读取…' : '没有符合条件的记录' }}
    </div>
    <Pager
      :page="page.page"
      :total="page.total"
      :page-size="page.page_size"
      :busy="busy"
      @change="router.push({ query: { ...route.query, page: $event } })"
    />
  </section>
  <AppConfirm ref="confirmation" />
</template>
