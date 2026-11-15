<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, errorText } from '../api'
import type { MemberCandidate, MemberSettings, Repository, RepositoryMember } from '../repositories'
import { repositoryUrl } from '../repositories'
import AppBadge from '../components/AppBadge.vue'
import AppButton from '../components/AppButton.vue'
import AppConfirm from '../components/AppConfirm.vue'
import AppInput from '../components/AppInput.vue'
import AppNotice from '../components/AppNotice.vue'
import AppPanel from '../components/AppPanel.vue'
import AppTextarea from '../components/AppTextarea.vue'
import RepositoryMemberForm from '../components/RepositoryMemberForm.vue'
import RepositorySettings from '../components/RepositorySettings.vue'
import CollaborationSettings from '../components/CollaborationSettings.vue'
import RepositoryProtection from '../components/RepositoryProtection.vue'
import RepositoryCISettings from '../components/RepositoryCISettings.vue'

const route = useRoute()
const router = useRouter()
const props = defineProps<{ repository: Repository }>()
const emit = defineEmits<{ repositoryUpdated: [repository: Repository] }>()
const repo = ref<Repository>(props.repository),
  members = ref<RepositoryMember[]>([])
const path = computed(() => `/repositories/${repo.value?.id}`)
const adding = ref(false)
const error = ref(''),
  notice = ref(''),
  busy = ref(false),
  description = ref('')
const query = ref(''),
  candidates = ref<MemberCandidate[]>([]),
  searched = ref(false)
const selected = ref<MemberCandidate>(),
  searching = ref(false),
  editing = ref<number>()
const confirmation = ref<InstanceType<typeof AppConfirm>>()
const initialMember: MemberSettings = { access: 'read', role: '', is_admin: false }
const cloneUrl = computed(() =>
  repo.value ? new URL(repo.value.clone_path, window.location.origin).href : '',
)
const canEdit = (member: RepositoryMember) =>
  !member.is_owner &&
  Boolean(repo.value?.permissions.can_manage_members) &&
  (!member.is_admin || repo.value?.permissions.is_owner)

