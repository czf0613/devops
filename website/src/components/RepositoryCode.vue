<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api, errorText } from '../api'
import { repositoryUrl, type Repository } from '../repositories'
import MarkdownDocument from './MarkdownDocument.vue'
import RepositoryCloneMenu from './RepositoryCloneMenu.vue'
import AppBadge from './AppBadge.vue'
import AppButton from './AppButton.vue'
import AppIcon from './AppIcon.vue'
import AppNotice from './AppNotice.vue'
import AppPanel from './AppPanel.vue'
import AppSelect from './AppSelect.vue'
import Pager from './Pager.vue'
import CICommitStatus from './CICommitStatus.vue'

interface Branches { items: { name: string; commit: string }[]; truncated: boolean }
interface Entry { name: string; path: string; type: string; oid: string; size: number | null }
interface Content {
  type: string; ref: string; path: string; commit?: string; size?: number
  entries?: Entry[]; total?: number; page?: number; page_size?: number
  content?: string | null; binary?: boolean; too_large?: boolean
  lfs?: { oid: string; size: number } | null
  readme?: Content & { name: string } | null
}
const props = defineProps<{ repository: Repository; cloneUrl: string }>()
const route = useRoute(), router = useRouter()
const branches = ref<Branches>({ items: [], truncated: false }), content = ref<Content>()
const busy = ref(true), ready = ref(false), error = ref('')
let generation = 0
const selectedRef = computed(() => typeof route.query.ref === 'string' ? route.query.ref :
  branches.value.items.find(item => item.name === props.repository.default_branch)?.name || branches.value.items[0]?.name || props.repository.default_branch)
const filePath = computed(() => typeof route.query.path === 'string' ? route.query.path : '')
const breadcrumbs = computed(() => filePath.value.split('/').filter(Boolean).map((name, index, parts) => ({ name, path: parts.slice(0, index + 1).join('/') })))
const options = computed(() => branches.value.items.length
  ? branches.value.items.map(item => ({ value: item.name, label: item.name }))
  : [{ value: props.repository.default_branch, label: props.repository.default_branch }])
const location = (path = '', branch = selectedRef.value, page = 1) => ({ path: route.path, query: { ref: branch, ...(path ? { path } : {}), ...(page > 1 ? { page: String(page) } : {}) } })
const bytes = (size: number | null | undefined) => size == null ? '—' : size < 1024 ? `${size} B` : size < 1024 ** 2 ? `${(size / 1024).toFixed(1)} KiB` : `${(size / 1024 ** 2).toFixed(1)} MiB`

async function load() {
  if (!ready.value) return
  const ticket = ++generation
  busy.value = true
  error.value = ''
  content.value = undefined
  try {
    if (!branches.value.items.length) { content.value = { type: 'empty', path: '', ref: selectedRef.value }; return }
    const params = new URLSearchParams({ ref: selectedRef.value, path: filePath.value, page: String(Math.max(1, Number(route.query.page) || 1)) })
    const data = await api<Content>(`/repositories/${props.repository.id}/contents?${params}`)
    if (ticket === generation) content.value = data
  } catch (cause) { if (ticket === generation) error.value = errorText(cause) }
  finally { if (ticket === generation) busy.value = false }
}
async function refresh() {
  const ticket = ++generation
  busy.value = true
  error.value = ''
  try {
    const result = await api<Branches>(`/repositories/${props.repository.id}/branches`)
    if (ticket !== generation) return
    branches.value = result
    ready.value = true
    await load()
  } catch (cause) { if (ticket === generation) { error.value = errorText(cause); busy.value = false } }
}
watch(() => [route.query.ref, route.query.path, route.query.page], load)
onMounted(refresh)
onBeforeUnmount(() => { generation++ })
</script>

