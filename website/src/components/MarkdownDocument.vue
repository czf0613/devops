<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { renderMarkdown } from '../markdown'
const props = defineProps<{ source: string; repositoryId: number; repositoryPath: string; branch: string; filePath: string }>()
const router = useRouter()
const html = computed(() => renderMarkdown(props.source, props))
function navigate(event: MouseEvent) {
  if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return
  const anchor = (event.target as Element).closest('a')
  const href = anchor?.getAttribute('href')
  if (href?.startsWith(`${props.repositoryPath}?`)) {
    event.preventDefault()
    void router.push(href)
  }
}
</script>
<template>
  <article class="markdown-document" aria-label="README 内容" @click="navigate" v-html="html" />
</template>
<style scoped>
.markdown-document { padding: 28px 32px; line-height: 1.7; font-size: 14px; overflow-wrap: anywhere; min-width: 0; color: var(--text); }
.markdown-document :deep(> :first-child) { margin-top: 0; }
.markdown-document :deep(> :last-child) { margin-bottom: 0; }
.markdown-document :deep(h1), .markdown-document :deep(h2) { padding-bottom: 10px; border-bottom: 1px solid var(--line); margin: 24px 0 16px; }
.markdown-document :deep(h1) { font-size: 27px; }
.markdown-document :deep(h2) { font-size: 21px; }
.markdown-document :deep(h3) { font-size: 17px; margin: 20px 0 12px; }
.markdown-document :deep(p) { color: inherit; font-size: inherit; margin: 0 0 16px; }
.markdown-document :deep(a) { color: var(--accent); text-decoration: underline; text-underline-offset: 3px; }
.markdown-document :deep(ul), .markdown-document :deep(ol) { padding-left: 26px; margin: 0 0 16px; }
.markdown-document :deep(li) { margin: 4px 0; }
.markdown-document :deep(code) { font-size: .92em; background: #8498b51a; padding: 2px 5px; border-radius: 4px; }
.markdown-document :deep(pre) { max-width: 100%; padding: 18px; margin: 16px 0; background: var(--input); border: 1px solid var(--line); border-radius: 8px; overflow-x: auto; white-space: pre; }
.markdown-document :deep(pre code) { padding: 0; background: transparent; border: 0; }
.markdown-document :deep(blockquote) { margin: 16px 0; padding: 0 18px; border-left: 4px solid var(--line); color: var(--muted); }
.markdown-document :deep(table) { display: block; max-width: 100%; width: max-content; overflow: auto; border-collapse: collapse; margin: 16px 0; }
.markdown-document :deep(th), .markdown-document :deep(td) { padding: 9px 14px; border: 1px solid var(--line); white-space: normal; text-transform: none; letter-spacing: normal; }
.markdown-document :deep(img) { max-width: 100%; height: auto; }
.markdown-document :deep(hr) { margin: 24px 0; border: 0; border-top: 1px solid var(--line); }
@media (max-width: 720px) { .markdown-document { padding: 20px 16px; } }
</style>
