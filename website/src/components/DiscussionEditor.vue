<script setup lang="ts">
import { ref, watch } from 'vue'
import { api, errorText } from '../api'
import AppButton from './AppButton.vue'
import AppTextarea from './AppTextarea.vue'
import AppNotice from './AppNotice.vue'
import DiscussionMarkdown from './DiscussionMarkdown.vue'
const props = defineProps<{
  repositoryId: number
  number?: number
  label?: string
  disabled?: boolean
}>()
const body = defineModel<string>({ default: '' })
const preview = ref(false),
  uploading = ref(false),
  error = ref(''),
  picker = ref<HTMLInputElement>()
watch(body, (value) => { if (!value) preview.value = false })
async function upload(event: Event) {
  const input = event.target as HTMLInputElement,
    file = input.files?.[0]
  if (!file) return
  error.value = ''
  if (file.size > 10 * 1024 * 1024) {
    error.value = '附件不能超过 10 MiB'
    input.value = ''
    return
  }
  uploading.value = true
  try {
    const query = new URLSearchParams({
      filename: file.name,
      ...(props.number ? { number: String(props.number) } : {}),
    })
    const result = await api<{ url: string; media_type: string; filename: string }>(
      `/repositories/${props.repositoryId}/attachments?${query}`,
      'POST',
      file,
    )
    const name = result.filename.replace(/[\[\]\\]/g, '\\$&')
    body.value += `\n${result.media_type.startsWith('image/') ? '!' : ''}[${name}](${result.url})\n`
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    uploading.value = false
    input.value = ''
  }
}
</script>
<template>
  <div class="discussion-editor">
    <div class="discussion-editor-tabs" role="tablist" :aria-label="label || 'Markdown 编辑器'">
      <button type="button" role="tab" :aria-selected="!preview" @click="preview = false">
        编写
      </button>
      <button type="button" role="tab" :aria-selected="preview" @click="preview = true">
        预览
      </button>
    </div>
    <div v-if="preview" class="editor-preview">
      <DiscussionMarkdown v-if="body" :source="body" :repository-id="repositoryId" />
      <p v-else class="muted">暂无内容</p>
    </div>
    <AppTextarea
      v-else
      v-model="body"
      :label="label || '正文'"
      rows="9"
      maxlength="65536"
      :disabled="disabled || uploading"
      placeholder="支持 Markdown、#Issue、提交哈希和 @提及"
    />
    <div class="editor-footer">
      <AppButton variant="secondary" :busy="uploading" :disabled="disabled" @click="picker?.click()"
        >添加附件</AppButton
      ><span class="muted">Markdown · 每个附件最多 10 MiB</span>
    </div>
    <input
      ref="picker"
      class="sr-only"
      type="file"
      tabindex="-1"
      aria-label="上传附件"
      @change="upload"
    />
    <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  </div>
</template>
