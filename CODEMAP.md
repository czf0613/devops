# 代码导航

本文按当前代码结构组织定位入口；稳定使用方式见各模块 README，阶段状态见 [CONTEXT.md](CONTEXT.md)。下方路径都相对仓库根目录，模块名和关键符号比行号更适合长期检索。

## 目录与职责

```text
.
  Makefile                     本地初始化、检查和网站发布入口
  compose.yaml                 本地 PG / Redis，端口仅绑定 loopback
  compose.deploy.yaml          六服务部署，双架构镜像，只有 Gateway 暴露 TCP / UDP 443
  server/
    src/devops_server/         FastAPI、权限、配置、存储
    migrations/               Alembic 迁移链
    tests/                    后端 / 配置 / 数据库 / 发布脚本测试
  runner/                     独立 CI worker、原生 Executor、协议日志与平台进程监督
  cli/                        TypeScript CLI、独立 SDK、凭据与命令测试
  website/
    src/components/           基础组件与成员、设备、授权组件
    src/views/                路由页面
    src/router/               路由和登录 / 管理员守卫
    tests/                    Vitest + jsdom
  gateway/                    Nginx、CGI 启动、GCM 客户端配置
  registry/                   官方 Registry 配置、上传状态签名密钥持久化和使用说明
  scripts/                    初始化、静态发布、真实 Git 联调
  sql/                        手动管理员 SQL 示例
```

`.env`、`.env.deploy`、`.data/`、`.venv/`、`node_modules/`、`dist/` 是本地或生成内容，不应作为源码入口或提交目标。锁文件 `uv.lock` / `pnpm-lock.yaml` 是需要维护的源码。

## 后端模块

Registry：`registry_models.py` 定义 namespace 归属与令牌摘要，`registry.py` 提供 Cookie 管理 API 及 `/_internal/registry/authorize` 实时鉴权，`registry_client.py` 只访问配置中的内部上游。Gateway 转发 `/v2/` 数据；网站 `RepositoryRegistryView.vue` 管理 repo 绑定与镜像，`RegistryCredentialList.vue` 仅在账号安全提供用户令牌。`registry_admin.py` / `RegistryAdminView.vue` 提供系统级 namespace 归属管理，保持镜像 ACL；`AppNotice` 支持 inline / floating 两种提示位置。迁移为 `0012_registry` → `0013_registry_management`；测试入口 `test_registry.py`、`registry.test.ts`、`verify_registry.py`（由真实 Git Gateway harness 调用）。稳定行为见 [Registry README](registry/README.md)。

CLI 入口为 [main.ts](cli/src/main.ts)：Commander 命令树 → [context.ts](cli/src/context.ts) 地址/仓库定位 → [sdk.ts](cli/src/sdk.ts) 原生 API；[output.ts](cli/src/output.ts) 负责 gh 风格 JSON 投影。[ci.ts](cli/src/ci.ts) 注册 workflow / run 命令、处理输入、日志分页和等待；[ci-output.ts](cli/src/ci-output.ts) 投影 run / job / PR checks，[ci-types.ts](cli/src/ci-types.ts) 定义原生契约，[ci-artifacts.ts](cli/src/ci-artifacts.ts) 流式下载并验证产物。[auth.ts](cli/src/auth.ts) 实现 PKCE / device flow / refresh，[storage.ts](cli/src/storage.ts) 管理按 origin + 账号隔离的系统凭据与进程锁。网站使用 [GuideView.vue](website/src/views/GuideView.vue) 和 [OAuthDeviceView.vue](website/src/views/OAuthDeviceView.vue)。发布调用 [deploy_cli.py](scripts/deploy_cli.py)，下载来自 Nginx 的独立静态目录。验证入口：`cli/test/cli.test.mjs`、`cli/test/ci.test.mjs`、`test_cli_oauth.py`、`test_cli_http.py`、`test_cli_ci_http.py`、`test_cli_deploy.py` 和扩展的 `verify_git_gateway.py`。

