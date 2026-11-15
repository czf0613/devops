<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { api, dateText, errorText } from '../api'
import { auth } from '../auth'
import { environmentLabel, type CIWorker } from '../ci'
import AppBadge from '../components/AppBadge.vue'
import AppButton from '../components/AppButton.vue'
import AppConfirm from '../components/AppConfirm.vue'
import AppInput from '../components/AppInput.vue'
import AppNotice from '../components/AppNotice.vue'
import AppPanel from '../components/AppPanel.vue'

const rows = ref<CIWorker[]>([]), name = ref(''), pool = ref('default'), registration = ref('')
const error = ref(''), busy = ref(false), loading = ref(false)
const confirmation = ref<InstanceType<typeof AppConfirm>>()
const wheel = `/res/devops-runner/devops_runner-${import.meta.env.VITE_RUNNER_VERSION}-py3-none-any.whl`
const serverOrigin = window.location.origin
const installCommand = `uv tool install --python 3.14 "${serverOrigin}${wheel}"`
let lifetime = 0, reads = 0, timer: ReturnType<typeof setTimeout> | undefined
function clearTimer() { if (timer) clearTimeout(timer) }
onUnmounted(() => { lifetime++; reads++; clearTimer(); registration.value = '' })
watch(() => auth.user?.id, () => {
  lifetime++; rows.value = []; registration.value = ''; error.value = ''; busy.value = false; clearTimer(); void load()
}, { immediate: true })
async function load() {
  clearTimer()
  const life = lifetime, current = ++reads
  loading.value = true
  try {
    const result = await api<{ items: CIWorker[] }>('/admin/ci/workers')
    if (life === lifetime && current === reads) { rows.value = result.items; error.value = '' }
  } catch (cause) { if (life === lifetime && current === reads) { error.value = errorText(cause); rows.value = [] } }
  finally { if (life === lifetime && current === reads) { loading.value = false; timer = setTimeout(load, 10000) } }
}
async function change(action: 'create' | 'drain' | 'resume' | 'revoke' | 'register', worker?: CIWorker) {
  if (busy.value) return
  const life = lifetime, workerName = worker?.name ?? name.value
  const labels = { create: '创建 worker', drain: '暂停接单', resume: '恢复接单', revoke: '撤销 worker', register: '重新注册' }
  const description = action === 'revoke' || action === 'register'
    ? `${workerName} 的原令牌将立即失效，进行中的任务会标记为失联。`
    : action === 'drain' ? `${workerName} 完成当前任务后不再接收新任务。` : `为 ${workerName} ${labels[action]}。`
  busy.value = true
  try {
    const accepted = await confirmation.value?.askVerified(description, labels[action], '验证并继续', async code => {
      if (life !== lifetime) throw new Error('账号已变化，请刷新重试')
      const result = await api<CIWorker>(worker ? `/admin/ci/workers/${worker.id}` : '/admin/ci/workers', 'POST', worker ? { action, code } : { name: name.value, pool: pool.value, code })
      if (life === lifetime) registration.value = result.registration_token ?? ''
    })
    if (accepted && life === lifetime) { name.value = ''; await load() }
  } finally { if (life === lifetime) busy.value = false }
}
</script>
<template>
  <div class="page-heading"><p class="eyebrow">CI WORKERS</p><h1>构建机</h1><p>构建机主动连接 DevOps，按平台和资源池领取任务。每台同时运行一个任务。</p></div>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  <AppPanel v-if="registration" title="一次性注册令牌" description="令牌仅在此显示，15 分钟内有效。请在专用构建机的注册命令提示中输入。">
    <code class="registration">{{ registration }}</code>
    <AppButton variant="secondary" @click="registration = ''">已保存，关闭</AppButton>
  </AppPanel>
  <AppPanel title="添加构建机" description="仓库默认使用 default 资源池；其他资源池由仓库拥有者在 Settings 中选择。">
    <form class="worker-form" @submit.prevent="change('create')">
      <AppInput v-model.trim="name" label="名称" placeholder="例如 mac-mini-build" maxlength="80" required :disabled="busy" />
      <AppInput v-model.trim="pool" label="资源池" pattern="[a-zA-Z0-9][a-zA-Z0-9_.\-]{0,63}" maxlength="64" required :disabled="busy"><template #actions><AppButton type="submit" :disabled="busy || !name || !pool">添加</AppButton></template></AppInput>
    </form>
    <details class="installation"><summary>安装与启动 worker</summary><p class="muted">先安装 uv 和 Git；使用 Git LFS 的仓库还需安装 Git LFS。下面的安装命令自动准备 Python 3.14。</p><pre>{{ installCommand }}</pre><p><a class="text-link" :href="wheel">下载安装包</a> · <a class="text-link" :href="wheel + '.sha256'">SHA-256 校验</a></p><pre>devops-runner register --server {{ serverOrigin }}
