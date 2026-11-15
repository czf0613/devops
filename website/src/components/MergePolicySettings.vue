<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { api, errorText } from '../api'
import { useDraft } from '../drafts'
import type { Repository } from '../repositories'
import type { MergeMethod, MergePolicy } from '../collaboration'
import { methodLabels } from '../collaboration'
import AppPanel from './AppPanel.vue'
import AppInput from './AppInput.vue'
import AppTextarea from './AppTextarea.vue'
import AppButton from './AppButton.vue'
import AppNotice from './AppNotice.vue'
import DraftStatus from './DraftStatus.vue'

const props = defineProps<{ repository: Repository }>()
const form = ref<HTMLFormElement>()
const loading = ref(true), loaded = ref(false), busy = ref(false), error = ref(''), notice = ref('')
const methods = ref<MergeMethod[]>([]), approvals = ref('0'), stale = ref(false), resolved = ref(false), checks = ref('')
let baseline = ''
const savedValue = ref<MergePolicy>()
const values = () => ({ methods: methods.value, approvals: approvals.value, stale: stale.value, resolved: resolved.value, checks: checks.value })
const changed = computed(() => loaded.value && JSON.stringify(values()) !== baseline)
const disabled = computed(() => busy.value || props.repository.archived)
const draft = useDraft(() => `${props.repository.id}:merge-policy`, { methods, approvals, stale, resolved, checks },
  () => loaded.value && !loading.value && !props.repository.archived)
const descriptions: Record<MergeMethod, string> = {
  merge: '保留分支提交，并创建一次合并提交。',
  squash: '将分支提交压缩为一个提交。',
  rebase: '将分支提交依次应用到目标分支。',
}
function assign(value: MergePolicy) {
  methods.value = [...value.methods]
  approvals.value = String(value.required_approvals)
  stale.value = value.dismiss_stale_approvals
  resolved.value = value.require_resolved_threads
  checks.value = value.required_checks.join('\n')
}
async function load() {
  loading.value = true
  loaded.value = false
  error.value = ''
  try {
    savedValue.value = await api<MergePolicy>(`/repositories/${props.repository.id}/merge-policy`)
    assign(savedValue.value)
    baseline = JSON.stringify(values())
    loaded.value = true
    if (!props.repository.archived) draft.restore()
  } catch (cause) { error.value = errorText(cause) }
  finally { loading.value = false }
}
function cancel() {
  if (disabled.value || !savedValue.value) return
  assign(savedValue.value)
  draft.clear()
  error.value = ''
  void nextTick(() => form.value?.querySelector<HTMLInputElement>('input[type="number"]')?.focus({ preventScroll: true }))
}
async function save() {
  if (disabled.value || !loaded.value || !changed.value) return
  error.value = ''
  if (!methods.value.length) { error.value = '至少保留一种合并方式。'; return }
  const requiredChecks = [...new Set(checks.value.split('\n').map(value => value.trim()).filter(Boolean))]
  if (requiredChecks.length > 30) { error.value = '最多设置 30 个必要检查。'; return }
  busy.value = true
  try {
    const value = await api<MergePolicy>(`/repositories/${props.repository.id}/merge-policy`, 'PUT', {
      methods: methods.value, required_approvals: Number(approvals.value),
      dismiss_stale_approvals: stale.value, require_resolved_threads: resolved.value, required_checks: requiredChecks,
    })
    savedValue.value = value
    assign(value)
    baseline = JSON.stringify(values())
    draft.clear()
    notice.value = 'PR 合并规则已保存'
  } catch (cause) { error.value = errorText(cause) }
  finally { busy.value = false }
}
onMounted(load)
</script>

<template>
  <AppPanel title="PR 合并规则" class="merge-policy-settings">
    <AppNotice v-if="error" tone="error">{{ error }}
      <AppButton v-if="!loaded" variant="secondary" :busy="loading" @click="load">重新加载</AppButton>
    </AppNotice>
    <AppNotice v-model="notice" tone="success" />
    <p v-if="loading" class="muted">正在加载合并规则…</p>
    <form v-else-if="loaded" ref="form" aria-label="PR 合并规则" @submit.prevent="save">
      <div class="policy-columns">
        <fieldset :disabled="disabled" class="policy-group">
          <legend>允许的合并方式</legend>
          <label v-for="method in ['merge', 'squash', 'rebase'] as MergeMethod[]" :key="method" class="policy-option">
            <input v-model="methods" type="checkbox" :value="method" :disabled="disabled" />
            <span><strong>{{ methodLabels[method] }}</strong><small>{{ descriptions[method] }}</small></span>
          </label>
          <p v-if="!methods.length" class="policy-invalid" role="alert">至少保留一种合并方式。</p>
        </fieldset>
        <fieldset :disabled="disabled" class="policy-group">
          <legend>合并前的要求</legend>
          <div class="approval-row">
            <AppInput :model-value="approvals" @update:model-value="approvals = String($event)" type="number"
              label="需要的批准数" min="0" max="10" step="1" required :disabled="disabled" />
            <span class="muted">0 表示不要求批准</span>
          </div>
          <label class="policy-option"><input v-model="stale" type="checkbox" :disabled="disabled" /><span>新提交需要重新批准</span></label>
          <label class="policy-option"><input v-model="resolved" type="checkbox" :disabled="disabled" /><span>合并前需要解决所有讨论</span></label>
          <AppTextarea v-model="checks" label="必要检查（可选，每行一个）" rows="3" :disabled="disabled"
            hint="尚未上报或未通过的必要检查会阻止合并。" />
        </fieldset>
      </div>
      <div v-if="!repository.archived" class="policy-footer">
        <div class="actions">
          <AppButton type="submit" :busy="busy" :disabled="!changed || !methods.length">保存合并规则</AppButton>
          <AppButton v-if="changed" variant="secondary" :disabled="busy" @click="cancel">取消修改</AppButton>
          <span v-if="changed" class="muted">有未保存的修改</span>
        </div>
        <DraftStatus :message="draft.message.value" />
      </div>
    </form>
  </AppPanel>
</template>

<style scoped>
.policy-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 32px; }
.policy-group { margin: 0; padding: 0; min-width: 0; border: 0; }
.policy-group legend { font-size: 14px; font-weight: 600; margin-bottom: 16px; padding: 0; }
.policy-option { display: flex; align-items: flex-start; gap: 10px; margin: 0 0 18px; font-size: 14px; cursor: pointer; }
.policy-option input { flex: 0 0 auto; width: 16px; height: 16px; min-height: 0; margin: 3px 0 0; padding: 0; accent-color: var(--accent); }
.policy-option strong { font-weight: 500; }
.policy-option small { display: block; margin-top: 5px; font-weight: 400; }
.approval-row { display: flex; align-items: end; gap: 12px; margin-bottom: 20px; }
.approval-row .form-field { width: 140px; margin: 0; }
.approval-row > span { padding-bottom: 12px; font-size: 12px; }
.policy-group > .form-field { margin-bottom: 0; }
.policy-footer { margin-top: 24px; padding-top: 20px; border-top: 1px solid var(--line); display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.policy-footer :deep(.draft-status) { margin: 0; }
.policy-footer .muted { font-size: 13px; }
.policy-invalid { color: var(--red); }
</style>
