<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '../api'
import { auth } from '../auth'
import { stateLabel, stateTone, type CIRun } from '../ci'
import { repositoryUrl, type Repository } from '../repositories'
import AppBadge from './AppBadge.vue'
const props = defineProps<{ repository: Repository; sha: string }>()
const data = ref<{ state: string; items: CIRun[] }>(), failed = ref(false)
const label = computed(() => failed.value ? '无法读取 CI 检查' : !data.value ? '读取 CI 检查…' : ({ none: '尚无 CI 检查', success: 'CI 检查通过', failure: 'CI 检查未通过', running: 'CI 检查进行中' }[data.value.state] ?? 'CI 检查'))
let generation = 0, timer: ReturnType<typeof setTimeout> | undefined
function stop() { if (timer) clearTimeout(timer) }
async function load(ticket: number) {
  try {
    const result = await api<{ state: string; items: CIRun[] }>(`/repositories/${props.repository.id}/ci/checks/${props.sha}`)
    if (ticket === generation) { data.value = result; failed.value = false }
  } catch { if (ticket === generation) { data.value = undefined; failed.value = true } }
  finally { if (ticket === generation) timer = setTimeout(() => load(ticket), 10000) }
}
watch([() => props.repository.id, () => props.sha, () => auth.user?.id], () => {
  stop(); data.value = undefined; failed.value = false
  const ticket = ++generation
  if (props.sha) void load(ticket)
}, { immediate: true })
onUnmounted(() => { generation++; stop() })
</script>
<template>
  <details class="ci-status" :class="data?.state" :key="`${repository.id}:${sha}`"><summary :title="`提交 ${sha}`"><span aria-hidden="true">{{ data?.state === 'success' ? '✓' : data?.state === 'failure' ? '×' : '○' }}</span> {{ label }}</summary>
    <div class="check-popover"><strong>{{ sha.slice(0, 8) }} 的检查</strong><p v-if="!data?.items.length">{{ label }}</p><RouterLink v-for="run in data?.items" :key="run.id" :to="repositoryUrl(repository) + `/ci/${run.id}`"><span>{{ run.name }}</span><AppBadge :tone="stateTone(run.state)">{{ stateLabel(run.state) }}</AppBadge></RouterLink><RouterLink class="all-runs" :to="repositoryUrl(repository) + '/ci'">查看所有工作流 →</RouterLink></div>
  </details>
</template>
<style scoped>
.ci-status { position: relative; font-size: 13px; color: var(--muted); }
summary { cursor: pointer; list-style: none; white-space: nowrap; } summary::-webkit-details-marker { display: none; }
.success > summary { color: #6dd9a5; } .failure > summary { color: #ff929e; } .running > summary { color: #e8bd62; }
.check-popover { position: absolute; z-index: 12; top: calc(100% + 10px); right: 0; width: 350px; padding: 18px; border-radius: 10px; border: 1px solid var(--line); background: #111e2c; box-shadow: 0 12px 36px #0006; color: var(--text); }
.check-popover a { display: flex; justify-content: space-between; gap: 14px; align-items: center; margin-top: 15px; } .check-popover a span { overflow-wrap: anywhere; }
.all-runs { color: var(--accent); padding-top: 14px; border-top: 1px solid var(--line); }
</style>
