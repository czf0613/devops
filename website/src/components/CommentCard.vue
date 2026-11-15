<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { api, dateText, errorText } from '../api'
import type { Comment, WorkItem } from '../collaboration'
import type { Repository } from '../repositories'
import { repositoryUrl } from '../repositories'
import AppButton from './AppButton.vue'
import AppNotice from './AppNotice.vue'
import AppConfirm from './AppConfirm.vue'
import DiscussionMarkdown from './DiscussionMarkdown.vue'
import DiscussionEditor from './DiscussionEditor.vue'
import ReactionMenu from './ReactionMenu.vue'
import DraftStatus from './DraftStatus.vue'
import { useDraft } from '../drafts'
const props = defineProps<{ comment: Comment; item: WorkItem; repository: Repository }>()
const emit = defineEmits<{ updated: [] }>()
const editing = ref(false),
  body = ref(props.comment.body),
  busy = ref(false),
  error = ref(''),
  copied = ref(false),
  showHistory = ref(false)
const history = ref<{ version: number; body: string; created_at: string }[]>([])
const confirmation = ref<InstanceType<typeof AppConfirm>>()
const root = ref<HTMLElement>(), version = ref(props.comment.version)
const draft = useDraft(() => `${props.repository.id}:comment:${props.comment.id}:edit`, { body, version }, () => editing.value)
editing.value = draft.restore() && props.comment.can_edit
function closeMenu() { const menu = root.value?.querySelector('details.comment-menu'); if (menu) menu.removeAttribute('open') }
function outside(event: Event) { if (!root.value?.querySelector('.comment-menu')?.contains(event.target as Node)) closeMenu() }
onMounted(() => document.addEventListener('pointerdown', outside))
onUnmounted(() => document.removeEventListener('pointerdown', outside))
async function cancel() {
  if (busy.value) return
  editing.value = false
  body.value = props.comment.body
  draft.clear()
  await nextTick()
  root.value?.querySelector<HTMLElement>('.comment-menu summary')?.focus()
}
async function save() {
  if (await action('PATCH', { version: version.value, body: body.value })) await cancel()
}
const path = () =>
  `/repositories/${props.repository.id}/items/${props.item.number}/comments/${props.comment.id}`
async function action(method: string, value?: unknown, suffix = '') {
  if (busy.value) return false
  busy.value = true
  error.value = ''
  try {
    await api(path() + suffix, method, value)
    emit('updated')
    return true
  } catch (cause) {
    error.value = errorText(cause)
    return false
  } finally {
    busy.value = false
  }
}
async function remove() {
  if (await confirmation.value?.ask('此评论将不再显示，引用也会移除。', '删除评论？'))
    await action('DELETE')
}
async function copy() {
  try {
    await navigator.clipboard.writeText(new URL(props.comment.url, location.origin).href)
    copied.value = true
  } catch {
    error.value = '复制失败，请复制评论时间上的链接'
  }
}
async function revisions() {
  showHistory.value = !showHistory.value
  if (!showHistory.value) return
  try {
    history.value = (
      await api<{ items: typeof history.value }>(
        `/repositories/${props.repository.id}/items/${props.item.number}/history?comment=${props.comment.id}`,
      )
    ).items
  } catch (cause) {
    error.value = errorText(cause)
  }
}
async function startEdit() {
  if (editing.value) return
  body.value = props.comment.body
  version.value = props.comment.version
  editing.value = true
  await nextTick()
  root.value?.querySelector('textarea')?.focus()
}
</script>
<template>
  <article ref="root" :id="`issuecomment-${comment.id}`" class="comment-card">
    <header>
      <div>
        <strong>{{ comment.author.display_name }}</strong
        ><span class="muted"> @{{ comment.author.username }} · </span
        ><RouterLink :to="comment.url" class="muted">{{ dateText(comment.created_at) }}</RouterLink
        ><span v-if="comment.version > 1" class="muted"> · 已编辑</span
        ><span v-if="comment.pending" class="muted"> · 待提交</span
        ><span v-if="comment.pinned"> · 已置顶</span>
      </div>
      <details class="comment-menu" @keydown.esc.stop.prevent="closeMenu">
        <summary aria-label="评论操作">•••</summary>
        <div @click="closeMenu">
          <button
            v-if="comment.can_edit"
            @click="startEdit"
          >
            编辑</button
          ><button @click="copy">{{ copied ? '链接已复制' : '复制链接' }}</button
          ><button @click="revisions">编辑历史</button
          ><RouterLink
            :to="{ path: `${repositoryUrl(repository)}/issue/new`, query: { source: comment.url } }"
            >由此创建 Issue</RouterLink
          ><button
            v-if="item.permissions.can_manage && !comment.pending"
            @click="action('PUT', undefined, `/pin?pinned=${!comment.pinned}`)"
          >
            {{ comment.pinned ? '取消置顶' : '置顶' }}</button
          ><button v-if="comment.can_edit" @click="remove">删除</button>
        </div>
      </details>
    </header>
    <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
    <form
      v-if="editing"
      class="comment-edit"
      @submit.prevent="save" @keydown.esc.stop.prevent="cancel"
    >
      <DiscussionEditor
        v-model="body"
        :repository-id="repository.id"
        :number="item.number"
        :disabled="busy"
      />
      <DraftStatus :message="draft.message.value" />
      <div class="actions">
        <AppButton type="submit" :busy="busy">保存评论</AppButton
        ><AppButton variant="secondary" :disabled="busy" @click="cancel">取消</AppButton>
      </div>
    </form>
    <DiscussionMarkdown
      v-else
      :source="comment.body"
      :repository-id="repository.id"
      :item-id="item.id"
      :comment-id="comment.id"
      :editable="comment.can_edit && item.permissions.can_comment && !busy"
      @update="action('PATCH', { version: comment.version, body: $event })"
    />
    <ReactionMenu
      :repository-id="repository.id"
      :number="item.number"
      :comment-id="comment.id"
      :reactions="comment.reactions"
      :disabled="!item.permissions.can_comment || comment.pending"
      @updated="emit('updated')"
      @error="error = $event"
    />
    <div v-if="showHistory" class="edit-history">
      <h4>编辑历史</h4>
      <details v-for="entry in history" :key="entry.version">
        <summary>版本 {{ entry.version }} · {{ dateText(entry.created_at) }}</summary>
        <DiscussionMarkdown :source="entry.body" :repository-id="repository.id" />
      </details>
      <p v-if="!history.length" class="muted">没有历史版本</p>
    </div>
  </article>
  <AppConfirm ref="confirmation" />
</template>