| 文件 | 关键入口 / 责任 |
|---|---|
| [main.py](server/src/devops_server/main.py) | `create_app` / lifespan 注入配置、DB、Redis、邮件；注册路由；CSRF 中间件、统一异常、request ID、健康检查 |
| [config.py](server/src/devops_server/config.py) | `ConnectionSettings` 连接参数、`Settings` 业务配置与关联校验 |
| [config_store.py](server/src/devops_server/config_store.py) | 配置默认值、完整校验、数据库读写和敏感 key 集合 |
| [config_cli.py](server/src/devops_server/config_cli.py) | `init` / `list` / `set`；旧 `.env` 导入、stdin JSON 更新 |
| [database.py](server/src/devops_server/database.py) | 连接池与隐藏 SQL 绑定参数的 engine |
| [models.py](server/src/devops_server/models.py) | 用户、仓库、Git / CLI OAuth 表、约束、索引、凭据延迟加载字段 |
| [schemas.py](server/src/devops_server/schemas.py) | 用户与认证请求 / 响应、拒绝额外字段、格式校验；仓库请求模型在 `repositories.py` |
| [dependencies.py](server/src/devops_server/dependencies.py) | `Database` 的响应前提交、`Authenticated` 的实时会话校验、`RepositoryAuthenticated` 的 CLI Bearer / Cookie 分流、管理员依赖 |
| [security.py](server/src/devops_server/security.py) | bcrypt、AES-GCM、CSRF、随机 Token、验证码摘要及安全原语 |
| [services.py](server/src/devops_server/services.py) | 审计、验证码签发 / 消费、会话与密码版本等共享操作；部分拒绝路径需要保留失败审计和次数 |
| [mail.py](server/src/devops_server/mail.py) | `SMTPMailer` / `code_email_html`；SSL / STARTTLS、发件人解析、纯文本 + HTML 验证码邮件 |
| [rate_limits.py](server/src/devops_server/rate_limits.py) | `EmailCodeRateLimiter`；Redis Lua 原子配额和 Retry-After |
| [auth.py](server/src/devops_server/auth.py) | CSRF 引导、验证码、注册、登录、退出 |
| [users.py](server/src/devops_server/users.py) | 本人资料 / 邮箱 / 密码 / 设备，全局用户管理、角色及审计 |
| [repositories.py](server/src/devops_server/repositories.py) | 仓库 API、`visible_repository`、`repository_access`、`editable_member`、生命周期和锁 |
| [dashboard.py](server/src/devops_server/dashboard.py) | `items` / `activity` / `repositories`；复用 `visible_repositories` 在分页前过滤权限，返回精简跨仓库摘要 |
| [repository_storage.py](server/src/devops_server/repository_storage.py) | `create_bare_repository`，按 ID 独占创建、受控 Git 对象初始化 main / README、不覆盖现有目录 |
| [git_oauth.py](server/src/devops_server/git_oauth.py) | `authorize` / `decide` / `token_exchange` / `revoke_grant` / `git_authorize`，CLI public client、device flow；授权到仓库 ACL |
| [repository_code.py](server/src/devops_server/repository_code.py) | 分支、12 周提交趋势 `weekly_activity`、Git 对象解析、当前目录 README、受限图片、字面路径与预览边界，不 checkout 或执行文件 |
| [git_lfs.py](server/src/devops_server/git_lfs.py) | Batch / Basic / verify、OAuth ACL、流式校验、仓库隔离存储、浏览器附件下载 |

## 数据与 API 分组

业务实体主键使用 BIGINT Identity；CI 仓库配置以 repository_id 外键作为一对一主键。当前表清单：

| 数据组 | 表 | 变更时联动 |
|---|---|---|
| 用户 / 角色 | `users`、`roles`、`user_roles` | 用户 schema、会话鉴权、全局管理 UI |
| 邮箱 / 登录 | `email_challenges`、`user_sessions` | CSRF、验证码用途、Redis 限流、设备列表 |
| 配置 / 审计 | `config`、`audit_events` | 配置初始化 / 迁移、脱敏、审计查询 |
| 仓库 | `repositories`、`repository_members` | API、Git 实时授权、物理存储、成员和设置 UI |
| Git OAuth | `git_oauth_requests`、`git_oauth_grants`、`git_oauth_tokens`、`oauth_device_requests` | 浏览器流程、GCM、续期 / 吊销、内部鉴权 |

