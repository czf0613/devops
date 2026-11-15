# DevOps 网站

侧栏「使用指南」路由 `/guide` 包含 GitHub Desktop 的 GCM 开关示意图、按当前 origin 生成的 OAuth 配置与 CLI tarball 安装命令。`OAuthConsentView` 同时显示 GCM / CLI 对应 scopes；`OAuthDeviceView` 要求用户先核对设备码，再通过原有 Cookie + CSRF API 决定是否授权。`GitAuthorizationList` 统一管理两种客户端授权，撤销不删除历史。

Vue 3、Vue Router、TypeScript、Vite；Vitest + Vue Test Utils + jsdom。网站只调用同源 API，不直接连接数据库。开始修改前阅读 [AGENTS.md](AGENTS.md)；功能与后端规则见 [DevOps README](../README.md)，文件导航见 [CODEMAP.md](../CODEMAP.md)。

## 开发与构建

依赖 Node.js `^22.12.0 || >=24.0.0`、pnpm 11；`package.json` 指定 `pnpm@11.11.0`。以下命令从仓库根目录执行：

```sh
pnpm --dir website install --frozen-lockfile
pnpm --dir website dev
```

通常先运行 `make -C . setup`，再在另一终端运行 `make -C . server`。开发网站为 `http://127.0.0.1:5188`，端口固定，冲突时退出；API 代理到 `127.0.0.1:8000`。`/oauth/authorize`、`/oauth/token`、`/oauth/revoke`、`/oauth/device/code` 有精确代理；`/oauth/consent` 和 `/oauth/device` 由前端处理。

```sh
pnpm --dir website type-check
pnpm --dir website test
pnpm --dir website build
pnpm --dir website preview
```

`build` 已包含 `vue-tsc -b`，产物位于 `dist/`。`preview` 是本机 `127.0.0.1:4188` 的构建预览工具，不作为生产部署方式。当前没有单独的 ESLint / Prettier 脚本，不要记录不存在的检查命令。

## 路由和状态

| 路由 | 页面 / 访问要求 |
|---|---|
| `/login`、`/register` | 公开邮箱认证页；已登录用户转回工作台 |
| `/` | 工作台，需登录 |
| `/work/pulls`、`/work/issues` | 本人跨仓库 PR / Issue 完整列表 |
| `/profile`、`/security` | 本人资料、账号安全 |
| `/repositories` | 可见仓库列表与创建 |
| `/repositories?create=1` | 打开现有创建仓库表单 |
| `/repositories/:owner/:name` | 兼容根地址，跳转 `/code`；旧 `?tab=settings` 跳转 `/settings` |
| `/repositories/:owner/:name/code` | Code：分支、文件树和预览 |
| `/repositories/:owner/:name/issue`、`/issue/new`、`/issue/:number` | Issue 列表、新建、详情 |
| `/repositories/:owner/:name/pr`、`/pr/new`、`/pr/:number` | PR 列表、比较并创建、讨论 |
| `/repositories/:owner/:name/pr/:number/commits`、`/changes`、`/checks` | PR 提交、文件变更、检查 |
| `/repositories/:owner/:name/ci`、`/ci/:runId` | GitHub 工作流导航、运行列表、步骤日志和产物；CI 配置位于 Settings，运行需写权限 |
| `/admin/ci/workers` | 全局管理员配置构建机、查看状态和安装指引；写操作逐次邮箱验证 |
| `/repositories/:owner/:name/settings` | 设置、成员、保护、协作元数据和合并策略 |
| `/repositories/:owner/:name/commit/:sha` | 完整 / 唯一短哈希的提交详情及引用 |
| `/notifications` | 本人的站内通知 |
| `/repositories/:id` | 兼容旧链接，读取后 `replace` 到名称地址 |
| `/oauth/consent` | 已登录用户确认 Git 客户端授权 |
| `/users`、`/users/:id`、`/audit`、`/admin/registry` | 全局 admin；菜单和路由守卫同时限制 |
| `/status` | 环境就绪状态，需登录；仅保留直接访问，侧栏不展示入口 |
| 其他路径 | 页面不存在；仍经过登录守卫 |

