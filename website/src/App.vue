<script setup lang="ts">
import { computed, ref, watch, watchEffect } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { api, errorText } from './api'
import { auth, forgetLogin } from './auth'
import AppButton from './components/AppButton.vue'
import AppBadge from './components/AppBadge.vue'
import AppIcon from './components/AppIcon.vue'
import AppNotice from './components/AppNotice.vue'
import SidebarNav from './components/SidebarNav.vue'
import { version } from '../package.json'

const route = useRoute()
const router = useRouter()
const isAdmin = computed(() => auth.user?.roles.includes('admin'))
const busy = ref(false)
const retrying = ref(false)
const error = ref('')
const copyrightYear = new Date().getFullYear()
async function retryNavigation() {
  retrying.value = true
  const target = router.resolve(auth.retryPath || route.fullPath)
  try { await router.push({ path: target.path, query: target.query, hash: target.hash, force: true, replace: true }) }
  finally { retrying.value = false }
}
function savedSidebar() {
  try { return localStorage.getItem('devops.sidebar.collapsed') === 'true' } catch { return false }
}
const sidebarCollapsed = ref(savedSidebar())
watch(sidebarCollapsed, value => {
  try { localStorage.setItem('devops.sidebar.collapsed', String(value)) } catch { /* Storage may be unavailable. */ }
})
watchEffect(() => {
  document.title = `${String(route.meta.title ?? '工作台')} · DevOps`
})

async function logout() {
  busy.value = true
  error.value = ''
  try {
    await api('/auth/logout', 'POST')
    forgetLogin('已退出登录')
    await router.replace('/login')
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div v-if="route.meta.public" class="auth-shell"><RouterView :key="route.path" /></div>
  <div v-else class="workspace" :class="{ 'sidebar-collapsed': sidebarCollapsed }">
    <aside id="workspace-sidebar" class="sidebar">
      <RouterLink class="brand" to="/" aria-label="DevOps 工作台" :title="sidebarCollapsed ? 'DevOps 工作台' : undefined"
        ><img class="brand-mark" src="/logo.webp" alt="" width="35" height="35" /><span class="brand-name">DevOps</span></RouterLink
      >
      <p class="nav-label">工作空间</p>
      <SidebarNav :roles="auth.user?.roles ?? []" :collapsed="sidebarCollapsed" :user-id="auth.user?.id" />
      <div class="sidebar-footer">
        <div class="sidebar-caption"><span>DevOps v{{ version }}</span><span>© {{ copyrightYear }} kevinc.ltd</span></div>
      </div>
    </aside>
    <div class="workspace-body">
      <header class="topbar">
        <div class="topbar-left">
        <button type="button" class="sidebar-toggle" :aria-label="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'" :title="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'" :aria-expanded="!sidebarCollapsed" aria-controls="workspace-sidebar" @click="sidebarCollapsed = !sidebarCollapsed"><AppIcon name="sidebar" :size="20" /></button>
        <div class="breadcrumb">
          <span>工作空间</span><span class="divider">/</span><span>{{ route.meta.title }}</span>
        </div>
        </div>
        <div v-if="auth.user" class="user-menu">
          <span class="avatar">{{ auth.user.display_name.slice(0, 1) }}</span>
          <span>{{ auth.user.display_name }}</span>
          <AppBadge :tone="isAdmin ? 'blue' : undefined">{{
            isAdmin ? '管理员' : '成员'
          }}</AppBadge>
          <AppButton variant="ghost" :busy="busy" @click="logout"
            ><AppIcon name="logout" :size="15" />退出</AppButton
          >
        </div>
      </header>
      <main class="main">
        <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
        <AppNotice v-if="auth.error" tone="error">{{ auth.error }} <AppButton variant="secondary" :busy="retrying" @click="retryNavigation">重试</AppButton></AppNotice>
        <RouterView v-if="auth.loaded" :key="route.params.owner && route.params.name ? `${route.params.owner}/${route.params.name}` : route.path" />
      </main>
    </div>
  </div>
</template>