| URL 分组 | 实现 | 特殊约定 |
|---|---|---|
| `/api/auth/*` | `auth.py` | 邮箱验证码；登录和注册也要 CSRF |
| `/api/me*`、`/api/users*`、`/api/roles`、`/api/audit-events` | `users.py` | 本人 / 全局管理员边界；分页响应 |
| `/api/repositories*` | `repositories.py` | 名称查询用于导航，写操作使用 ID；无访问权通常 404 |
| `/oauth/authorize`、`/oauth/token`、`/oauth/revoke`、`/oauth/device/code` | `git_oauth.py` | 协议路径不带 `/api`；后三个精确 POST 免 Cookie CSRF |
| `/api/oauth/authorization-requests/*` | `git_oauth.py` | 浏览器授权页使用，绑定当前浏览器流程 |
| `/api/me/git-authorizations*` | `git_oauth.py` | 当前用户未撤销的授权列表 / 吊销，不输出 Token，不删除历史 |
| `/_internal/git/authorize` | `git_oauth.py` + Nginx | 仅内部 GET；不进 OpenAPI，外部路径拒绝 |
| `/api/repositories/{id}/branches`、`/contents`、`/image` | `repository_code.py` | 私有仓库 ACL，ref 只接受分支，路径按字面解析 |
| `/api/repositories/{id}/activity` | `repository_code.py` | 同一私有 ACL，所有分支唯一提交、UTC 周一分桶、上限 / 超时 |
| `/git/{owner}/{name}.git/info/lfs/*` | `git_lfs.py` + Nginx | 精确机器写路由免 Cookie CSRF，全部使用 Git OAuth |
| `/api/repositories/{id}/lfs/{oid}` | `git_lfs.py` | 浏览器 Cookie + 仓库 ACL 的原文件下载 |
| `/api/health`、`/api/health/ready` | `main.py` | 存活与 PG / Redis 就绪分别检查 |

详细仓库 / OAuth API 表见 [Gateway README](gateway/README.md)，认证参数见 [DevOps README](README.md)，数据库升级见 [迁移 README](server/migrations/README.md)。

## 前端入口与组件

启动链：[main.ts](website/src/main.ts) → [App.vue](website/src/App.vue) → [router/index.ts](website/src/router/index.ts) → 页面。

| 文件 | 责任 |
|---|---|
| [api.ts](website/src/api.ts) | 同源 `/api`、Cookie、CSRF、20 秒超时、错误信息、会话过期事件 |
| [auth.ts](website/src/auth.ts) | 响应式当前用户、加载 / 接受 / 清除登录状态 |
| [repositories.ts](website/src/repositories.ts) | 仓库 / 成员类型，`repositoryUrl` 编码名称 URL |
| [style.css](website/src/style.css) | 深色设计变量、布局 / 表单 / 表格公共样式 |
| [AuthView.vue](website/src/views/AuthView.vue) | 登录与注册；保留安全的站内 redirect |
| [ProfileView.vue](website/src/views/ProfileView.vue) | 当前账号资料及更换邮箱 |
| [SecurityView.vue](website/src/views/SecurityView.vue) | 兼容密码、固定区域设备列表、Git 授权 |
| [RepositoriesView.vue](website/src/views/RepositoriesView.vue) | 仓库列表、查询、创建 |
| [RepositoryActivity.vue](website/src/components/RepositoryActivity.vue) | 独立加载各仓库的 12 周真实提交趋势、SVG、空值 / 错误 / 截断提示 |
| [RepositoryDetailView.vue](website/src/views/RepositoryDetailView.vue) | 名称路由、紧凑页头、Settings 简介、克隆地址、统一成员面板与权限操作 |
| [RepositoryCode.vue](website/src/components/RepositoryCode.vue) | 默认 Code 页、分支 / 目录 / 文件、URL 状态和过时响应保护 |
| [RepositoryCloneMenu.vue](website/src/components/RepositoryCloneMenu.vue) | 统一克隆按钮、地址弹层、完整复制、键盘 / 外部关闭与焦点恢复 |
| [MarkdownDocument.vue](website/src/components/MarkdownDocument.vue)、[markdown.ts](website/src/markdown.ts) | README Markdown、安全 URL / HTML 清洗、相对路径和图片 |
| [RepositoryProtection.vue](website/src/components/RepositoryProtection.vue)、[AppSwitch.vue](website/src/components/AppSwitch.vue) | Settings 拥有者开关，后端确认后更新状态 |
| [RepositoryMemberForm.vue](website/src/components/RepositoryMemberForm.vue) | 添加 / 编辑共用表单；岗位预设、自定义、独立读写 / 管理权限 |
| [RepositorySettings.vue](website/src/components/RepositorySettings.vue) | 拥有者移交、归档、删除；确认当前名称和操作后导航 |
| [OAuthConsentView.vue](website/src/views/OAuthConsentView.vue) | 展示授权请求、同意 / 拒绝、有效回调跳转 |
| [GitAuthorizationList.vue](website/src/components/GitAuthorizationList.vue) | 独立于网页登录设备的 Git 授权撤销 |
| [SessionList.vue](website/src/components/SessionList.vue) | 有效设备分页、退出后移除与末页回退、当前设备标识、撤销确认、内部滚动 |
| [SidebarNav.vue](website/src/components/SidebarNav.vue) | 工作台 → 工程仓库；按全局角色展示系统管理 |
| [UsersView.vue](website/src/views/UsersView.vue)、[UserDetailView.vue](website/src/views/UserDetailView.vue) | 全局人员查询 / 新建 / 编辑、会话与兼容密码清除 |
| [AuditView.vue](website/src/views/AuditView.vue)、[StatusView.vue](website/src/views/StatusView.vue) | 审计分页、系统就绪显示 |

