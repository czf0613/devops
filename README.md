# DevOps · 开发协作平台

独立的 Python 3.14 / uv / FastAPI 服务与 Vue 3 / Vite / Vue Router 网站；线上使用阿里云 RDS PostgreSQL 与 Tair（Redis 7.0 兼容，仅 DB 0），本地开发使用 Compose 里的 PostgreSQL / Redis。需求跟踪沿用历史 Issue #22；本文以当前实现为准：**只支持邮箱验证码登录，密码是可选的旧系统兼容资料，全局管理员身份只能通过数据库授予。**

HTTPS / Git Gateway 的部署与 OAuth 登录见 [gateway/README.md](gateway/README.md)。原生 Docker 登录、repo namespace 绑定、镜像管理及人工维护窗口内的管理员 GC 见 [Registry README](registry/README.md)。本地开发继续使用下面的流程；部署使用独立的 `compose.deploy.yaml`。

部署目标为 `ssh kdevops`：`./deploy.sh` 选择 `server`、`website`、`gateway`、`registry`、`cli` 或 `all`，支持组合与 `--dry-run`。服务器访问不到 Docker Hub，镜像在本机按 `linux/amd64` 构建后用 `docker save` / `docker load` 经 SSH 传输并核对 image ID，不使用远端 registry，也不执行 `docker push` / `docker pull`。连接信息来自本地 `deploy.env`（RDS、Tair、SMTP），网站发布到 `/dockerdata/gateway/website`，证书读取 `/dockerdata/gateway/cer`；Gateway 固定 `devops.kevinc.ltd`，使用 host 网络直接绑定 IPv4 / IPv6 的 TCP 443 与 UDP 443，同时提供 HTTP/2 与 HTTP/3。详见 [scripts/README.md](scripts/README.md#kdevops-部署)。

## 阅读入口与能力边界

新接手项目时先读 [AGENTS.md](AGENTS.md)、[项目上下文](CONTEXT.md) 和 [代码导航](CODEMAP.md)。各模块的具体工作方式：

| 文档 | 内容 |
|---|---|
| [CI.md](CI.md) / [runner/README.md](runner/README.md) | 流水线配置、独立构建机、权限、日志与产物 |
| [cli/README.md](cli/README.md) | 公开安装包、CLI 命令、授权与 TypeScript SDK |
| [server/README.md](server/README.md) | 服务结构、事务、API 分组、测试和排障 |
| [website/README.md](website/README.md) | 路由、组件、仓库交互、构建和视觉检查 |
| [gateway/README.md](gateway/README.md) | 部署、仓库 / OAuth 协议、GCM 客户端 |
| [scripts/README.md](scripts/README.md) | 各脚本参数、副作用、静态发布和 Git 集成验证 |
| [server/migrations/README.md](server/migrations/README.md) | 完整迁移链、历史数据前提和降级限制 |
| [sql/README.md](sql/README.md) | 人工管理员初始化与已有账号提权 |

当前已提供账号管理、私有 Git 仓库、成员权限、移交、归档与软删除。工程仓库位于工作台二级菜单，详情 URL 使用拥有者用户名与仓库名；API 写入和物理目录继续使用稳定 ID。

工作台首页聚合跨仓库的 Pull requests 与 Issues，默认仅显示开放事项；可切换与你相关、你创建的、待你审阅 / 分配给你的事项，并进入完整列表搜索、筛选状态和分页。右侧展示可见仓库的协作动态及未读通知。左侧最近仓库按本机访问顺序展示，首次使用补充可访问仓库，也可搜索拥有者 / 仓库名。所有汇总先按当前仓库访问权限过滤，全局管理员没有额外私有仓库权限。

岗位描述、仓库读写权限、仓库管理资格和全局管理员身份各自独立。全局管理员不能读取没有成员关系的私有仓库；归档期间所有人只能读取 Git / LFS 内容。详细权限矩阵见 [CONTEXT.md](CONTEXT.md)。仓库 Code 页支持分支、文件树与文本预览，Settings 集中原有管理功能及拥有者保护开关。Issue / PR 已提供独立路径、子项、评论、双向引用、审阅和原生 Git 合并；范围、权限、后台进程及限制见 [COLLABORATION.md](COLLABORATION.md)。Registry 页签提供 namespace 绑定、镜像查看与删除；CI 页签支持独立 Runner 执行及运行记录，详见 [CI 说明](CI.md)。SSH 和旧系统实际同步尚未实现。

## 版本管理

后端、网站、CLI 和 Runner 独立维护和发布版本，可以使用不同版本号：

| 组件 | 版本定义 | 显示入口 |
|---|---|---|
| Runner | `runner/pyproject.toml` | `devops-runner --version`、构建机上报与安装包 |
| server | [`server/pyproject.toml`](server/pyproject.toml) 的 `project.version`，修改后运行 `uv lock --project server` 更新锁文件 | FastAPI / OpenAPI 从已安装的后端包元数据读取 |
| website | [`website/package.json`](website/package.json) 的 `version` | 网页左下角显示构建时的网站版本和当年版权信息 |
| CLI | [`cli/package.json`](cli/package.json) 的 `version` | `devops --version` 读取已安装包版本；网站指南单独读取 CLI 包版本生成下载地址 |

CLI 安装包按版本永久保留且不可覆盖不同内容。更新 CLI 并展示新下载地址时选择 `./deploy.sh cli website`，先发布安装包再切换指南，无须同步提升网站或 server 的版本。镜像标签和网站 release 标记继续记录提交及构建批次。

## 本地启动

需要 uv、Python 3.14、Docker Compose、Git、Node.js 22.12+ 的 22.x（或 24+）和 pnpm 11。`website/package.json` 指定包管理器 `pnpm@11.11.0`，安装时保留锁文件。在 `仓库根目录` 执行：

```sh
make setup
```

该命令补齐 PostgreSQL / Redis 连接参数、安装锁定依赖、启动两个数据库、执行迁移并初始化 `config` 表。初始化自动生成数据库密码、Redis 密码、AES-256 密钥与认证 HMAC 密钥；重复执行保留已有配置。已有 `.env` 的业务配置会先迁入数据库并验证，再从文件删除；SMTP 信息和现有密钥保留。

`.env` 只保存 PostgreSQL / Redis 的 11 个连接字段，权限为 `0600`，已被 Git 忽略。运行配置直接保存在数据库，不需要额外的配置密钥文件或 Secret 挂载。

在两个终端分别运行：

```sh
# 终端一，仓库根目录
make server
```

```sh
# 终端二，仓库根目录
make website
```

| 入口 | 地址 |
|---|---|
| 网站 / 登录 | http://127.0.0.1:5188/login |
| 注册普通成员 | http://127.0.0.1:5188/register |
| API 文档 | http://127.0.0.1:8000/api/docs |
| 存活 / PostgreSQL + Redis 就绪检查 | `/api/health` / `/api/health/ready` |
| PostgreSQL | `127.0.0.1:55432`，数据库与用户均为 `devops` |
| Redis | `127.0.0.1:56379`，用户 `default`，DB `0` |

网站通过 Vite `/api` 代理访问后端；开发 Git 授权时还代理 `/oauth/authorize`、`/oauth/token`、`/oauth/revoke` 三个精确协议路径。开发端口固定为 **5188**，被占用时明确报错。开发响应禁用缓存；独立的 `devops_dev_session` / `devops_dev_csrf` Cookie 名称用于避免与其他本地工程混用。Cookie 不按端口隔离，因此仅换端口不能隔离 Cookie。

`SESSION_COOKIE_SECURE=false` 使本地 HTTP 能够携带 Cookie；`HttpOnly`、`SameSite=Lax`、CSRF 校验仍生效。这里由后端明确控制 Cookie 属性，Vite 不改写 `Secure` 标记。所有 API 响应使用 `Cache-Control: no-store`。前端只访问同源 `/api`，不在 localStorage 保存登录 Token。

## 共享运行配置

`config` 表使用自增 `BIGINT` 主键，`key` 唯一，`value` 为 JSONB，另有 `is_secret` 和创建 / 修改时间。布尔值、数字、列表、映射保留其类型。SMTP、验证码限流、Cookie、注册开关及 `GIT_OAUTH_ENABLED` 等运行配置统一从这里加载。

服务启动时读取并校验完整配置，不使用环境变量覆盖业务配置；缺少必要配置或值不合法会阻止启动。修改后需重启所有后端实例生效，当前不做自动热更新。多个实例连接同一个 PostgreSQL / Redis 即可共享配置和限流状态。

所有配置以原始 JSON 值保存，包括 `SMTP_PASSWORD`、`PASSWORD_CIPHER_KEYS`、`AUTH_HMAC_KEY`；配置表不再做额外加密，可以直接在数据库中编辑。`is_secret` 仅标记敏感配置，不表示加密；命令行查看时仍按配置 key 脱敏，应用模型中的敏感值使用 `SecretStr`，日志和 API 不输出这些值。用户兼容密码的 bcrypt 哈希与 AES-GCM 密文仍保留。

在 `仓库根目录` 管理配置：

```sh
make config-list  # 敏感值显示 <redacted>

# 单个值通过 stdin 传入 JSON
printf '%s\n' 'false' | uv run --project server python -m devops_server.config_cli set REGISTRATION_ENABLED

# 关联配置可在一个事务内修改；先校验完整结果再保存
uv run --project server python -m devops_server.config_cli set <<'JSON'
{"SMTP_HOST":"smtp.example.com","SMTP_PORT":465,"SMTP_SECURITY":"ssl","SMTP_USERNAME":"noreply@example.com","SMTP_FROM":""}
JSON

# 密码不写在命令参数或 Shell 历史中
uv run --project server python -c 'import getpass,json; print(json.dumps(getpass.getpass("SMTP password: ")))' \
  | uv run --project server python -m devops_server.config_cli set SMTP_PASSWORD
```

所有配置都可直接更新数据库中的 JSONB 值；字符串用 JSON 字符串，数字、布尔值和对象使用对应 JSON 类型。上述命令可在保存前完成类型和关联校验。配置不通过普通用户或管理端 API 暴露。

## SMTP 配置

现有 `.env` 的 SMTP 值由 `make config-init` 导入 `config`。新安装时默认留空，通过上面的命令补充。`SMTP_FROM` 为可选覆盖项：留空时使用 `SMTP_USERNAME` 中的有效邮箱作为发件人；用户名不是邮箱时才需要显式指定。也支持 `devops <noreply@your-domain.com>` 形式。

**465 是隐式 TLS，配置值为 `ssl`**，连接建立后立即进行 TLS 握手，使用 `SMTP_SSL`；587 通常使用 `starttls`。依据 [RFC 8314 §3.3](https://www.rfc-editor.org/rfc/rfc8314.html#section-3.3)。未设置 `SMTP_SECURITY` 时按这两个标准端口推断，未知端口需要显式指定。使用 TLS 时始终校验证书。无认证内网 SMTP 可留空用户名与密码，并按服务配置设置 `none`。

SMTP 尚未配置时，应用仍可启动，申请验证码返回 `503` 和明确提示。开发环境不把验证码返回到 API、日志或网页；测试使用独立的内存收件替身，不发送真实邮件。

## 手动创建管理员

**应用不会创建或提升管理员。** 注册和管理端创建用户均只赋予 `member`；请求中的额外角色字段被拒绝，角色分配接口禁止赋予 `admin`。`admin` 用户名也保留给数据库创建。

迁移完成后，参考 [`sql/create_admin.example.sql`](sql/create_admin.example.sql)，替换其中邮箱，再手动执行。例如：

```sql
BEGIN;
WITH created AS (
    INSERT INTO users (username, display_name, email)
    VALUES ('admin', '管理员', lower('your-real-email@company.com'))
    RETURNING id
)
INSERT INTO user_roles (user_id, role_id)
SELECT id, (SELECT id FROM roles WHERE code = 'admin') FROM created;
COMMIT;
```

不用填写任何密码字段。邮箱须为可收信的真实地址；直接写库时也按小写保存。重复执行会因用户名或邮箱唯一约束失败，不会覆盖已有用户。该 SQL 示例不会被初始化脚本执行。

已经手动创建的用户还需在 `user_roles` 关联 `admin`；仅插入 `users` 不会获得管理员权限。可手动执行以下 SQL，替换用户名：

```sql
INSERT INTO user_roles (user_id, role_id)
SELECT users.id, roles.id
FROM users CROSS JOIN roles
WHERE users.username = 'existing_username' AND roles.code = 'admin'
ON CONFLICT (user_id, role_id) DO NOTHING;
```

管理员可管理成员、启停账号、调整普通角色、清除兼容密码、吊销会话和查看审计。管理员角色的授予与移除始终由数据库维护；应用保留已有 `admin` 角色，并保护最后一个有效管理员不被停用。管理员的登录邮箱需本人验证后更换，或直接在数据库中修改，避免通过修改管理员邮箱转移管理权限。

## 用户与认证规则

- 邮箱非空、全局唯一；接口统一验证并规范为小写，数据库同时约束规范化和唯一性。
- 用户名是系统内全局唯一的标识名称，用于识别用户，并作为后续协作中通过 `@用户名` 提及同事的依据。统一以小写存储，长度为 3–32 位，以字母开头，支持数字、下划线、点和短横线。
- 手机号可空，接口格式为 `+86.13912345678`，国家码与电话号码合计不超过 15 位数字。
- 注册必须验证邮箱，只生成普通成员。`REGISTRATION_ENABLED=false` 可关闭公开注册。
- 管理员创建成员时不设置密码，成员通过对应邮箱验证码登录。启停保留账号与历史关联，不提供物理删除用户接口。
- 自助更换邮箱需要验证新邮箱，并吊销全部会话。管理员修改普通成员邮箱也会吊销该成员全部会话。

验证码为 6 位安全随机数字，默认 10 分钟有效、最多 5 次错误尝试、60 秒重发间隔、每邮箱每小时 5 次、每 IP 每小时 30 次。登录、注册、换邮箱、修改兼容密码、管理员操作和仓库生命周期操作分别绑定用途；除登录 / 注册外均绑定当前用户，兼容密码、管理员及仓库操作验证码只发往当前账号邮箱。管理员操作还要求当前用户持有全局 admin 角色。数据库只保存带随机 nonce 的 HMAC 摘要。数据库行锁和事务保证验证码单次使用及错误次数，窄范围 advisory lock 串行替换同一邮箱的验证码。参数通过 `config` 调整。

发送频率由 Redis DB 0 共享：Lua 脚本原子预占邮箱与 IP 的滚动一小时配额，并设置重发间隔；使用 Redis 时间避免实例时钟偏差。键有应用前缀，邮箱 / IP 以摘要参与键名，过期后自动清理。拒绝返回 `429` 和 `Retry-After`，不会延长封禁窗口；Redis 不可用返回 `503`，不会绕过限流发信。SMTP 发送失败也占用本次限额，对应验证码作废。

本地 Redis 开启认证、AOF 持久化、`--databases 1`，从服务端禁止选择 DB 1 等其他库；后端配置同样只允许 `REDIS_DB=0`。清空 Redis 会重置配额，但不会恢复已消费的验证码或已吊销的登录会话，它们仍以 PostgreSQL 为准。

登录 Token 由 `secrets.token_urlsafe(48)` 生成，64 字符原值保存在数据库中，使用区分大小写的唯一索引；发生唯一冲突会重试。会话不是 JWT，采用滑动有效期，`SESSION_DAYS` 默认 7 天。成功访问需要网页登录的接口时，最近活动与到期时间按分钟节流更新，期限延长至本次活动后 7 天；临近到期不足一分钟时立即尝试续期。数据库提交成功后同步延长浏览器 Cookie，有效 Token 和 CSRF 值保持不变，已打开的标签页可继续使用。持续活跃可保持登录；超过有效期未活动则需重新邮箱登录。已过期、吊销、账号停用或密码版本失配的会话不能通过续期恢复。每次请求仍实时检查这些条件及当前全局角色。

浏览器写请求必须携带 `/api/auth/csrf` 返回的 `X-CSRF-Token`，同时验证签名 Cookie、当前会话绑定及 Origin。登录后更换 CSRF Token，前端自动维护；登录与注册本身也受保护。API 文档可查看接口；直接调试写请求时需要先获取 CSRF Cookie 与 Token。

数据库请求使用 `Depends(scope="function")`，在响应发出前提交事务；提交失败时回滚并返回错误，不发出登录 Cookie。生命周期依据 [FastAPI 官方说明](https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/#early-exit-and-scope)。

## 可选的旧系统兼容密码

哈希、密文、密钥标识均可为空；未设置时密码版本为 0。设置时原样使用 UTF-8 字节，同时生成 bcrypt 哈希与 `cryptography.AESGCM` 密文，输入限制为 8–72 字节，绝不截断、去空格或修改内容。

每次加密使用 12 字节随机 nonce，密文封装包含格式版本、nonce 和认证标签，AAD 绑定稳定用户 ID 与字段用途。哈希、密文、密钥 ID、密码版本、修改时间和有效会话的版本更新在同一事务中提交。清除时同时清空三份密码材料并递增版本。普通接口仅返回 `has_legacy_password`，没有密码读取或解密 API；解密原语仅供后续受控旧系统同步使用。

本人设置或清除密码时，先调用 `POST /api/auth/email-codes`，传入当前邮箱和 `purpose="legacy_password"`；再调用 `PUT /api/me/legacy-password`，传入 `{ "password": "your-legacy-password", "code": "6 位验证码" }`（清除时 `password` 使用 JSON `null`）。验证码绑定当前用户、当前邮箱与专用用途；登录验证码不能替代，已消费、过期或超过错误次数均拒绝。校验与凭据更新在同一事务中完成，更新前再次检查邮箱和会话状态。

管理员只能通过 `PUT /api/users/{id}/legacy-password` 提交 `{ "password": null }` 清除密码；非空密码与额外字段会被后端拒绝，界面也不提供查看或代设入口。清除记录为 `user.legacy_password_cleared`；历史重设记录仍可查询。

兼容密码变更不会强制退出通过邮箱登录的设备。事务只推进变更前仍有效、未吊销且版本匹配的会话版本；过期、吊销或已因版本不匹配失效的会话不会恢复。用户仍可在登录设备中主动吊销会话。

```json
{
  "PASSWORD_CIPHER_CURRENT_KEY": "local-v1",
  "PASSWORD_CIPHER_KEYS": {"local-v1": "Base64 编码的 32 字节密钥"},
  "AUTH_HMAC_KEY": "独立的随机密钥",
  "BCRYPT_ROUNDS": 12
}
```

这几项同样由 `config` 直接保存，不再经过配置密钥加密。`PASSWORD_CIPHER_KEYS` 支持多个版本：增加新键、切换当前键后，新密码用新键写入，旧密文仍用自己的 `password_key_id` 解密。旧密钥的保留应覆盖存量密文及其备份。批量重加密和旧系统实际同步在后续集成阶段实现。

## 代理、CDN 与正式部署参数

`CLIENT_IP_HEADERS` 默认为 `CF-Connecting-IP`、`X-Forwarded-For`、`X-Real-IP`，按顺序取第一个有效 IP；XFF 使用第一个地址。缺失或格式错误时回退到 socket remote address。Docker/Nginx/CDN 部署可按供应商修改顺序；入口代理负责覆盖客户端传入的这些 Header。`make server` 使用 `--no-proxy-headers`，由应用统一处理 IP 来源，保留真正的 socket 地址作为回退。

正式环境通过配置命令成组修改，至少包括：

```json
{
  "ENVIRONMENT": "production",
  "SESSION_COOKIE_SECURE": true,
  "SESSION_COOKIE_NAME": "devops_session",
  "CSRF_COOKIE_NAME": "devops_csrf",
  "ALLOWED_ORIGINS": ["https://devops.your-domain.com"]
}
```

同时提供正式 PostgreSQL / Redis 连接、SMTP 和生产环境的 AES / HMAC 密钥。`.env` 仍只存连接信息，其余直接在 `config` 中管理，不再挂载配置主密钥。`production` 模式拒绝关闭 Secure Cookie。后端即使在 Docker 中接收 HTTP，也按显式配置发出 Secure Cookie，不依赖代理内部连接协议。Nginx 同源转发 `/api`，前端 History 路由回退到 `index.html`；CDN 不缓存 `/api`。

## 数据与接口

`0001_initial` 保留原始建表迁移，`0002_email_auth` 修改邮箱、可选密码约束并增加验证码表，`0003_shared_config` 新建配置表，实际配置值由独立初始化命令导入。若旧库存在缺失或重复邮箱，迁移明确停止，需先修正数据；不会自动伪造联系邮箱。有无密码账号时，回退到强制密码的旧结构会被拒绝。

`0004_plaintext_config` 将旧版加密配置一次性解密为原始 JSON 值，完整保留已有 SMTP 密码、AES 与 HMAC 密钥，不轮换或重置。仅升级旧密文时需要原来的 `.secrets/config.key` 或 `/run/secrets/devops_config_key`；新安装不读取或生成该文件。迁移先解密全部目标值，再在同一事务中写入；密钥缺失或解密失败则保留原数据。升级成功并确认服务正常后可删除旧配置密钥文件。已有明文配置时拒绝降级到旧的加密配置版本。

`0005_password_email_verification` 增加 `legacy_password` 验证码用途，并要求绑定用户 ID；不修改已有账号、密码或会话。存在该用途的记录时，迁移会拒绝回退到旧约束，避免丢弃验证记录。

后续迁移 `0006_git_oauth_pilot` 增加 Git OAuth，`0007_repositories` 增加正式仓库 / 成员并撤销旧联调授权，`0008_repository_lifecycle` 增加归档 / 删除及未删除名称唯一索引，`0009_repository_protection` 增加默认关闭的 main / 推送保护。`0010_collaboration` 增加协作表与编号 / 合并策略，保留旧会话和授权。当前 head 为 `0018_registry_permanent_tokens`，完整升级前提见 [迁移说明](server/migrations/README.md)。

十二张表均使用 `BIGINT GENERATED ALWAYS AS IDENTITY` 主键：

| 表 | 内容 |
|---|---|
| `users` | 基本资料与可选的兼容密码材料 |
| `roles` / `user_roles` | 全局角色及关联，预置 `admin`、`member` |
| `user_sessions` | 原始 Token、会话、IP、到期和吊销状态 |
| `email_challenges` | 验证码摘要、用途、次数、到期与消费状态 |
| `audit_events` | 关键操作、结果、请求 ID、IP 与必要详情 |
| `config` | 唯一 key、JSONB value、敏感标记与时间 |
| `repositories` / `repository_members` | 仓库元数据、拥有者、归档 / 删除、成员岗位 / 读写 / 管理权限 |
| `git_oauth_requests` / `git_oauth_grants` / `git_oauth_tokens` | 浏览器授权请求、可吊销授权与 access / refresh Token |

普通用户资料、管理列表与审计不返回密码、密码哈希、密文、验证码或完整登录 Token；OAuth 换码接口按协议返回客户端凭据，不能写入日志。资料查询默认不加载凭据字段；SQL 错误隐藏绑定参数，输入验证错误不回显提交值。仓库 / OAuth 的完整接口表见 [gateway/README.md](gateway/README.md)。

| API | 用途 |
|---|---|
| `GET /api/auth/csrf` | CSRF Token 与注册开关 |
| `POST /api/auth/email-codes` | 请求指定用途的验证码 |
| `POST /api/auth/register`、`/login`、`/logout` | 注册、邮箱登录、退出 |
| `GET/PATCH /api/me` | 自己的资料 |
| `POST /api/me/email`、`PUT /api/me/legacy-password` | 更换邮箱、设置或清除兼容密码 |
| `GET/DELETE /api/me/sessions`、`DELETE /api/me/sessions/{id}` | 有效会话列表、全部或指定设备退出 |
| `GET/POST /api/users`、`GET/PATCH /api/users/{id}` | 管理员的成员管理 |
| `PUT /api/users/{id}/roles`、`/legacy-password` | 管理员调整普通角色、仅清除兼容密码 |
| `GET/DELETE /api/users/{id}/sessions` | 管理员查看有效会话与吊销会话 |
| `GET /api/roles`、`/api/audit-events` | 全局角色与分页审计记录 |

## 前端通用组件

全站固定为深色主题，没有主题切换选项。`website/src/style.css` 集中定义背景、表面、边框、文字、强调色与状态色；组件与页面共享样式，登录/注册页也保持一致。

通用组件位于 `website/src/components`：

| 组件 | 约定 |
|---|---|
| `AppButton` | primary / secondary / ghost / danger，统一 busy/disabled；默认 `type="button"`，提交表单需显式写 `type="submit"` |
| `AppInput` | 标签、提示、`v-model` / `.trim`；原生输入属性传给实际 input |
| `AppSelect` | 自定义单选菜单，`v-model` + options；弹层挂到 body，避免表格容器裁剪；支持方向键、Home/End、Enter/Space、Escape、Tab 和首字符查找 |
| `AppPanel` / `AppBadge` / `AppNotice` | 统一面板、状态徽标、错误与成功提示；提示使用 alert/status 语义 |
| `AppConfirm` | 深色确认弹窗，`ask(message, title)` 返回布尔结果；默认聚焦取消，关闭后恢复原焦点 |
| `AppIcon` | 共用 SVG 图标 |
| `EmailCodeField` | 登录、注册、换邮箱与兼容密码共用的验证码输入/发送组件；使用服务端重发时间与 429 Retry-After 倒计时 |
| `SidebarNav` | 可折叠的「工作台 → 工程仓库」和「系统管理」；后者按全局 admin 角色渲染 |
| `SessionList` / `Pager` | 共用设备列表、内部滚动、吊销确认与分页；账号安全页的列表区域不随设备数增长 |
| `RepositoryMemberForm` | 添加 / 编辑共用，岗位预设加自定义，读写和管理权限分开 |
| `RepositorySettings` | 拥有者专用移交、归档 / 取消归档、删除，包含名称确认和导航更新 |
| `RepositoryCode` | 默认 Code 页，分支、目录分页、路径导航、文本预览、当前目录 README 渲染和 LFS 原文件下载 |
| `RepositoryActivity` | 仓库列表近 12 周提交趋势，独立读取、空值 / 错误 / 截断提示 |
| `RepositoryProtection` / `AppSwitch` | 拥有者保护设置、可访问的 switch、保存失败保留旧值 |

仓库列表以条目展示，简介最多两行并允许无空格长文本换行；右侧按真实 Git 历史展示近 12 周提交趋势，统计所有分支去重后的提交数。

Git 授权面板隐藏已撤销记录但保留数据库历史；访问凭据由 GCM 按需刷新，每次成功刷新将授权续期至刷新后 30 天。连续 30 天未刷新或主动撤销后需重新网页授权；现有未过期授权在下次成功刷新时适用新期限。

仓库详情将搜索添加、编辑和移除成员统一在同一面板，保留一致内边距。仓库页顶部采用紧凑布局，简介只在 Settings 查看 / 编辑；左侧菜单可折叠，成功提示定时消失；克隆地址完整显示且可以复制。岗位预置产品经理、项目经理、开发人员、测试人员、设计师、运维人员，并保留自定义文本。具体布局约定见 [website/README.md](website/README.md)。

这些管理面板位于 Settings；默认 Code 展示代码。分支保护拒绝所有人直接创建、更新或删除 main；推送保护拒绝非快进更新、替换标签和删除 refs。两项均默认关闭，并在服务端 pre-receive 执行，不受客户端 `--force` 或 `--no-verify` 影响。

Git LFS 使用现有 Git OAuth，支持 Batch / Basic 上传、下载和 verify，单对象上限 5 GiB。文件按仓库 ID 隔离、流式上传、校验 SHA-256 与大小后原子保存；归档禁止上传，移交和删除遵循仓库权限。使用方式及备份边界见 [Gateway LFS 文档](gateway/README.md#git-lfs)。

工作台在工程仓库下提供“镜像管理”，按本人仓库关系汇总 namespace，可筛选绑定状态、为自己拥有且未归档的 repo 创建 namespace，并管理各 namespace 的镜像与标签。repo 内归档 / 取消归档、移交、删除也需逐次验证本人邮箱；CLI 同样执行服务端验证。

管理员菜单包含人员管理、操作记录、Registry 管理与仓库管理。仓库管理可强制修改成员、读写 / 管理权限、拥有者、归档状态及软删除；仓库与 Registry 管理的每次写操作都需要验证当前管理员邮箱。菜单显隐之外，前端路由守卫与后端每次请求的权限校验继续生效。自定义下拉框的键盘与 ARIA 设计参考 [WAI-ARIA 单选组合框模式](https://www.w3.org/WAI/ARIA/apg/patterns/combobox/examples/combobox-select-only/)。

## 检查与维护

```sh
make check
```

执行 Ruff、后端 PostgreSQL / Redis 集成测试、Alembic 模型差异检查、Vitest 组件/交互测试、Vue 类型检查及生产构建。数据库测试仅允许本地 PostgreSQL，在随机独立 schema 中执行迁移、创建合成账号并测试；Redis 测试同样仅允许本机 DB 0，并使用每次独立的键前缀，结束后只清理自身键，绝不 `FLUSHDB`。不会向日常开发库创建管理员或发送真实邮件。

测试覆盖邮箱唯一性、手动管理员权限、越权拦截、验证码有效期/重放/并发/共享限流、Cookie/CSRF、密码字节边界与 AES 篡改/用户绑定/密钥轮换、会话吊销、角色实时生效、IP Header 回退、旧配置无损迁移/明文编辑/输出脱敏/重启加载、Redis 故障与数据库迁移升降级。新增覆盖兼容密码专用验证码、跨用户/跨用途/并发消费、管理员只能清除、密码变更保持登录且不恢复失效会话。前端覆盖自定义下拉框键盘/鼠标操作与弹层位置、角色菜单变化、验证码倒计时、确认弹窗及本人/管理员密码流程；网络调用使用替身，不发送真实邮件或修改开发库账号。

仓库测试覆盖拥有者 / 管理员 / 普通成员权限、实时权限变更、移交冲突、归档与删除；OAuth 覆盖 PKCE、刷新轮换、重放撤销和每次 Git 请求的 ACL。发布测试覆盖压缩包校验、路径与链接拒绝、旧资源保留和切换失败行为。真实 Nginx / CGI / Git 验证需单独构建镜像并运行 [verify_git_gateway.py](scripts/verify_git_gateway.py)，不包含在 `make check` 内。

| 命令 | 用途 |
|---|---|
| `make env` | 只补齐缺失的连接参数，保留现有值 |
| `make db-up` / `make db-down` | 启停 PostgreSQL 与 Redis；停止保留数据卷 |
| `make migrate` | 升级数据库 |
| `make config-init` | 初始化配置表，或从旧版 `.env` 迁移；保留已有配置 |
| `make config-list` | 查看配置，敏感值脱敏 |
| `make test` | 运行后端与前端测试 |
| `pnpm --dir website test` | 单独运行前端交互测试 |
| `pnpm --dir website build` | 类型检查并构建到 `website/dist/` |
| `make website-deploy DEPLOY_HOST=devops` | 实际通过 SSH 发布网站；先完成服务器挂载准备，仅在发布授权范围内使用 |
| `make deploy COMPONENTS="server gateway"` | 构建并更新选中的测试服务，默认 COMPONENTS 为 all |
| `make server-deploy` / `make gateway-deploy` | 更新测试后端及 worker / 网关；`DEPLOY_ARGS="--dry-run"` 查看计划 |

SQLAlchemy 在 ORM 更新时维护 `updated_at`；直接修改数据库时请同时维护此字段。服务默认只监听本机，部署构建与对外发布另行处理。
