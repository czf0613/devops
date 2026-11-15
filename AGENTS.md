# Agent 工作规范

本仓库是 DevOps 开发协作平台的独立仓库，从旧 monorepo 的 `devops/` 模块整体迁出。规范适用于整个仓库；进入子目录时继续读取更近的 `AGENTS.md`，模块局部规则位于 `server/`、`website/`、`gateway/`、`scripts/`。用户本次明确指令和已有授权优先；不要因为本文再次询问已经授权的同一操作。

## 开始工作

1. 先查看 `git status --short`、当前分支及相关差异，保留其他人的未提交工作。
2. 阅读 [README.md](README.md)、[CONTEXT.md](CONTEXT.md)、[CODEMAP.md](CODEMAP.md)，再阅读目标模块的 README / AGENTS。
3. 用当前代码、锁文件和实际配置核实文档。日期快照、旧迁移名称和历史讨论不能代替当前实现。
4. 当前 Git 业务范围是本仓库根目录下的 `server/`、`website/`、`gateway/`、`runner/`、`cli/`、`registry/`、`scripts/`、`sql/`。用 `git ls-files` 确认归属，不把缓存、构建目录或历史工作副本写成当前已交付模块。
5. 搜索先用 `rg` / `rg --files`，限定模块；不要输出 `.env`、证书、数据库备份、原始会话或 OAuth 凭据。

## 工作与提交

- “先提交所有更改”意味着先做独立 checkpoint，再开展新工作。提交前检查包含未跟踪文件的完整清单，并执行 `git diff --cached --check`。
- 本地提交不自动包含 push、合并或远端部署。根据当前任务已有授权继续；不要在文档里把某次服务器或浏览器授权固化为今后的无限授权。
- 保留原有工作目录和分支关系；需要新分支时默认使用 `codex/` 前缀。不要随意 stash、reset 或覆盖其他工作。
- 复用已有服务、路由、API 客户端、通用组件和部署脚本。不要引入第二套认证、配置来源或绕过权限的管理员捷径。
- 代码修改围绕本次任务展开；发现无关问题，记录影响而不是顺手重构整个模块。

## 当前系统的关键边界

- 产品展示名使用 `DevOps`。历史仓库路径、Cookie 名称、OAuth client ID 和内部协议标识保持兼容；品牌文案调整不能使已有会话或客户端配置失效。
- DevOps 仅支持邮箱验证码登录；兼容密码服务于旧系统，不能作为本站登录或 Git 密码。
- 全局 `admin`、仓库拥有者、仓库 `is_admin`、Git `access` 和岗位 `role` 是不同维度。全局管理员不能隐式读取其他人的私有仓库。
- 浏览器会话及 Git OAuth 授权可在数据库中吊销，不是 JWT。退出网页登录不等于退出 Git 客户端。
- 用户核心资料和可选密码材料当前实际存放在 `users`；不要按早期设计臆造已存在的 `user_credentials` 或 profile 表。
- `.env` 只保存连接参数；业务配置从 PostgreSQL `config` 加载，修改后重启相关后端实例。
- 仓库物理路径由数据库 ID 决定；拥有者 / 名称只是访问地址。删除当前是软删除，不能替换成直接 `rm -rf`。
- 代码浏览只读取 Git 对象，不检出执行仓库文件；README 通过禁用原始 HTML 的 Markdown 解析和 HTML 白名单清洗后渲染；推送保护必须在服务端接收钩子执行。LFS 与 Git 共用可吊销 OAuth / ACL，不得增加 Cookie 或密码回退。
- 前端在本机构建并单独发布；Gateway 只读挂载静态目录，不在 Dockerfile 里重新加入 Node.js 构建阶段。

详细规则和权限矩阵见下文各节与 [README.md](README.md)。

## Registry 边界

Registry 的协议与存储边界见 [registry/README.md](registry/README.md)。修改时保持：每个 `/v2/` 请求使用当前 DB ACL；namespace 名称在解绑 / 删除源 repo 后仍保留；mount 同时检查源读和目标写；manifest 删除仅限 repo 拥有者；Docker / 镜像内容访问没有全局管理员旁路；全局管理员可在独立 Registry 管理 API 显式重新分配 namespace，必须审计、确认权限影响、校验目标 repo 状态并检测版本冲突。Docker 令牌只关联用户；永久有效用空到期时间表示，必须显式确认风险，仍计入有效令牌限额并逐请求检查撤销与 ACL；上线前的项目限定令牌通过迁移删除，不保留兼容分支。GC 只允许管理员确认人工维护窗口后执行，不自动切只读或阻断写入；使用固定官方版本、全局互斥、先预检再回收，过期 / 中断任务不自动重跑。私有 Registry 不暴露端口、不重定向存储下载，专用令牌摘要存储且不能与 Cookie / Git OAuth / 兼容密码互换。

