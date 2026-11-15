<script setup lang="ts">
import { computed, ref, watch, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import type { Reference } from '../collaboration'
import { renderDiscussion } from '../discussion-markdown'
const props = defineProps<{
  source: string
  repositoryId: number
  editable?: boolean
  itemId?: number
  commentId?: number
}>()
const emit = defineEmits<{ update: [body: string] }>()
const router = useRouter(),
  references = ref<Reference[]>([])
let sequence = 0
watch(
  () => [props.source, props.repositoryId, props.itemId, props.commentId],
  async () => {
    const current = ++sequence
    references.value = []
    if (!props.source.trim()) return
    try {
      const result = await api<Reference[]>(
        `/repositories/${props.repositoryId}/markdown/resolve`,
        'POST',
        {
          body: props.source,
          ...(props.itemId ? { item_id: props.itemId } : {}),
          ...(props.commentId ? { comment_id: props.commentId } : {}),
        },
      )
      if (current === sequence) references.value = result
    } catch {
      /* Content remains readable if reference summaries are unavailable. */
    }
  },
  { immediate: true },
)
onUnmounted(() => sequence++)
const html = computed(() => renderDiscussion(props.source, references.value, props.editable))
function changeTask(event: Event) {
  const target = event.target as HTMLElement
  if (target instanceof HTMLInputElement && target.dataset.taskLine && props.editable) {
    const lines = props.source.split('\n'),
      index = Number(target.dataset.taskLine)
    if (lines[index])
      lines[index] = lines[index].replace(/\[([ xX])\]/, target.checked ? '[x]' : '[ ]')
    emit('update', lines.join('\n'))
  }
}
function click(event: MouseEvent) {
  const target = event.target as HTMLElement
  if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return
  const href = target.closest('a')?.getAttribute('href')
  if (href?.startsWith('/repositories/')) {
    event.preventDefault()
    void router.push(href)
  }
}
</script>
<template>
  <article class="discussion-markdown" @click="click" @change="changeTask" v-html="html" />
</template>
