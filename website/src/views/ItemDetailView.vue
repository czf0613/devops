<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api, dateText, errorText, type Page } from '../api'
import { auth } from '../auth'
import { repositoryUrl, type Repository, type RepositoryMember } from '../repositories'
import {
  methodLabels,
  scrollAnchor,
  stateLabel,
  type Catalog,
  type Comparison,
  type MergeMethod,
  type Pull,
  type ReviewThread,
  type TimelineEntry,
  type WorkItem,
} from '../collaboration'
import AppBadge from '../components/AppBadge.vue'
import CICommitStatus from '../components/CICommitStatus.vue'
import AppButton from '../components/AppButton.vue'
import AppInput from '../components/AppInput.vue'
import AppSelect from '../components/AppSelect.vue'
import AppTextarea from '../components/AppTextarea.vue'
import AppNotice from '../components/AppNotice.vue'
import AppConfirm from '../components/AppConfirm.vue'
import Pager from '../components/Pager.vue'
import DiscussionMarkdown from '../components/DiscussionMarkdown.vue'
import DiscussionEditor from '../components/DiscussionEditor.vue'
import CommentCard from '../components/CommentCard.vue'
import ReactionMenu from '../components/ReactionMenu.vue'
import InlineMetadata from '../components/InlineMetadata.vue'
import DraftStatus from '../components/DraftStatus.vue'
import { useDraft } from '../drafts'
import ItemRelations from '../components/ItemRelations.vue'
import DiffViewer from '../components/DiffViewer.vue'

const props = defineProps<{ repository: Repository }>()
const route = useRoute(),
  router = useRouter(),
  number = Number(route.params.number)
const base = `/repositories/${props.repository.id}`,
  path = `${base}/items/${number}`,
  pullPath = `${base}/pulls/${number}`
const isPull = computed(() => route.meta.repositoryTab === 'pr'),
  section = computed(() => String(route.params.section || 'conversation'))
const diffVisited = ref(section.value === 'changes')
watch(section, value => { if (value === 'changes') diffVisited.value = true })
const item = ref<WorkItem>(),
  pull = ref<Pull>(),
  timeline = ref<Page<TimelineEntry> & { pinned_comments: import('../collaboration').Comment[] }>({
    items: [],
    total: 0,
    page: 1,
    page_size: 50,
    pinned_comments: [],
  })
const error = ref(''),
  notice = ref(''),
  loading = ref(false),
  busy = ref(false),
  titleEditing = ref(false),
  bodyEditing = ref(false),
  title = ref(''),
  body = ref(''),
  commentBody = ref(''),
  closeReason = ref('completed')
const catalog = ref<Catalog[]>([]),
  members = ref<RepositoryMember[]>([])
const comparison = ref<Comparison>(),
  threads = ref<ReviewThread[]>([]),
  reply = ref<Record<number, string>>({}),
  reviewBody = ref(''),
  reviewHead = ref(''),
  reviewState = ref('commented')
const method = ref<MergeMethod>('merge'),
  mergeMessage = ref(''),
  deleteBranch = ref(false),
  reviewerIds = ref<number[]>([]),
  reviewersEditing = ref(false),
  branches = ref<{ name: string }[]>([]),
  newBase = ref('')
const showHistory = ref(false),
  history = ref<{ version: number; title: string; body: string; created_at: string }[]>([]),
  transferring = ref(false),
  repositories = ref<Repository[]>([]),
  destination = ref('')
