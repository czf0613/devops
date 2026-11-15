# 数据库迁移

Alembic 入口为 [`../alembic.ini`](../alembic.ini)，模型位于 [`../src/devops_server/models.py`](../src/devops_server/models.py)。迁移处理结构及必要的数据转换；运行配置由 `config_cli init` / `set` 维护，管理员由人工 SQL 维护。

## 当前迁移链

当前单一 head 为 `0018_registry_permanent_tokens`（2026-10-05 核对）。新增迁移后同步更新本表与根 CONTEXT。

| revision / 文件 | 升级作用 | 需要注意 |
|---|---|---|
| [0001_initial](versions/0001_initial_create_user_system_foundation.py) | 用户、角色、会话、审计基础表，预置 admin / member 角色 | 原始版本要求密码；不是当前完整用户模型，也不创建管理员账号 |
| [0002_email_auth](versions/0002_email_auth_email_authentication_and_optional_.py) | 邮箱规范化 / 非空 / 唯一、验证码、可选密码 | 缺邮箱或规范化后重复邮箱会停止；存在无密码用户时拒绝恢复强制密码结构 |
| [0003_shared_config](versions/0003_shared_config.py) | 新增共享 `config` 表 | 业务值由独立初始化命令写入；历史版本曾加密敏感配置 |
| [0004_plaintext_config](versions/0004_plaintext_config.py) | 旧敏感配置解密成原始 JSONB | 仅旧密文需要原密钥；不轮换任何现有业务密钥，有明文敏感配置时拒绝降级 |
| [0005_password_email_verification](versions/0005_password_email_verification.py) | 增加绑定用户的 `legacy_password` 验证码用途 | 存在该用途记录时拒绝回退旧约束 |
| [0006_git_oauth_pilot](versions/0006_git_oauth_pilot.py) | OAuth 请求 / 授权 / Token 三表，已有配置库补 Git 开关 | 默认关闭；名称中的 pilot 是历史阶段，不代表当前仍只允许管理员联调 |
| [0007_repositories](versions/0007_repositories.py) | 正式仓库 / 成员、Git 根目录配置 | 撤销旧联调 Git 授权并过期待授权请求；有仓库记录时拒绝降级 |
| [0008_repository_lifecycle](versions/0008_repository_lifecycle.py) | 归档、软删除、未删除名称唯一索引 | 现有仓库保持可用；有归档或删除记录时拒绝降级 |
| [0009_repository_protection](versions/0009_repository_protection.py) | main / 推送保护布尔字段 | 新旧仓库均默认关闭；任一仓库启用保护时拒绝降级，不更改历史数据或授权 |
| [0010_collaboration](versions/0010_collaboration.py) | Issue / PR 共享编号、23 张协作表、仓库合并策略 | 不改动旧账号 / 会话 / Git 授权；任何协作表有数据时拒绝降级 |
| [0011_cli_oauth](versions/0011_cli_oauth.py) | OAuth client 绑定、设备码、merge intent 可引用 CLI 授权 | 旧授权默认 devops-gcm；保留旧客户端。降级先撤销 CLI 授权，有 CLI 合并历史则拒绝丢弃授权来源 |
| [0012_registry](versions/0012_registry.py) | namespace 与 Docker 令牌、Registry 配置项 | 有 Registry 历史时拒绝丢弃归属 / 撤销记录 |
| [0013_registry_management](versions/0013_registry_management.py) | namespace 变更版本号 | 保留归属与解绑状态，用于管理操作并发检查 |
| [0014_registry_gc](versions/0014_registry_gc.py) | 删除旧项目限定 Docker 令牌及范围字段；GC 任务 / 进程心跳表 | 保留用户级令牌；旧令牌不可恢复，有 GC 历史时拒绝降级 |
| [0015_admin_management](versions/0015_admin_management.py) | 增加绑定用户的管理员操作验证码用途及 namespace 删除标记 | 不改变账号、权限和会话；有该用途验证码或删除记录时拒绝降级 |
| [0016_registry_namespace_purge](versions/0016_registry_namespace_purge.py) | 记录已删除 namespace 的维护窗口 GC 清理完成时间 | 删除即禁用访问，物理清理成功后才记完成；有删除记录时拒绝丢弃清理状态 |
| [0017_repository_verification](versions/0017_repository_verification.py) | 仓库生命周期专用邮箱挑战及用户绑定 | 保留历史；存在该用途挑战记录时拒绝降级 |
| [0018_registry_permanent_tokens](versions/0018_registry_permanent_tokens.py) | Docker 令牌到期时间可空，表示永久有效 | 已有到期时间不变；有永久令牌记录（包括已撤销）时拒绝降级 |