## 开发入口

以下命令从仓库根目录执行：

```sh
make setup
make server
make website
```

后两条为独立终端的长驻进程。`setup` 会初始化本地依赖和数据库，不是远端部署命令。完整命令说明见 [README.md](README.md)。

## 不变量

1. Python 3.14 / uv、Vue 3 / TypeScript / pnpm 是现有技术栈；依赖变更同步维护对应锁文件。
2. PostgreSQL 是账号、会话、配置和仓库元数据的权威来源。Redis 只允许 DB 0，目前负责共享限流，不承载登录会话。
3. `ConnectionSettings` 从 `.env` / 进程环境读连接信息；`Settings` 从 `config` 读业务配置。不要增加业务环境变量回退分支。
4. `config.is_secret` 只影响敏感值展示，配置实际是原始 JSONB。日志、异常、文档、测试输出不得泄露这些值或用户凭据。
5. 普通成员可以创建仓库；全局 `admin` 不隐式取得私有仓库内容访问权。强制成员 / 权限 / 所有权 / 生命周期管理仅走独立 `/api/admin/repositories`，每次写操作验证本人邮箱，保留仓库行锁、并发版本和审计。
6. 岗位、读写、仓库管理、所有权分开处理。归档只覆盖有效 Git 写权限，不能覆盖保存的成员权限。
7. 仓库唯一性约束仅覆盖未删除的 `(owner_id, name)`。ID 存储目录不复用、不按用户输入拼路径、不因移交而迁移文件。
8. 仓库拥有者的归档 / 取消归档、移交、删除也必须逐次消费 `repository_operation` 邮箱验证码，Cookie 与 CLI 使用同一验证规则；不得让 `--yes` 跳过验证。各项写入在当前权限下完成，移交和成员编辑需要考虑并发。保持仓库行锁、重新加载与提交前校验。
9. 权限拒绝既要出现在页面，也必须由 API / Git 鉴权保证。前端隐藏按钮不是安全边界。
10. 静态网站本机构建，父目录只读挂载给 Gateway。不要把 `current` 软链接本身作为 Docker 挂载源。
11. 创建仓库只在独占新目录生成 README 初始提交；不回填或重写已有仓库。文档渲染保留 HTML / URL 清洗与仓库 ACL。
12. server / gateway 只构建 `linux/amd64`（kdevops 是 x64），镜像用 `docker save` 经 SSH 交给服务器 `docker load`，两端核对 image ID；不使用远端 registry，也不在服务器上 `docker pull`。Gateway 使用 host 网络直接绑定宿主机 443（IPv4 + IPv6）与 80（仅 301 跳转），server / registry 仅发布到 `127.0.0.1`；HTTP/3 需同时维护 Nginx `listen [::]:443 quic`、Alt-Svc 与实际握手验收，保留 TCP 访问。
13. main / 推送保护仅拥有者调整，在镜像内 pre-receive 执行；策略不可信或缺失时拒绝。LFS 复用 Git OAuth 和 ACL，按仓库隔离存储，流式校验后原子发布。

## 按改动选择验证

| 改动 | 通常需要的验证 |
|---|---|
| 纯文字 / 文档 | 本地链接、命令工作目录、代码事实、`git diff --check` |
| 前端布局 | `pnpm --dir website build`，按需要检查实际桌面浏览器；不做窄屏 / 手机验收 |
| 前端成员 / 登录交互 | 对应 Vitest 测试和生产构建 |
| 认证、配置、用户 API | 对应后端测试；跨模块变化运行完整 `make check` |
| 仓库权限、生命周期 | `test_repositories.py`、`test_git_oauth.py`、前端仓库测试 |
| Code、保护、LFS | `test_repository_code.py`、`test_git_lfs.py`、前端仓库测试及真实 Git / LFS 集成 |
| 模型 / 数据库约束 | 新 Alembic 迁移、`test_migrations.py`、模型差异检查 |
| Nginx / CGI / OAuth / Git 路径 | 本地镜像重建及 `verify_git_gateway.py` |
| 静态发布 | `test_website_deploy.py`，挂载与原子切换集成测试；远端验收仅在部署授权内 |