async function load() {
  const lookup = repo.value
    ? path.value
    : route.params.id
      ? `/repositories/${route.params.id}`
      : `/repositories/by-name/${encodeURIComponent(String(route.params.owner))}/${encodeURIComponent(String(route.params.name))}`
  const detail = await api<Repository>(lookup)
  repo.value = detail
  emit('repositoryUpdated', detail)
  description.value = detail.description
  members.value = await api<RepositoryMember[]>(`${path.value}/members`)
  if (route.path !== `${repositoryUrl(detail)}/settings`)
    await router.replace({ path: `${repositoryUrl(detail)}/settings`, query: route.query })
}
async function updated(detail: Repository, message: string) {
  repo.value = detail
  emit('repositoryUpdated', detail)
  description.value = detail.description
  notice.value = message
  error.value = ''
  try {
    if (route.path !== `${repositoryUrl(detail)}/settings`) {
      await router.replace({ path: `${repositoryUrl(detail)}/settings`, query: route.query })
      return
    }
    members.value = await api<RepositoryMember[]>(`${path.value}/members`)
  } catch (cause) {
    error.value = errorText(cause)
  }
}
async function change(action: () => Promise<unknown>, message: string) {
  busy.value = true
  error.value = notice.value = ''
  try {
    await action()
    await load()
    editing.value = undefined
    notice.value = message
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
async function search() {
  searching.value = true
  error.value = ''
  selected.value = undefined
  candidates.value = []
  try {
    candidates.value = await api<MemberCandidate[]>(
      `${path.value}/member-candidates?q=${encodeURIComponent(query.value.trim())}`,
    )
    searched.value = true
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    searching.value = false
  }
}
async function addMember(settings: MemberSettings) {
  if (!selected.value) return
  const userId = selected.value.id
  await change(async () => {
    await api(`${path.value}/members`, 'POST', { user_id: userId, ...settings })
    selected.value = undefined
    candidates.value = []
    searched.value = false
    query.value = ''
    adding.value = false
  }, '成员已加入仓库')
}
async function remove(member: RepositoryMember) {
  if (
    !(await confirmation.value?.ask(
      `移除 @${member.username} 后，该用户的后续 Git 请求将失去仓库访问权限。`,
      '移除仓库成员？',
    ))
  )
    return
  await change(() => api(`${path.value}/members/${member.user_id}`, 'DELETE'), '成员已移除')
}
async function copy() {
  try {
    await navigator.clipboard.writeText(cloneUrl.value)
    notice.value = '克隆地址已复制'
  } catch {
    error.value = '复制失败，请选中地址手动复制'
  }
}
onMounted(async () => {
  busy.value = true
  try {
    await load()
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
})
</script>

<template>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  <AppNotice v-model="notice" tone="success" />
  <template v-if="repo">
    <AppPanel
      title="连接仓库"
      description="通过 HTTPS 克隆。Git 客户端使用网页登录授权，按你当前的仓库权限访问。"
    >
      <div class="heading-row">
        <div class="actions">
          <AppBadge tone="blue">私有仓库</AppBadge>
          <AppBadge v-if="repo.archived">已归档</AppBadge>
          <AppBadge>{{
            repo.permissions.is_owner ? '拥有者' : repo.permissions.is_admin ? '仓库管理员' : '成员'
          }}</AppBadge>
          <AppBadge :tone="repo.permissions.access === 'write' ? 'green' : undefined">{{
            repo.permissions.access === 'write' ? '读写' : '只读'
          }}</AppBadge>
        </div>
        <span class="muted">默认分支 {{ repo.default_branch }}</span>
      </div>
      <div class="repo-clone">
        <span class="repo-clone-label">HTTPS 克隆地址</span>
        <div class="repo-clone-row">
          <code tabindex="0" aria-label="HTTPS 克隆地址">{{ cloneUrl }}</code>
          <AppButton variant="secondary" @click="copy">复制地址</AppButton>
        </div>
      </div>
      <small>通过 Git Credential Manager 完成本站的 OAuth 授权后即可克隆。</small>
    </AppPanel>
    <AppPanel title="仓库简介">
      <form
        v-if="repo.permissions.can_manage_members"
        @submit.prevent="change(() => api(path, 'PATCH', { description }), '仓库简介已保存')"
      >
        <AppTextarea v-model="description" label="简介" maxlength="2000" :disabled="busy" />
        <AppButton type="submit" variant="secondary" :busy="busy">保存简介</AppButton>
      </form>
      <p v-else class="repo-description">{{ repo.description || '暂无简介' }}</p>
    </AppPanel>
    <AppPanel
      title="仓库成员"
      description="管理员可编辑、移除普通成员；拥有者可管理其他所有成员，并任免管理员。岗位角色与读写权限独立。"
      class="repo-members"
    >
      <template #actions>
        <AppButton
          v-if="repo.permissions.can_manage_members"
          variant="secondary"
          :disabled="busy || searching"
          :aria-expanded="adding"
          aria-controls="add-repository-member"
          @click="adding = !adding"
        >
          {{ adding ? '收起添加成员' : '＋ 添加成员' }}
        </AppButton>
      </template>
      <div
        v-if="adding && repo.permissions.can_manage_members"
        id="add-repository-member"
        class="repo-add-member"
      >
        <p class="muted">搜索已有同事的用户名或姓名，选择后分配仓库权限。</p>
        <form class="repo-search" @submit.prevent="search">
          <AppInput
            v-model="query"
            aria-label="搜索可添加的同事"
            placeholder="输入至少 2 个字符，例如 @用户名"
            required
            minlength="2"
            maxlength="100"
            :disabled="busy || searching"
          />
          <AppButton type="submit" variant="secondary" :busy="searching" :disabled="busy"
            >查找同事</AppButton
          >
        </form>
        <ul v-if="candidates.length" class="repo-candidates" aria-label="搜索结果">
          <li v-for="candidate in candidates" :key="candidate.id">
            <span
              ><strong>{{ candidate.display_name }}</strong>
              <span class="muted">@{{ candidate.username }}</span></span
            >
            <AppButton variant="secondary" :disabled="busy" @click="selected = candidate">{{
              selected?.id === candidate.id ? '已选择' : '选择'
            }}</AppButton>
          </li>
        </ul>
        <p v-else-if="searched" class="muted">没有可添加的用户；已有成员和已停用账号不会显示。</p>
        <div v-if="selected" class="repo-member-form">
          <h3>添加 {{ selected.display_name }} · @{{ selected.username }}</h3>
          <RepositoryMemberForm
            :key="selected.id"
            :value="initialMember"
            :allow-admin="repo.permissions.is_owner"
            :busy="busy"
            submit-label="添加到仓库"
            @submit="addMember"
          />
        </div>
      </div>
      <p v-if="repo.archived" class="muted">
        以下为成员原有权限；归档期间统一只读，取消归档后恢复。
      </p>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>成员</th>
              <th>操作</th>
              <th>岗位角色</th>
              <th>读写权限</th>
              <th>管理权限</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="member in members" :key="member.user_id">
              <tr :id="`member-${member.user_id}`">
                <td>
                  <strong>{{ member.display_name }}</strong
                  ><small>@{{ member.username }}</small
                  ><AppBadge v-if="member.status === 'disabled'">已停用</AppBadge>
                </td>
                <td>
                  <div v-if="canEdit(member)" class="actions">
                    <AppButton
                      variant="secondary"
                      :disabled="busy"
                      :aria-label="`编辑 @${member.username}`"
                      :aria-expanded="editing === member.user_id"
                      :aria-controls="`edit-member-${member.user_id}`"
                      @click="editing = editing === member.user_id ? undefined : member.user_id"
                      >{{ editing === member.user_id ? '取消编辑' : '编辑成员' }}</AppButton
                    >
                    <AppButton
                      variant="danger"
                      :disabled="busy"
                      :aria-label="`移除 @${member.username}`"
                      @click="remove(member)"
                      >移除</AppButton
                    >
                  </div>
                  <span v-else class="muted">{{
                    member.is_owner
                      ? '拥有者'
                      : member.is_admin && repo.permissions.can_manage_members
                        ? '仅拥有者可管理'
                        : '—'
                  }}</span>
                </td>
                <td>{{ member.role || '未指定' }}</td>
                <td>{{ member.access === 'write' ? '读写' : '只读' }}</td>
                <td>
                  <AppBadge :tone="member.is_owner ? 'blue' : undefined">{{
                    member.is_owner ? '拥有者' : member.is_admin ? '仓库管理员' : '普通成员'
                  }}</AppBadge>
                </td>
              </tr>
              <tr
                v-if="editing === member.user_id && canEdit(member)"
                :id="`edit-member-${member.user_id}`"
              >
                <td colspan="5">
                  <h3>编辑 {{ member.display_name }} · @{{ member.username }}</h3>
                  <RepositoryMemberForm
                    :value="member"
                    :allow-admin="repo.permissions.is_owner"
                    :busy="busy"
                    @submit="
                      (settings) =>
                        change(
                          () => api(`${path}/members/${member.user_id}`, 'PATCH', settings),
                          '成员设置已保存',
                        )
                    "
                  >
                    <template #actions><AppButton variant="ghost" :disabled="busy" @click="editing = undefined">取消编辑</AppButton></template>
                  </RepositoryMemberForm>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </AppPanel>
    <RepositoryCISettings v-if="repo.permissions.is_owner" :repository="repo" />
    <CollaborationSettings :repository="repo" />
    <RepositoryProtection v-if="repo.permissions.is_owner" :repository="repo" @updated="updated" />
    <RepositorySettings
      v-if="repo.permissions.is_owner"
      :repository="repo"
      @updated="updated"
      @deleted="router.replace('/repositories')"
    />
  </template>
  <p v-else-if="busy" class="muted">正在读取仓库…</p>
  <AppConfirm ref="confirmation" />
</template>

<style scoped>
.repo-description {
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.repo-clone {
  margin: 24px 0 20px;
}
.repo-clone-label {
  display: block;
  margin-bottom: 8px;
  font-size: 12px;
  color: #c4d1e3;
}
.repo-clone-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.repo-clone code {
  flex: 1;
  min-width: 0;
  padding: 13px 16px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--input);
  overflow-wrap: anywhere;
  white-space: pre-wrap;
  user-select: all;
  font-size: 13px;
  line-height: 1.65;
}
.repo-clone-row .button {
  flex-shrink: 0;
}
.repo-search {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 20px;
}
.repo-search > :first-child {
  flex: 1;
  margin: 0;
}
.repo-add-member {
  border-bottom: 1px solid var(--line);
  padding-bottom: 24px;
  margin-bottom: 24px;
}
.repo-add-member > p {
  margin-top: 0;
}
.repo-members .table-scroll {
  border: 1px solid var(--line);
  border-radius: 8px;
}
.repo-members td {
  white-space: normal;
  overflow-wrap: anywhere;
}
.repo-members td:first-child:not([colspan]) {
  min-width: 130px;
  max-width: 220px;
}
.repo-members td:nth-child(3) {
  min-width: 100px;
  max-width: 200px;
}
.repo-members td .actions {
  flex-wrap: nowrap;
}
.repo-candidates {
  list-style: none;
  padding: 0;
  margin: 0;
}
.repo-candidates li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 12px 0;
  border-bottom: 1px solid var(--line);
}
.repo-member-form {
  margin-top: 24px;
}
.repo-member-form h3 {
  margin-bottom: 20px;
}
@media (max-width: 720px) {
  .repo-clone-row,
  .repo-search {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
