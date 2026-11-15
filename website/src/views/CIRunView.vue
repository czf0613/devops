<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api, dateText, errorText } from '../api'
import { auth } from '../auth'
import { type CIRun, isActive, stateLabel, stateTone } from '../ci'
import { repositoryUrl, type Repository } from '../repositories'
import AppBadge from '../components/AppBadge.vue'
import AppButton from '../components/AppButton.vue'
import AppConfirm from '../components/AppConfirm.vue'
import AppNotice from '../components/AppNotice.vue'
import AppPanel from '../components/AppPanel.vue'

const props = defineProps<{ repository: Repository }>()
const route = useRoute(), router = useRouter(), run = ref<CIRun>(), selected = ref<number>()
const text = ref(''), offset = ref(0), error = ref(''), logError = ref(''), busy = ref(false)
const confirmation = ref<InstanceType<typeof AppConfirm>>()
const base = computed(() => `/repositories/${props.repository.id}/ci`)
const canRun = computed(() => props.repository.permissions.access === 'write' && !props.repository.archived)
let lifetime = 0, reads = 0, logReads = 0, timer: ReturnType<typeof setTimeout> | undefined
function stop() { if (timer) clearTimeout(timer) }
onUnmounted(() => { lifetime++; reads++; logReads++; stop() })
watch([() => props.repository.id, () => route.params.runId, () => auth.user?.id], () => {
  lifetime++; run.value = undefined; selected.value = undefined; text.value = ''; offset.value = 0; error.value = ''; logError.value = ''; busy.value = false; stop(); void load()
}, { immediate: true })
watch(selected, () => { logReads++; text.value = ''; offset.value = 0; logError.value = ''; void logs() })
async function logs() {
  if (!selected.value) return
  const life = lifetime, current = ++logReads, id = selected.value
  try {
    const data = await api<{ chunks: { text: string }[]; next: number }>(`${base.value}/attempts/${id}/logs?after=${offset.value}`)
    if (life === lifetime && current === logReads && id === selected.value) { text.value += data.chunks.map(c => c.text).join(''); offset.value = data.next; logError.value = '' }
  } catch (cause) { if (life === lifetime && current === logReads) logError.value = errorText(cause) }
}
async function load() {
  stop()
  const life = lifetime, current = ++reads
  try {
    const data = await api<CIRun>(`${base.value}/runs/${route.params.runId}`)
    if (life !== lifetime || current !== reads) return
    run.value = data; error.value = ''
    if (!selected.value) selected.value = data.jobs.flatMap(j => j.attempts)[0]?.id
    else await logs()
  } catch (cause) { if (life === lifetime && current === reads) { error.value = errorText(cause); run.value = undefined; text.value = '' } }
  finally { if (life === lifetime && current === reads) timer = setTimeout(load, 3000) }
}
async function action(kind: 'cancel' | 'rerun') {
  if (!run.value || busy.value) return
  const life = lifetime, id = run.value.id
  if (kind === 'cancel' && !await confirmation.value?.ask('停止排队中的任务，并通知构建机终止当前执行。', '取消本次运行？')) return
  if (life !== lifetime) return
  busy.value = true
  try {
    const result = await api<CIRun>(`${base.value}/runs/${id}/${kind}`, 'POST')
    if (life !== lifetime) return
    if (kind === 'rerun') await router.push(repositoryUrl(props.repository) + `/ci/${result.id}`)
    else await load()
  } catch (cause) { if (life === lifetime) error.value = errorText(cause) }
  finally { if (life === lifetime) busy.value = false }
}
</script>
<template>
  <RouterLink class="text-link" :to="repositoryUrl(repository) + '/ci'">← 全部 CI 运行</RouterLink>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  <template v-if="run">
    <AppPanel :title="`${run.name} #${run.id}`" :description="`${run.ref} · ${run.sha} · ${dateText(run.created_at)}`">
      <template #actions><AppBadge :tone="stateTone(run.state)">{{ stateLabel(run.state) }}</AppBadge><AppButton v-if="canRun" :variant="isActive(run.state) ? 'danger' : 'secondary'" :busy="busy" @click="action(isActive(run.state) ? 'cancel' : 'rerun')">{{ isActive(run.state) ? '取消运行' : '重跑此提交' }}</AppButton></template>
      <p v-if="run.workflow_path" class="muted">{{ run.workflow_path }}</p>
      <AppNotice v-if="run.error" tone="error">{{ run.error }}</AppNotice>
      <p v-if="run.rerun_of" class="muted">重跑自 #{{ run.rerun_of }}，使用原提交和原流水线配置。</p>
      <div v-for="job in run.jobs" :key="job.id" class="job">
        <div class="job-title"><strong>{{ job.display_name || job.name }}</strong><AppBadge :tone="stateTone(job.state)">{{ stateLabel(job.state) }}</AppBadge><span class="muted">{{ job.environment.os || '任意系统' }} {{ job.environment.arch ?? '' }} · {{ job.environment.pool }}<span v-if="job.needs.length"> · 依赖 {{ job.needs.join(', ') }}</span></span></div>
        <p v-if="job.environment.constraints?.length" class="muted">环境要求：{{ job.environment.constraints.map(c => `${c.field} ${c.operator} ${c.value}`).join(' · ') }}</p>
        <p v-if="job.state === 'queued'" class="muted">等待符合系统版本、架构、资源池和依赖要求的构建机。</p>
        <div v-for="attempt in job.attempts" :key="attempt.id" class="attempt"><div class="actions"><AppButton :variant="selected === attempt.id ? 'primary' : 'secondary'" @click="selected = attempt.id">{{ attempt.worker }} · 第 {{ attempt.number }} 次执行</AppButton><span v-if="attempt.cancel_requested && isActive(attempt.state)" class="muted">正在请求停止…</span></div>
          <p v-if="attempt.error" class="attempt-error">{{ attempt.error }}</p>
          <ol v-if="attempt.steps.length" class="steps"><li v-for="(step, index) in attempt.steps" :key="index"><span>{{ step.name }}</span><AppBadge :tone="stateTone(step.state)">{{ stateLabel(step.state) }}</AppBadge><small v-if="step.exit_code !== null" class="muted">退出码 {{ step.exit_code }}</small></li></ol>
          <ul v-if="attempt.artifacts.length" class="artifacts"><li v-for="artifact in attempt.artifacts" :key="artifact.id"><a class="text-link" :href="`/api${base}/artifacts/${artifact.id}`">{{ artifact.name }}</a> <span class="muted">{{ (artifact.size / 1024).toFixed(1) }} KiB · 保留至 {{ dateText(artifact.expires_at) }}</span></li></ul>
        </div>
      </div>
    </AppPanel>
    <AppPanel v-if="selected" title="执行日志" description="执行期间自动更新；日志与产物保留 30 天。"><AppNotice v-if="logError" tone="error">{{ logError }}</AppNotice><pre class="ci-log" tabindex="0" aria-label="执行日志">{{ text || '等待日志…' }}</pre></AppPanel>
  </template>
  <AppConfirm ref="confirmation" />
</template>
<style scoped>
.job { padding: 20px 0; border-top: 1px solid var(--line); }
.job-title, .steps li { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.attempt { margin-top: 18px; }
.attempt-error { color: var(--danger); }
.steps { list-style: none; margin: 12px 0; padding: 0; }
.steps li { padding: 8px 0; }
.artifacts { padding-left: 20px; overflow-wrap: anywhere; }
.artifacts li { margin: 8px 0; }
.ci-log { width: 100%; max-width: 100%; white-space: pre-wrap; overflow-wrap: anywhere; overflow: auto; max-height: 640px; background: #090d14; padding: 20px; border-radius: 8px; line-height: 1.65; font-size: 13px; }
</style>
