<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { auth } from '../auth'
import AppIcon from '../components/AppIcon.vue'
import DashboardItems from '../components/DashboardItems.vue'
import DashboardActivity from '../components/DashboardActivity.vue'
import DashboardNotifications from '../components/DashboardNotifications.vue'
</script>

<template>
  <header class="dashboard-heading">
    <div><p class="eyebrow">WORKSPACE</p><h1>你好，{{ auth.user?.display_name }}</h1><p>从这里继续你的协作。</p></div>
    <RouterLink class="button secondary" to="/repositories?create=1"><AppIcon name="repo" :size="15" />新建仓库</RouterLink>
  </header>
  <div class="dashboard-layout">
    <div class="dashboard-work"><DashboardItems kind="pull" /><DashboardItems kind="issue" /></div>
    <aside class="dashboard-context" aria-label="协作概览"><DashboardActivity /><DashboardNotifications /></aside>
  </div>
</template>

<style scoped>
.dashboard-heading { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin: 7px 0 28px; }
.dashboard-heading h1 { font-size: 25px; line-height: 1.4; margin: 7px 0 5px; }
.dashboard-heading p { font-size: 12px; color: var(--muted); margin: 0; }
.dashboard-heading .eyebrow { font-size: 9px; color: var(--accent); letter-spacing: .18em; }
.dashboard-heading .button { font-size: 12px; }
.dashboard-layout { display: grid; grid-template-columns: minmax(0, 1fr) 274px; gap: 24px; align-items: start; }
.dashboard-work, .dashboard-context { display: grid; gap: 24px; min-width: 0; }
</style>
