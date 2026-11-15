<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { api, dateText, errorText, type Page } from '../api'
import { auth } from '../auth'
import type { AdminRepository } from '../repositories'
import AppBadge from '../components/AppBadge.vue'
import AppButton from '../components/AppButton.vue'
import AppInput from '../components/AppInput.vue'
import AppNotice from '../components/AppNotice.vue'
import AppPanel from '../components/AppPanel.vue'
import AppSelect from '../components/AppSelect.vue'
import Pager from '../components/Pager.vue'

const rows = ref<AdminRepository[]>([]), total = ref(0), page = ref(1)
const query = ref(''), state = ref('active'), loading = ref(false), error = ref('')
let reads = 0
onUnmounted(() => { reads++ })
watch(() => auth.user?.id, () => { rows.value = []; void load() }, { immediate: true })
async function load(next = 1) {
  const current = ++reads
  loading.value = true; error.value = ''
  try {
    const result = await api<Page<AdminRepository>>(`/admin/repositories?${new URLSearchParams({ q: query.value, state: state.value, page: String(next) })}`)
    if (current !== reads) return
    rows.value = result.items; total.value = result.total; page.value = next
  } catch (cause) { if (current === reads) { error.value = errorText(cause); rows.value = [] } }
  finally { if (current === reads) loading.value = false }
}
</script>

<template>
  <div class="page-heading"><p class="eyebrow">ADMINISTRATION</p><h1>仓库管理</h1><p>管理全站仓库的成员、权限、拥有者与生命周期。每次修改都需要验证当前管理员邮箱，并记录操作历史。</p></div>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  <AppPanel class="table-panel">
    <form class="toolbar" @submit.prevent="load()">
      <AppInput v-model.trim="query" aria-label="搜索拥有者或仓库名" placeholder="搜索拥有者 / 仓库名" maxlength="133" />
      <AppSelect v-model="state" label="仓库状态" hide-label :options="[{ value: 'active', label: '正常仓库' }, { value: 'archived', label: '已归档' }, { value: 'deleted', label: '已删除' }, { value: 'all', label: '全部状态' }]" />
      <AppButton type="submit" variant="secondary" :busy="loading">查询</AppButton>
    </form>
    <div class="table-scroll"><table>
      <thead><tr><th>仓库</th><th>拥有者</th><th>成员</th><th>状态</th><th>创建时间</th><th><span class="sr-only">操作</span></th></tr></thead>
      <tbody><tr v-for="repo in rows" :key="repo.id">
        <td class="admin-repo-name"><RouterLink :to="`/admin/repositories/${repo.id}`">{{ repo.full_name }}</RouterLink></td>
        <td>{{ repo.owner.display_name }}<small>@{{ repo.owner.username }}</small></td>
        <td>{{ repo.member_count }}</td>
        <td><AppBadge :tone="!repo.deleted_at && !repo.archived ? 'green' : undefined">{{ repo.deleted_at ? '已删除' : repo.archived ? '已归档' : '正常' }}</AppBadge></td>
        <td>{{ dateText(repo.created_at) }}</td>
        <td><RouterLink :to="`/admin/repositories/${repo.id}`">{{ repo.deleted_at ? '查看记录' : '管理' }}</RouterLink></td>
      </tr></tbody>
    </table></div>
    <p v-if="!rows.length" class="empty-state">{{ loading ? '正在读取仓库…' : '没有符合条件的仓库' }}</p>
    <Pager :page="page" :total="total" :busy="loading" @change="load" />
  </AppPanel>
</template>

<style scoped>
.admin-repo-name { max-width: 400px; overflow-wrap: anywhere; }
</style>
