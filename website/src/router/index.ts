import { createRouter, createWebHistory } from 'vue-router'
import { auth, forgetLogin, loadUser } from '../auth'
import { repositoryPageKey, scrollBehavior } from './navigation'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/work/pulls',
      component: () => import('../views/WorkItemsView.vue'),
      props: { kind: 'pull' },
      meta: { title: '我的 Pull requests' },
    },
    {
      path: '/work/issues',
      component: () => import('../views/WorkItemsView.vue'),
      props: { kind: 'issue' },
      meta: { title: '我的 Issues' },
    },
    {
      path: '/guide',
      component: () => import('../views/GuideView.vue'),
      meta: { title: '使用指南' },
    },
    {
      path: '/oauth/device',
      component: () => import('../views/OAuthDeviceView.vue'),
      meta: { title: 'CLI 设备授权' },
    },
    { path: '/images', component: () => import('../views/ImagesView.vue'), meta: { title: '镜像管理' } },
    {
      path: '/repositories',
      component: () => import('../views/RepositoriesView.vue'),
      meta: { title: '工程仓库' },
    },
    {
      path: '/repositories/:owner/:name',
      component: () => import('../views/RepositoryDetailView.vue'),
      meta: { title: '仓库详情' },
      children: [
        { path: 'ci', component: () => import('../views/RepositoryCIView.vue'), meta: { title: 'CI', repositoryTab: 'ci' } },
        { path: 'ci/:runId(\\d+)', component: () => import('../views/CIRunView.vue'), meta: { title: 'CI 运行', repositoryTab: 'ci' } },
        {
          path: 'code',
          component: () => import('../views/RepositoryCodeView.vue'),
          meta: { repositoryTab: 'code' },
        },
        {
          path: 'issue',
          alias: 'issues',
          component: () => import('../views/IssueListView.vue'),
          meta: { title: 'Issues', repositoryTab: 'issue' },
        },
        {
          path: 'issue/new',
          alias: 'issues/new',
          component: () => import('../views/NewItemView.vue'),
          meta: { title: '新建 Issue', repositoryTab: 'issue' },
        },
        {
          path: 'issue/:number(\\d+)',
          alias: 'issues/:number(\\d+)',
          component: () => import('../views/ItemDetailView.vue'),
          meta: { title: 'Issue', repositoryTab: 'issue' },
        },
        {
          path: 'pr',
          alias: 'pulls',
          component: () => import('../views/IssueListView.vue'),
          meta: { title: 'Pull requests', repositoryTab: 'pr' },
        },
        {
          path: 'pr/new',
          alias: 'compare',
          component: () => import('../views/NewItemView.vue'),
          meta: { title: '创建 PR', repositoryTab: 'pr' },
        },
        {
          path: 'pr/:number(\\d+)',
          alias: 'pull/:number(\\d+)',
          component: () => import('../views/ItemDetailView.vue'),
          meta: { title: 'Pull request', repositoryTab: 'pr' },
        },
        {
          path: 'pr/:number(\\d+)/:section(commits|changes|checks)',
          alias: 'pull/:number(\\d+)/:section(commits|changes|checks)',
          component: () => import('../views/ItemDetailView.vue'),
          meta: { title: 'Pull request', repositoryTab: 'pr' },
        },
        {
          path: 'settings',
          component: () => import('../views/RepositorySettingsView.vue'),
          meta: { title: '仓库设置', repositoryTab: 'settings' },
        },
        {
          path: 'registry',
          component: () => import('../views/RepositoryRegistryView.vue'),
          meta: { title: '镜像仓库', repositoryTab: 'registry' },
        },
        {
          path: 'commit/:sha([0-9a-fA-F]{7,40})',
          component: () => import('../views/CommitView.vue'),
          meta: { title: '提交详情', repositoryTab: 'code' },
        },
      ],
    },
    {
      path: '/repositories/:id',
      component: () => import('../views/RepositoryDetailView.vue'),
      meta: { title: '仓库详情' },
    },
    {
      path: '/oauth/consent',
      component: () => import('../views/OAuthConsentView.vue'),
      meta: { title: 'Git 客户端授权' },
    },
    {
      path: '/notifications',
      component: () => import('../views/NotificationsView.vue'),
      meta: { title: '通知' },
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('../views/AuthView.vue'),
      props: { mode: 'login' },
      meta: { title: '邮箱登录', public: true },
    },
    {
      path: '/register',
      name: 'register',
      component: () => import('../views/AuthView.vue'),
      props: { mode: 'register' },
      meta: { title: '注册账号', public: true },
    },
    {
      path: '/profile',
      component: () => import('../views/ProfileView.vue'),
      meta: { title: '个人资料' },
    },
    {
      path: '/security',
      component: () => import('../views/SecurityView.vue'),
      meta: { title: '账号安全' },
    },
    {
      path: '/users',
      component: () => import('../views/UsersView.vue'),
      meta: { title: '人员管理', admin: true },
    },
    {
      path: '/users/:id',
      component: () => import('../views/UserDetailView.vue'),
      meta: { title: '人员详情', admin: true },
    },
    {
      path: '/audit',
      component: () => import('../views/AuditView.vue'),
      meta: { title: '操作记录', admin: true },
    },
    {
      path: '/admin/repositories',
      component: () => import('../views/RepositoryAdminView.vue'),
      meta: { title: '仓库管理', admin: true },
    },
    {
      path: '/admin/repositories/:id(\\d+)',
      component: () => import('../views/RepositoryAdminDetailView.vue'),
      meta: { title: '仓库管理详情', admin: true },
    },
    {
      path: '/admin/registry',
      component: () => import('../views/RegistryAdminView.vue'),
      meta: { title: 'Registry 管理', admin: true },
    },
    { path: '/admin/ci/workers', component: () => import('../views/CIWorkersView.vue'), meta: { title: '构建机', admin: true } },
    {
      path: '/',
      name: 'home',
      component: () => import('../views/HomeView.vue'),
      meta: { title: '工作台' },
    },
    {
      path: '/status',
      name: 'status',
      component: () => import('../views/StatusView.vue'),
      meta: { title: '环境状态' },
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('../views/NotFoundView.vue'),
      meta: { title: '页面不存在' },
    },
  ],
  scrollBehavior,
})

router.beforeEach(async (to, from) => {
  if (to.meta.public && auth.loaded && !auth.user && !auth.error) return
  const localUpdate = from.matched.length && repositoryPageKey(to) === repositoryPageKey(from)
  if ((!auth.loaded || auth.error || !localUpdate) && await loadUser() === false) {
    auth.retryPath = to.fullPath
    // Keep existing editors on transient failures. Initial navigation retains
    // its URL, but App waits for a confirmed session before mounting content.
    return from.matched.length ? false : undefined
  }
  if (!to.meta.public && !auth.user) return { path: '/login', query: { redirect: to.fullPath } }
  if (to.meta.public && auth.user) return '/'
  if (to.meta.admin && !auth.user?.roles.includes('admin')) return '/'
})

window.addEventListener('session-expired', () => {
  forgetLogin('登录已失效，请重新登录')
  if (auth.loaded && !router.currentRoute.value.meta.public) {
    void router.replace({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } })
  }
})

export default router
