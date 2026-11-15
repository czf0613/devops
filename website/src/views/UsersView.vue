<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { api, dateText, errorText } from '../api'
import type { Page, User } from '../api'
import Pager from '../components/Pager.vue'
import AppBadge from '../components/AppBadge.vue'
import AppButton from '../components/AppButton.vue'
import AppInput from '../components/AppInput.vue'
import AppNotice from '../components/AppNotice.vue'
import AppPanel from '../components/AppPanel.vue'
import AppSelect from '../components/AppSelect.vue'

const rows = ref<User[]>([]),
  total = ref(0),
  page = ref(1),
  busy = ref(false),
  error = ref('')
const query = ref(''),
  status = ref(''),
  creating = ref(false)
const form = reactive({ email: '', username: '', display_name: '', phone: '' })
const router = useRouter()
const statuses = [
  { value: '', label: '全部状态' },
  { value: 'active', label: '正常' },
  { value: 'disabled', label: '已停用' },
]
async function load(next = 1) {
  busy.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({ page: String(next), q: query.value })
    if (status.value) params.set('status', status.value)
    const result = await api<Page<User>>(`/users?${params}`)
    rows.value = result.items
    total.value = result.total
    page.value = next
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
async function create() {
  busy.value = true
  error.value = ''
  try {
    const user = await api<User>('/users', 'POST', { ...form, phone: form.phone || null })
    await router.push(`/users/${user.id}`)
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
onMounted(() => load())
</script>

<template>
  <div class="page-heading heading-row">
    <div>
      <p class="eyebrow">PEOPLE</p>
      <h1>人员管理</h1>
      <p>管理团队成员的账号、联系信息与登录状态。</p>
    </div>
    <AppButton @click="creating = !creating">
      {{ creating ? '收起表单' : '＋ 添加成员' }}
    </AppButton>
  </div>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  <AppPanel
    v-if="creating"
    title="添加普通成员"
    description="创建后，成员可通过填写的邮箱接收验证码登录。"
  >
    <form @submit.prevent="create">
      <div class="form-grid">
        <AppInput v-model.trim="form.display_name" label="姓名" required maxlength="80" />
        <AppInput
          v-model.trim="form.username"
          label="用户名"
          required
          minlength="3"
          maxlength="32"
          hint="系统内全局唯一的标识名称，3–32 位，以字母开头。"
        />
        <AppInput v-model.trim="form.email" label="邮箱" type="email" required />
        <AppInput
          v-model="form.phone"
          label="手机号（选填）"
          type="tel"
          placeholder="+86.13912345678"
        />
      </div>
      <AppButton type="submit" :busy="busy">创建成员</AppButton>
    </form>
  </AppPanel>
  <AppPanel class="table-panel">
    <form class="toolbar" @submit.prevent="load()">
      <AppInput
        v-model.trim="query"
        aria-label="搜索人员"
        placeholder="搜索姓名、用户名或邮箱"
        maxlength="100"
      />
      <AppSelect v-model="status" label="账号状态" hide-label :options="statuses" />
      <AppButton type="submit" variant="secondary" :busy="busy">搜索</AppButton>
    </form>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>成员</th>
            <th>联系方式</th>
            <th>角色</th>
            <th>状态</th>
            <th>加入时间</th>
            <th><span class="sr-only">操作</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="user in rows" :key="user.id">
            <td>
              <RouterLink class="person-cell" :to="`/users/${user.id}`"
                ><span class="avatar">{{ user.display_name.slice(0, 1) }}</span>
                <div>
                  <strong>{{ user.display_name }}</strong
                  ><small>{{ user.username }}</small>
                </div></RouterLink
              >
            </td>
            <td>
              {{ user.email }}<small>{{ user.phone || '未填写手机号' }}</small>
            </td>
            <td>
              <AppBadge :tone="user.roles.includes('admin') ? 'blue' : undefined">{{
                user.roles.includes('admin') ? '管理员' : '成员'
              }}</AppBadge>
            </td>
            <td>
              <AppBadge :tone="user.status === 'active' ? 'green' : undefined">{{
                user.status === 'active' ? '正常' : '已停用'
              }}</AppBadge>
            </td>
            <td class="muted">{{ dateText(user.created_at) }}</td>
            <td><RouterLink class="text-link" :to="`/users/${user.id}`">管理</RouterLink></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="!rows.length" class="empty-state">
      {{ busy ? '正在读取成员…' : '没有找到符合条件的成员' }}
    </div>
    <Pager :page="page" :total="total" :busy="busy" @change="load" />
  </AppPanel>
</template>
