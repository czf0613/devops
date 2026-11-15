<script setup lang="ts">
import { computed, ref, watch, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, errorText } from '../api'
import type { Repository } from '../repositories'
import { scrollAnchor, type Comparison, type Pull, type Review } from '../collaboration'
import AppButton from './AppButton.vue'
import AppNotice from './AppNotice.vue'
import AppTextarea from './AppTextarea.vue'
import Pager from './Pager.vue'
import DraftStatus from './DraftStatus.vue'
import { useDraft } from '../drafts'
const props = withDefaults(defineProps<{
  repository: Repository
  comparison: Comparison
  number?: number
  pull?: Pull
  compareRefs?: { base: string; head: string }
  commit?: string
  canComment?: boolean
  active?: boolean
}>(), { active: true })
const emit = defineEmits<{ updated: [] }>()
const route = useRoute(),
  router = useRouter()
const listing = ref(props.comparison),
  selected = ref(String(route.query.file || props.comparison.files[0]?.path || ''))
const patch = ref(''),
  error = ref(''),
  loading = ref(false),
  split = ref(false),
  folded = ref(false),
  posting = ref(false),
  body = ref('')
const line = ref<{ side: 'left' | 'right'; start: number; end: number } | null>(null)
const displayed = ref({ base: '', head: '' })
const patchRegion = ref<HTMLElement>()
const pendingHeight = ref(0)
const lineDraft = useDraft(
  () => `${props.repository.id}:pull:${props.number}:line:${selected.value}:${displayed.value.base}:${displayed.value.head}`,
  { body, line }, () => Boolean(props.canComment && props.number && !posting.value && !loading.value),
)
async function cancelComment() {
  if (posting.value) return
  const anchor = line.value && id(selected.value, line.value.side, line.value.end)
  line.value = null
  body.value = ''
  lineDraft.clear()
  await nextTick()
  if (anchor) document.getElementById(anchor)?.querySelector('button')?.focus()
}
let sequence = 0, pageSequence = 0, localHash = ''
onUnmounted(() => { sequence++; pageSequence++ })
watch(
  [() => route.query.file, () => props.active],
  ([value]) => {
    if (props.active !== false) selected.value = typeof value === 'string' ? value : listing.value.files[0]?.path || ''
  },
)
watch(
  () => route.hash,
  (hash) => {
    if (props.active !== false && hash.startsWith('#diff-') && hash !== localHash && !loading.value) void scrollAnchor(hash)
    localHash = ''
  },
)
const prefix = computed(() => `/repositories/${props.repository.id}`)
const id = (path: string, side: string, value: number) =>
  `diff-${Array.from(path)
    .map((c) => c.codePointAt(0)!.toString(16))
    .join('-')}-${side}-${value}`
