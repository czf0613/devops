<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api, errorText } from '../api'
import { auth } from '../auth'
import { repositoryUrl, type Repository } from '../repositories'
import type { Namespace, RegistryPage } from '../registry'
import AppBadge from '../components/AppBadge.vue'
import AppButton from '../components/AppButton.vue'
import AppInput from '../components/AppInput.vue'
import AppNotice from '../components/AppNotice.vue'
import AppPanel from '../components/AppPanel.vue'
import AppSelect from '../components/AppSelect.vue'
import Pager from '../components/Pager.vue'
import RegistryImages from '../components/RegistryImages.vue'
import RegistryNamespaceCreate from '../components/RegistryNamespaceCreate.vue'

type UserNamespace = Namespace & { repository: Repository }
type Overview = RegistryPage<UserNamespace> & { enabled: boolean; host: string }
const route = useRoute(), router = useRouter()
const query = ref(''), state = ref('all'), page = ref(1), data = ref<Overview>(), selectedId = ref('')
const loading = ref(false), error = ref(''), message = ref(''), creating = ref(false)
const repoQuery = ref(''), repoPage = ref(1), repositories = ref<RegistryPage<Repository>>()
const searching = ref(false), searchError = ref(''), repositoryId = ref('')
const confirmationBusy = ref(false)
const selected = computed(() => data.value?.items.find(ns => String(ns.id) === selectedId.value))
const destination = computed(() => repositories.value?.items.find(repo => String(repo.id) === repositoryId.value))
const canManage = computed(() => !!selected.value?.repository.permissions.is_owner && !selected.value.repository.archived)
let generation = 0, reads = 0, searches = 0
onUnmounted(() => { generation++; reads++; searches++ })
watch(() => [auth.user?.id, route.query.q, route.query.state, route.query.page], () => {
  generation++; searches++; searching.value = false; repositories.value = undefined; repositoryId.value = ''
  creating.value = false; data.value = undefined; selectedId.value = ''; message.value = ''; confirmationBusy.value = false
  query.value = typeof route.query.q === 'string' ? route.query.q : ''
  state.value = ['all', 'bound', 'unbound'].includes(String(route.query.state)) ? String(route.query.state) : 'all'
  page.value = Math.max(1, Math.min(100000, Number(route.query.page) || 1))
  void load()
}, { immediate: true })
async function load() {
  const current = ++reads, life = generation
  loading.value = true; error.value = ''
  try {
    const result = await api<Overview>(`/me/registry/namespaces?${new URLSearchParams({ q: query.value, state: state.value, page: String(page.value) })}`)
    if (current !== reads || life !== generation) return
    data.value = result
    if (!result.items.some(ns => String(ns.id) === selectedId.value)) selectedId.value = ''
  } catch (cause) { if (current === reads && life === generation) { data.value = undefined; selectedId.value = ''; error.value = errorText(cause) } }
  finally { if (current === reads && life === generation) loading.value = false }
}
function filter(nextPage = 1) {
  const next = { q: query.value || undefined, state: state.value === 'all' ? undefined : state.value, page: nextPage > 1 ? String(nextPage) : undefined }
  if (query.value === (route.query.q ?? '') && (next.state ?? '') === (route.query.state ?? '') && nextPage === page.value) void load()
  else void router.push({ path: '/images', query: next })
}
async function searchRepositories(nextPage = 1) {
  const current = ++searches, life = generation
  repoPage.value = nextPage; searching.value = true; searchError.value = ''; repositoryId.value = ''; repositories.value = undefined
  try {
    const result = await api<RegistryPage<Repository>>(`/me/registry/repositories?${new URLSearchParams({ q: repoQuery.value, page: String(nextPage) })}`)
    if (current === searches && life === generation) repositories.value = result
  } catch (cause) { if (current === searches && life === generation) searchError.value = errorText(cause) }
  finally { if (current === searches && life === generation) searching.value = false }
}
function toggleCreate() { creating.value = !creating.value; if (creating.value) void searchRepositories() }
async function created(ns: Namespace) {
  creating.value = false; message.value = `已创建并绑定 ${ns.name}`
  if (query.value || state.value !== 'all' || page.value !== 1) await router.push('/images')
  else await load()
}
async function rebind() {
  if (!selected.value || !canManage.value || confirmationBusy.value) return
  const ns = selected.value, life = generation
  confirmationBusy.value = true; error.value = ''
  try {
    await api(`/repositories/${ns.repository_id}/registry/namespaces/${ns.id}`, 'PUT', { enabled: true, expected_version: ns.version })
    if (life === generation) { message.value = '已重新绑定 namespace'; await load() }
  } catch (cause) { if (life === generation) error.value = errorText(cause) }
  finally { if (life === generation) confirmationBusy.value = false }
}
</script>
<template>
  <div class="page-heading"><h1>镜像管理</h1><p>集中查看你参与的仓库所关联的 namespace、镜像和标签。</p></div>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  <AppNotice v-model="message" tone="success" placement="floating" />
  <AppPanel title="我的 namespaces" description="访问和管理权限沿用各自关联的工程仓库。">
    <template #actions><div class="actions"><AppButton variant="secondary" :busy="loading" @click="load">刷新</AppButton><AppButton v-if="data?.enabled" :aria-expanded="creating" aria-controls="create-namespace" @click="toggleCreate">{{ creating ? '取消创建' : '添加 namespace' }}</AppButton></div></template>
    <AppNotice v-if="data && !data.enabled">镜像仓库尚未启用。</AppNotice>
    <template v-else>
      <section v-if="creating" id="create-namespace" class="namespace-editor">
        <h3>添加 namespace 到仓库</h3><p class="muted">只有拥有者可为未归档的仓库创建 namespace。</p>
        <form @submit.prevent="searchRepositories()"><AppInput v-model.trim="repoQuery" label="查找我的仓库" placeholder="输入仓库名称" :maxlength="100"><template #actions><AppButton type="submit" variant="secondary" :busy="searching">搜索仓库</AppButton></template></AppInput></form>
        <AppNotice v-if="searchError" tone="error">{{ searchError }}</AppNotice>
        <AppSelect v-if="repositories?.items.length" v-model="repositoryId" label="关联仓库" :options="[{ value: '', label: '请选择仓库' }, ...repositories.items.map(repo => ({ value: String(repo.id), label: repo.full_name }))]" />
        <p v-else-if="repositories" class="muted">没有符合条件的可管理仓库。</p>
        <Pager v-if="repositories && repositories.total > 20" :page="repoPage" :total="repositories.total" :busy="searching" @change="searchRepositories" />
        <RegistryNamespaceCreate v-if="destination" :key="destination.id" :repository-id="destination.id" @created="created" />
      </section>
      <form class="namespace-filters" @submit.prevent="filter()"><AppInput v-model.trim="query" label="搜索" placeholder="namespace 或拥有者/仓库名" :maxlength="100"><template #actions><AppButton type="submit" variant="secondary" :disabled="loading">搜索</AppButton></template></AppInput><AppSelect v-model="state" label="绑定状态" :options="[{ value: 'all', label: '全部' }, { value: 'bound', label: '已绑定' }, { value: 'unbound', label: '已解绑' }]" @update:model-value="filter()" /></form>
      <p v-if="loading && !data" class="muted">正在读取…</p>
      <p v-else-if="data && !data.items.length" class="empty-state">没有符合条件的 namespace。可以为你拥有的仓库添加 namespace。</p>
      <ul v-else class="my-namespaces"><li v-for="ns in data?.items" :key="ns.id" :class="{ selected: selectedId === String(ns.id) }"><button type="button" :aria-pressed="selectedId === String(ns.id)" @click="selectedId = String(ns.id)"><strong>{{ ns.name }}</strong><small>{{ ns.repository.full_name }}</small></button><AppBadge :tone="ns.enabled ? 'green' : undefined">{{ ns.enabled ? '已绑定' : '已解绑' }}</AppBadge><AppBadge v-if="ns.repository.archived">仓库已归档</AppBadge><span class="muted">{{ ns.repository.permissions.is_owner ? '拥有者' : ns.repository.permissions.access === 'write' ? '读写' : '只读' }}</span></li></ul>
      <Pager v-if="data && data.total > 20" :page="page" :total="data.total" :busy="loading" @change="filter" />
    </template>
  </AppPanel>
  <template v-if="selected && data?.enabled">
    <div class="namespace-context"><p><strong>{{ selected.name }}</strong> · <RouterLink :to="repositoryUrl(selected.repository) + '/registry'" class="text-link">{{ selected.repository.full_name }} 的 Registry</RouterLink></p><span v-if="selected.repository.archived" class="muted">仓库已归档，镜像只读。</span></div>
    <RegistryImages v-if="selected.enabled" :repository="selected.repository" :namespace="selected" :host="data.host" />
    <AppPanel v-else title="namespace 已解绑" description="镜像数据仍保留。重新绑定后才能查看和拉取镜像。"><AppButton v-if="canManage" :busy="confirmationBusy" @click="rebind">重新绑定到原仓库</AppButton><p v-else class="muted">请联系仓库拥有者处理。</p></AppPanel>
  </template>
  <p class="muted">Docker 登录令牌统一在 <RouterLink to="/security" class="text-link">账号安全</RouterLink> 管理。</p>
</template>
<style scoped>
.namespace-filters { display: grid; grid-template-columns: minmax(320px, 1fr) 220px; align-items: start; gap: 24px; margin-bottom: 20px; }
.namespace-filters :deep(.form-field) { margin: 0; }
.namespace-editor { padding: 24px; margin-bottom: 24px; border: 1px solid var(--line); border-radius: 8px; }
.namespace-editor h3 { margin-top: 0; }
.my-namespaces { list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; }
.my-namespaces li { display: flex; align-items: center; gap: 14px; padding: 16px 20px; border: 1px solid var(--line); border-radius: 8px; }
.my-namespaces li.selected { border-color: var(--accent); background: var(--surface-hover); }
.my-namespaces button { flex: 1; min-width: 0; border: 0; padding: 0; text-align: left; color: var(--text); background: transparent; cursor: pointer; overflow-wrap: anywhere; }
.my-namespaces strong { display: block; font-size: 17px; }
.my-namespaces small { display: block; color: var(--muted); margin-top: 6px; }
.namespace-context { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin: 24px 0; overflow-wrap: anywhere; }
</style>
