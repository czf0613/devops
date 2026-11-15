<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { api } from '../api'

interface Activity {
  weeks: { start: string; commits: number }[]
  total: number
  truncated: boolean
  commit_limit: number
}
const props = defineProps<{ repositoryId: number }>()
const activity = ref<Activity>()
const failed = ref(false)
let generation = 0
watch(() => props.repositoryId, async id => {
  const current = ++generation
  activity.value = undefined
  failed.value = false
  try {
    const result = await api<Activity>(`/repositories/${id}/activity`)
    if (current === generation) activity.value = result
  } catch {
    if (current === generation) failed.value = true
  }
}, { immediate: true })
onUnmounted(() => { generation++ })

const points = computed(() => {
  const weeks = activity.value?.weeks ?? []
  const peak = Math.max(1, ...weeks.map(week => week.commits))
  return weeks.map((week, index) => ({
    ...week,
    x: 4 + index * 212 / Math.max(1, weeks.length - 1),
    y: 44 - week.commits / peak * 36,
  }))
})
const line = computed(() => points.value.map(point => `${point.x},${point.y}`).join(' '))
const label = computed(() => activity.value
  ? `近 12 周${activity.value.truncated ? '至少' : ''} ${activity.value.total} 次提交`
  : failed.value ? '提交趋势暂不可用' : '正在读取提交趋势…')
const detail = computed(() => `所有分支，按提交时间统计，UTC 周一至周日；共享提交只计一次。${
  activity.value?.truncated ? `仅统计读取到的前 ${activity.value.commit_limit.toLocaleString()} 条提交。` : ''
}`)
</script>

<template>
  <figure class="repo-activity" :aria-label="label" :title="detail">
    <svg v-if="activity" viewBox="0 0 220 52" role="img" :aria-label="label" class="activity-chart">
      <title>{{ label }}。{{ detail }}</title>
      <path v-if="activity.total" :d="`M4,44 L${line.replaceAll(' ', ' L')} L216,44 Z`" class="activity-area" />
      <line x1="4" y1="44" x2="216" y2="44" class="activity-baseline" />
      <polyline :points="line" class="activity-line" />
      <circle v-for="point in points" :key="point.start" :cx="point.x" :cy="point.y" r="4" class="activity-point">
        <title>{{ point.start }} 当周：{{ point.commits }} 次提交</title>
      </circle>
    </svg>
    <div v-else class="activity-placeholder" aria-hidden="true"></div>
    <figcaption>{{ label }}</figcaption>
  </figure>
</template>

<style scoped>
.repo-activity { width: 220px; max-width: 100%; margin: 0; flex-shrink: 0; }
.activity-chart, .activity-placeholder { display: block; width: 100%; height: 52px; }
.activity-line { stroke: var(--accent); stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; fill: none; }
.activity-area { fill: var(--accent); opacity: .08; }
.activity-baseline { stroke: var(--line); stroke-width: 1; }
.activity-point { fill: transparent; }
.activity-point:hover { fill: var(--accent); }
figcaption { margin-top: 6px; color: var(--muted); font-size: 12px; text-align: right; }
</style>
