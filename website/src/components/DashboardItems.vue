<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { dateText, type Page } from '../api'
import { stateLabel } from '../collaboration'
import { relativeTime, type DashboardItem } from '../dashboard'
import { useDashboardResource } from '../useDashboardResource'
import AppButton from './AppButton.vue'
import AppIcon from './AppIcon.vue'
import AppInput from './AppInput.vue'
import AppSelect from './AppSelect.vue'
import Pager from './Pager.vue'

const props = defineProps<{ kind: 'issue' | 'pull'; full?: boolean }>()
const route = useRoute(), router = useRouter()
const title = computed(() => props.kind === 'pull' ? 'Pull requests' : 'Issues')
const scopes = computed(() => [
  { value: 'related', label: '与我相关' },
  props.kind === 'pull' ? { value: 'review_requested', label: '待我审阅' } : { value: 'assigned', label: '分配给我' },
  { value: 'authored', label: '我创建的' },
])
const scopeKey = computed(() => props.full ? 'scope' : `${props.kind === 'pull' ? 'pr' : 'issue'}_scope`)
const scope = computed(() => scopes.value.find(option => option.value === route.query[scopeKey.value])?.value || 'related')
const states = computed(() => [
  { value: 'open', label: '开放中' }, { value: 'closed', label: '已关闭' },
  ...(props.kind === 'pull' ? [{ value: 'merged', label: '已合并' }] : []), { value: 'all', label: '全部状态' },
])
const state = computed(() => props.full && scope.value !== 'review_requested'
  ? states.value.find(option => option.value === route.query.state)?.value || 'open' : 'open')
const page = computed(() => props.full ? Math.min(100000, Math.max(1, Math.floor(Number(route.query.page) || 1))) : 1)
const query = computed(() => props.full && typeof route.query.q === 'string' ? route.query.q.slice(0, 100) : '')
const search = ref(query.value)
watch(query, value => { search.value = value })
const pageSize = computed(() => props.full ? 25 : 5)
const { data, busy, error, load } = useDashboardResource<Page<DashboardItem>>(
  () => `/me/dashboard/items?${new URLSearchParams({
    kind: props.kind, scope: scope.value, state: state.value, q: query.value,
    page: String(page.value), page_size: String(pageSize.value),
  })}`,
  () => ({ items: [], total: 0, page: page.value, page_size: pageSize.value }),
)
function changeScope(value: string) {
  void router.push({ query: { ...route.query, [scopeKey.value]: value, ...(props.full ? { page: undefined } : {}) } })
}
function filter(values: Record<string, string | undefined>) {
  void router.push({ query: { ...route.query, page: undefined, ...values } })
}
const emptyText = computed(() => {
  if (query.value || state.value !== 'open') return '没有符合筛选条件的结果'
  if (scope.value === 'review_requested') return '暂时没有等待你审阅的 Pull request'
  if (scope.value === 'assigned') return '暂时没有分配给你的开放 Issue'
  return `暂时没有${scope.value === 'authored' ? '你创建的' : '与你相关的'}开放 ${props.kind === 'pull' ? 'Pull request' : 'Issue'}`
})
</script>