基础组件 `AppButton`、`AppInput`、`AppTextarea`、`AppSelect`、`AppPanel`、`AppBadge`、`AppNotice`、`AppConfirm`、`AppIcon`、`Pager`、`EmailCodeField` 的约定见 [前端 README](website/README.md)。

## 关键调用链

### 组件版本

server / website / CLI 分别由 `server/pyproject.toml`、`website/package.json`、`cli/package.json` 定义独立版本。`main.create_app` 通过后端包元数据设置 OpenAPI 版本；CLI `main.ts` 从安装包读取版本；网站 `App.vue` 使用网站包版本显示侧栏，`GuideView.vue` 单独导入 CLI 包版本生成下载地址。`guide.test.ts` 使用不同于网站的 CLI 版本验证下载和复制内容；构建后检查 CLI `--version`、OpenAPI 和 Safari 侧栏。

### 邮箱登录和兼容密码

1. `api.ts` 获取 CSRF → `EmailCodeField` 请求验证码。
2. `auth.py` → Redis 限流 → `services.py` 存 HMAC 挑战 → `SMTPMailer`。
3. 登录消费专用挑战 → 新建数据库会话 → 请求事务提交成功后发送 Cookie。
4. 后续请求走 `dependencies.current_user` 实时鉴权；`renew_session` 在请求事务结束前按分钟节流并在会话行锁内重查有效性、更新活动 / 到期时间。提交成功后 `main.security_headers` 调用 `security.renew_session_cookies` 同步 Cookie 期限，保持 Token / CSRF 值；退出 / 吊销更新数据库且不续期。验证入口为 `test_session_renewal.py`（连续续期、过期 / 撤销 / 账号状态、并发、提交失败及 Response 类型）。
5. 兼容密码使用单独 `legacy_password` 用途，`users.py` 调用安全原语并同步有效会话版本，不重新使用登录验证码。

### 仓库、权限与 Git

1. 创建请求分配 Repository ID → `create_bare_repository` 独占创建 `<id>.git` → hash-object / mktree / commit-tree / update-ref 生成 main README 初始提交 → 元数据提交。
2. 修改 / 成员 / 移交 / 生命周期先锁仓库并重查当前权限；角色描述不进入权限判断。
3. GCM `/oauth/authorize` → DevOps 登录 → 授权确认 → loopback code → `/oauth/token` + PKCE。后续 refresh 在授权行锁内重新检查时效与撤销状态，成功后轮换凭据并续期至刷新后 30 天；失败不续期。
4. Git 请求到 Nginx；冻结原始方法 / URI → `auth_request` 内部鉴权 → 后端校验 Token、路径、仓库及访问等级。
5. 成功返回可信数字 ID → CGI 映射 `<id>.git` 执行 Git。不要直接拿 URL 中的用户名或仓库名拼物理路径。
6. 可信保护标志 → 镜像内 wrapper 固定 hooksPath → pre-receive 检查全部 refs，拒绝主分支写入 / 危险更新。只更新前端不能落实保护。
7. LFS 走 FastAPI 独立路由 → 复用 `authenticate_git` + 仓库 ACL → Batch 返回不含 Token 的对象 URL → 流式校验 → 重新鉴权与锁仓库 → 原子发布；下载同样实时鉴权。

### 静态网站发布

1. `Makefile website-deploy` → `deploy_website.py publish` → frozen pnpm install / build。
2. 打包 `dist` 和 release 标记 → scp 上传压缩包和安装脚本 → SHA-256 校验。
3. 服务器目录锁 → 安全解包 → 入口资源校验 → 合并内容哈希 assets → 原子切换 `current`。
4. Nginx 只读挂载父目录，入口取 current、assets 从共享目录取；无需重启。
5. `verify` 请求公网 release / 入口资源 / API readiness；失败报错，不自动回滚。

