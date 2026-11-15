<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, dateText, errorText } from '../api'
import type { AuditEvent, Page } from '../api'
import Pager from '../components/Pager.vue'
import AppBadge from '../components/AppBadge.vue'
import AppButton from '../components/AppButton.vue'
import AppNotice from '../components/AppNotice.vue'
import AppPanel from '../components/AppPanel.vue'
import AppSelect from '../components/AppSelect.vue'

const rows = ref<AuditEvent[]>([]),
  total = ref(0),
  page = ref(1),
  busy = ref(false),
  error = ref(''),
  action = ref('')
const actions: Record<string, string> = {
  'request.rejected': '操作被拒绝',
  'auth.login': '登录',
  'auth.logout': '退出登录',
  'auth.code_requested': '申请验证码',
  'auth.code_verified': '校验验证码',
  'user.registered': '注册账号',
  'user.created': '创建成员',
  'user.profile_updated': '修改个人资料',
  'user.updated': '修改账号',
  'user.email_changed': '更换邮箱',
  'user.roles_updated': '调整角色',
  'user.legacy_password_changed': '修改兼容密码',
  'user.legacy_password_reset': '重设兼容密码（历史）',
  'user.legacy_password_cleared': '清除兼容密码',
  'session.revoked': '退出设备',
  'session.all_revoked': '退出全部设备',
  'repository.created': '创建仓库',
  'repository.updated': '修改仓库简介',
  'repository.member_added': '添加仓库成员',
  'repository.member_updated': '修改仓库成员',
  'repository.member_removed': '移除仓库成员',
  'repository.transferred': '移交仓库',
  'repository.archived': '归档仓库',
  'repository.unarchived': '取消仓库归档',
  'repository.deleted': '删除仓库',
}
const actionOptions = [
  { value: '', label: '全部操作' },
  ...Object.entries(actions).map(([value, label]) => ({ value, label })),
]
async function load(next = 1) {
  busy.value = true
  error.value = ''
  try {
    const result = await api<Page<AuditEvent>>(
      `/audit-events?${new URLSearchParams({ page: String(next), action: action.value })}`,
    )
    rows.value = result.items
    total.value = result.total
    page.value = next
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
onMounted(() => load())
</script>

<template>
  <div class="page-heading">
    <p class="eyebrow">ACTIVITY LOG</p>
    <h1>操作记录</h1>
    <p>追踪关键账号操作与登录活动。</p>
  </div>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  <AppPanel class="table-panel">
    <form class="toolbar" @submit.prevent="load()">
      <AppSelect v-model="action" label="操作类型" hide-label :options="actionOptions" />
      <AppButton type="submit" variant="secondary" :busy="busy">筛选</AppButton>
    </form>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>操作</th>
            <th>操作人</th>
            <th>目标</th>
            <th>结果</th>
            <th>详情</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="event in rows" :key="event.id">
            <td>{{ dateText(event.created_at) }}</td>
            <td>{{ actions[event.action] || event.action }}</td>
            <td>{{ event.actor_user_id ? `用户 #${event.actor_user_id}` : '访客' }}</td>
            <td>
              {{
                event.target_id
                  ? `${event.target_type === 'repository' ? '仓库' : event.target_type === 'user' ? '用户' : '会话'} #${event.target_id}`
                  : '—'
              }}
            </td>
            <td>
              <AppBadge :tone="event.result === 'success' ? 'green' : 'red'">{{
                event.result === 'success' ? '成功' : '失败'
              }}</AppBadge>
            </td>
            <td>
              <details>
                <summary class="text-link">查看</summary>
                <pre>{{ JSON.stringify(event.details, null, 2) }}</pre>
                <small class="request-id">请求 {{ event.request_id }}</small>
              </details>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="!rows.length" class="empty-state">
      {{ busy ? '正在读取记录…' : '暂无符合条件的记录' }}
    </div>
    <Pager :page="page" :total="total" :busy="busy" @change="load" />
  </AppPanel>
</template>
