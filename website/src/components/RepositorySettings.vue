<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { auth } from '../auth'
import { api, errorText } from '../api'
import type { MemberCandidate, Repository } from '../repositories'
import AppButton from './AppButton.vue'
import AppConfirm from './AppConfirm.vue'
import AppInput from './AppInput.vue'
import AppNotice from './AppNotice.vue'
import AppPanel from './AppPanel.vue'

const props = defineProps<{ repository: Repository }>()
const emit = defineEmits<{ updated: [repository: Repository, message: string]; deleted: [] }>()
const path = computed(() => `/repositories/${props.repository.id}`)
const action = ref<'transfer' | 'delete'>(), busy = ref(false), error = ref('')
const query = ref(''), candidates = ref<MemberCandidate[]>([]), selected = ref<MemberCandidate>()
const searching = ref(false), searched = ref(false), confirmName = ref('')
const confirmation = ref<InstanceType<typeof AppConfirm>>()
const confirmed = computed(() => confirmName.value === props.repository.full_name)

function toggle(next: 'transfer' | 'delete') {
  action.value = action.value === next ? undefined : next
  error.value = confirmName.value = query.value = ''
  selected.value = undefined
  candidates.value = []
  searched.value = false
}
async function search() {
  const current = ++searches, life = lifetime
  searching.value = true
  error.value = ''
  selected.value = undefined
  candidates.value = []
  try {
    const result = await api<MemberCandidate[]>(`${path.value}/transfer-candidates?q=${encodeURIComponent(query.value.trim())}`)
    if (current === searches && life === lifetime) { candidates.value = result; searched.value = true }
  } catch (cause) { if (current === searches && life === lifetime) error.value = errorText(cause) }
  finally { if (current === searches && life === lifetime) searching.value = false }
}
let lifetime = 0, searches = 0
onUnmounted(() => { lifetime++; searches++ })
watch(() => [props.repository.id, auth.user?.id], () => {
  lifetime++; searches++; action.value = undefined; busy.value = false; searching.value = false
  error.value = query.value = confirmName.value = ''; candidates.value = []; selected.value = undefined
})
async function perform(suffix: string, method: string, body: Record<string, unknown>, message: string, title: string, success: string) {
  if (busy.value) return
  const life = lifetime, targetPath = path.value
  busy.value = true; error.value = ''
  let result: Repository | undefined
  try {
    const accepted = await confirmation.value?.askVerified(message, title, '验证并执行', async code => {
      if (life !== lifetime) throw new Error('当前账号或仓库已变化，请重新打开操作')
      result = await api<Repository>(targetPath + suffix, method, { ...body, code })
    }, 'repository_operation')
    if (accepted && life === lifetime) {
      if (method === 'DELETE') emit('deleted')
      else if (result) emit('updated', result, success)
    }
  } finally { if (life === lifetime) busy.value = false }
}
function archive() {
  const archived = !props.repository.archived
  return perform('/archive', 'PUT', { archived }, archived
    ? '归档后所有成员都无法推送代码或镜像，仍可读取。取消归档后将恢复原有读写权限。'
    : '取消归档后，恢复成员原有的 Git 和镜像读写权限。', archived ? '归档仓库？' : '取消归档？',
    archived ? '仓库已归档' : '已取消归档，原有读写权限已恢复')
}
function transfer() {
  if (!selected.value || !confirmed.value || busy.value) return
  return perform('/transfer', 'POST', { new_owner_id: selected.value.id, confirm_name: confirmName.value },
    `将 ${props.repository.full_name} 移交给 @${selected.value.username}？你将保留读写和仓库管理员权限。页面及 Git 地址将改为新拥有者名下，团队需要更新本地 remote 地址。namespace 继续绑定此仓库。`,
    '移交仓库？', '仓库已移交，请使用新的仓库地址')
}
function remove() {
  if (!confirmed.value || busy.value) return
  return perform('', 'DELETE', { confirm_name: confirmName.value },
    `删除 ${props.repository.full_name} 后，所有成员将失去网页、Git 和绑定镜像的访问权限。这项操作无法在页面中撤销。`, '删除仓库？', '')
}
</script>

