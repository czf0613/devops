<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { api, errorText } from '../api'
import { auth } from '../auth'
import AppBadge from './AppBadge.vue'
import AppButton from './AppButton.vue'
import AppConfirm from './AppConfirm.vue'
import AppNotice from './AppNotice.vue'
import AppPanel from './AppPanel.vue'
import Pager from './Pager.vue'

interface GCRun {
  id: number
  state: 'queued' | 'running' | 'succeeded' | 'failed'
  phase: string
  created_at: string
  finished_at: string | null
  before_bytes: number | null
  after_bytes: number | null
  summary: { eligible_blobs?: number; eligible_manifests?: number; deleted_namespaces?: number }
  error: string | null
}
interface History { worker_available: boolean; active: GCRun | null; total: number; items: GCRun[]; pending_namespaces?: number }
defineProps<{ enabled: boolean }>()
const emit = defineEmits<{ completed: [] }>()
const history = ref<History>(), page = ref(1), error = ref(''), busy = ref(false), loading = ref(false)
const confirmation = ref<InstanceType<typeof AppConfirm>>()
let lifetime = 0, reads = 0, timer: ReturnType<typeof setTimeout> | undefined
function clearTimer() { clearTimeout(timer); timer = undefined }
onUnmounted(() => { lifetime++; reads++; clearTimer() })
watch(() => auth.user?.id, () => { lifetime++; history.value = undefined; busy.value = false; void load(1) }, { immediate: true })

async function load(next = page.value) {
  clearTimer()
  const current = ++reads, life = lifetime
  loading.value = true; error.value = ''
  try {
    const data = await api<History>(`/admin/registry/gc?page=${next}`)
    if (current !== reads || life !== lifetime) return
    const completed = history.value?.active && !data.active
    history.value = data; page.value = next
    if (completed) emit('completed')
    if (data.active) timer = setTimeout(() => void load(), 3000)
  } catch (cause) { if (current === reads && life === lifetime) error.value = errorText(cause) }
  finally { if (current === reads && life === lifetime) loading.value = false }
}
async function start() {
  if (busy.value || history.value?.active || !history.value?.worker_available) return
  const life = lifetime
  busy.value = true; error.value = ''; clearTimer(); reads++
  const accepted = await confirmation.value?.askVerified(
    '请先暂停所有 CI 构建推送、docker push 和镜像删除，并确认在途写请求已结束。保持停止写入，直到任务完成。系统不会自动进入只读状态；并发写入可能损坏镜像。\n\n本次回收覆盖整个 Registry：先清理已删除 namespace 的全部镜像标签，再预检并回收无引用数据。其余 namespace 保留有标签镜像及其引用链。只通过 digest 使用的无标签历史版本也可能被删除，请为需保留的版本保留标签。namespace 解绑本身不会触发删除。\n\n清理不可撤销。',
    '已进入人工维护窗口？', '验证并确认已停止写入', async code => {
      if (life !== lifetime) throw new Error('当前账号已变化，请刷新后重试')
      await api('/admin/registry/gc', 'POST', { maintenance_confirmed: true, code })
    },
  )
  if (life !== lifetime) return
  busy.value = false
  if (accepted) {
    await load(1)
    if (!history.value?.active) emit('completed')
  }
}
function state(run: GCRun) {
  if (run.state === 'running') return run.phase === 'namespaces' ? '正在清理已删除 namespace' : run.phase === 'preview' ? '正在预检' : '正在回收'
  return { queued: '等待启动', succeeded: '已完成', failed: '失败 / 中断' }[run.state]
}
function bytes(value: number) { return `${(value / 1024 / 1024).toFixed(2)} MiB` }
defineExpose({ refresh: () => load(1) })
</script>

<template>
  <AppPanel title="空间回收" description="删除镜像后，执行 GC 才会释放不再被引用的数据。共享层仍被使用时会保留。">
    <p>仅在人工维护窗口执行：暂停所有推送和镜像删除，等待在途写请求结束，并保持到回收完成。系统不会自动阻止写入。</p>
    <p v-if="history?.pending_namespaces" class="muted">{{ history.pending_namespaces }} 个已删除 namespace 的全部镜像等待本次 GC 清理。</p>
    <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
    <AppNotice v-if="history?.active">空间回收任务{{ state(history.active) }}，请继续保持停止写入。离开页面不会终止任务。</AppNotice>
    <p v-else-if="history && !history.worker_available" class="muted">空间回收进程未就绪，请联系维护人员检查后刷新。</p>
    <div class="actions">
      <AppButton variant="danger" :busy="busy" :disabled="!enabled || loading || !!history?.active || !history?.worker_available" @click="start">回收空间</AppButton>
      <AppButton variant="secondary" :busy="loading" :disabled="busy" @click="load()">刷新回收记录</AppButton>
    </div>
    <ul class="gc-history">
      <li v-for="run in history?.items" :key="run.id">
        <div><AppBadge :tone="run.state === 'succeeded' ? 'green' : undefined">{{ state(run) }}</AppBadge><span>{{ new Date(run.created_at).toLocaleString() }}</span></div>
        <p v-if="run.state === 'succeeded' && run.before_bytes !== null && run.after_bytes !== null">存储占用减少 {{ bytes(Math.max(0, run.before_bytes - run.after_bytes)) }} · {{ bytes(run.before_bytes) }} → {{ bytes(run.after_bytes) }}</p>
        <p v-if="run.summary.eligible_blobs !== undefined" class="muted">{{ run.state === 'succeeded' ? '已处理' : '预检候选' }}：{{ run.summary.eligible_blobs }} 个数据块、{{ run.summary.eligible_manifests }} 个无标签清单</p>
        <p v-if="run.state === 'succeeded' && run.summary.deleted_namespaces" class="muted">已清理 {{ run.summary.deleted_namespaces }} 个已删除 namespace 的镜像。</p>
        <p v-if="run.error" class="gc-error">{{ run.error }}</p>
      </li>
    </ul>
    <p v-if="history && !history.items.length" class="muted">尚无回收记录。</p>
    <Pager v-if="history" :page="page" :total="history.total" :page-size="10" :busy="loading || busy" @change="load" />
    <AppConfirm ref="confirmation" />
  </AppPanel>
</template>

<style scoped>
.gc-history { list-style: none; padding: 0; margin: 20px 0 0; }
.gc-history li { border-top: 1px solid var(--line); padding: 16px 0; overflow-wrap: anywhere; }
.gc-history li > div { display: flex; align-items: center; gap: 12px; }
.gc-history p { margin: 8px 0 0; }
.gc-error { color: var(--red); }
</style>
