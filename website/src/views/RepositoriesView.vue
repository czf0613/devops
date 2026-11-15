<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api, dateText, errorText } from '../api'
import type { Page } from '../api'
import { auth } from '../auth'
import type { Repository } from '../repositories'
import { repositoryUrl } from '../repositories'
import AppBadge from '../components/AppBadge.vue'
import AppButton from '../components/AppButton.vue'
import AppInput from '../components/AppInput.vue'
import AppNotice from '../components/AppNotice.vue'
import AppPanel from '../components/AppPanel.vue'
import AppTextarea from '../components/AppTextarea.vue'
import Pager from '../components/Pager.vue'
import RepositoryActivity from '../components/RepositoryActivity.vue'

const router = useRouter()
const route = useRoute()
const rows = ref<Repository[]>([]), page = ref(1), total = ref(0), busy = ref(false)
const creating = ref(false), error = ref(''), query = ref('')
watch(() => route.query.create, value => { creating.value = value === '1' }, { immediate: true })
const form = reactive({ name: '', description: '' })
async function load(next = 1) {
  busy.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({ page: String(next), q: query.value })
    const result = await api<Page<Repository>>(`/repositories?${params}`)
    rows.value = result.items
    page.value = result.page
    total.value = result.total
  } catch (cause) { error.value = errorText(cause) }
  finally { busy.value = false }
}
async function create() {
  busy.value = true
  error.value = ''
  try {
    const repo = await api<Repository>('/repositories', 'POST', form)
    await router.push(repositoryUrl(repo))
  } catch (cause) { error.value = errorText(cause) }
  finally { busy.value = false }
}
onMounted(() => load())
</script>

<template>
  <div class="page-heading heading-row">
    <div><p class="eyebrow">REPOSITORIES</p><h1>工程仓库</h1><p>从代码开始，组织团队协作。这里显示你拥有或参与的私有仓库。</p></div>
    <AppButton :disabled="busy" @click="creating = !creating">{{ creating ? '收起表单' : '＋ 创建仓库' }}</AppButton>
  </div>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  <AppPanel v-if="creating" title="创建私有仓库" :description="`拥有者：@${auth.user?.username}。创建后可添加成员并分配权限。`">
    <form class="compact-form" @submit.prevent="create">
      <AppInput v-model.trim="form.name" label="仓库名称" required maxlength="100" placeholder="例如：devops-platform" hint="1–100 位字母、数字、点、下划线或短横线；自动转小写，不以 .git 结尾。" :disabled="busy" />
      <AppTextarea v-model="form.description" label="仓库简介（选填）" maxlength="2000" :disabled="busy" />
      <p class="muted">{{ auth.user?.username }}/{{ form.name.toLowerCase() || 'repository-name' }} · 默认分支 main，自动生成 README.md</p>
      <AppButton type="submit" :busy="busy">创建仓库</AppButton>
    </form>
  </AppPanel>
  <AppPanel class="table-panel repository-list-panel">
    <form class="toolbar" @submit.prevent="load()">
      <AppInput v-model.trim="query" aria-label="搜索仓库" placeholder="按仓库名称搜索" maxlength="100" />
      <AppButton type="submit" variant="secondary" :busy="busy">搜索</AppButton>
    </form>
    <ul v-if="rows.length" class="repository-list" aria-label="工程仓库列表">
      <li v-for="repo in rows" :key="repo.id" class="repository-row">
        <div class="repository-summary">
          <div class="repository-title">
            <RouterLink class="text-link" :to="repositoryUrl(repo)">{{ repo.full_name }}</RouterLink>
            <AppBadge>私有</AppBadge><AppBadge v-if="repo.archived">已归档</AppBadge>
          </div>
          <p class="repository-description" :title="repo.description || undefined">{{ repo.description || '暂无简介' }}</p>
          <div class="repository-meta">
            <AppBadge :tone="repo.permissions.is_owner ? 'blue' : undefined">{{ repo.permissions.is_owner ? '拥有者' : repo.permissions.is_admin ? '仓库管理员' : '成员' }}</AppBadge>
            <span v-if="repo.permissions.role">{{ repo.permissions.role }}</span>
            <span>{{ repo.permissions.access === 'write' ? '读写' : '只读' }}</span>
            <span>{{ repo.member_count }} 位成员</span>
            <time :datetime="repo.created_at">创建于 {{ dateText(repo.created_at) }}</time>
          </div>
        </div>
        <RepositoryActivity :repository-id="repo.id" />
      </li>
    </ul>
    <div v-else class="empty-state">{{ busy ? '正在读取仓库…' : query ? '没有找到符合条件的仓库' : '还没有参与的仓库。创建一个工程，或让仓库拥有者添加你。' }}</div>
    <Pager :page="page" :total="total" :busy="busy" @change="load" />
  </AppPanel>
</template>

<style scoped>
.repository-list-panel { min-width: 0; }
.repository-list { margin: 0; padding: 0 24px; list-style: none; }
.repository-row { display: flex; align-items: center; gap: 40px; padding: 26px 0; border-top: 1px solid var(--line); }
.repository-summary { min-width: 0; flex: 1; }
.repository-title { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.repository-title .text-link { min-width: 0; max-width: 100%; font-size: 18px; font-weight: 650; color: var(--accent); overflow-wrap: anywhere; }
.repository-description { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; overflow-wrap: anywhere; white-space: pre-line; color: var(--muted); margin: 10px 0 16px; line-height: 1.65; font-size: 14px; }
.repository-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
@media (max-width: 850px) {
  .repository-row { flex-direction: column; align-items: stretch; gap: 16px; }
  .repository-row :deep(.repo-activity) { align-self: flex-end; }
}
@media (max-width: 600px) {
  .repository-list { padding: 0 16px; }
}
</style>