<template>
  <AppPanel title="仓库设置" description="只有仓库拥有者可以移交、归档或删除仓库，每次操作需验证本人邮箱。" class="repo-settings">
    <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
    <div class="setting-row">
      <div><h3>{{ repository.archived ? '取消归档' : '归档仓库' }}</h3><p>归档期间所有成员只能查看和克隆。取消归档后恢复原有读写权限。</p></div>
      <AppButton variant="secondary" :disabled="busy || searching" @click="archive">{{ repository.archived ? '取消归档' : '归档仓库' }}</AppButton>
    </div>
    <div class="setting-row">
      <div><h3>移交仓库</h3><p>将所有权交给其他同事。你将保留读写与仓库管理员权限，团队成员保持不变。</p></div>
      <AppButton variant="secondary" :disabled="busy || searching" :aria-expanded="action === 'transfer'" aria-controls="transfer-repository" @click="toggle('transfer')">{{ action === 'transfer' ? '取消移交' : '移交仓库' }}</AppButton>
    </div>
    <div v-if="action === 'transfer'" id="transfer-repository" class="setting-form">
      <form class="candidate-search" @submit.prevent="search">
        <AppInput v-model="query" label="查找新拥有者" placeholder="用户名或姓名，至少 2 个字符" required minlength="2" maxlength="100" :disabled="busy || searching" />
        <AppButton type="submit" variant="secondary" :busy="searching" :disabled="busy">查找同事</AppButton>
      </form>
      <ul v-if="candidates.length" class="candidate-list" aria-label="新拥有者搜索结果">
        <li v-for="candidate in candidates" :key="candidate.id">
          <span>{{ candidate.display_name }} <span class="muted">@{{ candidate.username }}</span></span>
          <AppButton variant="secondary" :disabled="busy" :aria-pressed="selected?.id === candidate.id" @click="selected = candidate">{{ selected?.id === candidate.id ? '已选择' : '选择' }}</AppButton>
        </li>
      </ul>
      <p v-else-if="searched" class="muted">没有找到其他有效用户。</p>
      <form v-if="selected" @submit.prevent="transfer">
        <p>新拥有者：<strong>{{ selected.display_name }} · @{{ selected.username }}</strong></p>
        <p class="muted">新仓库名：{{ selected.username }}/{{ repository.name }}。移交后需更新本地 Git remote 地址。</p>
        <AppInput v-model="confirmName" label="输入当前完整仓库名以确认移交" :placeholder="repository.full_name" :hint="`请输入 ${repository.full_name}`" autocomplete="off" :disabled="busy" />
        <AppButton type="submit" variant="danger" :busy="busy" :disabled="!confirmed">确认移交</AppButton>
      </form>
    </div>
    <div class="setting-row">
      <div><h3>删除仓库</h3><p>从工程仓库中移除，所有成员失去网页和 Git 访问权限。无法在页面中撤销。</p></div>
      <AppButton variant="danger" :disabled="busy || searching" :aria-expanded="action === 'delete'" aria-controls="delete-repository" @click="toggle('delete')">{{ action === 'delete' ? '取消删除' : '删除仓库' }}</AppButton>
    </div>
    <form v-if="action === 'delete'" id="delete-repository" class="setting-form" @submit.prevent="remove">
      <AppInput v-model="confirmName" label="输入完整仓库名以确认删除" :placeholder="repository.full_name" :hint="`请输入 ${repository.full_name}`" autocomplete="off" :disabled="busy" />
      <AppButton type="submit" variant="danger" :busy="busy" :disabled="!confirmed">确认删除此仓库</AppButton>
    </form>
  </AppPanel>
  <AppConfirm :key="`${repository.id}-${auth.user?.id}`" ref="confirmation" />
</template>

<style scoped>
.setting-row { display: flex; justify-content: space-between; align-items: center; gap: 24px; padding: 22px 0; border-top: 1px solid var(--line); }
.setting-row:last-child { padding-bottom: 0; }
.setting-row h3 { margin: 0 0 6px; }
.setting-row p { margin: 0; color: var(--muted); }
.setting-row .button { flex-shrink: 0; }
.setting-form { padding: 20px; margin: 0 0 22px; border: 1px solid var(--line); border-radius: 8px; background: var(--input); overflow-wrap: anywhere; }
.candidate-search { display: flex; align-items: flex-end; gap: 12px; }
.candidate-search > :first-child { flex: 1; margin: 0; }
.candidate-list { list-style: none; padding: 0; }
.candidate-list li { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--line); }
@media (max-width: 720px) {
  .setting-row, .candidate-search { flex-direction: column; align-items: stretch; gap: 14px; }
}
</style>
