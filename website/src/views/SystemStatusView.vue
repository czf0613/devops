<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, dateText, errorText } from '../api'
import { auth } from '../auth'
import AppButton from '../components/AppButton.vue'
import AppNotice from '../components/AppNotice.vue'
import AppPanel from '../components/AppPanel.vue'

interface HostInfo {
  hostname: string
  system: string
  release: string
  machine: string
  python: string
  uptime_seconds: number | null
}
interface CpuMetric {
  usage_percent: number | null
  logical_cores: number
  load_average: number[] | null
}
interface MemoryMetric {
  total_bytes: number
  used_bytes: number
  available_bytes: number
  usage_percent: number
  swap_total_bytes: number
  swap_used_bytes: number
}
interface DiskMetric {
  path: string
  label: string
  total_bytes: number
  used_bytes: number
  free_bytes: number
  usage_percent: number
}
interface StorageMetric {
  path: string
  label: string
  bytes: number | null
  partial: boolean
}
interface SystemStatus {
  collected_at: string
  host: HostInfo
  cpu: CpuMetric
  memory: MemoryMetric | null
  disks: DiskMetric[]
  storage: StorageMetric[]
}

const REFRESH_MS = 15000
const data = ref<SystemStatus>()
const loading = ref(false)
const error = ref('')
const rootDisk = computed(() => data.value?.disks.find(disk => disk.path === '/') ?? data.value?.disks[0])
let lifetime = 0, reads = 0, timer: ReturnType<typeof setTimeout> | undefined

function clearTimer() {
  if (timer) clearTimeout(timer)
  timer = undefined
}
function visible() {
  if (document.visibilityState === 'visible') void load()
}
onMounted(() => document.addEventListener('visibilitychange', visible))
onUnmounted(() => {
  lifetime++
  reads++
  clearTimer()
  document.removeEventListener('visibilitychange', visible)
})
watch(() => auth.user?.id, () => {
  lifetime++
  reads++
  clearTimer()
  data.value = undefined
  error.value = ''
  loading.value = false
  void load()
}, { immediate: true })

async function load() {
  clearTimer()
  const life = lifetime, current = ++reads
  loading.value = true
  try {
    const result = await api<SystemStatus>('/admin/system')
    if (life !== lifetime || current !== reads) return
    data.value = result
    error.value = ''
  } catch (cause) {
    if (life === lifetime && current === reads) error.value = errorText(cause)
  } finally {
    if (life === lifetime && current === reads) {
      loading.value = false
      if (document.visibilityState === 'visible') timer = setTimeout(load, REFRESH_MS)
    }
  }
}