const confirmation = ref<InstanceType<typeof AppConfirm>>()
const titleVersion = ref(0), bodyVersion = ref(0)
const draftScope = (part: string) => `${props.repository.id}:item:${number}:${part}`
const titleDraft = useDraft(() => draftScope('title'), { title, version: titleVersion }, () => titleEditing.value)
const bodyDraft = useDraft(() => draftScope('body'), { body, version: bodyVersion }, () => bodyEditing.value)
const commentDraft = useDraft(() => draftScope('comment'), { body: commentBody })
const reviewDraft = useDraft(() => draftScope('review'), { body: reviewBody, state: reviewState, head: reviewHead })
const replyDraft = useDraft(() => draftScope('replies'), { reply })
const mergeDraft = useDraft(() => draftScope('merge'), { message: mergeMessage })
commentDraft.restore()
replyDraft.restore()
mergeDraft.restore()
let draftsLoaded = false
async function startEdit(part: 'title' | 'body') {
  if (part === 'title') {
    title.value = item.value!.title
    titleVersion.value = item.value!.version
    titleEditing.value = true
  } else {
    body.value = item.value!.body
    bodyVersion.value = item.value!.version
    bodyEditing.value = true
  }
  await nextTick()
  document.querySelector<HTMLElement>(part === 'title' ? '.title-edit input' : '#issue-body textarea')?.focus()
}
async function cancelEdit(part: 'title' | 'body') {
  if (busy.value) return
  if (part === 'title') {
    titleEditing.value = false
    title.value = item.value!.title
    titleDraft.clear()
  } else {
    bodyEditing.value = false
    body.value = item.value!.body
    bodyDraft.clear()
  }
  await nextTick()
  document.querySelector<HTMLElement>(`[aria-label="编辑${part === 'title' ? '标题' : '正文'}"]`)?.focus()
}
async function saveEdit(part: 'title' | 'body') {
  const success = await action(path, 'PATCH', part === 'title'
    ? { version: titleVersion.value, title: title.value }
    : { version: bodyVersion.value, body: body.value }, '已保存')
  if (success) await cancelEdit(part)
}
async function postReply(id: number) {
  if (await action(`${pullPath}/threads/${id}/comments`, 'POST', { body: reply.value[id] }, '回复已发表')) {
    reply.value[id] = ''
    if (!Object.values(reply.value).some(value => value.trim())) replyDraft.clear()
  }
}
let alive = true,
  sequence = 0,
  timer: ReturnType<typeof setTimeout> | undefined
