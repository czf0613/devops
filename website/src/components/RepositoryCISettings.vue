<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { api, errorText } from '../api'
import { auth } from '../auth'
import type { CIConfig } from '../ci'
import type { Repository } from '../repositories'
import AppButton from './AppButton.vue'
import AppNotice from './AppNotice.vue'
import AppPanel from './AppPanel.vue'
import AppSwitch from './AppSwitch.vue'
const props = defineProps<{ repository: Repository }>()
const config = ref<CIConfig>(), pools = ref<string[]>([]), error = ref(''), notice = ref(''), busy = ref(false)
const changed = computed(() => JSON.stringify([...pools.value].sort()) !== JSON.stringify([...(config.value?.pools ?? [])].sort()))
let generation = 0
onUnmounted(() => { generation++ })
watch([() => props.repository.id, () => auth.user?.id], async () => {
  const ticket = ++generation
  config.value = undefined; pools.value = []; error.value = ''; busy.value = false
  try {
    const result = await api<CIConfig>(`/repositories/${props.repository.id}/ci`)
    if (ticket === generation) { config.value = result; pools.value = [...result.pools] }
  } catch (cause) { if (ticket === generation) error.value = errorText(cause) }
}, { immediate: true })
async function save(enabled: boolean, selected = pools.value) {
  if (busy.value || !config.value) return
  const ticket = generation
  busy.value = true; error.value = ''; notice.value = ''
  try {
    await api(`/repositories/${props.repository.id}/ci`, 'PUT', { enabled, pools: selected })
    if (ticket === generation && config.value) { config.value = { ...config.value, enabled, pools: [...selected] }; pools.value = [...selected]; notice.value = 'CI 设置已保存' }
  } catch (cause) { if (ticket === generation) error.value = errorText(cause) }
  finally { if (ticket === generation) busy.value = false }
}
</script>
<template>
  <AppPanel id="ci" title="CI / Actions" description="仓库默认启用 CI，使用 default 资源池。工作流来自 .github/workflows。">
    <AppNotice v-if="error" tone="error">{{ error }}</AppNotice><AppNotice v-model="notice" tone="success" />
    <template v-if="config?.can_configure">
      <div class="enable-row"><div><strong>运行工作流</strong><p class="muted">推送、Pull request 和手动运行将使用所选资源池的专用构建机。</p></div><AppSwitch :checked="config.enabled" :disabled="busy || repository.archived" label="启用 CI" @change="save($event, config!.pools.length ? config!.pools : ['default'])" /></div>
      <details class="pool-settings"><summary>构建资源池 <span>{{ config.pools.join('、') || '未选择' }}</span></summary>
        <p class="muted">工作流通过 runs-on.group 选择资源池；未指定时使用 default。没有可用构建机时，任务会排队等待。</p>
        <div class="pool-options"><label v-for="pool in config.available_pools" :key="pool" :class="{ selected: pools.includes(pool) }"><input v-model="pools" type="checkbox" :value="pool" :disabled="busy || repository.archived" /><span>{{ pool }}<small v-if="pool === 'default'">默认资源池</small></span></label></div>
        <div class="pool-actions"><AppButton variant="secondary" :busy="busy" :disabled="!changed || !pools.length || repository.archived" @click="save(config.enabled)">保存资源池</AppButton><AppButton v-if="changed" variant="ghost" :disabled="busy" @click="pools = [...config.pools]">取消</AppButton></div>
      </details>
    </template>
  </AppPanel>
</template>
<style scoped>
.enable-row { display: flex; align-items: center; justify-content: space-between; gap: 24px; }
.enable-row p { margin: 8px 0 0; font-size: 13px; }
.pool-settings { border-top: 1px solid var(--line); margin-top: 22px; padding-top: 18px; }
summary { cursor: pointer; font-size: 14px; } summary span { margin-left: 16px; color: var(--muted); }
.pool-settings p { font-size: 13px; line-height: 1.7; }
.pool-options { display: flex; flex-wrap: wrap; gap: 12px; margin: 18px 0; }
.pool-options label { display: flex; align-items: center; gap: 12px; min-width: 190px; padding: 14px 18px; border: 1px solid var(--line); border-radius: 8px; cursor: pointer; }
.pool-options label.selected { border-color: var(--accent); background: #2ac9ce0b; }
.pool-options input { accent-color: var(--accent); width: 16px; height: 16px; }
.pool-options small { display: block; margin-top: 5px; color: var(--muted); }
.pool-actions { display: flex; gap: 12px; }
</style>
