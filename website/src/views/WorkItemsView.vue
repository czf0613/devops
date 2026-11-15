<script setup lang="ts">
import { RouterLink } from 'vue-router'
import DashboardItems from '../components/DashboardItems.vue'
defineProps<{ kind: 'issue' | 'pull' }>()
</script>
<template>
  <header class="work-heading"><div><RouterLink to="/">← 工作台</RouterLink><h1>{{ kind === 'pull' ? 'Pull requests' : 'Issues' }}</h1><p>集中查看你在各个仓库参与的{{ kind === 'pull' ? '代码审阅' : '事项' }}。</p></div></header>
  <DashboardItems :kind="kind" full />
</template>
<style scoped>
.work-heading { margin: 4px 0 24px; }
.work-heading a { color: var(--muted); font-size: 12px; }
.work-heading h1 { margin: 14px 0 5px; font-size: 26px; }
.work-heading p { color: var(--muted); font-size: 13px; margin: 0; }
</style>