### 测试服务器组件部署

`deploy.sh` / `Makefile deploy` → `scripts/deploy.py`：选择 server / gateway / website，Buildx 构建并推送选中镜像的 arm64 / amd64 索引 → `published_image_ids` 读取各架构 manifest / config ID → SSH 内 `native_platform` 选择服务器架构 → image 固定索引摘要、迁移已知旧 platform / Gateway UDP 443 → pull 并核对该架构镜像 ID / 运行容器 manifest 描述符 → 备份 Compose / 检查迁移 → up --no-deps --wait → 实际运行镜像和公网 readiness。`--migrate` 才备份数据库并升级；website 调用现有静态发布器，`--dry-run` 不执行发布。

## 网关、部署与脚本入口

| 文件 | 修改时关注 |
|---|---|
| [Makefile](Makefile) | 命令实际工作目录、检查范围和远端副作用；`http3-check` / `HTTP3_ARGS` 只读握手验收 |
| [compose.yaml](compose.yaml)、[compose.deploy.yaml](compose.deploy.yaml) | 本地 / 部署隔离、健康检查、端口、持久化与共享卷 |
| [server/Dockerfile](server/Dockerfile) | 锁定依赖、Git 可执行文件、UID 101、后端 readiness |
| [gateway/Dockerfile](gateway/Dockerfile) | Nginx / Git CGI 运行时；不构建、不 COPY 网站 |
| [nginx-main.conf](gateway/nginx-main.conf)、[nginx.conf](gateway/nginx.conf) | Brotli、TLS / QUIC / Alt-Svc、SPA / assets、OAuth 精确路径、Git 白名单和内部鉴权 |
| [start.sh](gateway/start.sh) | 证书 / 网站预检、CGI 与 Nginx 子进程监督 |
| [git-http-backend.sh](gateway/git-http-backend.sh)、[pre-receive](gateway/hooks/pre-receive) | 钉住服务端保护钩子、统一共享仓库默认配置、拒绝混合危险推送 |
| [configure-gcm.sh](gateway/configure-gcm.sh) | 全局 Git 的指定域名设置；普通模式 / Desktop 模式区别 |
| [init_env.py](scripts/init_env.py)、[redis-start.sh](scripts/redis-start.sh) | 连接文件权限、保留已有值、Redis 认证 / AOF / 单 DB |
| [deploy_website.py](scripts/deploy_website.py) | `package` / `install` / `publish` / `verify`；保留旧 assets、拒绝危险归档 |
| [deploy.sh](deploy.sh)、[deploy.py](scripts/deploy.py) | 测试服务器组件选择、本地镜像构建 / push、远端 pull / up、迁移前置检查与健康验收 |
| [verify_git_gateway.py](scripts/verify_git_gateway.py) | 原生架构真实 HTTP/3 / HTTPS / CGI / Git 集成、隔离数据及 finally 清理 |
| [verify_http3.py](scripts/verify_http3.py) | aioquic 只读 HTTP/3 探针；验证证书、h3 ALPN、静态首页与 API readiness，无 TCP 回退 |
| [create_admin.example.sql](sql/create_admin.example.sql) | 仅人工执行的管理员创建示例 |

## 改动到测试的映射

测试命令和依赖见 [后端 README](server/README.md)、[前端 README](website/README.md)。后端测试文件位于 [`server/tests/`](server/tests/)，前端位于 [`website/tests/`](website/tests/)。