`router.beforeEach` 加载当前用户并保留未登录时的站内目标。`auth.ts` 保存响应式当前用户，服务端 Cookie 是登录凭据；不向 localStorage 写 Token。`App.vue` 对仓库父页面按 owner / name 设置 key，保持 Code / Settings 切换的公共外壳；子视图按路径重建，query 筛选和 hash 锚点更新不销毁编辑器。

`api.ts` 统一添加 `/api` 前缀、同源 Cookie、no-store、20 秒超时和 CSRF。写操作按需获取 CSRF，登录后接收新 Token；403 清理旧 CSRF，相关 401 发出 `session-expired`。错误通过 `ApiError` / `errorText` 展示，`Retry-After` 用于验证码倒计时。不要在各页面另写一套 fetch / Token 缓存。

协作表单统一使用 `drafts.ts` 的 `useDraft` 与 `DraftStatus`：仅暂存表单内容，按当前账号、仓库及编辑上下文隔离，初始化完成后恢复，成功提交 / 明确放弃时清除。新建表单还区分父 Issue 和来源；行评论区分文件与 base / head SHA。编辑草稿携带原乐观版本，存储失败不阻塞输入。恢复期限为 30 天，完整行为见 [协作说明](../COLLABORATION.md#issue-和讨论)。

协议级 `/oauth/token` 等不走 `/api` 客户端。授权页只按服务端验证后的流程跳转；不要将任意用户输入作为站外回调。

## 组件约定

全站使用 `src/style.css` 中的深色变量，不提供主题切换。通用基础组件在 `src/components/`：

| 组件 | 约定 |
|---|---|
| `AppButton` | 默认 `type="button"`；提交必须显式 `type="submit"`；复用 busy / disabled 和 danger 变体 |
| `AppInput` / `AppTextarea` | 标签、提示和 v-model，输入属性传到原生控件；AppInput 的 actions 插槽与输入框在同一行对齐 |
| `AppSelect` | 受控单选；弹层挂到 body，支持键盘、ARIA 和焦点，不被表格裁剪；`compact` 用文字按钮，`searchable` 在弹层内搜索选项；actions 插槽与下拉控件对齐 |
| `AppSwitch` | `role=switch`、明确标签、键盘操作，checked 由已保存的服务端状态驱动 |
| `AppConfirm` | `ask(message, title)` 返回布尔值；默认焦点取消，结束恢复焦点 |
| `AppPanel` / `AppBadge` | 面板边距、状态统一 |
| `AppNotice` | 成功提示默认 5 秒消失，支持手动关闭；v-model 同步清空消息，重复操作可重新显示。错误 / 信息常驻，持续状态用 duration=0。placement 默认 inline，适合页面错误 / 状态；复制等短反馈用 floating，共享固定浮层并堆叠，不占页面空间、不改变滚动或抢焦点 |
| `EmailCodeField` | 共享验证码请求和服务端重发倒计时，不在页面重复实现限流 |
| `SessionList` / `Pager` | 可聚焦滚动区域、分页后回顶部、当前设备标识和退出确认 |
| `SidebarNav` / `App` | 工作台二级菜单；整条侧栏可折叠为图标栏，窄屏折叠后隐藏，保留可访问名称并用 localStorage 记忆 UI 偏好 |

## 仓库详情的产品约定

- 仓库列表使用条目布局；名称可换行，简介最多两行并保留完整 title。右侧 `RepositoryActivity` 独立加载近 12 周提交趋势，窄屏放到条目下方。图表按 UTC 周一分桶，统计所有分支可达的唯一提交；达到后端读取上限时显示“至少”，失败显示“暂不可用”，不以假数据补齐。
- 第一个标签 Code 是默认入口；Issue / PR / Settings 都有独立路径，Settings 收纳连接仓库、简介、成员、协作目录和生命周期。`ref` / `path` / `page` query 记录代码位置；标签、后退和刷新保留分支与路径，异步请求带代次防止旧结果覆盖。
- `RepositoryCode` 使用 branches / contents API，分支名允许斜杠；目录每页 200 项，文本预览上限 1 MiB。目录列表下展示当前目录 README；按大小写不敏感的 README.md / README.markdown / README.mdown / README 顺序选择，分页不影响选择，符号链接不作为 README。不执行文件 HTML，符号链接只显示目标文本，子模块显示引用但不跟随外部仓库。LFS 指针显示原大小与需会话鉴权的下载入口。
- `RepositoryProtection` 只为拥有者显示 main / 推送保护开关，保存失败显示错误并保留旧值。Code 显示当前保护徽标；归档仍覆盖所有 Git / LFS 写入权限。
- 导航链接通过 `repositories.ts` 的 `repositoryUrl` 编码，不自行拼接未经编码的路径。移交成功后跳转新名称地址；内部变更请求继续使用 ID。
- 仓库顶部仅保留紧凑的名称、私有标记和返回入口。简介只在 Settings 查看 / 编辑，支持长文本换行，编辑上限为 2000 字。
- HTTPS 克隆地址以可换行的 code 区域完整显示，复制使用完整字符串；不使用截断的禁用 input。
- Code 工具栏的刷新 / HTTPS 克隆共用 `AppButton` 尺寸。`RepositoryCloneMenu` 提供紧凑地址框与复制按钮；Esc / 关闭按钮恢复触发器焦点，外部点击或焦点离开时关闭，复制反馈 2 秒后恢复。窄屏右对齐并限制弹层宽度，复制失败保留可手动选择的完整地址。
- 搜索添加与现有成员列表属于同一个面板。表格操作列靠前，编辑表单就地展开，移除经过确认，面板和列表保留一致间距。
- 添加和编辑复用 `RepositoryMemberForm`；`actions` 插槽让编辑状态的保存和取消位于同一操作栏。岗位预设为产品经理、项目经理、开发人员、测试人员、设计师、运维人员；可选“未指定”或输入自定义岗位，编辑已有自定义岗位时保留原值。
- 仓库权限来自后端 `permissions`。普通成员看列表；仓库管理员可管理普通成员；拥有者可管理其他成员并任免管理员。拥有者行没有编辑 / 移除入口。
- `allowAdmin=false` 时表单不提交 `is_admin` 字段，不能仅将它隐藏后仍提交默认值。后端拒绝非拥有者修改该字段。
- `RepositorySettings` 仅向拥有者显示；归档 / 取消归档、移交和删除逐次验证本人邮箱，使用独立 `repository_operation` 用途。移交与删除另需完整名称确认，成功后的权限和页面刷新；账号或仓库切换清除待处理状态。

README 使用 [markdown-it](https://markdown-it.github.io/markdown-it/)（关闭原始 HTML）和 [DOMPurify](https://github.com/cure53/DOMPurify) 的明确标签 / 属性白名单。支持标题、列表、代码、引用、表格、删除线；相对链接保持当前仓库与分支，标题锚点使用独立前缀。相对图片走有登录 / 仓库权限的 `/image` API，仅支持不超过 5 MiB 的 PNG / JPEG / GIF / WebP；不执行 SVG 或 HTML。外部 HTTP(S) 图片使用 no-referrer，其他危险协议被拒绝。源码预览仍只显示转义文本。页面不展示 Git LFS 教程。

新建仓库自动包含 README 模板和 main 首次提交；历史仓库内容不自动回填。

账号安全页的设备列表使用内部滚动：桌面右侧高度跟随同排左侧卡片，最小 520px；窄屏变成单列时设备区域高 520px。标题和分页保持可见，不能随设备数不断拉高页面。共享 `SessionList` 的非 fill 模式滚动区域最大 400px。

登录设备只显示服务端返回的有效会话，总数与分页不包含已退出、已过期或其他失效记录。退出成功后立即移除对应设备；末页变空时回到最后一个有效页。本人账号安全页与管理员的人员详情页共用此行为。

Git 客户端授权只显示最近 100 条未撤销记录，撤销成功后立即移除；已过期但未撤销的记录保留到期提示。每次成功刷新将授权续期至刷新后 30 天，连续 30 天未刷新或主动撤销后需重新网页授权；面板不展示列表上限、网页登录独立性与自动续期流程说明。到期日期展示后端返回的最新期限。侧栏底部显示当前网页构建的包版本和当年 kevinc.ltd 版权信息，不再展示系统状态入口。网站版本独立于 server 和 CLI；使用指南单独读取 CLI 包版本生成下载地址，见 [版本管理](../README.md#版本管理)。产品展示名、浏览器标题和登录页统一为 `DevOps`；内部 Cookie / localStorage 键不因品牌调整而变更。

## 验证

| 文件 | 覆盖 |
|---|---|
| `tests/components.test.ts` | 下拉键盘 / 鼠标、弹层位置、全局角色导航和工作台二级菜单 |
| `tests/ci.test.ts` | CI 设置权限、工作流与分支选择、SHA 检查与账号切换隔离、日志转义 |
| `tests/password-flows.test.ts` | 本人专用验证码、管理员仅清除密码 |
| `tests/session-list.test.ts` | 退出后移除、末页回退、刷新失败时不恢复已退出设备、管理员全部退出及当前设备退出 |
| `tests/oauth-consent.test.ts` | 授权加载、拒绝 / 允许、错误与回调边界 |
| `tests/repositories.test.ts` | 名称路由、Code / README、保护、成员权限、岗位、生命周期和简介位置 |
| `tests/notices-markdown-shell.test.ts` | 提示定时关闭 / 重复反馈、侧栏折叠记忆、Markdown 注入拒绝及相对链接 / 图片 |
| `tests/repository-activity-authorizations.test.ts` | 仓库条目、提交趋势加载 / 空值 / 错误 / 截断 / 过时响应、未撤销授权与撤销后移除 |
| `tests/repository-clone-menu.test.ts` | 克隆面板关闭 / 焦点恢复、完整地址复制、反馈清理及复制失败 |

聚焦测试示例：

```sh
pnpm --dir website test -- tests/repositories.test.ts
```

测试使用 API 替身；`tests/setup.ts` 的 dialog 等补丁不等同于实际浏览器布局。DevOps 面向专业人员的桌面开发工作，不安排手机适配或窄屏验收。变更布局后按需要在桌面浏览器检查长用户名 / 无空格简介 / 长克隆地址 / 多成员 / 多设备及键盘操作。权限和生命周期测试不能用真实用户仓库代替合成数据。

## 发布

网站在本机安装锁定依赖并构建，通过 tar.gz / scp 发布到服务器静态目录。Gateway 镜像不包含网页产物，不安装 Node.js。已完成首次部署配置后，从仓库根目录运行：

```sh
make -C . website-deploy DEPLOY_HOST=devops
```

这是远端写操作；完整参数、失败行为和首次迁移见 [脚本 README](../scripts/README.md) 与 [Gateway README](../gateway/README.md)。不要只覆盖 `index.html`，也不要立即删除旧哈希 assets，否则已打开页面的懒加载可能失效。

## Issue / PR 页面

`IssueListView` 共用列表 / 筛选 / 批量操作；`NewItemView` 共用 Markdown / 表单模板与元数据；`ItemDetailView` 展示讨论、关联、审阅及合并。`DiffViewer` 按文件读取补丁，维护文件分页、查看标记和稳定行锚点；行评论提交显示中的 base / head，分支改变由服务端拒绝旧版本。

`router/navigation.ts` 区分页面导航与当前页面状态更新：筛选和差异文件切换保留滚动位置，浏览器后退恢复已保存位置，锚点由内容组件加载完成后定位。同一 PR 的讨论 / 提交 / 文件变更 / 检查复用详情与已打开编辑器；不同记录、新建表单的父项或来源改变时重新初始化。差异加载期间保留原区域高度；本地点击行号更新链接但不将已可见行滚到页顶。通知标题、未读 / 全部及全部已读操作位于同一行。

局部页面状态更新不重复请求 `/me`，业务 API 继续逐次鉴权。切换页面时，会话查询遇到网络错误或 5xx 会保留当前页面并提供重试；首次载入先确认会话再挂载业务内容，只有明确的 401 才跳到登录。已退出登录时直接进入登录页；不会以旧的页面状态替代服务端权限检查。

Issue / PR 列表顶部将搜索标题与正文、更多筛选和创建按钮放在同一行，不重复显示当前页签标题；作者、标签、类型、里程碑、负责人和排序位于列表表头的紧凑菜单，成员与元数据选项可在弹层内搜索。更新日期与保存的筛选在“更多筛选”中，已选条件可逐项移除；query 保存条件，刷新和后退会同步搜索框。子 Issue 按行展示，展开只读取并显示下一级子项，保留层级缩进，不重复父关联与整块操作面板；进入子项详情可管理其其他关联。

Settings 的标签、Issue 类型、里程碑与模板使用带数量的分类列表；新增按需打开，编辑替换当前条目，取消或 Esc 放弃本次修改并返回操作按钮。切换分类、离开或刷新保留按账号 / 仓库 / 分类 / 条目隔离的本地草稿，再次打开相应编辑器恢复；成功保存或取消才清除。颜色支持色盘、十六进制值及预览；模板支持 Markdown 预览、字段增删与排序。PR 合并规则按合并方式和前置要求分组，显示未保存状态并可取消，加载失败时不提供默认规则的保存入口。Catalog 和合并规则独立加载，单项失败不阻塞另一项。

`DiscussionMarkdown` / `discussion-markdown.ts` 禁止原始 HTML，经 DOMPurify 白名单后展示安全链接、任务列表和附件。正文 / 评论传递内容身份，保证移交后的相对引用不变。`DiscussionEditor` 复用 API / Cookie / CSRF 上传；尚未发表的附件只对上传者可见。功能边界和具体路径见 [协作说明](../COLLABORATION.md)。

## 个人工作台

`HomeView` 使用 `DashboardItems` 展示开放 PR / Issue 各 5 条，右侧由 `DashboardActivity` 和 `DashboardNotifications` 独立加载；`WorkItemsView` 复用相同列表展示每页 25 条。首页 query 使用 `pr_scope` / `issue_scope`，完整列表使用 `scope` / `state` / `q` / `page`，支持刷新及后退。每个区域有独立空态、错误和重试；`useDashboardResource` 在账号切换和请求更新时清空旧结果，并忽略过期响应，窗口重新获得焦点时重新查询。

`RecentRepositories` 只在工作台及跨仓库列表的展开侧栏展示。成功读取仓库后，`recent-repositories.ts` 按账号在本机最多保存 20 个编号，不保存仓库名、正文或凭据。展示顺序和名称均由服务端重新核验权限后返回，历史记录本身不授予访问权。通知预览不会自动标记已读。页面行为测试见 `tests/dashboard.test.ts`。

## Registry 页签

`/repositories/:owner/:name/registry` 使用 `RepositoryRegistryView` 管理 namespace 和镜像标签；只有拥有者能修改绑定、转移或删除 digest，归档仓库只读。`RegistryCredentialList` 仅用于账号安全，统一创建用户级令牌；repo 页只提供账号安全链接。令牌仅在创建时留在组件内存，关闭展示、切换账号或卸载即清除，不进入草稿或本地存储。`RegistryAdminView` 位于系统管理菜单，支持全局 namespace 搜索、绑定状态筛选、已删除 repo 的归属找回、绑定 / 解绑确认和版本冲突提示，不提供绕过 repo ACL 的镜像访问。测试入口 `tests/registry.test.ts`；权限和删除语义见 [Registry README](../registry/README.md)。

`RegistryGCPanel` 在管理员 Registry 页面提供人工维护提醒、确认发起、活动任务轮询及历史分页；服务器不自动阻止写入，界面明确要求维护窗口内停止所有推送和删除。异步结果按账号 / 请求版本隔离；测试见 `tests/registry-gc.test.ts`。通用 `AppConfirm` 支持自定义确认按钮文案、完整长文本换行及正文区域垂直滚动，保持操作按钮可见，关闭恢复焦点且不触发页面滚动。


## 管理操作邮箱验证

系统管理新增 `/admin/repositories` 列表与 `/admin/repositories/:id` 详情：搜索和筛选全站仓库、调整成员读写 / 岗位 / 管理权限、更换拥有者、归档 / 取消归档和软删除。复用 `RepositoryMemberForm`，详情只展示管理元数据，不自动加载私有代码。移交保留原拥有者的读写和管理员权限，后续可单独移除；已删除仓库仅查看记录。

`AppConfirm.askVerified` 复用确认弹窗与 `EmailCodeField`，显示操作对象、后果和当前管理员邮箱；提交成功前保持弹窗，错误原地显示，busy 期间防止重复提交。每次重新打开清空验证码，账号 / 邮箱 / 角色变化或卸载会取消待确认操作。验证码只存在于组件内存，不缓存特权或写入浏览器存储。普通 `ask` 行为保持不变。

Registry 管理的绑定、解绑、整组删除与 GC 均使用该流程。删除 namespace 包含全部镜像；立即停止访问，在维护窗口 GC 清理，名字保留。可筛选已删除记录，GC 完成后刷新清理状态。测试入口为 `repository-admin.test.ts`、`registry.test.ts`、`registry-gc.test.ts`，布局另用桌面 Safari 验收。


“工作台 → 镜像管理”位于工程仓库下方，路由 `/images`。`ImagesView` 将 namespace 名称、关联 repo、实际权限和绑定状态汇总展示，筛选 / 分页保留到 URL；创建先选择本人拥有且未归档的 repo。`RegistryImages` 与 repo Registry 页签共用镜像列表 / 标签分页、平台与证明信息、复制及 digest 删除；`RegistryNamespaceCreate` 共用创建表单。异步结果按账号 / namespace 隔离，解绑后不加载镜像内容。测试见 `images.test.ts` 与 `registry.test.ts`。

系统仓库详情的刷新区与面板保留间距，添加成员在成员面板内、表格之前展开；更换拥有者在生命周期面板内展开。`AppConfirm.askVerified` 可选择管理员或仓库操作的验证用途，错误保留在当前弹窗，不能绕过验证码直接写入。

Docker 登录令牌可选永久有效，显示风险提示并要求勾选确认；选择不同期限、创建成功或切换账号会清除确认状态。列表将空到期时间显示为“永久有效”，保留撤销入口。

使用指南的 GitHub Desktop 与 CLI 卡片在同一网格行等高；Docker Registry 部分占整行，只说明账号安全中的令牌准备和原生 `docker login`。每条命令独立展示和复制，长命令保持单行、局部横向滚动，命令与复制按钮垂直居中；复制反馈使用浮层。管理员成员表格将操作列放在姓名之后，普通成员和仓库管理员可编辑 / 移除；拥有者行明确显示移交入口及所有权权限说明，仅有拥有者时显示添加成员引导。测试见 `registry.test.ts`、`guide.test.ts`、`repository-admin.test.ts`。
