<script setup lang="ts">
import { nextTick, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { api, errorText } from '../api'
import { auth } from '../auth'
import type { AdminRepositoryDetail, MemberCandidate, MemberSettings, RepositoryMember } from '../repositories'
import AppBadge from '../components/AppBadge.vue'
import AppButton from '../components/AppButton.vue'
import AppConfirm from '../components/AppConfirm.vue'
import AppInput from '../components/AppInput.vue'
import AppNotice from '../components/AppNotice.vue'
import AppPanel from '../components/AppPanel.vue'
import RepositoryMemberForm from '../components/RepositoryMemberForm.vue'

const route = useRoute(), repo = ref<AdminRepositoryDetail>()
const error = ref(''), message = ref(''), busy = ref(false), loading = ref(false)
const editing = ref<RepositoryMember>(), mode = ref<'member' | 'transfer'>()
const query = ref(''), candidates = ref<MemberCandidate[]>([]), selected = ref<MemberCandidate>()
const searching = ref(false), searched = ref(false), searchError = ref('')
const confirmation = ref<InstanceType<typeof AppConfirm>>()
const transferEditor = ref<HTMLElement>()
let lifetime = 0, reads = 0, searches = 0
onUnmounted(() => { lifetime++; reads++; searches++ })
watch(() => [route.params.id, auth.user?.id], () => {
  lifetime++; repo.value = undefined; error.value = message.value = ''; busy.value = false
  resetEditor(); void load()
}, { immediate: true })
function resetEditor() { editing.value = undefined; mode.value = undefined; candidates.value = []; selected.value = undefined; query.value = ''; searched.value = false; searching.value = false; searchError.value = ''; searches++ }
async function load() {
  const current = ++reads, life = lifetime
  loading.value = true; error.value = ''
  try {
    const result = await api<AdminRepositoryDetail>(`/admin/repositories/${route.params.id}`)
    if (current === reads && life === lifetime) { repo.value = result; resetEditor() }
  } catch (cause) { if (current === reads && life === lifetime) { error.value = errorText(cause); repo.value = undefined } }
  finally { if (current === reads && life === lifetime) loading.value = false }
}
async function perform(suffix: string, method: string, body: Record<string, unknown>, title: string, description: string, success: string) {
  if (busy.value || !repo.value || repo.value.deleted_at) return
  const current = repo.value, life = lifetime
  busy.value = true; error.value = ''; message.value = ''
  let result: AdminRepositoryDetail | undefined
  try {
    const accepted = await confirmation.value?.askVerified(description, title, '验证并执行', async code => {
      if (life !== lifetime) throw new Error('当前账号或仓库已变化，请刷新后重试')
      result = await api<AdminRepositoryDetail>(`/admin/repositories/${current.id}${suffix}`, method, { ...body, expected_updated_at: current.updated_at, code })
    })
    if (accepted && result && life === lifetime) { repo.value = result; resetEditor(); message.value = success }
  } finally { if (life === lifetime) busy.value = false }
}
async function chooseMode(next: 'member' | 'transfer') {
  resetEditor(); mode.value = next
  if (next === 'transfer') { await nextTick(); transferEditor.value?.querySelector('input')?.focus() }
}
async function search() {
  if (!repo.value || !mode.value || busy.value) return
  const current = ++searches, life = lifetime
  selected.value = undefined; candidates.value = []; searchError.value = ''; searching.value = true
  try {
    const result = await api<MemberCandidate[]>(`/admin/repositories/${repo.value.id}/candidates?${new URLSearchParams({ q: query.value, purpose: mode.value })}`)
    if (current === searches && life === lifetime) { candidates.value = result; searched.value = true }
  } catch (cause) { if (current === searches && life === lifetime) searchError.value = errorText(cause) }
  finally { if (current === searches && life === lifetime) searching.value = false }
}
function edit(member: RepositoryMember) { resetEditor(); editing.value = member }
function saveMember(settings: MemberSettings) {
  const member = editing.value, candidate = selected.value
  if (!repo.value || (!member && !candidate)) return
  const name = member?.username ?? candidate!.username
  void perform(member ? `/members/${member.user_id}` : '/members', member ? 'PATCH' : 'POST', {
    ...settings, ...(!member ? { user_id: candidate!.id } : {}),
  }, member ? '强制修改成员权限？' : '强制添加成员？',
  `仓库：${repo.value.full_name}\n成员：@${name}\n读写权限：${settings.access === 'write' ? '读写' : '只读'}\n管理权限：${settings.is_admin ? '仓库管理员' : '普通成员'}\n岗位：${settings.role || '未指定'}\n\n修改将影响后续 Git 和所绑定 Registry 镜像请求。归档期间仍禁止写入。`, '成员设置已更新')
}
function removeMember(member: RepositoryMember) {
  void perform(`/members/${member.user_id}`, 'DELETE', {}, '强制移除成员？', `从 ${repo.value!.full_name} 移除 @${member.username}。该成员将失去此仓库及其绑定 namespace 的后续访问权限。`, '成员已移除')
}
function transfer() {
  if (!selected.value || !repo.value) return
  void perform('/transfer', 'POST', { new_owner_id: selected.value.id, confirm_name: repo.value.full_name }, '强制移交仓库？',
    `将 ${repo.value.full_name} 移交给 @${selected.value.username}。原拥有者保留读写和仓库管理员权限，可在移交后继续调整或移除。\n\nGit 地址将改为新拥有者名下，团队需要更新本地 remote。namespace 继续绑定此仓库，其他成员保持不变。`, '仓库已移交，请使用新的 Git 地址')
}
function archive() {
  const archived = !repo.value!.archived
  void perform('/archive', 'PUT', { archived }, archived ? '强制归档仓库？' : '取消归档？',
    `${repo.value!.full_name}\n\n${archived ? '归档后所有成员都无法继续 Git 推送或写入绑定的 Registry，仍可读取。' : '取消归档后恢复成员原有的 Git 和 Registry 读写权限。'}`, archived ? '仓库已归档' : '已取消归档')
}
function remove() {
  void perform('', 'DELETE', { confirm_name: repo.value!.full_name }, '强制删除仓库？',
    `删除 ${repo.value!.full_name} 后，所有成员将失去网页、Git 和绑定 Registry 的后续访问权限。\n\n仓库文件与历史保留，namespace 可在 Registry 管理中重新分配。此操作无法在页面中撤销。`, '仓库已删除')
}
</script>

<template>
  <div class="page-heading"><RouterLink to="/admin/repositories">← 仓库管理</RouterLink><h1>{{ repo?.full_name ?? '仓库管理详情' }}</h1><p>以系统管理员身份调整仓库。每次写操作需要单独验证邮箱。</p></div>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  <AppNotice v-model="message" tone="success" placement="floating" />
  <div class="actions admin-toolbar"><AppButton variant="secondary" :busy="loading" :disabled="busy" @click="load">刷新仓库状态</AppButton><AppBadge v-if="repo">{{ repo.deleted_at ? '已删除' : repo.archived ? '已归档' : '正常' }}</AppBadge></div>
  <template v-if="repo">
    <AppNotice v-if="repo.deleted_at">此仓库已删除，仅展示管理记录，不能继续修改。</AppNotice>
    <AppPanel title="成员与权限" :description="`拥有者：${repo.owner.display_name} · @${repo.owner.username}`">
      <div v-if="!repo.deleted_at" class="actions member-toolbar"><AppButton :disabled="busy || loading" :aria-expanded="mode === 'member'" aria-controls="admin-add-member" @click="mode === 'member' ? resetEditor() : chooseMode('member')">{{ mode === 'member' ? '取消添加' : '添加成员' }}</AppButton></div>
      <section v-if="mode === 'member' && !repo.deleted_at" id="admin-add-member" class="admin-editor" aria-label="添加仓库成员">
        <h3>添加仓库成员</h3>
      <AppNotice v-if="searchError" tone="error">{{ searchError }}</AppNotice>
      <form @submit.prevent="search"><AppInput v-model.trim="query" :label="mode === 'member' ? '查找成员' : '查找新拥有者'" placeholder="用户名或姓名，至少 2 个字符" minlength="2" maxlength="100" required :disabled="busy"><template #actions><AppButton type="submit" variant="secondary" :busy="searching" :disabled="busy">搜索用户</AppButton></template></AppInput></form>
      <ul class="admin-candidates"><li v-for="candidate in candidates" :key="candidate.id"><span>{{ candidate.display_name }} · @{{ candidate.username }}</span><AppButton variant="secondary" :disabled="busy" :aria-pressed="selected?.id === candidate.id" @click="selected = candidate">{{ selected?.id === candidate.id ? '已选择' : '选择' }}</AppButton></li></ul>
      <p v-if="searched && !candidates.length" class="muted">没有符合条件的有效用户。</p>
      <template v-if="selected"><p>已选择：{{ selected.display_name }} · @{{ selected.username }}</p><RepositoryMemberForm v-if="mode === 'member'" :key="selected.id" :value="{ access: 'read', role: '', is_admin: false }" :allow-admin="true" :busy="busy" submit-label="确认添加成员" @submit="saveMember" /><AppButton v-else variant="danger" :busy="busy" @click="transfer">确认移交</AppButton></template>
      <AppButton variant="ghost" :disabled="busy" @click="resetEditor">取消</AppButton>
      </section>
      <div class="table-scroll"><table class="admin-members"><colgroup><col class="identity-column"><col class="actions-column"><col><col><col></colgroup><thead><tr><th>成员</th><th>操作</th><th>读写权限</th><th>管理权限</th><th>岗位</th></tr></thead><tbody>
        <template v-for="member in repo.members" :key="member.user_id">
          <tr>
            <td>{{ member.display_name }}<small>@{{ member.username }}{{ member.status === 'disabled' ? ' · 账号已停用' : '' }}</small></td>
            <td>
              <template v-if="!repo.deleted_at">
                <template v-if="member.is_owner"><AppButton variant="secondary" class="small" :disabled="busy || loading" aria-controls="admin-transfer" :aria-expanded="mode === 'transfer'" @click="chooseMode('transfer')">更换拥有者</AppButton><small class="muted">权限由所有权决定</small></template>
                <div v-else class="actions"><AppButton variant="secondary" class="small" :disabled="busy || loading" @click="edit(member)">编辑</AppButton><AppButton variant="danger" class="small" :disabled="busy || loading" @click="removeMember(member)">移除</AppButton></div>
              </template>
              <span v-else class="muted">仅查看</span>
            </td>
            <td>{{ member.access === 'write' ? '读写' : '只读' }}</td><td>{{ member.is_owner ? '拥有者' : member.is_admin ? '仓库管理员' : '普通成员' }}</td><td>{{ member.role || '未指定' }}</td>
          </tr>
          <tr v-if="editing?.user_id === member.user_id"><td colspan="5"><RepositoryMemberForm :value="editing" :allow-admin="true" :busy="busy" @submit="saveMember"><template #actions><AppButton variant="ghost" :disabled="busy" @click="resetEditor">取消编辑</AppButton></template></RepositoryMemberForm></td></tr>
        </template>
      </tbody></table></div>
      <p v-if="!repo.deleted_at" class="muted">拥有者始终具有读写和管理权限。如需降低其权限或移除，请先更换拥有者，再编辑原拥有者的成员设置。</p>
      <p v-if="!repo.deleted_at && repo.members.length === 1" class="muted">当前只有拥有者。添加其他成员后，可在对应行编辑读写权限、管理权限和岗位，或移除成员。</p>
      <p v-if="repo.archived" class="muted">以上为保存的成员权限；归档期间所有成员的实际访问均为只读。</p>
    </AppPanel>
    <AppPanel v-if="!repo.deleted_at" title="仓库生命周期">
      <p>移交保留成员和 namespace 绑定；删除后代码文件及历史保留，所有后续访问被拒绝。</p>
      <div class="actions"><AppButton variant="secondary" :disabled="busy || loading" @click="chooseMode('transfer')">更换拥有者</AppButton><AppButton variant="secondary" :disabled="busy || loading" @click="archive">{{ repo.archived ? '取消归档' : '强制归档' }}</AppButton><AppButton variant="danger" :disabled="busy || loading" @click="remove">强制删除</AppButton></div>
      <section v-if="mode === 'transfer'" id="admin-transfer" ref="transferEditor" class="admin-editor" aria-label="更换仓库拥有者">
        <h3>更换仓库拥有者</h3>
      <AppNotice v-if="searchError" tone="error">{{ searchError }}</AppNotice>
      <form @submit.prevent="search"><AppInput v-model.trim="query" label="查找新拥有者" placeholder="用户名或姓名，至少 2 个字符" minlength="2" maxlength="100" required :disabled="busy"><template #actions><AppButton type="submit" variant="secondary" :busy="searching" :disabled="busy">搜索用户</AppButton></template></AppInput></form>
      <ul class="admin-candidates"><li v-for="candidate in candidates" :key="candidate.id"><span>{{ candidate.display_name }} · @{{ candidate.username }}</span><AppButton variant="secondary" :disabled="busy" :aria-pressed="selected?.id === candidate.id" @click="selected = candidate">{{ selected?.id === candidate.id ? '已选择' : '选择' }}</AppButton></li></ul>
      <p v-if="searched && !candidates.length" class="muted">没有符合条件的有效用户。</p>
      <template v-if="selected"><p>已选择：{{ selected.display_name }} · @{{ selected.username }}</p><AppButton variant="danger" :busy="busy" @click="transfer">确认移交</AppButton></template>
      <AppButton variant="ghost" :disabled="busy" @click="resetEditor">取消</AppButton>
      </section>

    </AppPanel>

  </template>
  <AppConfirm :key="`${route.params.id}-${auth.user?.id}`" ref="confirmation" />
</template>

<style scoped>
.admin-toolbar { margin-bottom: 24px; }
.member-toolbar { margin-bottom: 20px; }
.admin-editor { padding: 20px; margin: 20px 0; border: 1px solid var(--line); border-radius: 8px; background: var(--input); }
.admin-editor h3 { margin-top: 0; }
.page-heading h1 { overflow-wrap: anywhere; }
.admin-members { table-layout: fixed; }
.identity-column { width: 28%; }
.actions-column { width: 24%; }
.admin-members td { white-space: normal; overflow-wrap: anywhere; }
.admin-members .actions { flex-wrap: wrap; }
.admin-candidates { list-style: none; padding: 0; }
.admin-candidates li { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 0; border-bottom: 1px solid var(--line); }
</style>