<template>
  <section class="dashboard-items" :aria-label="title" :aria-busy="busy">
    <header class="items-heading">
      <h2><AppIcon :name="kind" :size="19" />{{ title }}<span class="item-count">{{ busy ? '…' : error ? '—' : data.total }}</span></h2>
      <span class="items-caption">{{ full ? '跨仓库协作' : '开放中' }}</span>
    </header>
    <div class="item-filters" :aria-label="`${title} 筛选`">
      <button v-for="option in scopes" :key="option.value" type="button" :aria-pressed="scope === option.value" :class="{ selected: scope === option.value }" @click="changeScope(option.value)">{{ option.label }}</button>
    </div>
    <form v-if="full" class="work-search" @submit.prevent="filter({ q: search || undefined })">
      <AppInput v-model.trim="search" aria-label="搜索标题" placeholder="搜索标题…" maxlength="100" />
      <AppSelect :model-value="state" label="状态" hide-label :options="states" :disabled="scope === 'review_requested'" @update:model-value="filter({ state: $event })" />
      <AppButton type="submit" variant="secondary" :busy="busy">搜索</AppButton>
    </form>
    <div v-if="error" class="section-message error-message" role="alert"><p>{{ error }}</p><AppButton variant="secondary" @click="load">重试</AppButton></div>
    <div v-else-if="busy" class="section-message" role="status"><span class="loading-dot"></span>正在读取{{ title }}…</div>
    <ul v-else-if="data.items.length" class="work-list">
      <li v-for="item in data.items" :key="item.id" class="work-row">
        <AppIcon :name="kind" :size="19" class="state-icon" :class="[item.state, { draft: item.draft }]" />
        <div class="work-description">
          <RouterLink class="work-title" :to="item.url">{{ item.title }}</RouterLink>
          <div class="work-meta"><RouterLink :to="item.repository.url">{{ item.repository.full_name }}</RouterLink><span>#{{ item.number }}</span><span>·</span><time :datetime="item.updated_at" :title="dateText(item.updated_at)">{{ relativeTime(item.updated_at) }}更新</time></div>
        </div>
        <span class="work-state" :class="item.state">{{ item.draft && item.state === 'open' ? '草稿' : stateLabel(item) }}</span>
      </li>
    </ul>
    <div v-else class="section-message empty-work"><AppIcon :name="kind" :size="26" /><p>{{ emptyText }}</p><span>参与仓库协作后，相关事项会出现在这里。</span></div>
    <Pager v-if="full && !error" :page="page" :page-size="pageSize" :total="data.total" :busy="busy" @change="filter({ page: String($event) })" />
    <RouterLink v-if="!full" class="view-all" :to="{ path: kind === 'pull' ? '/work/pulls' : '/work/issues', query: { scope } }">查看全部 {{ title }}<AppIcon name="arrow" :size="14" /></RouterLink>
  </section>
</template>

<style scoped>
.dashboard-items { border: 1px solid var(--line); border-radius: 10px; background: var(--surface); overflow: hidden; min-width: 0; }
.items-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 20px 22px 15px; }
.items-heading h2 { display: flex; gap: 9px; align-items: center; margin: 0; font-size: 16px; }
.items-heading .app-icon { color: var(--muted); }
.item-count { min-width: 22px; padding: 2px 7px; border-radius: 16px; background: #ffffff0b; color: var(--muted); font-size: 11px; text-align: center; }
.items-caption { color: var(--muted); font-size: 11px; white-space: nowrap; }
.item-filters { display: flex; padding: 0 22px; gap: 23px; border-bottom: 1px solid var(--line); }
.item-filters button { background: none; border: 0; border-bottom: 2px solid transparent; padding: 0 0 12px; font-size: 12px; color: var(--muted); white-space: nowrap; }
.item-filters button.selected { color: var(--text); border-bottom-color: var(--accent); }
.item-filters button:hover { color: var(--accent); }
.work-list { list-style: none; padding: 0; margin: 0; }
.work-row { display: flex; align-items: flex-start; gap: 12px; padding: 17px 22px; border-bottom: 1px solid var(--line); }
.work-row:last-child { border-bottom: 0; }
.work-row:hover { background: #ffffff02; }
.state-icon { margin-top: 3px; flex-shrink: 0; color: #72d9a0; }
.state-icon.closed { color: var(--red); }
.state-icon.merged { color: #bba0ff; }
.state-icon.draft { color: var(--muted); }
.work-description { min-width: 0; flex: 1; }
.work-title { display: inline-block; font-weight: 550; font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.work-title:hover, .work-meta a:hover { color: var(--accent); }
.work-meta { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 5px; font-size: 11px; color: var(--muted); line-height: 1.6; }
.work-meta a { overflow-wrap: anywhere; }
.work-state { flex-shrink: 0; color: var(--muted); border: 1px solid var(--line); border-radius: 12px; padding: 2px 7px; font-size: 10px; margin-top: 3px; }
.view-all { display: flex; align-items: center; justify-content: center; gap: 7px; border-top: 1px solid var(--line); padding: 12px; color: var(--blue); font-size: 12px; }
.view-all:hover { background: #ffffff04; color: var(--accent); }
.section-message { display: flex; justify-content: center; align-items: center; gap: 10px; min-height: 185px; padding: 26px; color: var(--muted); font-size: 13px; text-align: center; }
.empty-work { flex-direction: column; gap: 8px; }
.empty-work p { margin: 4px 0 0; color: var(--text); }
.empty-work > span { font-size: 12px; }
.error-message { flex-direction: column; color: var(--red); }
.loading-dot { height: 6px; width: 6px; border-radius: 50%; background: var(--accent); }
.work-search { display: flex; align-items: center; gap: 10px; padding: 16px 22px; border-bottom: 1px solid var(--line); }
.work-search > :first-child { flex: 1; }
</style>
