<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, dateText, errorText } from '../api'
import AppButton from './AppButton.vue'
import AppConfirm from './AppConfirm.vue'
import AppNotice from './AppNotice.vue'
import AppPanel from './AppPanel.vue'

interface Grant {
  id: number
  client_name: string
  created_at: string
  expires_at: string
  revoked_at: string | null
}
const grants = ref<Grant[]>([])
const busy = ref(false)
const error = ref('')
const confirmation = ref<InstanceType<typeof AppConfirm>>()
const active = (grant: Grant) => !grant.revoked_at && new Date(grant.expires_at).getTime() > Date.now()
async function load() {
  grants.value = (await api<Grant[]>('/me/git-authorizations')).filter(grant => !grant.revoked_at)
}
async function revoke(grant: Grant) {
  if (!(await confirmation.value?.ask('该客户端下次访问时需要重新授权。', '撤销客户端授权？'))) return
  busy.value = true
  error.value = ''
  try {
    await api(`/me/git-authorizations/${grant.id}`, 'DELETE')
    grants.value = grants.value.filter(item => item.id !== grant.id)
    await load()
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
onMounted(async () => {
  try { await load() } catch (cause) { error.value = errorText(cause) }
})
</script>

<template>
  <AppPanel title="Git / CLI 客户端授权">
    <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
    <div v-if="!grants.length" class="empty-state">暂无未撤销的客户端授权</div>
    <ul v-else class="session-list">
      <li v-for="grant in grants" :key="grant.id">
        <div class="session-main">
          <strong class="grant-name">{{ grant.client_name }}</strong>
          <small>仅限你有权限的仓库，权限变更在后续请求中生效。</small>
          <p>{{ active(grant) ? '已授权' : '已过期 · 请从客户端重新授权' }}</p>
          <small>授权 {{ dateText(grant.created_at) }} · 到期 {{ dateText(grant.expires_at) }}</small>
        </div>
        <AppButton v-if="active(grant)" variant="danger" :disabled="busy" @click="revoke(grant)">撤销授权</AppButton>
      </li>
    </ul>
  </AppPanel>
  <AppConfirm ref="confirmation" />
</template>

<style scoped>
.grant-name { display: block; margin-bottom: 8px; }
</style>
