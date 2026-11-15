<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, errorText } from '../api'
import AppButton from '../components/AppButton.vue'
import AppNotice from '../components/AppNotice.vue'
import AppPanel from '../components/AppPanel.vue'

const route = useRoute()
const code = ref(typeof route.query.user_code === 'string' ? route.query.user_code : '')
const request = ref<{ client_name: string; scope: string[] }>()
const error = ref('')
const complete = ref(false)
const busy = ref(false)
const path = () => `/oauth/device-requests/${encodeURIComponent(code.value.trim())}`
async function check() {
  busy.value = true
  error.value = ''
  try { request.value = await api(path()) } catch (cause) { error.value = errorText(cause) }
  finally { busy.value = false }
}
async function decide(approve: boolean) {
  busy.value = true
  error.value = ''
  try {
    await api(`${path()}/decision`, 'POST', { approve })
    complete.value = true
  } catch (cause) { error.value = errorText(cause) }
  finally { busy.value = false }
}
</script>
<template>
  <div class="page-heading"><p class="eyebrow">DEVICE AUTHORIZATION</p><h1>连接 DevOps CLI</h1><p>核对终端中显示的设备代码，再授权这台设备。</p></div>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  <AppNotice v-if="complete" tone="success" :duration="0">已处理，请返回终端查看结果。可以关闭此页面。</AppNotice>
  <AppPanel v-else title="确认设备" class="compact-form">
    <form v-if="!request" @submit.prevent="check">
      <label for="device-code">设备代码</label>
      <input id="device-code" v-model="code" required maxlength="9" placeholder="ABCD-EFGH" autocomplete="off" />
      <AppButton :busy="busy" type="submit">核对代码</AppButton>
    </form>
    <template v-else>
      <h2>{{ code.toUpperCase() }}</h2>
      <p>仅授权你刚刚主动启动的 {{ request.client_name }}。不要输入别人发给你的设备代码。</p>
      <ul>
        <li v-if="request.scope.includes('api:read')">读取你有权限的仓库、Issue 和 Pull request</li>
        <li v-if="request.scope.includes('api:write')">按你的仓库权限创建、编辑和管理协作内容</li>
      </ul>
      <p>授权有效期为 30 天，可在「账号安全」中撤销。</p>
      <div class="actions">
        <AppButton :busy="busy" @click="decide(true)">允许访问</AppButton>
        <AppButton variant="secondary" :disabled="busy" @click="decide(false)">取消授权</AppButton>
      </div>
    </template>
  </AppPanel>
</template>