const labels = {
  created: '创建了记录',
  edited: '更新了内容',
  closed: '关闭了记录',
  reopened: '重新打开了记录',
  commented: '发表了评论',
  comment_edited: '编辑了评论',
  comment_deleted: '删除了评论',
  relation_added: '添加了关联',
  relation_removed: '解除了关联',
  reparented: '调整了父 Issue',
  transferred: '移交了 Issue',
  synchronized: '更新了分支',
  reviewed: '提交了审阅',
  review_dismissed: '撤销了审阅',
  thread_resolved: '解决了讨论',
  thread_reopened: '重新打开了讨论',
  merged: '合并了 PR',
  closed_by_pull: '通过 PR 关闭',
  branch_deleted: '删除了源分支',
  branch_kept: '保留了已更新的源分支',
  pull_updated: '更新了 PR',
  check_updated: '更新了检查',
  review_requested: '请求了审阅',
  children_reordered: '调整了子 Issue 顺序',
  closed_by_commit: '通过提交关闭了 Issue',
} as Record<string, string>
const mergeCheck = computed(() => pull.value?.merge_checks[method.value])
const pendingMerge = computed(() =>
  Boolean(
    pull.value?.merge_intent &&
      ['pending', 'prepared', 'applied'].includes(pull.value.merge_intent.state),
  ),
)
const mergeable = computed(
  () =>
    item.value?.state === 'open' &&
    pull.value?.can_merge &&
    !pull.value.reasons.length &&
    !pull.value.base_missing &&
    !pull.value.head_missing &&
    mergeCheck.value?.status === 'clean' &&
    !pendingMerge.value,
)
const pendingReview = computed(() =>
  pull.value?.reviews.find(
    (review) => review.state === 'pending' && review.author.id === auth.user?.id,
  ),
)
function poll() {
  clearTimeout(timer)
  if (!alive || !pull.value || item.value?.state !== 'open') return
  timer = setTimeout(async () => {
    try {
      const data = await api<Pull>(pullPath)
      if (!alive) return
      const changed =
        data.head_sha !== pull.value?.head_sha ||
        data.base_sha !== pull.value?.base_sha ||
        data.merge_intent?.state !== pull.value?.merge_intent?.state
      pull.value = data
      if (!reviewBody.value.trim()) reviewHead.value = data.head_sha
      if (changed) await load(timeline.value.page, false)
    } catch {
      /* Keep the last known state; explicit actions still surface errors. */
    }
    poll()
  }, 5000)
}
async function load(page = timeline.value.page, scroll = true) {
  const current = ++sequence
  loading.value = true
  error.value = ''
  try {
    const initial = await api<WorkItem>(path)
    if (!alive || current !== sequence) return
    if (
      initial.repository_id !== props.repository.id ||
      initial.kind !== (isPull.value ? 'pull' : 'issue')
    ) {
      await router.replace(initial.url + route.hash)
      return
    }
    if (isPull.value) {
      const result = await api<Pull>(pullPath)
      if (!alive || current !== sequence) return
      pull.value = result
      if (!draftsLoaded) {
        reviewHead.value = result.head_sha
        reviewDraft.restore()
      } else if (!reviewBody.value.trim()) reviewHead.value = result.head_sha
      if (!result.policy.methods.includes(method.value)) method.value = result.policy.methods[0]!
    }
    const anchorComment = /^#issuecomment-(\d+)$/.exec(route.hash)?.[1]
    const [record, events] = await Promise.all([
      isPull.value ? api<WorkItem>(path) : Promise.resolve(initial),
      api<typeof timeline.value>(
        `${path}/timeline?page=${page}${anchorComment && scroll ? `&comment=${anchorComment}` : ''}`,
      ),
    ])
    if (!alive || current !== sequence) return
    const expected = isPull.value ? 'pull' : 'issue'
    if (record.repository_id !== props.repository.id || record.kind !== expected) {
      await router.replace(record.url + route.hash)
      return
    }
    const previous = item.value
    // A change to another field must not make an unchanged field's draft stale.
    // Keep old versions when the edited field changed remotely, so the server still rejects overwrites.
    if (previous && titleEditing.value && titleVersion.value === previous.version && record.title === previous.title)
      titleVersion.value = record.version
    if (previous && bodyEditing.value && bodyVersion.value === previous.version && record.body === previous.body)
      bodyVersion.value = record.version
    item.value = record
    timeline.value = events
    if (record.state === 'merged') { mergeMessage.value = ''; mergeDraft.clear() }
    if (!titleEditing.value) { title.value = record.title; titleVersion.value = record.version }
    if (!bodyEditing.value) { body.value = record.body; bodyVersion.value = record.version }
    if (!draftsLoaded) {
      titleEditing.value = titleDraft.restore() && record.permissions.can_edit
      bodyEditing.value = bodyDraft.restore() && record.permissions.can_edit
      draftsLoaded = true
    }
    if (isPull.value) {
      const [diff, discussions] = await Promise.all([
        api<Comparison>(`${pullPath}/diff`),
        api<ReviewThread[]>(`${pullPath}/threads`),
      ])
      if (!alive || current !== sequence) return
      comparison.value = diff
      threads.value = discussions
      if (!reviewersEditing.value) {
        reviewerIds.value = pull.value!.reviewers.map((p) => p.id)
        newBase.value = pull.value!.base_ref
      }
    }
    await nextTick()
    if (scroll) await scrollAnchor(route.hash)
    poll()
  } catch (cause) {
    if (alive && current === sequence) error.value = errorText(cause)
  } finally {
    if (alive && current === sequence) loading.value = false
  }
}
async function action(url: string, verb: string, value?: unknown, message = '') {
  if (busy.value) return false
  busy.value = true
  error.value = ''
  try {
    await api(url, verb, value)
    await load(timeline.value.page, false)
    notice.value = message
    return true
  } catch (cause) {
    error.value = errorText(cause)
    return false
  } finally {
    busy.value = false
  }
}
async function patch(value: Record<string, unknown>) {
  return action(path, 'PATCH', { version: item.value!.version, ...value }, '已保存')
}
async function postComment() {
  if (await action(`${path}/comments`, 'POST', { body: commentBody.value }, '评论已发表')) {
    commentBody.value = ''
    commentDraft.clear()
    await load(Math.ceil((timeline.value.total || 1) / timeline.value.page_size), false)
    return true
  }
  return false
}
async function changeState() {
  if (commentBody.value.trim() && !(await postComment())) return
  await patch({ state: item.value!.state === 'open' ? 'closed' : 'open',
    ...(item.value!.state === 'open' ? { close_reason: closeReason.value } : {}) })
}
async function toggleReviewers() {
  if (busy.value) return
  reviewersEditing.value = !reviewersEditing.value
  reviewerIds.value = pull.value!.reviewers.map(person => person.id)
  newBase.value = pull.value!.base_ref
  await nextTick()
  document.querySelector<HTMLElement>(reviewersEditing.value ? '.reviewer-settings input, .reviewer-settings [role="combobox"]' : '.reviewer-settings > button')?.focus()
}
async function historyLoad() {
  showHistory.value = !showHistory.value
  if (showHistory.value) {
    try {
      history.value = (await api<{ items: typeof history.value }>(`${path}/history`)).items
    } catch (cause) {
      error.value = errorText(cause)
    }
  }
}
async function remove() {
  if (!(await confirmation.value?.ask('删除后此 Issue 将不再显示，子项仍然保留。', '删除 Issue？')))
    return
  try {
    await api(path, 'DELETE')
    await router.push(`${repositoryUrl(props.repository)}/issue`)
  } catch (cause) {
    error.value = errorText(cause)
  }
}
async function transferOpen() {
  transferring.value = !transferring.value
  if (transferring.value) {
    try {
      repositories.value = (await api<Page<Repository>>('/repositories')).items.filter(
        (r) =>
          r.id !== props.repository.id &&
          !r.archived &&
          (r.permissions.access === 'write' || r.permissions.is_admin),
      )
    } catch (cause) {
      error.value = errorText(cause)
    }
  }
}
async function transfer() {
  if (
    !(await confirmation.value?.ask(
      'Issue 将移交到所选仓库，原地址继续跳转；可见性采用目标仓库权限。',
      '移交 Issue？',
    ))
  )
    return
  try {
    const result = await api<WorkItem>(`${path}/transfer`, 'POST', {
      repository_id: Number(destination.value),
    })
    await router.push(result.url)
  } catch (cause) {
    error.value = errorText(cause)
  }
}
async function merge() {
  if (
    !(await confirmation.value?.ask(
      `将使用“${methodLabels[method.value]}”把 ${pull.value!.head_ref} 合并到 ${pull.value!.base_ref}。`,
      '合并此 PR？',
    ))
  )
    return
  await action(
    `${pullPath}/merge`,
    'POST',
    {
      base_sha: pull.value!.base_sha,
      head_sha: pull.value!.head_sha,
      method: method.value,
      message: mergeMessage.value,
      delete_branch: deleteBranch.value,
    },
    '合并请求已提交',
  )
  poll()
}
async function submitReview() {
  const success = await action(
    `${pullPath}/reviews`,
    'POST',
    { head_sha: reviewHead.value, state: reviewState.value, body: reviewBody.value },
    '审阅已提交',
  )
  if (success) { reviewBody.value = ''; reviewDraft.clear() }
}
async function copy() {
  try {
    await navigator.clipboard.writeText(new URL(item.value!.url, location.origin).href)
    notice.value = '链接已复制'
  } catch {
    error.value = '复制失败，请复制浏览器地址'
  }
}
watch(
  () => route.hash,
  async () => {
    if (item.value && /^#issuecomment-/.test(route.hash)) await load()
    else if (!route.hash.startsWith('#diff-')) await scrollAnchor(route.hash)
  },
)
onMounted(async () => {
  void load()
  try {
    ;[catalog.value, members.value] = await Promise.all([
      api<Catalog[]>(`${base}/catalog`),
      api<RepositoryMember[]>(`${base}/members`),
    ])
    if (isPull.value)
      branches.value = (await api<{ items: { name: string }[] }>(`${base}/branches`)).items
  } catch (cause) {
    error.value = errorText(cause)
  }
})
onUnmounted(() => {
  alive = false
  sequence++
  clearTimeout(timer)
})
</script>
<template>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice
  ><AppNotice v-model="notice" tone="success" />
  <template v-if="item">
    <header class="item-heading">
      <div class="heading-row">
        <form v-if="titleEditing" class="title-edit" @submit.prevent="saveEdit('title')" @keydown.esc.stop.prevent="cancelEdit('title')">
          <AppInput v-model="title" aria-label="标题" required maxlength="256" :disabled="busy" />
          <AppButton type="submit" :busy="busy" :disabled="!title.trim()">保存标题</AppButton>
          <AppButton variant="secondary" :disabled="busy" @click="cancelEdit('title')">取消</AppButton>
          <DraftStatus :message="titleDraft.message.value" />
        </form>
        <h1 v-else>{{ item.title }} <span class="muted">#{{ item.number }}</span></h1>
        <div class="actions">
          <AppButton
            v-if="item.permissions.can_edit && !titleEditing"
            variant="secondary"
            aria-label="编辑标题" :disabled="busy"
            @click="startEdit('title')"
            >编辑标题</AppButton
          ><button class="text-button" @click="copy">复制链接</button>
        </div>
      </div>
      <div class="actions">
        <AppBadge
          :tone="item.state === 'open' ? 'green' : item.state === 'merged' ? 'blue' : undefined"
          >{{ pull?.draft && item.state === 'open' ? '草稿' : stateLabel(item) }}</AppBadge
        ><span class="muted"
          >{{ item.author.display_name }} 创建于 {{ dateText(item.created_at) }} ·
          {{ item.comment_count }} 条评论</span
        ><AppBadge v-if="item.locked">已锁定</AppBadge>
        <CICommitStatus v-if="pull?.head_sha" :repository="repository" :sha="pull.head_sha" />
      </div>
      <p v-if="pull" class="muted">
        <code>{{ pull.head_ref }}</code> → <code>{{ pull.base_ref }}</code>
        <span v-if="pull.merged_sha"
          >· 合并提交
          <RouterLink :to="`${repositoryUrl(repository)}/commit/${pull.merged_sha}`">{{
            pull.merged_sha.slice(0, 8)
          }}</RouterLink></span
        >
      </p>
    </header>
    <nav v-if="pull" class="pull-tabs" aria-label="PR 导航">
      <RouterLink
        v-for="tab in [
          { key: 'conversation', label: '讨论' },
          { key: 'commits', label: '提交' },
          { key: 'changes', label: '文件变更' },
          { key: 'checks', label: '检查' },
        ]"
        :key="tab.key"
        :class="{ active: section === tab.key }"
        :to="{ path: `${item.url}${tab.key === 'conversation' ? '' : `/${tab.key}`}`, query: route.query }"
        >{{ tab.label }} <span v-if="tab.key === 'commits'">{{ comparison?.commits.length }}</span
        ><span v-if="tab.key === 'changes'">{{ comparison?.total }}</span
        ><span v-if="tab.key === 'checks'">{{ pull.checks.length }}</span></RouterLink
      >
    </nav>
    <section v-if="section === 'commits' && comparison" class="commit-list">
      <p v-if="comparison.commits_truncated" class="muted">仅显示前 1000 个提交</p>
      <div v-for="commit in comparison.commits" :key="commit.sha" class="commit-row">
        <div>
          <RouterLink :to="`${repositoryUrl(repository)}/commit/${commit.sha}`">{{
            commit.title
          }}</RouterLink
          ><small>{{ commit.author }} · {{ dateText(commit.date) }}</small>
        </div>
        <RouterLink :to="`${repositoryUrl(repository)}/commit/${commit.sha}`"
          ><code>{{ commit.sha.slice(0, 8) }}</code></RouterLink
        >
      </div>
    </section>
    <section v-if="section === 'checks' && pull" class="checks-panel">
      <h3>提交检查</h3>
      <p v-if="!pull.checks_configured" class="muted">未配置检查</p>
      <p v-else-if="!pull.checks.length" class="muted">等待必要检查结果</p>
      <div v-for="check in pull.checks" :key="check.id" class="check-row">
        <AppBadge
          :tone="
            check.status === 'success' ? 'green' : check.status === 'failure' ? 'red' : undefined
          "
          >{{
            {
              queued: '等待中',
              running: '运行中',
              success: '通过',
              failure: '失败',
              cancelled: '已取消',
            }[check.status] || check.status
          }}</AppBadge
        ><strong>{{ check.name }}</strong
        ><span>{{ check.details }}</span
        ><a
          v-if="check.external_url"
          :href="check.external_url"
          target="_blank"
          rel="noopener noreferrer"
          >详情 ↗</a
        >
      </div>
    </section>
    <DiffViewer
      v-if="diffVisited && comparison && pull"
      v-show="section === 'changes'"
      :active="section === 'changes'"
      :repository="repository"
      :comparison="comparison"
      :number="item.number"
      :pull="pull"
      :can-comment="item.permissions.can_comment && item.state === 'open'"
      @updated="load(timeline.page, false)"
    />
    <div v-show="section === 'conversation'" class="item-layout">
      <main class="item-main">
        <article id="issue-body" class="comment-card">
          <header>
            <strong>{{ item.author.display_name }}</strong
            ><div class="actions"><button class="text-button" :aria-expanded="showHistory" @click="historyLoad">
              {{ showHistory ? '收起编辑历史' : item.version > 1 ? '已编辑 · 编辑历史' : '编辑历史' }}
            </button><button v-if="item.permissions.can_edit && !bodyEditing" class="text-button" aria-label="编辑正文" :disabled="busy" @click="startEdit('body')">编辑正文</button></div>
          </header>
          <form v-if="bodyEditing" class="comment-edit" @submit.prevent="saveEdit('body')" @keydown.esc.stop.prevent="cancelEdit('body')">
            <DiscussionEditor v-model="body" :repository-id="repository.id" :number="item.number" :disabled="busy" />
            <DraftStatus :message="bodyDraft.message.value" />
            <div class="actions"><AppButton type="submit" :busy="busy">保存正文</AppButton>
              <AppButton variant="secondary" :disabled="busy" @click="cancelEdit('body')">取消</AppButton></div>
          </form>
          <DiscussionMarkdown v-else
            :source="item.body || '*暂无描述*'"
            :repository-id="repository.id"
            :item-id="item.id"
            :editable="item.permissions.can_edit && !busy"
            @update="patch({ body: $event })"
          /><ReactionMenu
            :repository-id="repository.id"
            :number="item.number"
            :reactions="item.reactions"
            :disabled="!item.permissions.can_comment"
            @updated="load(timeline.page, false)"
            @error="error = $event"
          />
          <div v-if="showHistory" class="edit-history">
            <details v-for="entry in history" :key="entry.version">
              <summary>
                版本 {{ entry.version }} · {{ entry.title }} · {{ dateText(entry.created_at) }}
              </summary>
              <DiscussionMarkdown :source="entry.body" :repository-id="repository.id" />
            </details>
            <p v-if="!history.length" class="muted">没有历史版本</p>
          </div>
        </article>
        <ItemRelations
          :item="item"
          :repository="repository"
          @updated="load(timeline.page, false)"
        />
        <section v-if="timeline.pinned_comments.length" class="pinned-comments">
          <h3>置顶评论</h3>
          <RouterLink
            v-for="comment in timeline.pinned_comments"
            :key="comment.id"
            :to="comment.url"
            >{{ comment.author.display_name }}：{{ comment.body.slice(0, 100) }}</RouterLink
          >
        </section>
        <div class="item-timeline">
          <template v-for="entry in timeline.items" :key="entry.id"
            ><CommentCard
              v-if="entry.comment && !entry.comment.thread_id"
              :comment="entry.comment"
              :item="item"
              :repository="repository"
              @updated="load(timeline.page, false)"
            />
            <div v-else-if="!entry.comment" :id="`event-${entry.id}`" class="timeline-event">
              <span class="timeline-symbol">●</span
              ><span
                ><strong>{{ entry.actor?.display_name || '系统' }}</strong>
                {{ labels[entry.action] || entry.action }}
                <RouterLink v-if="entry.data.related_item" :to="entry.data.related_item.url"
                  >{{ entry.data.related_item.title }} #{{
                    entry.data.related_item.number
                  }}</RouterLink
                ><span v-if="entry.data.state">
                  ·
                  {{
                    { approved: '批准', changes_requested: '要求修改', commented: '评论' }[
                      entry.data.state
                    ] || entry.data.state
                  }}</span
                ><small>{{ dateText(entry.created_at) }}</small></span
              >
            </div></template
          >
        </div>
        <Pager
          v-if="timeline.total > timeline.page_size"
          :page="timeline.page"
          :total="timeline.total"
          :page-size="timeline.page_size"
          :busy="loading"
          @change="load($event, false)"
        />
        <section v-if="item.backlinks.length" class="backlinks">
          <h3>被提及</h3>
          <div v-for="reference in item.backlinks" :key="reference.id" class="timeline-event">
            <span>↗</span
            ><RouterLink :to="reference.url"
              >{{ reference.source.title
              }}<template v-if="reference.source.kind !== 'commit'">
                #{{ reference.source.number }}</template
              ></RouterLink
            ><small>{{ dateText(reference.created_at) }}</small>
          </div>
        </section>
        <form v-if="item.permissions.can_comment" class="new-comment" @submit.prevent="postComment">
          <h3>发表评论</h3>
          <DiscussionEditor
            v-model="commentBody"
            :repository-id="repository.id"
            :number="item.number"
            :disabled="busy"
          />
          <DraftStatus :message="commentDraft.message.value" />
          <div class="comment-actions">
            <AppButton class="post-comment" type="submit" :busy="busy" :disabled="!commentBody.trim()">评论</AppButton
            ><template v-if="item.permissions.can_edit && item.state !== 'merged'"
              ><AppSelect
                v-if="item.kind === 'issue' && item.state === 'open'"
                v-model="closeReason"
                label="关闭原因" hide-label :disabled="busy"
                :options="[
                  { value: 'completed', label: '已完成' },
                  { value: 'not_planned', label: '不计划处理' },
                  { value: 'duplicate', label: '重复' },
                ]"
              /><AppButton
                variant="secondary"
                :disabled="busy"
                @click="changeState"
                >{{ item.state === 'open' ? '关闭' : '重新打开' }}
                {{ isPull ? 'PR' : 'Issue' }}{{ commentBody.trim() ? '并评论' : '' }}</AppButton
              ></template
            >
          </div>
        </form>
        <AppNotice v-else>{{ item.locked ? '讨论已锁定' : '仓库已归档' }}</AppNotice>
      </main>
      <aside class="item-sidebar">
        <InlineMetadata :item="item" :catalog="catalog" :members="members" @updated="load(timeline.page, false)" />
        <div class="sidebar-block">
          <h3>通知</h3>
          <AppButton
            variant="secondary"
            :disabled="busy"
            @click="
              action(`${path}/subscription`, 'PUT', {
                watching: !item.subscription.watching,
                email: item.subscription.email,
              })
            "
            >{{ item.subscription.watching ? '取消订阅' : '订阅' }}</AppButton
          ><label class="check-label"
            ><input
              type="checkbox"
              :checked="item.subscription.email"
              :disabled="busy"
              @change="
                action(`${path}/subscription`, 'PUT', {
                  watching: true,
                  email: ($event.target as HTMLInputElement).checked,
                })
              "
            />邮件通知</label
          >
        </div>
        <div v-if="item.permissions.can_manage" class="sidebar-block">
          <button class="text-button" @click="patch({ pinned: !item.pinned })">
            {{ item.pinned ? '取消置顶' : '置顶' }}</button
          ><button class="text-button" @click="patch({ locked: !item.locked })">
            {{ item.locked ? '解锁讨论' : '锁定讨论' }}</button
          ><template v-if="item.kind === 'issue'"
            ><button class="text-button" @click="transferOpen">移交 Issue</button
            ><button class="text-button danger" @click="remove">删除 Issue</button></template
          >
          <form v-if="transferring" @submit.prevent="transfer" @keydown.esc.stop.prevent="transferring = false">
            <AppSelect
              v-model="destination"
              label="目标仓库"
              :options="[
                { value: '', label: '请选择' },
                ...repositories.map((repo) => ({ value: String(repo.id), label: repo.full_name })),
              ]"
            /><AppButton type="submit" :disabled="!destination" variant="danger"
              >确认移交</AppButton><AppButton variant="secondary" @click="transferring = false">取消</AppButton>
          </form>
        </div>
      </aside>
    </div>
    <section
      v-if="pull && (section === 'conversation' || section === 'changes')"
      class="pull-discussions"
    >
      <h3>代码审阅</h3>
      <div
        v-for="thread in threads"
        :id="`reviewthread-${thread.id}`"
        :key="thread.id"
        class="review-thread"
      >
        <div class="heading-row">
          <div>
            <code>{{ thread.path }}</code> · {{ thread.start_line }}–{{ thread.end_line }} 行
            <AppBadge v-if="thread.outdated">旧版本</AppBadge
            ><AppBadge v-if="thread.resolved" tone="green">已解决</AppBadge>
          </div>
          <AppButton
            v-if="item.permissions.can_comment && thread.comments.some((c) => !c.pending)"
            variant="secondary"
            :disabled="busy"
            @click="
              action(
                `${pullPath}/threads/${thread.id}/resolved?resolved=${!thread.resolved}`,
                'PUT',
              )
            "
            >{{ thread.resolved ? '重新打开讨论' : '解决讨论' }}</AppButton
          >
        </div>
        <CommentCard
          v-for="comment in thread.comments"
          :key="comment.id"
          :comment="comment"
          :item="item"
          :repository="repository"
          @updated="load(timeline.page, false)"
        />
        <form
          v-if="item.permissions.can_comment"
          class="thread-reply"
          @submit.prevent="postReply(thread.id)"
        >
          <AppTextarea v-model="reply[thread.id]" label="回复讨论" rows="2" required :disabled="busy" /><AppButton
            type="submit"
            variant="secondary"
            :disabled="busy || !reply[thread.id]?.trim()"
            >回复</AppButton><DraftStatus v-if="reply[thread.id]" :message="replyDraft.message.value" />
        </form>
      </div>
      <p v-if="!threads.length" class="muted">
        暂无行评论。可在文件变更中点击行号开始讨论，按住 Shift 选择多行。
      </p>
      <div
        v-for="review in pull.reviews.filter((r) => r.state !== 'pending')"
        :id="`review-${review.id}`"
        :key="review.id"
        class="review-summary"
      >
        <strong>{{ review.author.display_name }}</strong> ·
        {{
          {
            pending: '审阅草稿',
            approved: '批准了这些变更',
            changes_requested: '要求修改',
            commented: '提交了评论',
            dismissed: '审阅已撤销',
          }[review.state]
        }}
        <span v-if="review.head_sha !== pull.head_sha" class="muted">· 对应旧提交</span
        ><button
          v-if="
            item.permissions.can_manage && ['approved', 'changes_requested'].includes(review.state)
          "
          class="text-button"
          @click="action(`${pullPath}/reviews/${review.id}/dismiss`, 'POST')"
        >
          撤销审阅
        </button>
      </div>
      <form
        v-if="item.permissions.can_comment && item.state === 'open'"
        class="review-form"
        @submit.prevent="submitReview"
      >
        <h3>{{ pendingReview ? '提交待发表的审阅' : '提交审阅' }}</h3>
        <DiscussionEditor
          v-model="reviewBody"
          :repository-id="repository.id"
          :number="item.number"
          label="审阅说明"
          :disabled="busy"
        /><DraftStatus :message="reviewDraft.message.value" />
        <AppNotice v-if="reviewHead !== pull.head_sha">源分支已有新提交。请检查最新变更后继续审阅。
          <button type="button" class="text-button" :disabled="busy" @click="reviewHead = pull.head_sha">已检查，基于当前提交继续</button>
        </AppNotice>
        <AppSelect
          v-model="reviewState"
          label="审阅结论"
          :options="[
            { value: 'commented', label: '评论' },
            ...(repository.permissions.access === 'write' && item.author.id !== auth.user?.id
              ? [
                  { value: 'approved', label: '批准' },
                  { value: 'changes_requested', label: '要求修改' },
                ]
              : []),
          ]"
        /><AppButton type="submit" :busy="busy" :disabled="reviewHead !== pull.head_sha || (reviewState === 'commented' && !reviewBody.trim() && !pendingReview)">提交审阅</AppButton>
      </form>
      <div v-if="item.permissions.can_edit && item.state === 'open'" class="reviewer-settings">
        <button class="text-button" :disabled="busy" :aria-expanded="reviewersEditing" @click="toggleReviewers">
          {{ reviewersEditing ? '取消调整' : '调整审阅者 / 目标分支' }}
        </button>
        <form
          v-if="reviewersEditing"
          @keydown.esc.stop.prevent="toggleReviewers"
          @submit.prevent="
            action(pullPath, 'PATCH', {
              version: item.version,
              reviewer_ids: reviewerIds,
              base_ref: newBase,
            }).then(() => {
              if (!error) reviewersEditing = false
            })
          "
        >
          <fieldset>
            <legend>审阅者</legend>
            <label v-for="member in members" :key="member.user_id" class="check-label"
              ><input v-model="reviewerIds" type="checkbox" :value="member.user_id" />{{
                member.display_name
              }}</label
            >
          </fieldset>
          <AppSelect
            v-model="newBase"
            label="目标分支"
            :options="
              branches
                .filter((branch) => branch.name !== pull?.head_ref)
                .map((branch) => ({ value: branch.name, label: branch.name }))
            "
          /><div class="actions"><AppButton type="submit" :busy="busy">保存</AppButton><AppButton variant="secondary" :disabled="busy" @click="toggleReviewers">取消</AppButton></div>
        </form>
        <AppButton
          variant="secondary"
          :disabled="busy"
          @click="action(pullPath, 'PATCH', { version: item.version, draft: !pull.draft })"
          >{{ pull.draft ? '标记为可审阅' : '转为草稿' }}</AppButton
        >
      </div>
    </section>
    <section v-if="pull && item.state === 'open'" class="merge-panel">
      <h3>合并 Pull request</h3>
      <AppSelect
        v-model="method"
        label="合并方式"
        :options="pull.policy.methods.map((value) => ({ value, label: methodLabels[value] }))"
      />
      <div class="merge-status" :class="mergeCheck?.status">
        <strong>{{
          {
            clean: '没有合并冲突',
            conflict: '存在合并冲突',
            checking: '正在检测合并冲突…',
            error: '冲突检测失败',
          }[mergeCheck?.status || 'checking']
        }}</strong
        ><span v-if="mergeCheck?.error">{{ mergeCheck.error }}</span
        ><button
          v-if="mergeCheck?.status === 'error'"
          class="text-button"
          @click="action(`${pullPath}/retry-checks`, 'POST')"
        >
          重新检测
        </button>
      </div>
      <template v-if="mergeCheck?.status === 'conflict'"
        ><ul>
          <li v-for="file in mergeCheck.conflicts" :key="file">
            <code>{{ file }}</code>
          </li>
        </ul>
        <p class="muted">在本地更新源分支并解决冲突后推送，系统会重新检测。</p></template
      >
      <ul v-if="pull.reasons.length">
        <li v-for="reason in pull.reasons" :key="reason">{{ reason }}</li>
      </ul>
      <p v-if="pull.base_missing || pull.head_missing" class="error">源分支或目标分支已删除</p>
      <AppNotice v-if="pull.merge_intent?.error" tone="error">{{
        pull.merge_intent.error
      }}</AppNotice>
      <p v-if="pendingMerge" class="muted">正在合并并更新记录…</p>
      <template v-if="pull.can_merge"
        ><AppTextarea
          v-model="mergeMessage"
          label="合并说明（可选）"
          rows="2"
          maxlength="65536"
        /><DraftStatus :message="mergeDraft.message.value" /><label class="check-label"
          ><input
            v-model="deleteBranch"
            type="checkbox"
            :disabled="
              repository.protect_push || (repository.protect_main && pull.head_ref === 'main')
            "
          />合并后删除源分支</label
        ><AppButton :disabled="!mergeable || busy" :busy="pendingMerge" @click="merge">{{
          methodLabels[method]
        }}</AppButton></template
      >
    </section>
  </template>
  <p v-else-if="loading" class="muted">正在读取…</p>
  <AppConfirm ref="confirmation" />
</template>