| 变更点 | 首选测试 | 扩展验证 |
|---|---|---|
| 登录 / CSRF / 会话 | `test_auth.py`、`test_security.py`、`test_session_lists.py` | `components.test.ts`、`password-flows.test.ts`、`session-list.test.ts` |
| 人员与兼容密码 | `test_users.py`、`test_legacy_password.py` | `password-flows.test.ts` |
| 限流 / SMTP / 就绪 | `test_rate_limits.py`、`test_auth.py`、`test_health.py` | 本地依赖故障路径，不发真实邮件 |
| 配置 / 模型 / DDL | `test_config.py`、`test_migrations.py` | `alembic check`，保留历史迁移数据 |
| 仓库权限 / 生命周期 | `test_repositories.py`、`test_git_oauth.py` | `repositories.test.ts`、真实 Git 脚本 |
| 初始化 README / 已有仓库导入 | `test_repository_storage.py`（执行模板命令，验证独立历史、README 冲突和原远端保留） | Code 页 README 桌面预览 |
| OAuth / Git 网关 | `test_git_oauth.py`、`oauth-consent.test.ts` | 重建镜像后 `verify_git_gateway.py` |
| Code / 推送保护 / LFS | `test_repository_code.py`、`test_git_lfs.py`、`test_migrations.py` | `repositories.test.ts` 和真实 Git / LFS 客户端集成 |
| 通用 UI / 导航 / 角色表单 | `components.test.ts`、`repositories.test.ts`、`notices-markdown-shell.test.ts` | 生产构建、按需实际浏览器视觉验证 |
| 仓库列表 / 提交趋势 / 授权隐藏 | `repository-activity-authorizations.test.ts`、`test_repository_code.py`、`test_git_oauth.py` | 真实 Git 统计边界、列表限额前过滤、Safari 桌面长简介 |
| 网站发布 | `test_website_deploy.py` | 集成脚本验证 Linux 卷与不重启切换 |
| 测试服务器发布编排 | `test_deploy.py` | 双架构索引、摘要固定、原生 image ID、旧平台 / UDP 迁移、dry-run；获授权后的真实发布 |

`conftest.py` 提供本地随机 PostgreSQL schema、独立 Redis 键前缀、`Mailbox`、带 CSRF 的 `mutate` 和合成用户。不要绕过这些隔离工具去操作真实账号。jsdom 的 dialog / 布局替身只验证交互逻辑；不会证明 Safari 中的视觉效果。

## Issue / PR 协作调用链

| 入口 | 文件 / 关键符号 | 影响与验证 |
|---|---|---|
| 模型与编号 | `collaboration_models.py`、`0010_collaboration`、`collaboration_service.next_number` | 共享编号、迁移、仓库行锁；`test_migrations.py`、并发编号测试 |
| Issue / 子项 / 评论 | `collaboration_issues.py`、`collaboration_service.py` | 当前 ACL、乐观版本、图锁 / 有序仓库锁、无环层级、历史、目录、移交；`test_collaboration.py` |
| Markdown / 双向引用 | `collaboration_references.py`、`discussion-markdown.ts`、`DiscussionMarkdown.vue` | token 提取、移交上下文、当前权限过滤、代码 / 转义 / XSS；前后端 collaboration 测试 |
| PR / 审阅 | `collaboration_pulls.py`、`ItemDetailView.vue`、`DiffViewer.vue` | 比较、草稿、行线程、审批 / checks / 策略、SHA 锚点、私有草稿 |
| Git 与后台 | `collaboration_git.py`、`collaboration_worker.py`、`collaboration_pushes.py` | 隔离 Git、持久任务、缓存、租约、意图 / 原子引用 / SQL 恢复；真实 Git 测试 |
| 推送入口 | `gateway/hooks/post-receive`、`pre-receive` | 内部身份写回执、禁止客户端修改内部 refs；真实 Docker Gateway 集成 |
| 内容与通知 | `collaboration_content.py`、`mail.SMTPMailer.send_notification` | 私有附件发布前后权限、通知 ACL、提交详情；邮件替身 |
| 页面 | `IssueListView`、`NewItemView`、`ItemDetailView`、`CommitView`、`NotificationsView`、`RepositorySettingsView` | `/issue/:number` / `/pr/:number` / PR 子路径；旧根 / query 兼容跳转；桌面验收 |
| 原地编辑与本地草稿 | `drafts.ts/useDraft`、`DraftStatus.vue`、`InlineMetadata.vue`、`CommentCard.vue`、`ItemRelations.vue` | 账号 / 位置隔离、刷新恢复、提交失败保留、乐观版本、取消与焦点；`collaboration-ux.test.ts`，布局另用 Safari 验收 |
| 局部导航与编辑状态 | `router/navigation.ts`、`auth.ts`、`RepositoryDetailView.vue`、`ItemDetailView.vue`、`DiffViewer.vue` | query 更新保留滚动、同 PR 子页复用编辑器、差异加载高度与过期请求保护、会话查询网络错误重试；`auth-navigation.test.ts`、`collaboration.test.ts`、`collaboration-ux.test.ts` |
| 列表筛选与子项展开 | `IssueListView.vue/filterMenus`、`AppSelect.vue`、`ItemRelations.vue/expand` | 搜索菜单、筛选 query / 后退同步、只展开后代、失败重试；`components.test.ts`、`collaboration-navigation.test.ts` |
| 协作设置 | `CollaborationSettings.vue`、`CatalogEditor.vue`、`MergePolicySettings.vue` | 分类数量、按需新增 / 原地编辑、颜色与模板预览、字段排序、独立加载和草稿 / 取消；`collaboration-settings.test.ts`，Safari 桌面验收 |
| 部署 | `compose.deploy.yaml` worker、`collaboration_health.py`、`scripts/deploy.update_images/apply_remote` | server 同时更新 worker，同镜像 / 挂载 / 标准库心跳探针；`test_deploy.py`、`test_collaboration_health.py` |