所有表使用 BIGINT Identity；邮箱 / Token 大小写语义、部分唯一索引和锁依赖 PostgreSQL。不要用 SQLite 或手改版本号代替实际升级。

## 本地命令

从仓库根目录执行：

```sh
uv run --project server alembic -c server/alembic.ini heads
uv run --project server alembic -c server/alembic.ini history
uv run --project server alembic -c server/alembic.ini current
make -C . migrate
make -C . config-init
uv run --project server alembic -c server/alembic.ini check
```

`heads` / `history` 读取迁移脚本，`current` / `check` 访问配置指向的数据库；`migrate` 实际变更结构，`config-init` 实际写配置。不要把它们都归类为只读操作。

先修改模型，再生成新迁移候选并人工审查：

```sh
uv run --project server alembic -c server/alembic.ini revision --autogenerate -m "describe_change"
```

检查顺序、命名、旧数据转换、约束、索引和 downgrade。自动生成不会替你完成数据修复或安全的权限迁移。已使用的历史迁移保持稳定，新行为放新 revision。

## 历史升级注意事项

`0002` 不给旧账号伪造邮箱；先人工修正缺失 / 重复值。`0004` 只有旧库还有加密配置时才要求原 `.secrets/config.key` 或 `/run/secrets/devops_config_key`；解密失败整批保留，不能新生成密钥替代原密钥。新安装不需要配置主密钥文件。

`0007` 刻意撤销授权：原用户只确认固定测试仓库，新版扩展为实际成员仓库，必须重新确认。它不吊销网页登录会话。首次从原联调版升级还需人工核对旧 `/dockerdata/git-repos/1.git`，仅确认属于测试仓库且没有正式元数据时才移出根目录备份；迁移不处理物理目录。后续升级不能重复移动正式仓库 `1.git`。

`0008` 不移动 Git 文件，不重置成员或 OAuth 授权。阻止降级是为了避免旧代码重新开放归档 / 已删除仓库；不能清空状态或删除记录来绕过保护。

`0009` 需与支持保护钩子的 Gateway 一起发布，不能只更新网页开关。LFS 复用已有 Git 目录，无新增数据库表；旧仓库无需迁移物理文件。测试部署入口为 `./deploy.sh all --migrate`，先备份数据库再升级。存在启用的保护时，不得为了降级而自动关闭保护。

部署升级同时考虑数据库、Git 目录、配置密钥材料和镜像版本。恢复旧镜像不等于恢复旧数据库；前置步骤见 [Gateway 部署说明](../../gateway/README.md)。

## 测试

```sh
uv run --project server pytest server/tests/test_migrations.py -q
```

从仓库根目录执行，本地 PostgreSQL 已启动。测试在随机 schema 中运行升级、历史数据转换和降级拒绝，不重置开发库。`env.py` 接受测试传入的 connection，保持此隔离机制。

提交模型 / 迁移变化前运行相关业务测试与 `alembic check`，确认当前库已升到目标 head 且没有遗漏模型差异。降级或手动 `stamp` 只用于明确的数据恢复任务，不是普通排障捷径。

`0010` 发布需同步 server / worker、Gateway post-receive 和网站。先备份数据库，再迁移，最后启动应用；`deploy.sh all --migrate` 会为旧 Compose 补齐 worker 并固定同一 server 镜像。附件存 PostgreSQL，Git 对象、PR 快照、合并恢复标记和推送回执位于 ID 对应的 bare 仓库，恢复时两者必须配套。不要删除 `refs/devops/` 或 `devops-events/`。

`0012_registry` 增加 `registry_namespaces` / `registry_credentials` 和已初始化配置的 Registry 默认项；空库仍由 config init 初始化。存在任一 namespace 或凭据历史时拒绝降级，防止丢失镜像归属或撤销记录。执行迁移与配置后重启后端；部署与存储备份见 [Registry README](../../registry/README.md)。

`0014_registry_gc` 是上线前明确清理：仅删除 `registry_credentials.repository_id IS NOT NULL` 的旧项目令牌，再移除该列，不影响用户级 Docker 令牌、Git OAuth 或网页登录会话。GC 心跳和任务存数据库，活动任务通过部分唯一索引互斥；原有迁移不重写。


`0017_repository_verification` 增加 `repository_operation` 邮箱挑战用途并要求关联用户 ID，保留已有挑战、会话和仓库数据。存在该用途历史时拒绝降级；不会为了回退清除安全记录。

`0019_ci` 在 `0018_registry_permanent_tokens` 后增加 worker、仓库授权、运行、Job、Attempt、产物表，以及 PR check 的 CI 来源关联。活跃 worker/Job 的 Attempt 有部分唯一索引。已有 CI worker 或运行记录时拒绝降级，避免删除身份/执行历史。日志与产物文件与 Git 持久卷一起备份。
