<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import type { MemberSettings } from '../repositories'
import AppButton from './AppButton.vue'
import AppInput from './AppInput.vue'
import AppSelect from './AppSelect.vue'

const props = withDefaults(defineProps<{
  value: MemberSettings
  allowAdmin: boolean
  busy: boolean
  submitLabel?: string
}>(), { submitLabel: '保存成员设置' })
const emit = defineEmits<{ submit: [value: MemberSettings] }>()
const form = reactive({ ...props.value })
const roles = ['产品经理', '项目经理', '开发人员', '测试人员', '设计师', '运维人员']
const roleOptions = [
  { value: '', label: '未指定' },
  ...roles.map(role => ({ value: role, label: role })),
  { value: 'custom', label: '自定义岗位…' },
]
const roleChoice = ref('')
const customRole = ref('')
watch(() => props.value, (value) => {
  Object.assign(form, value)
  roleChoice.value = !value.role || roles.includes(value.role) ? value.role : 'custom'
  customRole.value = roleChoice.value === 'custom' ? value.role : ''
}, { immediate: true })
const level = computed({
  get: () => form.is_admin ? 'admin' : 'member',
  set: (value: string) => { form.is_admin = value === 'admin' },
})
const accessOptions = [{ value: 'read', label: '只读 · clone / pull' }, { value: 'write', label: '读写 · clone / pull / push' }]
const levelOptions = [{ value: 'member', label: '普通成员' }, { value: 'admin', label: '仓库管理员' }]
function submit() {
  emit('submit', {
    access: form.access, role: roleChoice.value === 'custom' ? customRole.value.trim() : roleChoice.value,
    ...(props.allowAdmin ? { is_admin: Boolean(form.is_admin) } : {}),
  })
}
</script>

<template>
  <form @submit.prevent="submit">
    <div class="form-grid">
      <AppSelect v-model="form.access" label="仓库读写权限" :options="accessOptions" :disabled="busy" />
      <div class="form-field role-field">
        <AppSelect v-model="roleChoice" label="岗位角色" :options="roleOptions" :disabled="busy" />
        <AppInput v-if="roleChoice === 'custom'" v-model="customRole" label="自定义岗位角色" placeholder="输入岗位名称" maxlength="50" :disabled="busy" />
        <small>角色用于协作分工，不影响读写权限。</small>
      </div>
      <AppSelect v-if="allowAdmin" v-model="level" label="管理权限" :options="levelOptions" :disabled="busy" />
    </div>
    <div class="actions">
      <AppButton type="submit" :busy="busy">{{ submitLabel }}</AppButton>
      <slot name="actions" />
    </div>
  </form>
</template>

<style scoped>
.role-field > .form-field { margin: 0; }
.role-field > .form-field + .form-field { margin-top: 12px; }
</style>
