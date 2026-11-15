<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api, dateText, errorText, type Page } from '../api'
import { auth } from '../auth'
import { type CIConfig, type CIRun, type CIWorkflow, stateLabel, stateTone, eventLabel } from '../ci'
import { repositoryUrl, type Repository } from '../repositories'
import AppBadge from '../components/AppBadge.vue'
import AppButton from '../components/AppButton.vue'
import AppInput from '../components/AppInput.vue'
import AppNotice from '../components/AppNotice.vue'
import AppSelect from '../components/AppSelect.vue'
import Pager from '../components/Pager.vue'
const props = defineProps<{ repository: Repository }>()
const router = useRouter(), route = useRoute(), base = computed(() => `/repositories/${props.repository.id}/ci`)
const config = ref<CIConfig>(), rows = ref<CIRun[]>([]), workflows = ref<CIWorkflow[]>([]), total = ref(0), page = ref(1)
const branches = ref<{ name: string; commit: string }[]>([]), branch = ref('main'), state = ref(''), error = ref(''), catalogError = ref(''), busy = ref(false), showRun = ref(false)
const inputs = ref<Record<string, string | boolean | number>>({})
const selectedPath = computed(() => typeof route.query.workflow === 'string' ? route.query.workflow : '')
const selected = computed(() => workflows.value.find(item => item.path === selectedPath.value))
const stateOptions = [{ value: '', label: '全部状态' }, ...['queued', 'running', 'success', 'failure', 'cancelled'].map(value => ({ value, label: stateLabel(value) }))]
const branchOptions = computed(() => branches.value.map(item => ({ value: item.name, label: item.name })))
const workflowUrl = (path: string) => ({ path: repositoryUrl(props.repository) + '/ci', query: path ? { workflow: path } : {} })
let lifetime = 0, reads = 0, catalogs = 0, timer: ReturnType<typeof setTimeout> | undefined
function stop() { if (timer) clearTimeout(timer) }
onUnmounted(() => { lifetime++; reads++; catalogs++; stop() })
watch([() => props.repository.id, () => auth.user?.id], () => {
  lifetime++; config.value = undefined; rows.value = []; workflows.value = []; branches.value = []; page.value = 1; error.value = ''; busy.value = false; showRun.value = false; branch.value = props.repository.default_branch || 'main'; stop(); void load(true); void catalog()
}, { immediate: true })
watch([selectedPath, state], () => { page.value = 1; showRun.value = false; void load() })
watch(branch, () => { void catalog() })
watch(selected, () => { inputs.value = Object.fromEntries(Object.entries(selected.value?.inputs ?? {}).map(([key, field]) => [key, field.default ?? (field.type === 'boolean' ? false : '')])) })
async function catalog() {
  const life = lifetime, current = ++catalogs
  try {
    const data = await api<{ items: CIWorkflow[] }>(`${base.value}/workflows?ref=${encodeURIComponent(branch.value)}`)
    if (life === lifetime && current === catalogs) { workflows.value = data.items; catalogError.value = '' }
  } catch (cause) { if (life === lifetime && current === catalogs) { workflows.value = []; catalogError.value = errorText(cause) } }
}
async function load(withConfig = false) {
  stop()
  const life = lifetime, current = ++reads
  try {
    const params = new URLSearchParams({ page: String(page.value), workflow: selectedPath.value, state: state.value })
    const [runs, settings, refs] = await Promise.all([api<Page<CIRun>>(`${base.value}/runs?${params}`), withConfig ? api<CIConfig>(base.value) : undefined, withConfig ? api<{ items: { name: string; commit: string }[] }>(`/repositories/${props.repository.id}/branches`) : undefined])
    if (life !== lifetime || current !== reads) return
    rows.value = runs.items; total.value = runs.total; error.value = ''
    if (settings) config.value = settings
    if (refs) branches.value = refs.items
  } catch (cause) { if (life === lifetime && current === reads) { error.value = errorText(cause); rows.value = [] } }
  finally { if (life === lifetime && current === reads) timer = setTimeout(() => load(!config.value), 5000) }
}
async function trigger() {
  if (busy.value || !selected.value) return
  const life = lifetime
  busy.value = true; error.value = ''
  try {
    const values = Object.fromEntries(Object.entries(inputs.value).map(([key, value]) => [key, selected.value?.inputs[key]?.type === 'number' && value !== '' ? Number(value) : value]))
    const run = await api<CIRun>(base.value + '/runs', 'POST', { ref: branch.value, workflow: selectedPath.value, inputs: values })
    if (life === lifetime) await router.push(repositoryUrl(props.repository) + `/ci/${run.id}`)
  } catch (cause) { if (life === lifetime) error.value = errorText(cause) }
  finally { if (life === lifetime) busy.value = false }
}
function changePage(next: number) { page.value = next; void load() }
</script>
<template>
  <div class="ci-layout">
    <aside class="workflow-sidebar"><h2>工作流</h2><nav aria-label="CI 工作流"><RouterLink :to="workflowUrl('')" :class="{ active: !selectedPath }">全部工作流</RouterLink><RouterLink v-for="workflow in workflows" :key="workflow.path" :to="workflowUrl(workflow.path)" :class="{ active: selectedPath === workflow.path }"><span>{{ workflow.name }}</span><small>{{ workflow.path.split('/').at(-1) }}</small><small v-if="workflow.error" class="invalid">配置需要调整</small></RouterLink></nav>
      <div class="sidebar-footer"><span>{{ config?.enabled ? 'CI 已启用' : 'CI 已关闭' }}</span><p>资源池：{{ config?.pools.join('、') || 'default' }}</p><RouterLink v-if="config?.can_configure" class="text-link" :to="repositoryUrl(repository) + '/settings#ci'">CI 设置 →</RouterLink></div>
    </aside>
    <section class="workflow-content" aria-label="工作流运行记录">
      <header class="workflow-heading"><div><h2>{{ selected?.name || (selectedPath ? selectedPath.split('/').at(-1) : '全部工作流') }}</h2><p>{{ selectedPath || '.github/workflows 中的所有工作流' }}</p></div><div class="actions"><AppButton variant="secondary" @click="load(true); catalog()">刷新</AppButton><AppButton v-if="config?.enabled && config.can_run && selected?.manual && !repository.archived" :aria-expanded="showRun" aria-controls="manual-workflow" @click="showRun = !showRun">运行工作流</AppButton></div></header>
      <AppNotice v-if="error" tone="error">{{ error }}</AppNotice><AppNotice v-if="catalogError" tone="error">{{ catalogError }}</AppNotice><AppNotice v-if="selected?.error" tone="error">{{ selected.error }}</AppNotice>
      <AppNotice v-if="config && !config.enabled">此仓库已关闭 CI，历史运行记录仍可查看。仓库拥有者可以在 Settings 中开启。</AppNotice>
      <form v-if="showRun && selected?.manual" id="manual-workflow" class="run-form" @submit.prevent="trigger"><AppSelect v-model="branch" :options="branchOptions" label="使用此分支的工作流" :disabled="busy" searchable />
        <template v-for="(field, key) in selected.inputs" :key="key"><label v-if="field.type === 'boolean'" class="boolean-input"><input v-model="inputs[key]" type="checkbox" :disabled="busy" />{{ field.description || key }}</label><AppSelect v-else-if="field.type === 'choice'" :model-value="String(inputs[key] ?? '')" :label="field.description || key" :options="(field.options || []).map(value => ({ value, label: value }))" :disabled="busy" @change="inputs[key] = $event" /><AppInput v-else :model-value="String(inputs[key] ?? '')" :label="field.description || key" :type="field.type === 'number' ? 'number' : 'text'" :required="field.required" :disabled="busy" @update:model-value="inputs[key] = $event" /></template>
        <div class="actions"><AppButton type="submit" :busy="busy" :disabled="!branch">开始运行</AppButton><AppButton variant="ghost" :disabled="busy" @click="showRun = false">取消</AppButton></div>
      </form>
      <div class="runs-panel"><div class="runs-toolbar"><strong>{{ total }} 次运行</strong><AppSelect v-model="state" :options="stateOptions" label="运行状态" hide-label /></div>
        <ul class="run-list"><li v-for="run in rows" :key="run.id"><RouterLink :to="repositoryUrl(repository) + `/ci/${run.id}`"><span class="run-icon" :class="run.state" aria-hidden="true">{{ run.state === 'success' ? '✓' : run.state === 'failure' ? '×' : '○' }}</span><div class="run-title"><strong>{{ run.name }} <span class="muted">#{{ run.id }}</span></strong><p>{{ eventLabel(run.event) }} · <code>{{ run.sha.slice(0, 8) }}</code> · {{ dateText(run.created_at) }}</p></div><span class="run-branch">{{ run.ref }}</span><AppBadge :tone="stateTone(run.state)">{{ stateLabel(run.state) }}</AppBadge></RouterLink></li></ul>
        <div v-if="!rows.length && !error" class="empty-state"><strong>还没有运行记录</strong><p v-if="workflows.length">推送代码、创建 Pull request，或选择支持手动触发的工作流运行。</p><template v-else><p>将 GitHub Actions 格式的 YAML 文件放到 <code>.github/workflows/</code> 并推送，即可开始。</p><pre>name: CI
