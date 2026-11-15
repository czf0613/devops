<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { dateText } from '../api'
import { activityLabels, relativeTime, type DashboardEvent } from '../dashboard'
import { useDashboardResource } from '../useDashboardResource'
import AppButton from './AppButton.vue'
import AppIcon from './AppIcon.vue'

const { data, busy, error, load } = useDashboardResource<{ items: DashboardEvent[] }>(
  () => '/me/dashboard/activity?page_size=3', () => ({ items: [] }),
)
</script>
<template>
  <section class="activity-section" aria-label="最近动态" :aria-busy="busy">
    <h2><AppIcon name="activity" :size="17" />最近动态</h2>
    <p class="activity-caption">来自你参与的仓库</p>
    <div v-if="error" class="activity-message" role="alert"><p>{{ error }}</p><AppButton variant="secondary" @click="load">重试</AppButton></div>
    <p v-else-if="busy" class="activity-message" role="status">正在读取动态…</p>
    <ol v-else-if="data.items.length" class="activity-timeline">
      <li v-for="entry in data.items" :key="entry.id">
        <span class="timeline-dot" aria-hidden="true"></span>
        <p class="actor-action"><strong>{{ entry.actor?.display_name || '系统' }}</strong> {{ activityLabels[entry.action] || '更新了' }} {{ entry.item.kind === 'pull' ? 'PR' : 'Issue' }}</p>
        <RouterLink class="activity-title" :to="entry.item.url">{{ entry.item.title }}</RouterLink>
        <span class="activity-repository">{{ entry.item.repository.full_name }} #{{ entry.item.number }}</span>
        <time :datetime="entry.created_at" :title="dateText(entry.created_at)">{{ relativeTime(entry.created_at) }}</time>
      </li>
    </ol>
    <p v-else class="activity-message">还没有协作动态。仓库中的 Issue、PR 和讨论更新会显示在这里。</p>
  </section>
</template>
<style scoped>
.activity-section { padding: 20px 21px; border: 1px solid var(--line); border-radius: 10px; background: #101a2980; }
h2 { display: flex; align-items: center; gap: 8px; font-size: 13px; margin: 0; }
h2 .app-icon { color: var(--muted); }
.activity-caption { font-size: 11px; margin: 7px 0 22px; color: var(--muted); }
.activity-timeline { padding: 0; margin: 0 0 0 4px; list-style: none; }
li { position: relative; border-left: 1px solid var(--line); padding: 0 0 23px 17px; min-width: 0; }
li:last-child { padding-bottom: 0; border-left-color: transparent; }
.timeline-dot { position: absolute; width: 7px; height: 7px; border: 2px solid var(--surface); box-sizing: content-box; border-radius: 50%; background: var(--accent); left: -6px; top: 3px; }
.actor-action { font-size: 11px; line-height: 1.7; color: var(--muted); margin: 0 0 6px; overflow-wrap: anywhere; }
.actor-action strong { color: var(--text); font-weight: 500; }
.activity-title { font-size: 12px; line-height: 1.65; overflow-wrap: anywhere; display: block; }
.activity-title:hover { color: var(--accent); }
.activity-repository, time { display: block; font-size: 10px; line-height: 1.6; color: var(--muted); margin-top: 4px; overflow-wrap: anywhere; }
.activity-message { font-size: 12px; color: var(--muted); line-height: 1.8; }
</style>
