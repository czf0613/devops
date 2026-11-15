<script setup lang="ts">
import { ref } from 'vue'
import { api, errorText } from '../api'
import type { Repository } from '../repositories'
import AppPanel from './AppPanel.vue'
import AppNotice from './AppNotice.vue'
import AppSwitch from './AppSwitch.vue'
const props = defineProps<{ repository: Repository }>()
const emit = defineEmits<{ updated: [repository: Repository, message: string] }>()
const busy = ref(false), error = ref('')
async function change(key: 'protect_main' | 'protect_push', enabled: boolean) {
  busy.value = true
  error.value = ''
  try {
    const updated = await api<Repository>(`/repositories/${props.repository.id}/protection`, 'PUT', {
      protect_main: props.repository.protect_main, protect_push: props.repository.protect_push, [key]: enabled,
    })
    emit('updated', updated, '仓库保护设置已保存')
  } catch (cause) { error.value = errorText(cause) }
  finally { busy.value = false }
}
</script>

<template>
  <AppPanel title="分支与推送保护" description="仅仓库拥有者可以调整。规则对所有成员生效，包括拥有者本人。">
    <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
    <div class="protection-row">
      <div><h3>分支保护</h3><p id="protect-main-help">禁止直接推送到 main，包括创建、更新与删除。开启后请向其他分支推送，再通过 PR 审阅并合并。</p></div>
      <AppSwitch :checked="repository.protect_main" label="分支保护" describedby="protect-main-help" :disabled="busy" @change="change('protect_main', $event)" />
    </div>
    <div class="protection-row">
      <div><h3>推送保护</h3><p id="protect-push-help">禁止 force push 改写历史、覆盖已有标签，以及删除分支或标签。正常提交与新建分支不受影响。</p></div>
      <AppSwitch :checked="repository.protect_push" label="推送保护" describedby="protect-push-help" :disabled="busy" @change="change('protect_push', $event)" />
    </div>
  </AppPanel>
</template>

<style scoped>
.protection-row { display: flex; justify-content: space-between; align-items: center; gap: 24px; padding: 20px 0; }
.protection-row + .protection-row { border-top: 1px solid var(--line); }
.protection-row h3 { margin: 0 0 8px; font-size: 15px; }
.protection-row p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.7; }
</style>
