<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { api, errorText } from '../api'
import { auth } from '../auth'
import type { Repository } from '../repositories'
import type { Namespace, RegistryOverview } from '../registry'
import AppBadge from '../components/AppBadge.vue'
import AppButton from '../components/AppButton.vue'
import AppConfirm from '../components/AppConfirm.vue'
import AppNotice from '../components/AppNotice.vue'
import AppPanel from '../components/AppPanel.vue'
import AppSelect from '../components/AppSelect.vue'
import RegistryImages from '../components/RegistryImages.vue'
import RegistryNamespaceCreate from '../components/RegistryNamespaceCreate.vue'

const props = defineProps<{ repository: Repository }>()
const base = computed(() => `/repositories/${props.repository.id}/registry`)
const canManage = computed(() => props.repository.permissions.is_owner && !props.repository.archived)
const overview = ref<RegistryOverview>(), candidates = ref<Namespace[]>([])
const candidateId = ref(''), selectedId = ref('')
const loading = ref(false), busy = ref(false)
const error = ref(''), message = ref('')
const confirmation = ref<InstanceType<typeof AppConfirm>>()
const selected = computed(() => overview.value?.items.find(item => String(item.id) === selectedId.value))
const imageOptions = computed(() => overview.value?.items.filter(item => item.enabled).map(item => ({ value: String(item.id), label: item.name })) ?? [])
let lifetime = 0, reads = 0
onUnmounted(() => { lifetime++; reads++ })
watch([() => props.repository.id, () => auth.user?.id], () => {
  lifetime++; overview.value = undefined; candidates.value = []; selectedId.value = ''; candidateId.value = ''; error.value = ''; message.value = ''; busy.value = false
  void load()
}, { immediate: true })
async function load() {
  const current = ++reads, life = lifetime
  loading.value = true; error.value = ''
  try {
    const data = await api<RegistryOverview>(base.value)
    if (current !== reads || life !== lifetime) return
    overview.value = data
    if (!data.items.some(item => String(item.id) === selectedId.value && item.enabled)) selectedId.value = String(data.items.find(item => item.enabled)?.id ?? '')
    if (canManage.value && data.enabled) {
      const list = await api<Namespace[]>(base.value + '/candidates')
      if (current === reads && life === lifetime) candidates.value = list
    }
  } catch (cause) {
    if (current === reads && life === lifetime) { error.value = errorText(cause); overview.value = undefined; selectedId.value = ''; candidates.value = [] }
  } finally { if (current === reads && life === lifetime) loading.value = false }
}
async function mutate(action: () => Promise<unknown>, success: string) {
  if (busy.value) return
  const life = lifetime
  busy.value = true; error.value = ''; message.value = ''
  try {
    await action()
    if (life !== lifetime) return
    message.value = success; candidateId.value = ''
    await load()
  } catch (cause) { if (life === lifetime) error.value = errorText(cause) }
  finally { if (life === lifetime) busy.value = false }
}
async function toggle(ns: Namespace) {
  const life = lifetime
  if (ns.enabled && !await confirmation.value?.ask(`解绑后，所有客户端立即失去 ${ns.name} 下镜像的访问权限。镜像数据和名称仍保留，可重新绑定。`, `解绑 ${ns.name}？`)) return
  if (life !== lifetime) return
  await mutate(() => api(`${base.value}/namespaces/${ns.id}`, 'PUT', { enabled: !ns.enabled, expected_version: ns.version }), ns.enabled ? '已解绑 namespace' : '已重新绑定 namespace')
}
async function bind() {
  const ns = candidates.value.find(item => String(item.id) === candidateId.value), life = lifetime
  if (!ns || !await confirmation.value?.ask(`将 ${ns.name} 及其中所有镜像从 ${ns.repository_name} 转移到当前 repo。旧 repo 的成员随即失去通过旧 repo 获得的访问权限。`, '转移并绑定 namespace？') || life !== lifetime) return
  await mutate(() => api(base.value + '/bind', 'POST', { namespace_id: ns.id, expected_repository_id: ns.repository_id, expected_version: ns.version }), 'namespace 已转移到当前 repo')
}
</script>

<template>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  <AppNotice v-model="message" tone="success" placement="floating" />
  <AppPanel title="Registry namespaces" description="成员沿用此 repo 的读写权限。一个 repo 可绑定多个 namespace，每个 namespace 归属一个 repo。">
    <template #actions><AppButton variant="secondary" class="small" :disabled="busy || loading" @click="load">刷新</AppButton></template>
    <p v-if="loading && !overview" class="muted">正在读取…</p>
    <AppNotice v-else-if="overview && !overview.enabled">镜像仓库尚未启用。</AppNotice>
    <template v-if="overview?.enabled">
      <ul v-if="overview.items.length" class="namespace-list">
        <li v-for="ns in overview.items" :key="ns.id"><div><strong>{{ ns.name }}</strong><AppBadge :tone="ns.enabled ? 'green' : undefined">{{ ns.enabled ? '已绑定' : '已解绑 · 名称保留' }}</AppBadge><small>{{ overview.host }}/{{ ns.name }}/&lt;image&gt;:&lt;tag&gt;</small></div><AppButton v-if="canManage" :variant="ns.enabled ? 'danger' : 'secondary'" class="small" :disabled="busy" @click="toggle(ns)">{{ ns.enabled ? '解绑' : '重新绑定' }}</AppButton></li>
      </ul>
      <div v-else class="empty-state">暂无 namespace。{{ canManage ? '创建后即可使用 Docker 推送镜像。' : '请联系 repo 拥有者进行绑定。' }}</div>
      <template v-if="canManage">
        <RegistryNamespaceCreate :repository-id="repository.id" :disabled="busy" @created="load" />
        <form v-if="candidates.length" class="namespace-form" @submit.prevent="bind">
          <AppSelect v-model="candidateId" label="从其他 repo 转移 namespace" :options="[{ value: '', label: '选择你拥有的 namespace' }, ...candidates.map(item => ({ value: String(item.id), label: `${item.name} · ${item.repository_name}` }))]" :disabled="busy">
            <template #actions><AppButton type="submit" variant="secondary" :disabled="busy || !candidateId">转移并绑定</AppButton></template>
          </AppSelect>
        </form>
      </template>
    </template>
  </AppPanel>
  <div v-if="overview?.enabled && imageOptions.length" class="image-toolbar"><AppSelect v-model="selectedId" label="Namespace" :options="imageOptions" :disabled="busy" /></div>
  <RegistryImages v-if="overview?.enabled && selected?.enabled" :repository="repository" :namespace="selected" :host="overview.host" />
  <p v-if="overview?.enabled" class="muted registry-login-help">Docker 登录令牌统一在 <RouterLink to="/security" class="text-link">账号安全</RouterLink> 管理，一次登录即可访问你有权限的所有项目。</p>
  <AppConfirm ref="confirmation" />
</template>

<style scoped>
.namespace-list { list-style: none; margin: 0; padding: 0; }
.namespace-list li { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 18px 0; border-bottom: 1px solid var(--line); }
.namespace-list strong { margin-right: 12px; }
.namespace-list small { display: block; margin-top: 8px; overflow-wrap: anywhere; color: var(--muted); }
.namespace-form { max-width: 680px; margin-top: 24px; }
.namespace-form :deep(.form-field) { margin: 0; }
.image-toolbar { max-width: 560px; margin-bottom: 20px; }
.image-toolbar :deep(.form-field) { margin: 0; }
.registry-login-help { margin: 24px 0; }
</style>