后端测试依赖本地 PostgreSQL / Redis，创建随机 schema 和限流键前缀；使用测试邮件替身，禁止 `FLUSHDB`。不要为了通过测试把连接改成共享测试服务器。

管理员 Registry 绑定、解绑、整组删除和 GC 同样必须逐次消费 `admin_operation` 邮箱验证码。删除 namespace 原子关闭访问并保留名称，所有查询 / 绑定 / Docker ACL 都排除删除标记；不可恢复、不可抢注。GC 在人工维护窗口通过官方内部 API 删除这些 namespace 的全部标签，再预检 / 回收，保留其他 namespace 的共享层；仅 GC 成功后记录已清理，失败不能伪报完成或自动重试。

mount 来源无读权限时，仅在目标写权限通过后允许回退普通上传：由受信任的鉴权响应指示 Gateway 移除 mount / from，不能让无权访问的源 blob 建立目标引用。测试应同时覆盖目标 404（未复用）、上传本地字节后成功、源读取拒绝及客户端伪造 Header 无效。

## 跨模块联动

- 改 API 字段：同步后端响应、`website/src/api.ts` 或 `repositories.ts`、调用页面与测试。
- 改仓库规则：同时检查 `repositories.py`、`git_oauth.py`、`git_lfs.py`、Gateway hooks、RepositoryDetail / Code / Protection / MemberForm / Settings，以及拒绝路径测试。
- 改 Git URL：同时检查后端正则、Nginx map / location、前端链接和真实 Git 测试。
- 改配置 key：同步 `Settings`、初始化 / 迁移、配置测试、示例与文档；现有数据库不能因少了新 key 无法启动。
- 改运行路径：同步 Dockerfile、Compose、启动检查、发布脚本、集成脚本和对应 README。

## 操作边界

`make website-deploy` 会实际通过 SSH 写服务器；`configure-gcm.sh` 会修改当前用户的全局 Git 配置。不要把它们当作只读检查命令。部署前核对本次授权和目标，已有明确授权时继续执行，无需重复申请。

本地 `compose.yaml` 与服务器 `compose.deploy.yaml` 的目录、端口和卷不同。不要把本机 `.env` 复制成 `.env.deploy`，不要把 `docker compose down -v` 用作普通重启步骤。

## CI 边界

独立 `runner/` 执行可信代码；Docker 内的协作 worker 只做服务端控制、事件和保留期处理，不运行构建脚本。保留 WebSocket 注册、摘要身份、连接代次、租约和任务级 OAuth 范围；未配置的仓库默认启用并使用 default；明确关闭的仓库不能被默认规则重新开启。资源池由仓库拥有者配置，显式授权在移交后失效；保留运行者实时写权限检查。`ci/` PR check 名称禁止手工写入。新执行器通过 Executor 生命周期加入，不能把 native 标成沙盒。日志/产物遵守仓库 ACL 和保留期；跨平台真机验证状态记录在 CONTEXT，不能把 wheel 的通用标签当作六平台验收。

## 验证与交付

- 按改动选有意义的测试，修复失败后再交付。纯文档修改检查路径、链接、命令和事实，不为文字改动强制重跑全套业务测试。
- 改动权限、认证、事务、迁移或 Git 网关时，至少覆盖正常操作与拒绝路径；不得通过删除防护断言让测试通过。
- 默认使用本地隔离 schema、临时文件和邮件替身，不用真实用户做吊销、移交、删除或提权试验。
- 对部署任务，先识别当前镜像、挂载和配置，再发布所需组件；验收要检查实际运行版本和健康状态，不能只看命令返回 0。
- 完成报告说明改了什么、验证了什么、是否提交 / 推送 / 部署，以及剩余限制。未执行的验证不能写成通过。

## 文档维护

| 文件 | 内容职责 |
|---|---|
| README.md / 模块 README | 使用方法、稳定行为、准确命令和排障入口 |
| AGENTS.md / 模块 AGENTS | 后续修改需要守住的规则、范围与验证方式 |
| CONTEXT.md | 带日期的当前阶段、决策、已验证状态及未完成功能 |
| CODEMAP.md | 目录、关键符号、调用链、变更影响和测试入口 |

文档示例使用占位值，不复制真实邮箱、Token、密码或会话。部署快照集中放 CONTEXT.md，并注明重新核验方式；避免在每个 README 中复制会变动的测试数量和镜像标签。