function endpoint(file?: string, page = 1) {
  const query = new URLSearchParams({
    ...(file ? { path: file } : {}),
    page: String(page),
    ...(props.compareRefs || {}),
  })
  return `${prefix.value}/${props.commit ? `commits/${props.commit}` : props.number ? `pulls/${props.number}/diff` : 'compare'}?${query}`
}
async function loadPatch() {
  const current = ++sequence
  pendingHeight.value = patchRegion.value?.getBoundingClientRect().height || 0
  loading.value = true
  patch.value = ''
  error.value = ''
  line.value = null
  body.value = ''
  folded.value = false
  if (!selected.value) { loading.value = false; return }
  try {
    const data = await api<Comparison | { comparison: Comparison }>(endpoint(selected.value))
    if (current === sequence) {
      const result = 'comparison' in data ? data.comparison : data
      patch.value = result.patch || ''
      displayed.value = { base: result.base_sha, head: result.head_sha }
      lineDraft.restore()
      loading.value = false
      await nextTick()
      if (props.active !== false) await scrollAnchor(route.hash)
    }
  } catch (cause) {
    if (current === sequence) error.value = errorText(cause)
  } finally {
    if (current === sequence) loading.value = false
  }
}
watch(selected, loadPatch, { immediate: true })
watch(
  () => props.comparison,
  (value) => {
    // Background polling only supplies page one. Keep a paged selection until
    // the underlying revisions actually change.
    const changed = value.base_sha !== listing.value.base_sha || value.head_sha !== listing.value.head_sha
    if (listing.value.page === 1 || changed) listing.value = value
    if (changed && !value.files.some((file) => file.path === selected.value))
      selected.value = value.files[0]?.path || ''
    else if (value.base_sha !== displayed.value.base || value.head_sha !== displayed.value.head) void loadPatch()
  },
)
const rows = computed(() => {
  let left = 0,
    right = 0,
    inHunk = false
  return patch.value.split('\n').map((text, index) => {
    const match = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(text)
    if (match) {
      left = Number(match[1])
      right = Number(match[2])
      inHunk = true
      return { index, text, type: 'hunk', left: undefined, right: undefined }
    }
    if (!inHunk || ![' ', '+', '-'].includes(text[0] || ''))
      return { index, text, type: 'meta', left: undefined, right: undefined }
    return {
      index,
      text: text.slice(1),
      type: text.startsWith('+') ? 'add' : text.startsWith('-') ? 'delete' : 'context',
      left: text.startsWith('+') ? undefined : left++,
      right: text.startsWith('-') ? undefined : right++,
    }
  })
})
async function page(value: number) {
  if (posting.value) return
  const current = ++pageSequence
  try {
    const data = await api<Comparison | { comparison: Comparison }>(endpoint(undefined, value))
    if (current !== pageSequence) return
    listing.value = 'comparison' in data ? data.comparison : data
    choose(listing.value.files[0]?.path || '')
  } catch (cause) {
    if (current === pageSequence) error.value = errorText(cause)
  }
}
function choose(path: string) {
  if (posting.value) return
  selected.value = path
  void router.replace({ query: { ...route.query, file: path }, hash: '' })
}
function selectLine(side: 'left' | 'right', value: number | undefined, event: MouseEvent) {
  if (!value || loading.value || posting.value) return
  if (props.canComment && props.number && props.pull) {
    if (event.shiftKey && line.value?.side === side)
      line.value = {
        side,
        start: Math.min(line.value.start, value),
        end: Math.max(line.value.start, value),
      }
    else line.value = { side, start: value, end: value }
  }
  localHash = `#${id(selected.value, side, value)}`
  void router.replace({
    query: { ...route.query, file: selected.value },
    hash: localHash,
  })
}
async function comment(pending: boolean) {
  if (!line.value || !props.number || !props.pull || loading.value) return
  posting.value = true
  error.value = ''
  try {
    let reviewId: number | undefined
    if (pending)
      reviewId = (
        await api<Review>(`${prefix.value}/pulls/${props.number}/reviews`, 'POST', {
          head_sha: props.pull.head_sha,
          state: 'pending',
        })
      ).id
    await api(`${prefix.value}/pulls/${props.number}/threads`, 'POST', {
      body: body.value,
      path: selected.value,
      side: line.value.side,
      start_line: line.value.start,
      end_line: line.value.end,
      base_sha: displayed.value.base,
      head_sha: displayed.value.head,
      ...(reviewId ? { review_id: reviewId } : {}),
    })
    body.value = ''
    line.value = null
    lineDraft.clear()
    emit('updated')
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    posting.value = false
  }
}
async function viewed() {
  const file = listing.value.files.find((entry) => entry.path === selected.value)
  if (!file || !props.pull || !props.number) return
  try {
    await api(
      `${prefix.value}/pulls/${props.number}/viewed?${new URLSearchParams({ path: file.path, head: displayed.value.head, active: String(!file.viewed) })}`,
      'PUT',
    )
    file.viewed = !file.viewed
  } catch (cause) {
    error.value = errorText(cause)
  }
}
</script>
<template>
  <section class="diff-viewer">
    <div class="heading-row">
      <h3>
        文件变更 <span class="muted">{{ listing.total }}</span>
        <small class="diff-additions">+{{ listing.additions }}</small>
        <small class="diff-deletions">−{{ listing.deletions }}</small>
      </h3>
      <div class="actions">
        <AppButton variant="secondary" :aria-pressed="!split" @click="split = false"
          >统一视图</AppButton
        ><AppButton variant="secondary" :aria-pressed="split" @click="split = true"
          >并排视图</AppButton
        >
      </div>
    </div>
    <div class="diff-layout">
      <aside class="diff-tree" aria-label="变更文件">
        <button
          v-for="file in listing.files"
          :key="file.path"
          :title="file.path"
          :disabled="posting"
          :class="{ active: selected === file.path }"
          @click="choose(file.path)"
        >
          <span class="diff-status">{{ file.status[0] }}</span
          ><span
            :style="{ paddingLeft: `${Math.min(4, file.path.split('/').length - 1) * 8}px` }"
            >{{ file.path }}</span
          ><span v-if="file.viewed">✓</span></button
        ><Pager
          v-if="listing.total > listing.page_size"
          :page="listing.page"
          :total="listing.total"
          :page-size="listing.page_size"
          :busy="posting"
          @change="page"
        />
      </aside>
      <div class="diff-content">
        <div class="diff-file-heading">
          <button class="text-button" :aria-expanded="!folded" @click="folded = !folded">
            {{ folded ? '▸' : '▾' }} {{ selected || '没有文件变更' }}</button
          ><AppButton v-if="number" variant="secondary" :disabled="loading || !!error" @click="viewed">{{
            listing.files.find((f) => f.path === selected)?.viewed ? '✓ 已查看' : '标记已查看'
          }}</AppButton>
        </div>
        <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
        <div ref="patchRegion" :aria-busy="loading" :style="{ minHeight: loading ? `${pendingHeight}px` : undefined }">
        <p v-if="loading" class="muted" role="status">正在读取差异…</p>
        <div v-else-if="!folded" class="diff-scroll" tabindex="0" aria-label="代码差异">
          <table class="diff-table" :class="{ split }">
            <tbody>
              <tr v-for="row in rows" :key="row.index" :class="row.type">
                <template v-if="split"
                  ><td
                    :id="row.left ? id(selected, 'left', row.left) : undefined"
                    class="diff-line"
                  >
                    <button v-if="row.left" @click="selectLine('left', row.left, $event)">
                      {{ row.left }}
                    </button>
                  </td>
                  <td class="diff-code old">
                    <pre>{{ row.type === 'add' ? '' : row.text }}</pre>
                  </td>
                  <td
                    :id="row.right ? id(selected, 'right', row.right) : undefined"
                    class="diff-line"
                  >
                    <button v-if="row.right" @click="selectLine('right', row.right, $event)">
                      {{ row.right }}
                    </button>
                  </td>
                  <td class="diff-code new">
                    <pre>{{ row.type === 'delete' ? '' : row.text }}</pre>
                  </td></template
                ><template v-else
                  ><td
                    :id="row.left ? id(selected, 'left', row.left) : undefined"
                    class="diff-line"
                  >
                    <button v-if="row.left" @click="selectLine('left', row.left, $event)">
                      {{ row.left }}
                    </button>
                  </td>
                  <td
                    :id="row.right ? id(selected, 'right', row.right) : undefined"
                    class="diff-line"
                  >
                    <button v-if="row.right" @click="selectLine('right', row.right, $event)">
                      {{ row.right }}
                    </button>
                  </td>
                  <td class="diff-code">
                    <pre><span class="diff-marker">{{ row.type === 'add' ? '+' : row.type === 'delete' ? '−' : ' ' }}</span>{{ row.text }}</pre>
                  </td></template
                >
              </tr>
            </tbody>
          </table>
        </div>
        </div>
        <form v-if="line && canComment" class="line-comment-form" @submit.prevent="comment(false)" @keydown.esc.stop.prevent="cancelComment">
          <h4>
            {{ selected }} · {{ line.side === 'left' ? '原文件' : '新文件' }} {{ line.start }}–{{
              line.end
            }}
            行
          </h4>
          <AppTextarea v-model="body" label="行评论" required maxlength="65536" :disabled="posting" />
          <DraftStatus :message="lineDraft.message.value" />
          <div class="actions">
            <AppButton type="submit" :busy="posting" :disabled="!body.trim()">发表单条评论</AppButton
            ><AppButton
              variant="secondary"
              :busy="posting"
              :disabled="!body.trim()"
              @click="comment(true)"
              >添加到审阅</AppButton
            ><AppButton variant="secondary" :disabled="posting" @click="cancelComment">取消</AppButton>
          </div>
        </form>
      </div>
    </div>
  </section>
</template>