完整行为及运行边界见 [COLLABORATION.md](COLLABORATION.md)。

## 个人工作台调用链

`HomeView` → `DashboardItems`（首页 / `WorkItemsView` 复用）、`DashboardActivity`、`DashboardNotifications` → `useDashboardResource` → 同源 `api.ts`。工作台 query 保存筛选，跨仓库完整列表路由为 `/work/pulls` 和 `/work/issues`。`SidebarNav` → `RecentRepositories`；`RepositoryDetailView` 成功读取后通过 `recent-repositories.ts` 记录当前账号的仓库编号。

`dashboard.py` → `repositories.visible_repositories` → `WorkItem` / `ItemAssignee` / `PullReviewer` / 已发表 `DiscussionComment` 和 `PullReview`。待审阅同时匹配 PR 当前 head、最近审阅请求时间；动态通过事件白名单和公开评论 / 审阅条件过滤，不暴露原始事件 JSON。没有新表或迁移。权限及生命周期测试：`server/tests/test_dashboard.py`；独立加载、过期响应、账号切换、查询历史及最近仓库测试：`website/tests/dashboard.test.ts`。

## Registry 空间回收

`RegistryAdminView` → `RegistryGCPanel` → `registry_admin.py /gc`：管理员 Cookie / CSRF、维护确认、活动任务互斥、分页历史。`registry_gc_service.py` 处理权限重检、启动时限和审计；`registry_gc_worker.py` 持有存储 / 部署共享锁，使用镜像内固定官方二进制预检再回收，`registry_gc_health.py` 提供依赖轻量的心跳探针。`registry_gc_worker` / `registry_gc_runs` 表由 `0014_registry_gc` 创建，该迁移同时删除旧项目限定令牌及 scope 列。部署 `registry-gc` 与 server 同镜像，仅前者挂载 Registry 数据。验证入口：`test_registry_gc.py`、`test_migrations.py`、`test_deploy.py`、`registry-gc.test.ts` 和真实 `verify_registry_gc.py`。


## 管理操作与逐次邮箱验证

`RepositoryAdminView` / `RepositoryAdminDetailView` → `/api/admin/repositories` → `repository_admin.py`：全站仓库管理元数据、成员 / 权限 / 所有权 / 归档 / 软删除；仓库行锁 + `expected_updated_at`。普通成员修改同步推进仓库时间；移交复用 `repositories.transfer_ownership`，内容读取仍走原 ACL。

管理写操作统一由 `AppConfirm.askVerified` → `EmailCodeField` → `auth.py` 的 `admin_operation` 用途发信，再随具体写请求提交 code → `admin_verification.verify_admin_operation` 在同一事务消费并重查当前会话 / 邮箱 / admin。没有通用临时特权 Token；审计只存事件和必要前后状态。

Registry 删除：`registry_admin.delete_namespace` 原子标记 `deleted_at` 并关闭访问，原 repo、name、version 保留；`registry.py` 的列表 / 候选 / 绑定 / 镜像 / Docker 鉴权拒绝删除记录。`registry_gc_worker.purge_deleted_namespaces` 在维护窗口通过官方 DELETE tag API 清空已删除 namespace，复查后执行原 GC；成功才写 `purged_at`，共享层由官方引用遍历保留。迁移 `0015_admin_management` / `0016_registry_namespace_purge`。

验证：`test_repository_admin.py`、`test_registry.py`、`test_registry_gc.py`、`test_migrations.py` 和 `repository-admin.test.ts`；真实 `verify_registry_gc.py` 验证包含双平台 / 多路径 / 别名标签的整组删除、独占 blob 物理回收及存活 namespace 的共享层保留。

Buildx 来源收权后的回退：`registry.authorize` 先验证目标写权限，只有 mount 来源不可读时返回内部 `X-Registry-Upload-Only`；Nginx `auth_request_set` → `devops_registry_target` 去除 mount / from，Registry 创建普通上传。`test_registry.py` 与 `verify_registry.py` 覆盖无越权引用、正常重新上传及伪造信号拒绝。