<template>
  <div class="code-toolbar">
    <div class="branch-picker"><AppIcon name="branch" /><AppSelect :model-value="selectedRef" :options="options" label="分支" hide-label :disabled="busy || !branches.items.length" @change="router.push(location('', $event))" /></div>
    <span class="muted">{{ branches.items.length }} 个分支</span>
    <AppBadge v-if="repository.protect_main">main 已保护</AppBadge>
    <AppBadge v-if="repository.protect_push">推送保护已开启</AppBadge>
    <CICommitStatus v-if="content?.commit" :repository="repository" :sha="content.commit" />
    <div class="code-toolbar-actions"><RouterLink v-if="content?.type === 'blob' && !repository.archived" :to="{ path: `${repositoryUrl(repository)}/issue/new`, query: { source: route.fullPath } }" class="text-button">由此创建 Issue</RouterLink>
      <AppButton variant="secondary" :disabled="busy" @click="refresh">刷新</AppButton>
      <RepositoryCloneMenu :clone-url="cloneUrl" />
    </div>
  </div>
  <AppNotice v-if="branches.truncated">分支选择器最多显示 1,000 个分支，其他分支可使用 Git 客户端访问。</AppNotice>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  <p v-if="busy" class="muted" role="status">正在读取代码…</p>
  <AppPanel v-else-if="content?.type === 'empty'" title="仓库还是空的" description="克隆到本地，添加文件并推送首个提交后，即可在这里浏览代码。">
    <pre class="quick-start"><code>git clone {{ cloneUrl }}</code></pre>
    <p v-if="repository.protect_main" class="muted">main 已开启分支保护。首次推送到 main 前，拥有者需要先在 Settings 中关闭分支保护。</p>
  </AppPanel>
  <section v-else-if="content" class="code-browser" aria-label="仓库代码">
    <header class="code-heading"><nav aria-label="文件路径">
      <RouterLink :to="location()">{{ repository.name }}</RouterLink>
      <template v-for="crumb in breadcrumbs" :key="crumb.path"><span>/</span><RouterLink :to="location(crumb.path)">{{ crumb.name }}</RouterLink></template>
    </nav><code :title="content.commit">{{ content.commit?.slice(0, 7) }}</code></header>
    <template v-if="content.type === 'tree'">
      <div v-if="filePath" class="file-row"><RouterLink :to="location(filePath.split('/').slice(0, -1).join('/'))">..</RouterLink></div>
      <div v-for="entry in content.entries" :key="entry.path" class="file-row">
        <AppIcon :name="entry.type === 'tree' ? 'folder' : 'file'" />
        <span v-if="entry.type === 'submodule'" class="file-name">{{ entry.name }} <small>子模块 · {{ entry.oid.slice(0, 7) }}</small></span>
        <RouterLink v-else class="file-name" :to="location(entry.path)">{{ entry.name }}<small v-if="entry.type === 'symlink'"> · 符号链接</small></RouterLink>
        <span class="file-size">{{ entry.type === 'tree' ? '目录' : bytes(entry.size) }}</span>
      </div>
      <Pager v-if="(content.total ?? 0) > (content.page_size ?? 200)" :page="content.page ?? 1" :page-size="content.page_size" :total="content.total ?? 0" :busy="busy" @change="router.push(location(filePath, selectedRef, $event))" />
    </template>
    <template v-else>
      <div class="blob-summary"><span>{{ bytes(content.lfs?.size ?? content.size) }}</span><AppBadge v-if="content.lfs">Git LFS</AppBadge></div>
      <div v-if="content.lfs" class="blob-message"><p>此文件使用 Git LFS 存储。</p><a class="text-button" :href="`/api/repositories/${repository.id}/lfs/${content.lfs.oid}`" :download="filePath.split('/').at(-1)">下载原始文件</a></div>
      <p v-else-if="content.binary || content.too_large" class="blob-message">{{ content.binary ? '二进制文件暂不支持预览。' : '文件超过 1 MiB，暂不支持在线预览。' }}请使用 Git 客户端查看。</p>
      <pre v-else class="source-code" tabindex="0" aria-label="文件内容"><code>{{ content.content }}</code></pre>
    </template>
  </section>
  <section v-if="!busy && content?.type === 'tree' && content.readme" class="readme-panel" aria-label="目录 README">
    <header class="readme-heading"><AppIcon name="repo" /><RouterLink :to="location(content.readme.path)">{{ content.readme.name }}</RouterLink></header>
    <MarkdownDocument v-if="typeof content.readme.content === 'string' && !content.readme.lfs" :source="content.readme.content" :repository-id="repository.id" :repository-path="route.path" :branch="selectedRef" :file-path="content.readme.path" />
    <p v-else class="blob-message">{{ content.readme.lfs ? '此 README 使用 LFS 存储。' : content.readme.too_large ? 'README 超过 1 MiB，暂不支持在线预览。' : '此 README 不是 UTF-8 文本，暂不支持预览。' }} <RouterLink :to="location(content.readme.path)" class="text-link">查看文件</RouterLink></p>
  </section>
</template>

<style scoped>
.code-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-bottom: 20px; font-size: 13px; }
.branch-picker { display: flex; align-items: center; gap: 10px; min-width: 160px; max-width: 360px; }
.branch-picker :deep(.form-field) { margin: 0; min-width: 0; flex: 1; }
.code-toolbar-actions { display: flex; align-items: center; gap: 12px; margin-left: auto; }
.code-browser { min-width: 0; border: 1px solid var(--line); border-radius: 12px; overflow: hidden; background: #0d1725; }
.code-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 16px; justify-content: space-between; padding: 14px 18px; border-bottom: 1px solid var(--line); background: #142131; font-size: 13px; }
.code-heading nav { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; min-width: 0; }
.code-heading a { overflow-wrap: anywhere; color: var(--accent); }
.file-row { display: flex; align-items: center; gap: 12px; padding: 10px 18px; border-bottom: 1px solid var(--line); font-size: 13px; }
.file-row:last-child { border-bottom: 0; }
.file-row:hover { background: #ffffff04; }
.file-row .app-icon { color: #7da6c9; flex-shrink: 0; }
.file-name { min-width: 0; overflow-wrap: anywhere; }
.file-name:hover { color: var(--accent); }
.file-size { margin-left: auto; flex-shrink: 0; color: var(--muted); font-size: 12px; }
.blob-summary { display: flex; gap: 12px; align-items: center; padding: 14px 22px; font-size: 12px; color: var(--muted); border-bottom: 1px solid var(--line); }
.source-code { margin: 0; padding: 22px; overflow: auto; max-width: none; border: 0; border-radius: 0; max-height: 70vh; font-size: 13px; line-height: 1.8; tab-size: 4; white-space: pre; }
.blob-message { padding: 22px; color: var(--muted); font-size: 14px; }
.quick-start { max-width: none; white-space: pre-wrap; overflow-wrap: anywhere; }
.readme-panel { margin-top: 20px; border: 1px solid var(--line); border-radius: 10px; background: #0d1725; overflow: hidden; }
.readme-heading { display: flex; align-items: center; gap: 10px; padding: 10px 18px; border-bottom: 1px solid var(--line); font-size: 13px; font-weight: 600; }
.readme-heading .app-icon { color: var(--muted); }
.readme-heading a:hover { color: var(--accent); }
@media (max-width: 720px) { .code-toolbar-actions { width: 100%; justify-content: flex-end; margin-left: 0; } .branch-picker { max-width: 100%; } .file-row, .code-heading { padding: 14px; } }
</style>
