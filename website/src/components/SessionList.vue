<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, dateText, errorText } from '../api'
import type { Page, Session } from '../api'
import { forgetLogin } from '../auth'
import Pager from './Pager.vue'
import AppBadge from './AppBadge.vue'
import AppButton from './AppButton.vue'
import AppConfirm from './AppConfirm.vue'
import AppNotice from './AppNotice.vue'
import AppPanel from './AppPanel.vue'

const props = defineProps<{ userId?: number; fill?: boolean }>()
const scrollArea = ref<HTMLElement>()
const base = computed(() => (props.userId ? `/users/${props.userId}/sessions` : '/me/sessions'))
const rows = ref<Session[]>([]),
  total = ref(0),
  page = ref(1),
  busy = ref(false),
  error = ref('')
const router = useRouter()
const confirmation = ref<InstanceType<typeof AppConfirm>>()
async function load(next = page.value) {
  busy.value = true
  error.value = ''
  try {
    const result = await api<Page<Session>>(`${base.value}?page=${next}`)
    rows.value = result.items
    total.value = result.total
    page.value = Math.min(next, Math.max(1, Math.ceil(result.total / result.page_size)))
    if (page.value < next) {
      await load(page.value)
      return
    }
    await nextTick()
    if (scrollArea.value) scrollArea.value.scrollTop = 0
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
async function revoke(session?: Session) {
  if (
    !(await confirmation.value?.ask(
      session ? '该设备需要重新验证邮箱才能登录。' : '该账号的所有设备都需要重新验证邮箱才能登录。',
      session ? '退出这个设备？' : '退出全部设备？',
    ))
  )
    return
  busy.value = true
  error.value = ''
  try {
    await api(`${base.value}${session ? `/${session.id}` : ''}`, 'DELETE')
    if (
      session?.current ||
      (!session && !props.userId) ||
      (!session && rows.value.some((row) => row.current))
    ) {
      forgetLogin('会话已退出，请重新登录')
      await router.replace('/login')
    } else {
      rows.value = session ? rows.value.filter((row) => row.id !== session.id) : []
      total.value = session ? Math.max(0, total.value - 1) : 0
      await load()
    }
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
onMounted(() => load())
</script>

<template>
  <AppPanel title="登录设备" description="查看当前有效的登录设备，随时退出不再使用的设备。" class="session-panel" :class="{ 'session-panel-fill': fill }">
    <template #actions
      ><AppButton variant="danger" :disabled="busy || !total" @click="revoke()"
        >退出全部设备</AppButton
      ></template
    >
    <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
    <div ref="scrollArea" class="session-scroll" role="region" aria-label="登录设备列表" tabindex="0" :aria-busy="busy">
    <div v-if="busy && !rows.length" class="empty-state">正在读取登录设备…</div>
    <div v-else-if="!rows.length" class="empty-state">暂无有效登录设备</div>
    <ul v-else class="session-list">
      <li v-for="session in rows" :key="session.id">
        <div class="session-main">
          <div class="actions">
            <strong>{{ session.login_ip || '未知 IP' }}</strong
            ><AppBadge v-if="session.current" tone="blue">当前设备</AppBadge
            ><AppBadge :tone="session.active ? 'green' : undefined">{{
              session.active ? '有效' : session.revoked_at ? '已退出' : '已失效'
            }}</AppBadge>
          </div>
          <p class="user-agent">{{ session.user_agent || '未知客户端' }}</p>
          <small
            >登录 {{ dateText(session.created_at) }} · 最近活动 {{ dateText(session.last_seen_at)
            }}<br />到期 {{ dateText(session.expires_at) }}</small
          >
        </div>
        <AppButton
          v-if="session.active"
          variant="danger"
          class="small"
          :disabled="busy"
          @click="revoke(session)"
        >
          退出设备
        </AppButton>
      </li>
    </ul>
    </div>
    <Pager :page="page" :total="total" :busy="busy" @change="load" />
  </AppPanel>
  <AppConfirm ref="confirmation" />
</template>

<style scoped>
.session-panel { display: flex; flex-direction: column; }
.session-panel-fill { position: absolute; inset: 0; margin: 0; }
.session-panel :deep(.section-heading), .session-panel :deep(.pager) { flex-shrink: 0; }
.session-panel :deep(.section-heading) { align-items: flex-start; }
.session-scroll { min-height: 0; max-height: 400px; overflow-y: auto; overscroll-behavior-y: contain; scrollbar-gutter: stable; }
.session-panel-fill .session-scroll { flex: 1; max-height: none; }
.session-scroll:focus-visible { outline: 2px solid var(--accent); outline-offset: 4px; }
.session-panel :deep(.pager) { border-top: 1px solid var(--line); padding: 18px 0 0; }
.session-list li:last-child { border-bottom: 0; }
</style>
