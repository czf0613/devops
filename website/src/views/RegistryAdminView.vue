<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { api, errorText } from '../api'
import { auth } from '../auth'
import type { AdminNamespace, RegistryPage, RegistryRepository } from '../registry'
import AppBadge from '../components/AppBadge.vue'
import AppButton from '../components/AppButton.vue'
import AppConfirm from '../components/AppConfirm.vue'
import AppInput from '../components/AppInput.vue'
import AppNotice from '../components/AppNotice.vue'
import AppPanel from '../components/AppPanel.vue'
import AppSelect from '../components/AppSelect.vue'
import Pager from '../components/Pager.vue'
import RegistryGCPanel from '../components/RegistryGCPanel.vue'

const rows = ref<AdminNamespace[]>([]), total = ref(0), page = ref(1)
const query = ref(''), state = ref('all'), host = ref(''), enabled = ref(false)
const loading = ref(false), busy = ref(false), error = ref(''), message = ref('')
const editing = ref<AdminNamespace>(), destination = ref(''), repoQuery = ref('')
const repositories = ref<RegistryRepository[]>([]), repoPage = ref(1), repoTotal = ref(0), repoLoading = ref(false), repoError = ref('')
const confirmation = ref<InstanceType<typeof AppConfirm>>()
const gcPanel = ref<InstanceType<typeof RegistryGCPanel>>()
let lifetime = 0, reads = 0, repoReads = 0
onUnmounted(() => { lifetime++; reads++; repoReads++ })
watch(() => auth.user?.id, () => {
  lifetime++; rows.value = []; editing.value = undefined; error.value = ''; message.value = ''; busy.value = false
  void load()
}, { immediate: true })
async function load(next = 1) {
  const current = ++reads, life = lifetime
  loading.value = true; error.value = ''
  try {
    const params = new URLSearchParams({ q: query.value, state: state.value, page: String(next) })
    const data = await api<RegistryPage<AdminNamespace> & { enabled: boolean; host: string }>(`/admin/registry?${params}`)
    if (current !== reads || life !== lifetime) return
    rows.value = data.items; total.value = data.total; page.value = next; host.value = data.host; enabled.value = data.enabled
    if (next > 1 && !data.items.length && data.total) await load(Math.ceil(data.total / 20))
  } catch (cause) { if (current === reads && life === lifetime) { error.value = errorText(cause); rows.value = [] } }
  finally { if (current === reads && life === lifetime) loading.value = false }
}
function edit(ns: AdminNamespace) {
  editing.value = ns; destination.value = ''; repoQuery.value = ''; repositories.value = []; repoTotal.value = 0
  void loadRepositories()
}
function cancel() { editing.value = undefined; repoReads++; repoLoading.value = false }
async function loadRepositories(next = 1) {
  const current = ++repoReads, life = lifetime, ns = editing.value
  destination.value = ''; repositories.value = []; repoLoading.value = true; repoError.value = ''
  try {
    const data = await api<RegistryPage<RegistryRepository>>(`/admin/registry/repositories?${new URLSearchParams({ q: repoQuery.value, page: String(next) })}`)
    if (current !== repoReads || life !== lifetime || editing.value !== ns) return
    repositories.value = data.items; repoTotal.value = data.total; repoPage.value = next
  } catch (cause) { if (current === repoReads && life === lifetime) repoError.value = errorText(cause) }
  finally { if (current === repoReads && life === lifetime) repoLoading.value = false }
}
async function change(ns: AdminNamespace, target?: RegistryRepository) {
  if (busy.value) return
  const life = lifetime
  const description = target
    ? `将 ${ns.name} 及其中所有镜像绑定到 ${target.full_name}。${ns.bound ? `原 repo ${ns.repository.full_name} 的成员会失去由原 repo 获得的访问权限。` : ''}目标 repo 的成员将按各自读写权限访问镜像，下一次请求立即生效。`
    : `解绑 ${ns.name} 后，所有客户端在下一次请求失去访问权限。镜像数据、名称和上次归属保留，可在此处重新分配。`
  busy.value = true; error.value = ''; message.value = ''
  try {
    if (!await confirmation.value?.askVerified(description, target ? '确认 namespace 归属变更？' : `解绑 ${ns.name}？`, target ? '验证并绑定' : '验证并解绑', async code => {
      if (life !== lifetime) throw new Error('当前账号已变化，请刷新后重试')
      await api(`/admin/registry/namespaces/${ns.id}/binding`, 'PUT', { repository_id: target?.id ?? null, expected_version: ns.version, code })
    })) return
    if (life !== lifetime) return
    message.value = target ? `已绑定到 ${target.full_name}` : '已解绑 namespace'
    cancel()
    await load(page.value)
  } catch (cause) { if (life === lifetime) error.value = errorText(cause) }
  finally { if (life === lifetime) busy.value = false }
}
async function remove(ns: AdminNamespace) {
  if (busy.value) return
  const life = lifetime
  busy.value = true; error.value = ''; message.value = ''
  try {
    const accepted = await confirmation.value?.askVerified(
      `删除 ${ns.name} 及其中全部镜像？${ns.bound ? `这会同时解除与 ${ns.repository.full_name} 的绑定。` : ''}\n\n所有成员在下一次请求失去镜像访问权限。下次维护窗口 GC 将删除其中全部标签和镜像版本，并回收不再共享的数据。\n\n名称继续保留，不能重新创建或绑定。此操作无法在页面中撤销。`,
      '删除 namespace？', '验证并删除', async code => {
        if (life !== lifetime) throw new Error('当前账号已变化，请刷新后重试')
        await api(`/admin/registry/namespaces/${ns.id}`, 'DELETE', { expected_version: ns.version, confirm_name: ns.name, code })
      },
    )
    if (!accepted || life !== lifetime) return
    message.value = 'namespace 及全部镜像已停止访问，等待维护窗口 GC 清理'
    cancel(); await load(page.value); await gcPanel.value?.refresh?.()
  } finally { if (life === lifetime) busy.value = false }
}
function bind() {
  const target = repositories.value.find(repo => String(repo.id) === destination.value)
  if (editing.value && target) void change(editing.value, target)
}
</script>

