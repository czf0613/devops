<script setup lang="ts">
import { ref } from 'vue'
import { api, errorText } from '../api'
import { emojiLabels, type Reaction } from '../collaboration'
const props = defineProps<{
  repositoryId: number
  number: number
  reactions: Reaction[]
  commentId?: number
  disabled?: boolean
}>()
const emit = defineEmits<{ updated: []; error: [message: string] }>()
const busy = ref(false)
async function toggle(emoji: string) {
  busy.value = true
  try {
    await api(`/repositories/${props.repositoryId}/items/${props.number}/reaction`, 'PUT', {
      emoji,
      comment_id: props.commentId || null,
      active: !props.reactions.some((r) => r.emoji === emoji && r.mine),
    })
    emit('updated')
  } catch (cause) {
    emit('error', errorText(cause))
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <div class="reactions">
    <button
      v-for="reaction in reactions"
      :key="reaction.emoji"
      :disabled="disabled || busy"
      :class="{ mine: reaction.mine }"
      :aria-pressed="reaction.mine"
      :aria-label="`${emojiLabels[reaction.emoji]} ${reaction.count}`"
      @click="toggle(reaction.emoji)"
    >
      {{ emojiLabels[reaction.emoji] }} {{ reaction.count }}
    </button>
    <details v-if="!disabled">
      <summary aria-label="添加表态">☺ ＋</summary>
      <div class="reaction-picker">
        <button
          v-for="(emoji, key) in emojiLabels"
          :key="key"
          :disabled="busy"
          :aria-label="emoji"
          @click="toggle(key)"
        >
          {{ emoji }}
        </button>
      </div>
    </details>
  </div>
</template>