function level(value: number | null | undefined) {
  if (value == null) return ''
  return value >= 90 ? 'critical' : value >= 75 ? 'warning' : ''
}
function percentText(value: number | null | undefined) {
  return value == null ? '不可用' : `${value.toFixed(1)}%`
}
function byteText(value: number | null | undefined) {
  if (value == null) return '不可用'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let size = value, unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit++
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`
}
function loadText(value: number[] | null) {
  return value ? value.map(item => item.toFixed(2)).join(' / ') : '不可用'
}
function uptimeText(seconds: number | null) {
  if (seconds == null) return '不可用'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days) return `${days} 天 ${hours} 小时`
  if (hours) return `${hours} 小时 ${minutes} 分钟`
  return `${minutes} 分钟`
}
</script>

<template>
  <div class="page-heading">
    <p class="eyebrow">SYSTEM</p>
    <h1>服务器运行状态</h1>
    <p>CPU、内存和磁盘使用情况每 15 秒自动刷新。CPU 使用率按整台服务器计算，最高为 100%。</p>
  </div>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  <div class="status-toolbar">
    <span v-if="data" class="muted">{{ loading ? '正在更新…' : `更新于 ${dateText(data.collected_at)}` }}</span>
    <span v-else-if="loading" class="muted">正在读取服务器状态…</span>
    <AppButton variant="secondary" :busy="loading" @click="load">刷新</AppButton>
  </div>
  <template v-if="data">
    <div class="metric-grid">
      <article class="metric-card">
        <div class="metric-heading"><h2>CPU 使用率</h2><span class="metric-note">{{ data.cpu.logical_cores }} 个逻辑核心</span></div>
        <p class="metric-value" :class="level(data.cpu.usage_percent)">{{ percentText(data.cpu.usage_percent) }}</p>
        <div class="meter" role="progressbar" aria-label="CPU 使用率" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="data.cpu.usage_percent ?? undefined"><span :class="level(data.cpu.usage_percent)" :style="{ width: `${data.cpu.usage_percent ?? 0}%` }"></span></div>
        <small>1 / 5 / 15 分钟负载：{{ loadText(data.cpu.load_average) }}</small>
      </article>
      <article class="metric-card">
        <div class="metric-heading"><h2>内存使用率</h2><span class="metric-note">物理内存</span></div>
        <p class="metric-value" :class="level(data.memory?.usage_percent)">{{ percentText(data.memory?.usage_percent) }}</p>
        <div class="meter" role="progressbar" aria-label="内存使用率" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="data.memory?.usage_percent ?? undefined"><span :class="level(data.memory?.usage_percent)" :style="{ width: `${data.memory?.usage_percent ?? 0}%` }"></span></div>
        <small v-if="data.memory">{{ byteText(data.memory.used_bytes) }} / {{ byteText(data.memory.total_bytes) }} · 可用 {{ byteText(data.memory.available_bytes) }}</small>
        <small v-else>当前环境无法读取内存信息。</small>
        <small v-if="data.memory?.swap_total_bytes">交换空间：{{ byteText(data.memory.swap_used_bytes) }} / {{ byteText(data.memory.swap_total_bytes) }}</small>
      </article>
      <article class="metric-card">
        <div class="metric-heading"><h2>磁盘占用</h2><span class="metric-note">{{ rootDisk?.label ?? '系统盘' }}</span></div>
        <p class="metric-value" :class="level(rootDisk?.usage_percent)">{{ percentText(rootDisk?.usage_percent) }}</p>
        <div class="meter" role="progressbar" aria-label="系统盘使用率" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="rootDisk?.usage_percent ?? undefined"><span :class="level(rootDisk?.usage_percent)" :style="{ width: `${rootDisk?.usage_percent ?? 0}%` }"></span></div>
        <small v-if="rootDisk">{{ byteText(rootDisk.used_bytes) }} / {{ byteText(rootDisk.total_bytes) }} · 可用 {{ byteText(rootDisk.free_bytes) }}</small>
        <small v-else>当前环境无法读取磁盘信息。</small>
      </article>
    </div>
    <AppPanel title="磁盘占用" description="按文件系统统计；Git 仓库数据单独扫描，目录过大时显示下限。Docker 镜像、容器日志和 Registry 数据也位于系统盘，已计入磁盘占用。">
      <p v-if="!data.disks.length" class="empty-state">当前环境无法读取磁盘信息。</p>
      <div v-for="disk in data.disks" :key="disk.path" class="disk-row">
        <div class="disk-heading"><div><strong>{{ disk.label }}</strong><small class="path">{{ disk.path }}</small></div><span>{{ percentText(disk.usage_percent) }}</span></div>
        <div class="meter" role="progressbar" :aria-label="`${disk.label}使用率`" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="disk.usage_percent"><span :class="level(disk.usage_percent)" :style="{ width: `${disk.usage_percent}%` }"></span></div>
        <div class="disk-meta"><span>已用 {{ byteText(disk.used_bytes) }}</span><span>总容量 {{ byteText(disk.total_bytes) }}</span><span>可用 {{ byteText(disk.free_bytes) }}</span></div>
      </div>
      <div v-if="data.storage.length" class="storage-list">
        <h3>数据目录</h3>
        <div v-for="item in data.storage" :key="item.path" class="storage-row">
          <div><strong>{{ item.label }}</strong><small class="path">{{ item.path }}</small></div>
          <span>{{ item.bytes == null ? '不可用' : `${item.partial ? '至少 ' : ''}${byteText(item.bytes)}` }}</span>
        </div>
      </div>
    </AppPanel>
    <AppPanel title="服务器信息">
      <dl class="info-grid">
        <div><dt>主机名</dt><dd>{{ data.host.hostname }}</dd></div>
        <div><dt>系统</dt><dd>{{ data.host.system }} {{ data.host.release }} · {{ data.host.machine }}</dd></div>
        <div><dt>已运行</dt><dd>{{ uptimeText(data.host.uptime_seconds) }}</dd></div>
        <div><dt>Python</dt><dd>{{ data.host.python }}</dd></div>
      </dl>
    </AppPanel>
  </template>
</template>

<style scoped>
.status-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin: 20px 0; }
.status-toolbar .muted { margin: 0; }
.metric-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; margin-bottom: 22px; }
.metric-card { padding: 22px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); }
.metric-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; }
.metric-heading h2 { margin: 0; }
.metric-note { color: var(--muted); font-size: 12px; text-align: right; }
.metric-value { margin: 18px 0 4px; color: var(--text); font-size: 34px; font-weight: 650; letter-spacing: -0.04em; line-height: 1.1; }
.metric-value.warning { color: #f5c26b; }
.metric-value.critical { color: var(--red); }
.metric-card small { display: block; margin-top: 10px; }
.meter { height: 7px; margin: 16px 0 8px; overflow: hidden; border-radius: 999px; background: var(--input); }
.meter > span { display: block; height: 100%; border-radius: inherit; background: var(--accent); transition: width 0.3s ease; }
.meter > span.warning { background: #f5c26b; }
.meter > span.critical { background: var(--red); }
.disk-row { padding: 20px 0; border-bottom: 1px solid var(--line); }
.disk-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 20px; }
.disk-heading > span { color: var(--text); font-weight: 600; }
.path { display: block; margin-top: 6px; overflow-wrap: anywhere; }
.disk-meta { display: flex; flex-wrap: wrap; gap: 8px 24px; color: var(--muted); font-size: 12px; }
.storage-list { padding-top: 20px; }
.storage-list h3 { margin: 0 0 6px; font-size: 14px; }
.storage-row { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 14px 0 0; overflow-wrap: anywhere; }
.storage-row > span { flex-shrink: 0; }
.info-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px 32px; margin: 0; }
.info-grid dt { color: var(--muted); font-size: 12px; }
.info-grid dd { margin: 4px 0 0; overflow-wrap: anywhere; }
@media (max-width: 900px) { .metric-grid, .info-grid { grid-template-columns: 1fr; } }
</style>
