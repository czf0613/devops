<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { api, errorText } from '../api'
import { auth } from '../auth'
import type { Namespace } from '../registry'
import AppButton from './AppButton.vue'
import AppInput from './AppInput.vue'
import AppNotice from './AppNotice.vue'
const props = defineProps<{ repositoryId: number; disabled?: boolean }>()
const emit = defineEmits<{ created: [namespace: Namespace] }>()
const name = ref(''), error = ref(''), busy = ref(false)
let generation = 0
watch(() => [props.repositoryId, auth.user?.id], () => { generation++; name.value = error.value = ''; busy.value = false })
onUnmounted(() => { generation++ })
async function create() {
  if (busy.value || props.disabled) return
  const current = generation
  busy.value = true; error.value = ''
  try {
    const result = await api<Namespace>(`/repositories/${props.repositoryId}/registry/namespaces`, 'POST', { name: name.value })
    if (current === generation) { name.value = ''; emit('created', result) }
  } catch (cause) { if (current === generation) error.value = errorText(cause) }
  finally { if (current === generation) busy.value = false }
}
</script>
<template>
  <form class="namespace-create" @submit.prevent="create">
    <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
    <AppInput v-model="name" label="新 namespace" placeholder="例如：my-team" pattern="[a-z0-9]+(-[a-z0-9]+)*" :maxlength="63" hint="小写字母、数字和中间的短横线，全站名称唯一。" required :disabled="disabled || busy">
      <template #actions><AppButton type="submit" :busy="busy" :disabled="disabled || !name">创建并绑定</AppButton></template>
    </AppInput>
  </form>
</template>
<style scoped>
.namespace-create { max-width: 680px; margin-top: 24px; }
.namespace-create :deep(.form-field) { margin: 0; }
</style>
