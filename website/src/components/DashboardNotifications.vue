<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { dateText } from '../api'
import { relativeTime, type Notifications } from '../dashboard'
import { useDashboardResource } from '../useDashboardResource'
import AppButton from './AppButton.vue'
import AppIcon from './AppIcon.vue'

const { data, busy, error, load } = useDashboardResource<Notifications>(
  () => '/me/notifications?unread=true&page=1',
  () => ({ items: [], total: 0, unread_count: 0, page: 1, page_size: 30 }),
)
</script>
<template>
  <section class="dashboard-notifications" aria-label="未读通知" :aria-busy="busy">
    <header><h2><AppIcon name="bell" :size="16" />未读通知</h2><span v-if="!busy && !error" class="unread-count">{{ data.unread_count }}</span></header>
    <div v-if="error" class="notification-message" role="alert"><p>{{ error }}</p><AppButton variant="secondary" @click="load">重试</AppButton></div>
    <p v-else-if="busy" class="notification-message" role="status">正在读取通知…</p>
    <ul v-else-if="data.items.length">
      <li v-for="entry in data.items.slice(0, 3)" :key="entry.id"><RouterLink :to="entry.url">{{ entry.item.title }}</RouterLink><span>{{ entry.actor?.display_name || '系统' }} · #{{ entry.item.number }}</span><time :datetime="entry.created_at" :title="dateText(entry.created_at)">{{ relativeTime(entry.created_at) }}</time></li>
    </ul>
    <p v-else class="notification-message">没有未读通知，你已掌握最新进展。</p>
    <RouterLink to="/notifications" class="all-notifications">查看所有通知<AppIcon name="arrow" :size="13" /></RouterLink>
  </section>
</template>
<style scoped>
.dashboard-notifications { padding: 19px 21px 15px; border: 1px solid var(--line); border-radius: 10px; background: #101a2980; }
header { display: flex; align-items: center; justify-content: space-between; }
h2 { margin: 0; display: flex; align-items: center; gap: 8px; font-size: 13px; }
h2 .app-icon { color: var(--muted); }
.unread-count { border-radius: 12px; padding: 2px 7px; font-size: 10px; color: var(--accent); background: #56ddd611; }
ul { list-style: none; margin: 12px 0 0; padding: 0; }
li { padding: 12px 0; border-bottom: 1px solid var(--line); }
li a { display: block; font-size: 12px; line-height: 1.65; overflow-wrap: anywhere; }
li a:hover { color: var(--accent); }
li span, time { display: block; color: var(--muted); font-size: 10px; margin-top: 5px; overflow-wrap: anywhere; }
.notification-message { color: var(--muted); font-size: 12px; line-height: 1.8; padding: 6px 0; }
.all-notifications { display: flex; gap: 6px; align-items: center; padding-top: 13px; color: var(--blue); font-size: 11px; }
</style>