<template>
  <div class="page-heading"><p class="eyebrow">REGISTRY</p><h1>Registry 管理</h1><p>统一查看 namespace，处理解绑、重新分配及原 repo 删除后的归属。</p></div>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  <AppNotice v-model="message" tone="success" placement="floating" />
  <AppNotice v-if="!loading && !enabled && !error">镜像仓库尚未启用。</AppNotice>
  <AppPanel title="Namespaces" :description="host ? `镜像地址：${host} · 归属变更立即影响相关 repo 成员的镜像权限。` : undefined">
    <form class="registry-filters" @submit.prevent="load()">
      <AppInput v-model.trim="query" aria-label="搜索 namespace 或 repo" placeholder="搜索 namespace 或 repo" maxlength="100" :disabled="busy" />
      <AppSelect v-model="state" label="绑定状态" hide-label :options="[{ value: 'all', label: '未删除 namespace' }, { value: 'bound', label: '已绑定' }, { value: 'unbound', label: '未绑定 / 原 repo 已删除' }, { value: 'deleted', label: '已删除 / 清理记录' }]" :disabled="busy" />
      <AppButton type="submit" variant="secondary" :busy="loading" :disabled="busy">查询</AppButton>
    </form>
    <ul class="admin-namespaces">
      <li v-for="ns in rows" :key="ns.id">
        <div class="namespace-heading">
          <div class="namespace-info"><strong>{{ ns.name }}</strong><AppBadge :tone="ns.bound ? 'green' : undefined">{{ ns.deleted_at ? (ns.purged_at ? '已删除 · 已清理' : '已删除 · 待 GC 清理') : ns.bound ? (ns.repository.archived ? '已绑定 · repo 已归档' : '已绑定') : (ns.repository.deleted ? '原 repo 已删除' : '未绑定') }}</AppBadge><p>{{ ns.bound ? '当前 repo' : '上次归属' }}：{{ ns.repository.full_name }}</p><small>{{ host }}/{{ ns.name }}/&lt;image&gt;:&lt;tag&gt;</small><p v-if="ns.purged_at" class="muted">最近清理：{{ new Date(ns.purged_at).toLocaleString() }}</p></div>
          <div v-if="!ns.deleted_at" class="actions"><AppButton variant="secondary" class="small" :disabled="busy || !enabled" @click="edit(ns)">{{ ns.bound ? '调整绑定' : '绑定 repo' }}</AppButton><AppButton v-if="ns.bound" variant="secondary" class="small" :disabled="busy || !enabled" @click="change(ns)">解绑</AppButton><AppButton variant="danger" class="small" :disabled="busy || !enabled" @click="remove(ns)">删除 namespace</AppButton></div>
        </div>
        <div v-if="editing?.id === ns.id" class="binding-editor">
          <h3>绑定 {{ ns.name }}</h3>
          <form @submit.prevent="loadRepositories()"><AppInput v-model.trim="repoQuery" label="查找目标 repo" placeholder="拥有者 / repo 名称" maxlength="100" :disabled="busy"><template #actions><AppButton type="submit" variant="secondary" :busy="repoLoading" :disabled="busy">搜索 repo</AppButton></template></AppInput></form>
          <AppNotice v-if="repoError" tone="error">{{ repoError }}</AppNotice>
          <p v-if="repoLoading" class="muted">正在读取 repo…</p>
          <form v-else-if="repositories.length" @submit.prevent="bind"><AppSelect v-model="destination" label="目标 repo" :options="[{ value: '', label: '请选择目标 repo' }, ...repositories.map(repo => ({ value: String(repo.id), label: repo.full_name }))]" :disabled="busy"><template #actions><AppButton type="submit" :busy="busy" :disabled="!destination">确认绑定</AppButton></template></AppSelect></form>
          <p v-else-if="!repoError" class="muted">没有可绑定的 repo。仅能绑定未归档、未删除的 repo。</p>
          <Pager v-if="repoTotal > 20" :page="repoPage" :total="repoTotal" :busy="busy || repoLoading" @change="loadRepositories" />
          <AppButton variant="ghost" :disabled="busy" @click="cancel">取消</AppButton>
        </div>
      </li>
    </ul>
    <p v-if="!rows.length" class="empty-state">{{ loading ? '正在读取 namespace…' : '没有符合条件的 namespace' }}</p>
    <Pager :page="page" :total="total" :busy="busy || loading" @change="load" />
  </AppPanel>
  <RegistryGCPanel ref="gcPanel" :enabled="enabled" @completed="load(page)" />
  <AppConfirm ref="confirmation" />
</template>

<style scoped>
.registry-filters { display: flex; align-items: stretch; gap: 12px; margin: 20px 0; }
.registry-filters :deep(.form-field) { margin: 0; }
.registry-filters > :first-child { flex: 1; }
.registry-filters > :nth-child(2) { min-width: 220px; }
.admin-namespaces { list-style: none; margin: 0; padding: 0; }
.admin-namespaces > li { padding: 20px 0; border-bottom: 1px solid var(--line); }
.namespace-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; }
.namespace-info { min-width: 0; overflow-wrap: anywhere; }
.namespace-info strong { margin-right: 12px; }
.namespace-info p { margin: 10px 0 6px; }
.namespace-heading .actions { flex-shrink: 0; }
.binding-editor { margin-top: 20px; padding: 20px; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; max-width: 760px; }
</style>
