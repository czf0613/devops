<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import type { DashboardRepository } from '../dashboard'
import { recentRepositoryIds, recentRepositoryRevision } from '../recent-repositories'
import { useDashboardResource } from '../useDashboardResource'
import AppIcon from './AppIcon.vue'
import AppInput from './AppInput.vue'

const props = defineProps<{ userId: number }>()
const search = ref(''), query = ref('')
let timer: ReturnType<typeof setTimeout> | undefined
watch(search, value => {
  clearTimeout(timer)
  timer = setTimeout(() => { query.value = value.trim() }, 250)
})
watch(() => props.userId, () => { clearTimeout(timer); search.value = ''; query.value = '' })
onUnmounted(() => clearTimeout(timer))
const path = computed(() => {
  void recentRepositoryRevision.value
  const params = new URLSearchParams({ q: query.value, page_size: '6' })
  for (const id of recentRepositoryIds(props.userId)) params.append('recent', String(id))
  return `/me/dashboard/repositories?${params}`
})
const { data, busy, error, load } = useDashboardResource<{ items: DashboardRepository[] }>(
  () => path.value, () => ({ items: [] }),
)
</script>

<template>
  <section class="recent-repositories" aria-label="最近仓库" :aria-busy="busy">
    <h2>最近仓库</h2>
    <div class="repository-search"><AppIcon name="search" :size="13" /><AppInput v-model="search" aria-label="搜索最近仓库" placeholder="搜索仓库…" maxlength="100" /></div>
    <p v-if="error" class="recent-message" role="alert">{{ error }} <button type="button" class="retry" @click="load">重试</button></p>
    <p v-else-if="busy" class="recent-message" role="status">正在读取仓库…</p>
    <ul v-else-if="data.items.length">
      <li v-for="repo in data.items" :key="repo.id"><RouterLink :to="repo.url" :title="repo.full_name"><AppIcon name="repo" :size="14" /><span>{{ repo.full_name }}</span><small v-if="repo.archived">归档</small></RouterLink></li>
    </ul>
    <p v-else class="recent-message">{{ query ? '没有找到仓库' : '暂无可访问的仓库' }}</p>
    <RouterLink to="/repositories" class="all-repositories">查看全部仓库 <AppIcon name="arrow" :size="12" /></RouterLink>
  </section>
</template>

<style scoped>
.recent-repositories { margin: 6px 0; padding: 18px 10px 14px; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); min-width: 0; }
h2 { margin: 0 0 13px; font-size: 11px; font-weight: 550; color: var(--muted); }
.repository-search { position: relative; }
.repository-search > .app-icon { position: absolute; left: 8px; top: 11px; z-index: 1; color: var(--muted); }
.repository-search :deep(input) { padding: 8px 8px 8px 27px; font-size: 11px; height: 34px; }
ul { list-style: none; margin: 12px 0 6px; padding: 0; }
li a { display: flex; align-items: center; gap: 7px; font-size: 11px; padding: 9px 0; color: var(--text); min-width: 0; }
li a:hover { color: var(--accent); }
li span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
li .app-icon { color: var(--muted); flex-shrink: 0; }
li small { color: var(--muted); font-size: 9px; flex-shrink: 0; }
.all-repositories { display: flex; align-items: center; gap: 5px; font-size: 11px; color: var(--blue); padding-top: 7px; }
.recent-message { font-size: 11px; color: var(--muted); overflow-wrap: anywhere; line-height: 1.7; }
.retry { background: transparent; border: 0; color: var(--blue); padding: 0; font-size: 11px; }
</style>
