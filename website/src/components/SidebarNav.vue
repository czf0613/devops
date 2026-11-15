<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import AppIcon from './AppIcon.vue'
import RecentRepositories from './RecentRepositories.vue'
const props = defineProps<{ roles: string[]; collapsed?: boolean; userId?: number }>()
const route = useRoute()
const admin = computed(() => props.roles.includes('admin'))
const inDashboard = computed(() => route.path === '/' || route.path.startsWith('/work/'))
const inWorkspace = computed(() => inDashboard.value || route.path === '/images' || route.path.startsWith('/repositories'))
const workspaceExpanded = ref(inWorkspace.value)
const expanded = ref(Boolean(route.meta.admin))
watch(
  () => route.path,
  () => {
    if (inWorkspace.value) workspaceExpanded.value = true
    if (route.meta.admin) expanded.value = true
  },
)
watch(admin, (value) => {
  if (!value) expanded.value = false
})
</script>
<template>
  <nav aria-label="主导航" class="workspace-nav">
    <div class="workspace-nav-group">
      <div class="workspace-nav-heading" :class="{ active: inWorkspace }">
        <RouterLink to="/" aria-label="工作台" :title="collapsed ? '工作台' : undefined"><AppIcon name="grid" /><span>工作台</span></RouterLink>
        <button v-if="!collapsed" type="button" class="nav-disclosure" :aria-expanded="workspaceExpanded"
          aria-controls="workspace-navigation" :aria-label="workspaceExpanded ? '收起工作台菜单' : '展开工作台菜单'"
          @click="workspaceExpanded = !workspaceExpanded">
          <AppIcon name="chevron" :size="14" :class="{ rotated: workspaceExpanded }" />
        </button>
      </div>
      <div v-if="workspaceExpanded || collapsed" id="workspace-navigation" class="nav-children" aria-label="工作台功能">
        <RouterLink to="/repositories" aria-label="工程仓库" :title="collapsed ? '工程仓库' : undefined" :class="{ 'is-active': route.path.startsWith('/repositories/') }"
          :aria-current="route.path.startsWith('/repositories/') ? 'location' : undefined">
          <AppIcon name="repo" :size="15" /><span>工程仓库</span>
        </RouterLink>
        <RouterLink to="/images" aria-label="镜像管理" :title="collapsed ? '镜像管理' : undefined"><AppIcon name="repo" :size="15" /><span>镜像管理</span></RouterLink>
      </div>
    </div>
    <RouterLink to="/notifications" aria-label="通知" :title="collapsed ? '通知' : undefined"><AppIcon name="activity" /><span>通知</span></RouterLink>
    <RecentRepositories v-if="inDashboard && !collapsed && userId" :key="userId" :user-id="userId" />
    <RouterLink to="/guide" aria-label="使用指南" :title="collapsed ? '使用指南' : undefined"><AppIcon name="repo" /><span>使用指南</span></RouterLink>
    <RouterLink to="/profile" aria-label="个人资料" :title="collapsed ? '个人资料' : undefined"><AppIcon name="user" /><span>个人资料</span></RouterLink>
    <RouterLink to="/security" aria-label="账号安全" :title="collapsed ? '账号安全' : undefined"><AppIcon name="shield" /><span>账号安全</span></RouterLink>
    <div v-if="admin" class="nav-group">
      <button
        v-if="!collapsed"
        type="button"
        class="nav-group-trigger"
        :class="{ active: route.meta.admin }"
        :aria-expanded="expanded"
        aria-controls="admin-navigation"
        @click="expanded = !expanded"
      >
        <AppIcon name="users" /><span>系统管理</span
        ><AppIcon name="chevron" :size="14" :class="{ rotated: expanded }" />
      </button>
      <div v-if="expanded || collapsed" id="admin-navigation" class="nav-children" aria-label="管理员功能">
        <RouterLink
          to="/users"
          aria-label="人员管理" :title="collapsed ? '人员管理' : undefined"
          :class="{ 'is-active': route.path.startsWith('/users/') }"
          :aria-current="route.path.startsWith('/users/') ? 'location' : undefined"
          ><AppIcon v-if="collapsed" name="users" /><span v-else class="nav-node"></span><span>人员管理</span></RouterLink
        >
        <RouterLink to="/audit" aria-label="操作记录" :title="collapsed ? '操作记录' : undefined"><AppIcon v-if="collapsed" name="activity" /><span v-else class="nav-node"></span><span>操作记录</span></RouterLink>
        <RouterLink to="/admin/registry" aria-label="Registry 管理" :title="collapsed ? 'Registry 管理' : undefined"><AppIcon v-if="collapsed" name="repo" /><span v-else class="nav-node"></span><span>Registry 管理</span></RouterLink>
        <RouterLink to="/admin/repositories" aria-label="仓库管理" :title="collapsed ? '仓库管理' : undefined" :class="{ 'is-active': route.path.startsWith('/admin/repositories/') }"><AppIcon v-if="collapsed" name="repo" /><span v-else class="nav-node"></span><span>仓库管理</span></RouterLink>
        <RouterLink to="/admin/ci/workers" aria-label="构建机" :title="collapsed ? '构建机' : undefined"><AppIcon v-if="collapsed" name="repo" /><span v-else class="nav-node"></span><span>构建机</span></RouterLink>
      </div>
    </div>
  </nav>
</template>