devops-runner doctor
devops-runner run</pre><p class="muted">注册时交互输入一次性令牌。配置和工作目录默认位于用户目录下的 .devops-runner。macOS arm64 已有实际测试，其他平台待真机验收。</p></details>
  </AppPanel>
  <AppPanel title="已配置构建机">
    <template #actions><AppButton variant="secondary" :disabled="loading" @click="load">刷新</AppButton></template>
    <div v-for="worker in rows" :key="worker.id" class="worker-row">
      <div><strong>{{ worker.name }}</strong> <AppBadge :tone="worker.enabled && worker.online ? 'green' : undefined">{{ !worker.enabled ? '已撤销' : !worker.registered ? '待注册' : worker.draining ? '暂停接单' : worker.online ? '在线' : '离线' }}</AppBadge>
        <p>{{ worker.pool }} · {{ environmentLabel(worker.capabilities) }} <span v-if="worker.capabilities.version">· v{{ worker.capabilities.version }}</span></p>
        <p v-if="worker.capabilities.os === 'linux'" class="muted">内核 {{ worker.capabilities.kernel_version || '待上报' }} · {{ worker.capabilities.libc?.name === 'glibc' ? 'GNU libc' : worker.capabilities.libc?.name || 'libc 待上报' }} {{ worker.capabilities.libc?.version || '版本未知' }}</p>
        <p v-if="worker.registered && (worker.capabilities.runtime ?? 1) < 2" class="muted">请升级 worker，以运行 GitHub 格式的工作流并上报系统版本。</p>
        <small class="muted">{{ worker.last_seen_at ? `最近心跳 ${dateText(worker.last_seen_at)}` : '等待首次连接' }}<span v-if="worker.capabilities.labels?.length"> · {{ worker.capabilities.labels.join(' / ') }}</span></small>
      </div>
      <div class="actions"><AppButton v-if="worker.enabled && worker.registered" variant="secondary" :disabled="busy" @click="change(worker.draining ? 'resume' : 'drain', worker)">{{ worker.draining ? '恢复接单' : '暂停接单' }}</AppButton><AppButton variant="secondary" :disabled="busy" @click="change('register', worker)">重新注册</AppButton><AppButton v-if="worker.enabled" variant="danger" :disabled="busy" @click="change('revoke', worker)">撤销</AppButton></div>
    </div>
    <p v-if="!rows.length" class="empty-state">{{ loading ? '读取构建机…' : '尚未配置构建机' }}</p>
  </AppPanel>
  <AppConfirm ref="confirmation" />
</template>
<style scoped>
.worker-form { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.worker-form :deep(.form-field) { margin: 0; }
.installation { margin-top: 24px; }
.installation pre { max-width: 100%; width: 100%; white-space: pre-wrap; overflow-wrap: anywhere; }
.worker-row { display: flex; align-items: center; justify-content: space-between; gap: 24px; padding: 22px 0; border-bottom: 1px solid var(--line); }
.worker-row > div:first-child { min-width: 0; overflow-wrap: anywhere; }
.worker-row p { margin: 10px 0; color: var(--muted); }
.registration { display: block; overflow-wrap: anywhere; padding: 16px; margin-bottom: 16px; background: var(--bg); }
</style>