## 个人镜像管理与仓库生命周期验证

`SidebarNav` → `/images` → `ImagesView` → `registry.my_namespaces / my_registry_repositories`，服务端通过 `visible_repositories` 在计数 / 分页前排除不可见 repo。页面与 `RepositoryRegistryView` 共用 `RegistryImages`（镜像 / 标签 / digest）和 `RegistryNamespaceCreate`；写操作仍回到现有 repo Registry 路由。验证入口 `images.test.ts`、`registry.test.ts`、`test_registry.py`。

`RepositorySettings` → `AppConfirm.askVerified(..., 'repository_operation')` → `auth.issue_email_code` → `operation_verification.verify_repository_operation` → 原归档 / 移交 / 删除事务。CLI 在拥有者专用 `/repositories/{id}/operation-code` 请求同一用途验证码，生命周期请求必须包含 code，验证后重查原 Cookie 或 CLI Bearer；迁移 `0017_repository_verification` 约束用途与用户绑定。验证入口 `test_repository_verification.py`、`test_cli_http.py`、CLI 测试和真实 Git harness。

`RegistryCredentialList` → `registry.CredentialInput / create_credential`：永久令牌需显式确认，`expires_at: null`；`unexpired_credential` 统一有效列表排序与配额，`authenticate` 仍逐请求拒绝撤销 / 停用。迁移 `0018_registry_permanent_tokens` 及测试 `test_registry.py` / `test_migrations.py`。`GuideView` 提供同源 Registry 命令示例和浮动复制反馈，测试 `guide.test.ts`。系统仓库成员表格的拥有者行通过移交入口改变所有权，普通成员仍由逐次邮箱验证的成员 API 修改。

## CI 执行链路

- [ci_models.py](server/src/devops_server/ci_models.py)：CIWorker、CIRepository、CIRun、CIJob、CIAttempt、CIArtifact；迁移 `0019_ci`，PullCheck 关联具体 CI Job。
- [ci_schema.py](server/src/devops_server/ci_schema.py)：有界 YAML 1.2、Job DAG、执行环境和产物路径校验；[ci_github.py](server/src/devops_server/ci_github.py) 归一化 GitHub 工作流与矩阵，[ci_environment.py](server/src/devops_server/ci_environment.py) 解析 runs-on 和版本 / libc 约束。
- [ci_service.py](server/src/devops_server/ci_service.py)：不可变 Git 对象配置、事件去重、仓库池授权、调度/状态投影、任务 OAuth grant、租约回收和过期文件清理。
- [ci_socket.py](server/src/devops_server/ci_socket.py)：machine-only WSS 注册/连接代次、offer/accept/start、步骤日志 ACK、取消及结果；状态先提交再发送。
- [ci_api.py](server/src/devops_server/ci_api.py)：管理员邮箱验证的 worker 操作，仓库配置、运行/重跑/取消、成员日志下载及任务级流式产物传输。
- push 回执 → `collaboration_pushes.index_push` → `push_event`；PR 创建/后台同步 → `pull_event` → 同 SHA `ci/<job>` check；后台主循环定期 `sweep`。
- [agent.py](runner/src/devops_runner/agent.py) 负责永久身份、WSS、落盘 outbox、重连和执行协调；[executor.py](runner/src/devops_runner/executor.py) 定义未来隔离执行器的生命周期边界，并提供 NativeExecutor；POSIX supervisor / Windows Job Object 负责进程树；[environment.py](runner/src/devops_runner/environment.py) 采集系统和 libc，[workflow.py](runner/src/devops_runner/workflow.py) 执行 GitHub 风格步骤、文件命令及内置 Actions。
- 网页 `CIWorkersView`、`RepositoryCIView`、`CIRunView` 分别提供机器管理、工作流导航与运行详情；`RepositoryCISettings` 位于 Settings，`CICommitStatus` 在 Code / PR 展示当前 SHA 检查；沿用 api/auth/shared components，轮询含页面/账号代次校验。
- [deploy_runner.py](scripts/deploy_runner.py) 发布独立 wheel；Gateway 的 `/res/devops-runner/` 静态下载与 `/api/ci/worker` Upgrade 转发；`deploy.sh runner` 可单独发布。
- 验证：`test_ci.py`、`test_runner_deploy.py`、`runner/tests/test_native.py`、`runner/tests/test_workflow.py`、`website/tests/ci.test.ts`；[verify_ci.py](scripts/verify_ci.py) 由真实 Gateway harness 调用本机 worker。
