# DevOps 后端

CLI 认证沿用数据库可吊销 OAuth，增加独立 public client `devops-cli`、设备码和 api:read/write scopes。`RepositoryAuthenticated` 对仓库 / 协作 API 分流 Cookie 与 Bearer；带无效 Authorization 时不回退 Cookie。中间件仅在成功验证 Bearer 后豁免对应 API 的 CSRF，路由事务再次验证。网页登录 / 全局人员管理保持 Cookie + CSRF。异步合并保存浏览器 session 或 CLI grant 的来源，worker 执行前再次核验有效性、ACL 与 SHA。详见 [CLI 文档](../cli/README.md)、[迁移链](migrations/README.md)；测试入口为 `test_cli_oauth.py`、`test_cli_http.py`。

Python 3.14 / uv / FastAPI / SQLAlchemy 2 / psycopg 3，使用 PostgreSQL、Redis DB 0 和本机 Git。继承 [模块工作规范](AGENTS.md)；账号规则与配置示例见 [DevOps README](../README.md)，完整代码入口见 [CODEMAP.md](../CODEMAP.md)。

## 启动与配置

以下命令从仓库根目录执行：

```sh
make -C . setup
make -C . server
# 另一个终端
make -C . worker
```

服务只监听 `127.0.0.1:8000`，开发使用 reload。OpenAPI 在 `/api/openapi.json`，交互文档在 `/api/docs`。`/api/health` 只检查进程，`/api/health/ready` 分别执行 PostgreSQL `SELECT 1` 与 Redis `PING`。

