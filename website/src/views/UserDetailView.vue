<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api, errorText } from '../api'
import type { Role, User } from '../api'
import { auth, forgetLogin } from '../auth'
import AppBadge from '../components/AppBadge.vue'
import AppButton from '../components/AppButton.vue'
import AppConfirm from '../components/AppConfirm.vue'
import AppInput from '../components/AppInput.vue'
import AppNotice from '../components/AppNotice.vue'
import AppPanel from '../components/AppPanel.vue'
import AppSelect from '../components/AppSelect.vue'
import SessionList from '../components/SessionList.vue'

const route = useRoute(),
  router = useRouter()
const id = Number(route.params.id)
const user = ref<User | null>(null),
  roles = ref<Role[]>([]),
  selectedRoles = ref<string[]>([])
const busy = ref(false),
  error = ref(''),
  message = ref('')
const confirmation = ref<InstanceType<typeof AppConfirm>>()
const form = reactive({ username: '', display_name: '', email: '', phone: '', status: 'active' })
const isAdmin = computed(() => user.value?.roles.includes('admin'))
const statuses = [
  { value: 'active', label: '正常' },
  { value: 'disabled', label: '停用' },
]
async function load() {
  busy.value = true
  error.value = ''
  try {
    const [data, available] = await Promise.all([api<User>(`/users/${id}`), api<Role[]>('/roles')])
    user.value = data
    roles.value = available
    selectedRoles.value = data.roles.filter((code) => code !== 'admin')
    Object.assign(form, {
      username: data.username,
      display_name: data.display_name,
      email: data.email,
      phone: data.phone ?? '',
      status: data.status,
    })
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
async function save() {
  if (
    form.status === 'disabled' &&
    user.value?.status === 'active' &&
    !(await confirmation.value?.ask('停用后，该成员的所有设备将立即退出。', '停用成员？'))
  )
    return
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    user.value = await api<User>(`/users/${id}`, 'PATCH', { ...form, phone: form.phone || null })
    if (id === auth.user?.id && user.value.status === 'disabled') {
      forgetLogin('账号已停用')
      await router.replace('/login')
      return
    }
    if (id === auth.user?.id) auth.user = user.value
    message.value = '成员资料已保存'
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
async function saveRoles() {
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    user.value = await api<User>(`/users/${id}/roles`, 'PUT', { roles: selectedRoles.value })
    if (id === auth.user?.id) auth.user = user.value
    message.value = '角色已更新，后续请求立即生效'
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
async function clearPassword() {
  if (
    !(await confirmation.value?.ask(
      '清除后，该成员需自行验证邮箱才能设置新的兼容密码。此操作不会退出其登录设备。',
      '清除成员的兼容密码？',
    ))
  )
    return
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    const result = await api<{ message: string; user: User }>(
      `/users/${id}/legacy-password`,
      'PUT',
      { password: null },
    )
    user.value = result.user
    if (id === auth.user?.id) auth.user = user.value
    message.value = result.message
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
onMounted(load)
</script>

<template>
  <RouterLink class="text-link back-link" to="/users">← 返回人员列表</RouterLink>
  <div class="page-heading">
    <p class="eyebrow">MEMBER DETAILS</p>
    <h1>{{ user?.display_name || '人员详情' }}</h1>
    <p>管理账号资料、全局角色与登录设备。</p>
  </div>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  <AppNotice v-model="message" tone="success" />
  <div v-if="busy && !user" class="empty-state">正在读取成员资料…</div>
  <template v-if="user">
    <div class="two-columns">
      <AppPanel title="账号资料">
        <form @submit.prevent="save">
          <AppInput v-model.trim="form.display_name" label="姓名" required maxlength="80" />
          <AppInput
            v-model.trim="form.username"
            label="用户名"
            required
            minlength="3"
            maxlength="32"
            hint="系统内全局唯一的标识名称，3–32 位，以字母开头。"
          />
          <AppInput
            v-model.trim="form.email"
            label="邮箱"
            type="email"
            required
            :readonly="isAdmin"
            :hint="isAdmin ? '管理员需在个人资料中验证并更换邮箱。' : undefined"
          />
          <AppInput v-model="form.phone" label="手机号" type="tel" placeholder="+86.13912345678" />
          <AppSelect v-model="form.status" label="账号状态" :options="statuses" :disabled="busy" />
          <AppButton type="submit" :busy="busy">保存账号资料</AppButton>
        </form>
      </AppPanel>
      <div>
        <AppPanel title="全局角色">
          <p v-if="isAdmin"><AppBadge tone="blue">管理员</AppBadge> 管理员角色由部署人员维护。</p>
          <form @submit.prevent="saveRoles">
            <label
              v-for="role in roles.filter((item) => item.assignable)"
              :key="role.code"
              class="checkbox-label"
            >
              <input v-model="selectedRoles" type="checkbox" :value="role.code" :disabled="busy" />
              <span
                >{{ role.name }}<small>{{ role.description }}</small></span
              >
            </label>
            <AppButton type="submit" variant="secondary" :busy="busy">保存角色</AppButton>
          </form>
        </AppPanel>
        <AppPanel title="兼容密码" description="用于旧系统兼容。本站不支持密码登录。">
          <template #actions
            ><AppBadge :tone="user.has_legacy_password ? 'green' : undefined">{{
              user.has_legacy_password ? '已设置' : '未设置'
            }}</AppBadge></template
          >
          <p>
            管理员只能清除兼容密码，不能查看或代为设置。成员可在「账号安全」中通过邮箱验证码自行设置。
          </p>
          <AppButton
            variant="danger"
            :disabled="busy || !user.has_legacy_password"
            @click="clearPassword"
            >清除兼容密码</AppButton
          >
        </AppPanel>
      </div>
    </div>
    <SessionList :key="`${user.updated_at}-${user.status}`" :user-id="id" />
  </template>
  <AppConfirm ref="confirmation" />
</template>