on: [push, pull_request, workflow_dispatch]
jobs:
  check:
    runs-on: [self-hosted]
    steps:
      - uses: actions/checkout@v4
      - run: echo "Hello CI"</pre></template></div>
        <div class="runs-footer"><Pager :page="page" :total="total" :busy="busy" @change="changePage" /></div>
      </div>
    </section>
  </div>
</template>
<style scoped>
.ci-layout { display: grid; grid-template-columns: 225px minmax(0, 1fr); gap: 30px; align-items: start; }
.workflow-sidebar h2, .workflow-heading h2 { font-size: 19px; margin: 0 0 16px; }
.workflow-sidebar nav { display: grid; gap: 5px; }.workflow-sidebar nav a { padding: 13px 15px; border-radius: 7px; font-size: 14px; border-left: 3px solid transparent; overflow-wrap: anywhere; }.workflow-sidebar nav a:hover { background: #ffffff05; }.workflow-sidebar nav a.active { background: #1a3542; border-left-color: var(--accent); color: #a7f2ee; }
.workflow-sidebar small { display: block; margin-top: 7px; color: var(--muted); font-size: 11px; }.workflow-sidebar small.invalid { color: #e8bd62; }
.sidebar-footer { border-top: 1px solid var(--line); margin-top: 25px; padding: 22px 15px; font-size: 12px; color: var(--muted); }.sidebar-footer p { line-height: 1.8; overflow-wrap: anywhere; }
.workflow-heading { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 25px; }.workflow-heading h2 { margin-bottom: 8px; }.workflow-heading p { color: var(--muted); font-size: 12px; margin: 0; overflow-wrap: anywhere; }.workflow-heading .actions { flex-shrink: 0; }
.runs-panel { border: 1px solid var(--line); border-radius: 10px; background: #101b29; overflow: hidden; }.runs-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 16px 20px; background: #142231; border-bottom: 1px solid var(--line); font-size: 13px; }.runs-toolbar :deep(.form-field) { margin: 0; width: 155px; }
.run-list { list-style: none; padding: 0; margin: 0; }.run-list a { display: flex; align-items: center; gap: 16px; padding: 22px 20px; border-bottom: 1px solid var(--line); }.run-list a:hover { background: #ffffff03; }.run-title { flex: 1; min-width: 0; }.run-title strong { overflow-wrap: anywhere; font-size: 14px; }.run-list p { color: var(--muted); margin: 8px 0 0; font-size: 12px; overflow-wrap: anywhere; }.run-icon { font-size: 23px; color: #e8bd62; }.run-icon.success { color: #6dd9a5; }.run-icon.failure { color: #ff929e; }.run-branch { color: #9dccff; background: #24446455; padding: 5px 9px; border-radius: 5px; font-size: 12px; max-width: 180px; overflow-wrap: anywhere; }
.run-form { padding: 20px; border: 1px solid var(--line); border-radius: 8px; background: #142231; margin-bottom: 20px; max-width: 620px; }.boolean-input { display: flex; align-items: center; gap: 10px; margin-bottom: 18px; }.runs-footer { padding: 0 20px 16px; }.empty-state { padding: 48px 24px; line-height: 1.8; }.empty-state p { font-size: 13px; }.empty-state pre { text-align: left; margin: 20px auto 0; width: fit-content; max-width: 100%; font-size: 12px; }
</style>