`pyproject.toml` 的 `project.version` 定义后端版本；FastAPI 从已安装的 `devops-server` 包元数据读取，体现在 OpenAPI 的 `info.version`。网站与 CLI 独立维护版本，见 [版本管理](../README.md#版本管理)。仅调整版本号无需数据库迁移。

首次 setup 安装锁定依赖、启动本地数据库、升级迁移并初始化配置，不创建用户。仅安装 Python 依赖可运行 `uv sync --project server --locked`。生产镜像的迁移和配置初始化是显式前置步骤，不在应用启动时偷偷执行。

`ConnectionSettings` 从 `.env` / 环境读取 PostgreSQL 与 Redis 连接信息；`Settings` 从数据库 `config` 表加载运行参数。启动必须有完整有效配置和认证密钥。缺少 SMTP 不阻止启动，但发验证码返回 503；缺少数据库配置则不能启动。

配置查看 / 修改命令从 `仓库根目录` 执行，具体 JSON 示例见 [共享运行配置](../README.md#共享运行配置)。`config_cli list` 脱敏，不要通过直接 SELECT 所有配置来排障。业务配置没有环境变量覆盖或热加载，更新后重启后端实例。

验证码邮件由 `mail.SMTPMailer` 发送 `multipart/alternative`：纯文本在前，带 DevOps 品牌的 HTML 在后。注册、登录、换邮箱和兼容密码验证共用 `code_email_html`，用途和有效期随实际请求 / 配置生成。HTML 使用表格与内联样式，验证码保持连续文本可复制；插值先转义，不加载外部图片、字体或跟踪资源。此更改不调整发件地址、SMTP 传输和认证规则。邮件测试使用 SMTP 替身检查两种正文、用途、有效期及转义，不向真实用户发送测试邮件。

## 应用结构与事务

- `main.create_app` 允许注入 settings、engine、mailer、Redis 和限流器；测试用这一入口替换邮件和连接。
- `dependencies.Database` 管理请求事务，使用 `Depends(scope="function")`，在响应 / Cookie 发出前提交。数据库错误回滚，客户端不应得到未提交的登录态。
- `Authenticated` 每次读取当前账号、会话和角色；全局管理员依赖用于显式系统管理，不可替代普通仓库或内容 ACL。
- `services.py` 复用审计、验证码、会话和兼容密码操作。拒绝路径可能需要提交错误次数或失败审计；重构事务时不能一概回滚这些安全状态。
- `Input` 拒绝额外字段，错误响应不回显密码 / 验证码。SQL engine 隐藏绑定参数，模型延迟加载敏感字段。
- `security.py` 放原语，`auth.py` / `users.py` / `repositories.py` / `git_oauth.py` 负责协议和业务边界；`repository_code.py` 只读 Git 对象，`git_lfs.py` 处理大文件协议。

## API 分区

工作台使用仅接受浏览器会话的 `/api/me/dashboard/items`、`/activity`、`/repositories`，通知沿用 `/api/me/notifications`。`items` 接受 `kind=issue|pull`、`scope=related|authored|assigned|review_requested`、`state=open|closed|merged|all`、标题搜索 `q`、`page` 和 `page_size`（默认 5，最大 25）；`assigned` 用于 Issue，`review_requested` 用于 PR。`repositories` 接受最多 20 个重复的 `recent` 编号参数及拥有者 / 名称搜索 `q`；无有效历史时补充可见仓库。

汇总复用 `visible_repositories`，在 SQL 统计、排序、分页前应用拥有者 / 成员 ACL 和软删除条件。相关事项包括本人创建、被分配、被请求审阅，以及发表过评论或审阅；审阅草稿和已删除评论不计入。待审阅仅包括非草稿、未归档的开放 PR，当前 head 上在最近审阅请求之后的批准 / 请求修改视为已处理，重新请求审阅或新 head 会重新进入队列。动态只提供公开的创建、评论、审阅、关闭、重开和合并摘要，不返回原始事件数据、正文或私有草稿。查询读取 worker 维护的 PR head 快照，不逐条运行 Git；推送处理存在短暂延迟。验证入口为 `test_dashboard.py`。

| 分区 | 认证与职责 |
|---|---|
| `/api/auth` | CSRF 引导、邮件码、注册、登录、退出；写请求同样要求 CSRF |
| `/api/me` | 本人资料、换邮箱、兼容密码和网页登录设备 |
| `/api/users`、`/api/roles`、`/api/audit-events` | 全局管理员；不提供通过 API 授予或移除 admin 的入口 |
| `/api/repositories` | 当前用户可见仓库；成员管理、生命周期采用仓库权限 |
| `/api/admin/repositories` | 全局管理员的仓库元数据、成员 / 权限 / 移交 / 归档 / 软删除；写操作要求单次邮箱验证 |
| `/api/admin/registry` | 全局 namespace 管理和手动 GC；写操作要求单次邮箱验证 |
| `/api/admin/ci/workers` | 全局管理员配置构建机；写操作要求单次邮箱验证 |
| `/api/repositories/{id}/ci` | 沿用仓库 ACL 的配置、运行、日志和产物；只有拥有者可以授权资源池 |
| `/api/ci/worker`、`/api/ci/artifacts/{id}` | 独立 worker WSS 与任务级产物传输；不接受 Cookie 回退 |
| `/oauth/authorize`、`/oauth/token`、`/oauth/revoke` | GCM OAuth 协议；后两者精确 POST 用 code / PKCE 或 Token 鉴权 |
| `/api/oauth/authorization-requests`、`/api/me/git-authorizations` | 浏览器确认和个人授权管理，仍要求登录 / 写入 CSRF |
| `/_internal/git/authorize` | Nginx 内部 Git 鉴权；不暴露于公网及 OpenAPI |

浏览器写请求要先获取 `/api/auth/csrf` 的 Cookie 和 Token，再带 `X-CSRF-Token` 与允许的 Origin。登录后 CSRF 绑定会变化，不要缓存旧 Token。OAuth 协议例外不可扩大成 `/oauth/*` 全路径豁免。

仓库无访问权或已删除时返回 404；可见但不能管理通常返回 403。Git 无效凭据返回带 Basic challenge 的 401，有凭据而无权限返回 403。具体参数以 OpenAPI、请求 schema 和 [Gateway 接口表](../gateway/README.md) 为准。

系统管理员通过 `POST /api/auth/email-codes` 申请 `purpose="admin_operation"`，邮箱必须是当前管理员的邮箱。管理写请求提交 `code`，由 `admin_verification.py` 在业务事务内消费；成功后不能复用，不能使用其他用途或其他用户的验证码。复用原有有效期、错误次数、Redis 限流和邮件模板，提交时重查邮箱、账号、会话及 admin 角色，不签发临时特权 Token。列表、搜索、详情等只读请求无需验证码。

仓库管理详情仅包含身份、状态和成员字段，不读取代码、Issue 或 PR 内容。所有强制修改使用仓库锁及 `expected_updated_at`，普通成员修改也推进这一版本；冲突返回 409。移交沿用原有语义：原拥有者成为读写仓库管理员，其他成员及 Registry 绑定保留，可再单独移除原拥有者。删除仍为软删除，不删除 Git 文件或历史。审计使用 `repository.admin_*`，记录操作者、对象及必要的前后状态。

`GET /api/repositories/{id}/activity` 使用相同私有仓库 ACL，按 UTC 周一统计包含当前周在内的 12 周提交数。读取所有分支可达的唯一提交，以 committer 时间归档，不统计仅被 tag 引用或未来时间的提交；`--since-as-filter` 避免时间倒序的提交使遍历提前终止。单次读取最多 10,000 条匹配提交，超出时 `truncated=true`，Git 子进程最多运行 10 秒。返回统计不等于 push 次数；空仓库返回全零，存储故障返回错误，独立于仓库列表加载。

`GET /api/me/sessions` 与管理员的 `GET /api/users/{id}/sessions` 只返回有效登录设备：未吊销、尚未到期、密码版本匹配且账号正常。过滤在总数统计和分页之前完成，响应使用同一时间点判断有效期；历史会话仍保留在数据库中。

网页登录会话按活动滑动续期，沿用 `SESSION_DAYS`（默认 7 天）。`dependencies.renew_session` 在成功请求的事务结束前按分钟节流，锁定会话后重新检查当前时间、撤销状态与账号 / 密码版本，再更新活动时间和到期时间。过期或失效会话不恢复，退出不重新签发 Cookie。提交成功后 `main.security_headers` 在实际响应上续期会话与现有 CSRF Cookie，Token 值保持不变；直接返回文件 / Response 的接口同样适用。验证入口为 `tests/test_session_renewal.py`。

`GET /api/me/git-authorizations` 在排序和 100 条上限之前排除已撤销记录，保留过期但未撤销的记录；数据库历史不删除。Access token 最长 1 小时，refresh 每次轮换，旧 refresh 重放撤销整条授权。新授权有效期 30 天，每次成功刷新将到期时间设为本次刷新时间加 30 天；活跃客户端可持续续期，连续 30 天未刷新则需重新网页授权。

刷新在授权行锁内重新读取当前时间、校验有效期、撤销状态、账号和 scope，再以同一事务轮换凭据与续期。拒绝请求不延长有效期，过期或撤销的授权不能恢复。现有有效授权在下次刷新时自动续期，无需迁移或重新授权；网页登录会话独立按网页活动续期，不影响 Git / CLI 授权期限。

## 数据与存储

[models.py](src/devops_server/models.py) 是 ORM 结构入口，[迁移 README](migrations/README.md) 列出历史数据要求与降级保护。业务实体使用 BIGINT Identity 和带时区时间；CI 仓库配置使用 repository_id 外键作为一对一主键。更新模型必须新增迁移，不能靠启动时 `create_all` 升级。

CI 的配置、状态机、权限和独立 Runner 安装见 [CI 说明](../CI.md)。服务端仅读取提交对象中的流水线配置，构建脚本由外部 Runner 执行；现有 collaboration worker 负责触发、租约回收和文件保留期清理。

仓库拥有者隐式获得成员身份，不建重复成员行。写操作锁定仓库、重新读取权限，避免移交或管理员降级后旧请求继续写入。全局 admin 不影响私有仓库可见性。

`repository_storage.create_bare_repository` 根据已分配的数字 ID 独占创建目录，使用受控 Git 环境初始化 `main` 并生成首个 README 提交。模板包含仓库名、克隆命令及已有本地仓库的导入步骤：添加独立远端、合并本站初始历史、解决冲突后推送；main 受保护时改推新分支并创建 PR。地址使用配置的可信 origin，作者为系统身份。不会修改已有仓库。创建失败只清理当前调用确认新建的目录；DB 提交结果不明时保留目录供人工核对，不能删除可能已提交的代码。归档不重写成员权限，删除为元数据软删除，移交不搬代码目录。

## 测试

先确保本地 PG / Redis 运行。命令从仓库根目录执行：

```sh
uv run --project server pytest server/tests
uv run --project server ruff check server scripts
uv run --project server ruff format --check server scripts
uv run --project server alembic -c server/alembic.ini check
```

聚焦仓库或发布行为时：

```sh
uv run --project server pytest server/tests/test_repositories.py server/tests/test_git_oauth.py -q
uv run --project server pytest server/tests/test_website_deploy.py -q
```

`tests/conftest.py` 的 `system` fixture 创建随机 PostgreSQL schema，完整运行迁移后注入应用；`limiter` 仅使用本地 Redis DB 0 的随机前缀。`Mailbox` 在内存接收合成验证码，`mutate` 帮助写请求携带有效 CSRF / Origin。结束清理各自 schema / 键，不清空日常开发库。

测试需要真实 PostgreSQL 语义，不使用 SQLite 替代 Identity、部分唯一索引、行锁或 advisory lock。测试客户端以 `http://testserver` 为来源，测试配置为该 HTTP 来源关闭 Secure Cookie；生产仍必须启用。不要改变生产配置来配合测试。

测试职责参见 [测试映射](../CODEMAP.md#改动到测试的映射)。Gateway 集成验证还需构建两份镜像与网站，详见 [脚本 README](../scripts/README.md)。

## 常见问题

| 现象 | 先查什么 |
|---|---|
| 启动缺配置 / key 校验失败 | 本地迁移、`make config-init`、连接的数据库是否正确；不要生成新密钥覆盖已有数据 |
| 邮件码 503 | SMTP 是否配置 / 发送失败、Redis 是否可用；不要在日志打印验证码 |
| 写请求 403 | CSRF Cookie / Header、登录后的 Token、Origin，再查业务权限 |
| 登录后不断回到登录页 | Cookie 名称 / Secure / 域名、会话吊销、账号状态、版本匹配 |
| 仓库创建失败 | 后端有无 Git、`GIT_REPOSITORIES_ROOT` 和 UID 101 写权限、ID 目录是否冲突 |
| Git 401 / 403 | OAuth 开关和客户端凭据、授权 / 账号状态、实时成员权限及归档状态 |
| Alembic 差异或旧库升级失败 | [迁移说明](migrations/README.md) 的前提，不能跳过保护继续启动 |

排障优先用响应 `X-Request-ID`、审计和脱敏配置。API readiness 成功只证明 PG / Redis 连通，不证明 SMTP、Git 路径或用户授权流程正常。

## Issue / PR 后端

公共模型由 `collaboration_models.py` 注册到同一 Base，初始迁移为 `0010_collaboration`，CLI 授权来源由 `0011_cli_oauth` 扩展。`collaboration_issues.py` 负责 Issue、共享讨论、层级、元数据和移交；`collaboration_pulls.py` 负责比较、审阅、checks 和合并入口；`collaboration_content.py` 负责私有附件、通知和提交页。它们复用 Database / RepositoryAuthenticated：网页保持 Cookie + CSRF，CLI 使用同一 OAuth 授权表和仓库 ACL。

`collaboration_git.py` 在临时 bare 仓库中只读来源对象，使用原生 Git 计算差异 / 合并，不检出或执行仓库内容。`collaboration_worker.py` 从 PostgreSQL 领取持久任务，检测结果按 repo / base / head / method 缓存；`collaboration_pushes.py` 摄取 Gateway 回执并分批建立提交引用。合并先持久化意图，原子校验两端 SHA 并更新目标引用 / 恢复标记，再幂等更新数据库。worker 必须与 API 使用同一镜像、配置、数据库和 Git 挂载。

权限矩阵、限制、恢复和开发入口见 [COLLABORATION.md](../COLLABORATION.md)。验证入口为 `test_collaboration.py`、`test_collaboration_pulls.py`、`test_migrations.py`、`test_deploy.py` 与真实 `verify_git_gateway.py`。


仓库拥有者的归档 / 取消归档、移交、软删除使用独立的 `repository_operation` 邮箱验证码（`0017_repository_verification`）。请求体必须含 `code`；不能使用登录或管理员操作码。`operation_verification.py` 复用挑战锁与单次消费，在同一业务事务中重新验证 Cookie 会话或 CLI Bearer、用户状态及当前邮箱。CLI 可向 `POST /api/repositories/{id}/operation-code` 申请当前账号邮箱的验证码，该入口先检查拥有者权限，复用 `auth.issue_email_code` 的限流与邮件逻辑，不扩展机器授权到通用账号 API。

个人镜像页读取 `GET /api/me/registry/namespaces?q=&state=all&page=1`，使用 `visible_repositories` 在计数和分页前过滤权限，排除已删除 repo / namespace；全局 admin 没有内容旁路。`GET /api/me/registry/repositories` 提供可创建 namespace 的本人未归档仓库。创建、绑定及镜像操作仍调用原 repo Registry API，不增加第二套权限规则。验证入口：`test_repository_verification.py`、`test_registry.py`、`test_cli_http.py` 和原生命周期 / Git / LFS 测试。

Docker 令牌创建的 `days` 默认 90；`days: null` 需显式 `acknowledge_permanent: true`，返回 `expires_at: null`。永久令牌继续计入每用户 50 个有效令牌限额，并在每次 Docker 请求检查撤销、用户状态和 repo 权限。迁移 `0018_registry_permanent_tokens` 只放宽到期列可空，不修改旧令牌；验证入口为 `test_registry.py`、`test_migrations.py` 和真实 Registry harness。
