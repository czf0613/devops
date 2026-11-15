<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, errorText } from '../api'
import { auth } from '../auth'
import AppButton from '../components/AppButton.vue'
import AppNotice from '../components/AppNotice.vue'
import AppPanel from '../components/AppPanel.vue'

interface AuthorizationRequest {
  client_name: string
  scope: string[]
  redirect_uri: string
}
const route = useRoute()
const request = ref<AuthorizationRequest>()
const error = ref('')
const busy = ref(false)
const complete = ref(false)
const requestId = typeof route.query.request === 'string' ? route.query.request : ''
const path = `/oauth/authorization-requests/${encodeURIComponent(requestId)}`

onMounted(async () => {
  try {
    if (!/^[A-Za-z0-9_-]{43}$/.test(requestId)) throw new Error('请从客户端重新发起授权')
    request.value = await api<AuthorizationRequest>(path)
  } catch (cause) {
    error.value = errorText(cause)
  }
})

async function decide(approve: boolean) {
  busy.value = true
  error.value = ''
  try {
    const result = await api<{ redirect_uri: string }>(`${path}/decision`, 'POST', { approve })
    complete.value = true
    window.location.assign(result.redirect_uri)
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page-heading">
    <p class="eyebrow">GIT AUTHORIZATION</p>
    <h1>连接{{ request?.client_name === 'DevOps CLI' ? ' DevOps CLI' : ' Git 客户端' }}</h1>
    <p>以 @{{ auth.user?.username }} 的身份授权本机上的 {{ request?.client_name || '客户端' }}。</p>
  </div>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  <AppNotice v-if="complete" tone="success" :duration="0">正在返回客户端；完成后可以关闭此页面。</AppNotice>
  <AppPanel v-else-if="request" class="compact-form" :title="request.client_name">
    <p>允许此客户端访问你有权限的仓库。每次操作都会检查你的当前成员权限。</p>
    <ul>
      <li v-if="request.scope.includes('git:read')">读取仓库：clone、fetch、pull</li>
      <li v-if="request.scope.includes('git:write')">写入仓库：push</li>
      <li v-if="request.scope.includes('api:read')">读取你有权限的仓库、Issue 和 Pull request</li>
      <li v-if="request.scope.includes('api:write')">按你的仓库权限创建、编辑和管理协作内容</li>
    </ul>
    <p>授权有效期为 30 天，可在「账号安全」中随时撤销。</p>
    <p class="muted">返回本机：{{ request.redirect_uri }}</p>
    <div class="actions">
      <AppButton :busy="busy" @click="decide(true)">允许访问</AppButton>
      <AppButton variant="secondary" :disabled="busy" @click="decide(false)">取消授权</AppButton>
    </div>
  </AppPanel>
</template>
