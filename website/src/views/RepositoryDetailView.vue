<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { api, errorText } from '../api'
import { auth } from '../auth'
import { rememberRepository } from '../recent-repositories'
import { repositoryUrl, type Repository } from '../repositories'
import { repositoryPageKey } from '../router/navigation'
import AppBadge from '../components/AppBadge.vue'
import AppNotice from '../components/AppNotice.vue'
import AppIcon from '../components/AppIcon.vue'

const route = useRoute(),
  router = useRouter()
const repo = ref<Repository>(),
  error = ref(''),
  busy = ref(false)
let sequence = 0
onUnmounted(() => { sequence++ })
const tab = computed(() => String(route.meta.repositoryTab || 'code'))
function redirectRoot(detail: Repository) {
  const base = repositoryUrl(detail)
  if (
    (route.params.id && String(route.params.id) === String(detail.id)) ||
    route.path === base ||
    route.path === `${base}/`
  ) {
    const query = { ...route.query }
    const section = query.tab === 'settings' ? 'settings' : 'code'
    delete query.tab
    return router.replace({ path: `${base}/${section}`, query, hash: route.hash })
  }
}
watch(
  [() => route.params.owner, () => route.params.name, () => route.params.id],
  async () => {
    const current = ++sequence
    const userId = auth.user?.id
    repo.value = undefined
    error.value = ''
    busy.value = true
    try {
      const lookup = route.params.id
        ? `/repositories/${route.params.id}`
        : `/repositories/by-name/${encodeURIComponent(String(route.params.owner))}/${encodeURIComponent(String(route.params.name))}`
      const detail = await api<Repository>(lookup)
      if (current !== sequence) return
      await redirectRoot(detail)
      if (current === sequence) {
        repo.value = detail
        if (userId === auth.user?.id) rememberRepository(userId, detail.id)
      }
    } catch (cause) {
      if (current === sequence) error.value = errorText(cause)
    } finally {
      if (current === sequence) busy.value = false
    }
  },
  { immediate: true },
)
watch(
  () => route.path,
  () => {
    if (repo.value) void redirectRoot(repo.value)
  },
)
const tabs = [
  { key: 'code', label: 'Code' },
  { key: 'issue', label: 'Issues' },
  { key: 'pr', label: 'Pull requests' },
  { key: 'ci', label: 'CI' },
  { key: 'registry', label: 'Registry' },
  { key: 'settings', label: 'Settings' },
]
function tabLink(key: string) {
  return {
    path: `${repositoryUrl(repo.value!)}/${key}`,
    query: ['code', 'settings'].includes(key)
      ? { ref: route.query.ref, path: route.query.path }
      : {},
  }
}
</script>

<template>
  <header class="repository-heading">
    <div class="repository-title">
      <AppIcon name="repo" :size="22" />
      <h1>{{ repo?.full_name || '工程仓库' }}</h1>
      <AppBadge>私有仓库</AppBadge>
    </div>
    <RouterLink class="repository-back" to="/repositories">← 工程仓库</RouterLink>
  </header>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  <template v-if="repo">
    <AppNotice v-if="repo.archived">此仓库已归档，目前仅可查看和克隆。</AppNotice>
    <nav class="repo-tabs" aria-label="仓库导航">
      <RouterLink
        v-for="entry in tabs"
        :key="entry.key"
        :to="tabLink(entry.key)"
        :class="{ active: tab === entry.key }"
        :aria-current="tab === entry.key ? 'page' : undefined"
        >{{ entry.label }}</RouterLink
      >
    </nav>
    <RouterView v-slot="{ Component }"
      ><component
        :is="Component"
        :key="repositoryPageKey(route)"
        :repository="repo"
        @repository-updated="repo = $event"
    /></RouterView>
  </template>
  <p v-else-if="busy" class="muted">正在读取仓库…</p>
</template>

<style scoped>
.repository-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin: 0 0 16px;
}
.repository-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  min-width: 0;
}
.repository-title > .app-icon {
  color: var(--muted);
  flex-shrink: 0;
}
.repository-title h1 {
  margin: 0;
  font-size: 22px;
  line-height: 1.4;
  letter-spacing: -0.02em;
  overflow-wrap: anywhere;
  min-width: 0;
}
.repository-back {
  font-size: 12px;
  flex-shrink: 0;
  color: var(--muted);
}
.repository-back:hover {
  color: var(--accent);
}
.repo-tabs {
  display: flex;
  gap: 8px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 24px;
}
.repo-tabs a {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 12px 20px;
  border-bottom: 2px solid transparent;
  color: var(--muted);
}
.repo-tabs a.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
}
@media (max-width: 720px) {
  .repository-heading {
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .repository-title h1 {
    font-size: 19px;
  }
}
</style>
