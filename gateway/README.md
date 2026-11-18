# 工程仓库、Git HTTP Gateway 与 OAuth

## CLI 下载与授权

`/res/devops-cli/<version>.tgz` 和 `.tgz.sha256` 是公开静态文件，映射已有只读网站父目录下的 `res/devops-cli/`，不依赖 `current` 软链接。该目录禁止列表、写入和软链接，缺失文件返回 404，不回退 SPA。发布器保留历史版本并拒绝同版本替换，见 [脚本说明](../scripts/README.md)。

CLI 使用独立 public client `devops-cli`，权限为 `api:read api:write`，支持原有 loopback PKCE 和新设备授权。Nginx 精确代理 `/oauth/device/code`；`/oauth/device` 仍返回 SPA。Bearer 仅访问仓库 / 协作及 `/api/cli/me`，不能访问全局用户管理；每次执行仓库 ACL。Git / LFS 仍使用 `devops-gcm` 的 git:read/write，不混用令牌。侧栏 `/guide` 提供 GitHub Desktop 设置示意图和安装步骤。

所有有效 DevOps 用户都可以创建私有仓库，在「工程仓库」添加已有同事、分配读写权限和岗位角色。GCM 通过 DevOps 邮箱登录获得授权，每次 Git 操作都按当前仓库成员权限判断。系统管理员没有私有仓库的隐式访问权。

链路：Git / GCM → HTTPS Nginx → DevOps 内部鉴权 → fcgiwrap → git-http-backend。网站、API 和 OAuth 使用同一 HTTPS 域名。前端在本机通过 pnpm 构建，压缩上传到服务器，Gateway 只读挂载静态文件；镜像不安装 Node.js、不编译或包含前端产物。

修改本目录前读 [AGENTS.md](AGENTS.md)。账号配置见 [DevOps README](../README.md)，发布参数 / 副作用见 [脚本 README](../scripts/README.md)，迁移前提见 [迁移 README](../server/migrations/README.md)。当前服务器镜像与 release 快照集中在 [CONTEXT.md](../CONTEXT.md)，下文 `:latest` 是构建 / Compose 示例，不代表服务器当前运行的标签。

## 镜像与目录

| 项目 | 配置 |
|---|---|
| 部署文件 | `compose.deploy.yaml`，与开发用 `compose.yaml` 分开 |
| 后端镜像 | `devops.kevinc.ltd/core/devops:latest`，Python 3.14 Alpine + uv.lock |
| Gateway 镜像 | `devops.kevinc.ltd/core/devops-gateway:latest` |
| Gateway 基础镜像 | `nginx:1.31.9-alpine`（mainline，含 `--with-http_v3_module` 与 `--with-compat`，修复 nginx2root / CVE-2026-123007 等 1.31.8 及更早版本漏洞），Brotli 模块按同版本源码编译 |
| 网络模式 | Gateway 使用 `network_mode: host`，直接绑定宿主机 TCP / UDP 443（IPv4 + IPv6）；server 与 registry 只发布到 `127.0.0.1` |
| 客户端地址 | host 网络下 `$remote_addr` 就是真实客户端（含 IPv6），Nginx 仍写入 `X-Forwarded-For` / `X-Real-IP`，后端按配置顺序取第一个有效 IP |
| 目标架构 | 只构建 `linux/amd64`；镜像用 `docker save` / `docker load` 传输到 x64 服务器，不经过 registry |
| HTTPS | `devops.kevinc.ltd:443`；TCP 支持 HTTP/1.1 / HTTP/2，UDP 支持 HTTP/3；80 只做 301 跳转到 HTTPS |
| 日志 | `json-file`，`50m × 3`（每容器最多约 150 MB）；Compose 显式声明，宿主机 daemon 使用同样默认值 |
| 缓存 | `/assets/` 与 `/res/` 使用一年 `immutable`（文件名含内容哈希 / 版本号），SPA 入口、`/gateway-health` 与 API 保持 `no-store` |
| 证书挂载 | `/dockerdata/gateway/cer` → `/cert:ro`，`kevinc.pem` / `kevinc.key` |
| Git 挂载 | `/dockerdata/git-repos` → `/mnt/git-repos` |
| 网站挂载 | `/dockerdata/gateway/website` → `/usr/share/nginx/html:ro`，Nginx root 指向 `current` |
| 仓库地址 | `/git/<拥有者用户名>/<仓库名>.git` → `/mnt/git-repos/<数据库ID>.git`，bare，默认 main |
| Git 运行身份 | 后端 `app` 与 CGI `nginx` 均使用 UID/GID **101**；两个容器共享 Git 挂载 |
| CGI | `/usr/local/bin/devops-git-http-backend` 包装 Git backend 并固定保护钩子；fcgiwrap 仅监听 `127.0.0.1:9000` |
| 数据持久化 | PG `/dockerdata/devops/postgres`；Redis `/dockerdata/devops/redis`，默认 DB 0（`REDIS_DB` 可配置） |

`nginx-main.conf` 是主配置，`nginx.conf` 是 http 上下文的站点配置。FastAPI、PG、Redis 不映射宿主端口。内部鉴权 URL 不允许外部直接访问。

仓库目录只由服务端根据自增数据库 ID 生成，用户不能指定存储路径。跨镜像操作使用 UID/GID 101。单次 Git 请求上限 4 GiB，Nginx 会缓冲上传，临时文件目录需预留空间。

LFS 使用独立流式代理到 FastAPI，单对象最大 5 GiB，不经过 CGI。它的数据在 ID 对应的 Git 目录内，沿用现有持久化挂载。

## HTTP/3 与兼容访问

Nginx 同时监听 `443 ssl`（TCP）及 `443 quic reuseport`（UDP），响应带 `Alt-Svc: h3=":443"; ma=86400`。HTTP/1.1 / HTTP/2 保持可用；浏览器会按客户端和网络条件选择协议。启用 QUIC Retry，保持 TLS 0-RTT 关闭，避免登录、OAuth、Git 等写操作被 early data 重放。HTTP/3 沿用相同路由、证书和 ACL；代理 Host 在缺少普通 Host 头时取 `:authority` 对应的 `$host` / `$request_port`，保留显式端口，避免上游收到缺失 Host 的 HTTP/1.1 请求；日志只新增协议版本，不记录查询或凭据。

基础镜像固定 Nginx 1.31.9+（当前 Dockerfile 校验精确版本），必须编译 `--with-http_v3_module`（Dockerfile 构建时检查），保留 Brotli。1.31.8 及更早版本受 nginx2root 的 HTTP/2 / HTTP/3 漏洞影响，不能继续使用。当前配置依据 [Nginx QUIC 指南](https://nginx.org/en/docs/quic.html) 与 [HTTP/3 模块](https://nginx.org/en/docs/http/ngx_http_v3_module.html)。Compose 必须同时发布 `443:443/tcp`、`443:443/udp`，宿主防火墙 / 云安全组也需分别放行。不要开启 API / FastCGI 的外网端口。

实际握手验收使用 `make -C . http3-check`（仓库根目录）；可加 `HTTP3_ARGS="--ipv4"` 或 `HTTP3_ARGS="--ipv6"` 单独检查地址族，详情见 [HTTP/3 只读验收](../scripts/README.md#http3-只读验收)。`Alt-Svc`、`nginx -t` 或容器 healthy 单独都不能证明 UDP 链路正常。

## 构建与准备

日常发布直接执行 `./deploy.sh all`（或 `server gateway`）：脚本在本机按 `linux/amd64` 构建 server / gateway，用 `docker save` 经 SSH 交给服务器 `docker load` 并核对 image ID，然后写入部署配置、迁移、初始化业务配置、创建管理员并 `up --wait`。服务器没有 registry 访问能力，也没有本地 PostgreSQL / Redis，连接指向阿里云 RDS 与 Tair。完整说明见 [kdevops 部署](../scripts/README.md#kdevops-部署)。

手工排障时仍可在项目根目录单独构建：

```sh
docker buildx build --platform linux/amd64 --load -t devops.kevinc.ltd/core/devops:manual server
docker buildx build --platform linux/amd64 --load -t devops.kevinc.ltd/core/devops-gateway:manual -f gateway/Dockerfile .
```

Gateway 镜像仅在 Nginx、CGI 或运行时变化时重建，日常前端更新使用下方的独立发布命令。本地集成测试使用原生单架构 `--load`，见 [脚本 README](../scripts/README.md#真实-git--gateway-集成验证)。

把 `compose.deploy.yaml` 与 `registry/` 放到 `/dockerdata/devops/deploy`；`.env.deploy` 由 `scripts/deploy.py` 从本地 `deploy.env` 生成（0600），包含 RDS / Tair 连接、SMTP 和两个镜像标签：

```sh
ssh kdevops 'ls -l /dockerdata/devops/deploy'
```

`.env.deploy` 只有连接凭据与镜像标签，业务配置仍在 PG `config` 表；Compose 不再包含 postgres / redis 服务。

## 由部署者执行的首次启动

以下命令在服务器部署目录执行。已有数据升级前先备份数据库和仓库；不会自动同步开发数据库。

```sh
docker compose --env-file .env.deploy -f compose.deploy.yaml config --quiet
docker compose --env-file .env.deploy -f compose.deploy.yaml run --rm --no-deps server alembic upgrade head
docker compose --env-file .env.deploy -f compose.deploy.yaml run --rm --no-deps server python -m devops_server.config_cli init
```

`init` 在空库中生成 AES/HMAC 密钥等配置，不创建用户；已初始化的库保留已有配置。首次初始化后、启动后端前，设置正式域名、HTTPS Cookie、Git 开关与存储目录：

```sh
docker compose --env-file .env.deploy -f compose.deploy.yaml run --rm -T --no-deps server python -m devops_server.config_cli set <<'JSON'
{
  "ENVIRONMENT": "production",
  "SESSION_COOKIE_SECURE": true,
  "SESSION_COOKIE_NAME": "devops_session",
  "CSRF_COOKIE_NAME": "devops_csrf",
  "REGISTRATION_ENABLED": false,
  "REGISTRY_ENABLED": true,
  "ALLOWED_ORIGINS": ["https://devops.kevinc.ltd"],
  "GIT_OAUTH_ENABLED": true,
  "GIT_REPOSITORIES_ROOT": "/mnt/git-repos"
}
JSON
```

SMTP 由 `deploy.env` 的 `SMTP_HOST`、`SMTP_PORT`、`SMTP_SSL`（`none` / `starttls` / `ssl`）、`SMTP_USER`、`SMTP_PASSWORD`、`SMTP_FROM` 提供，`scripts/deploy.py` 通过 `config_cli set` 写成 `SMTP_SECURITY` 等运行时配置。SMTP 凭据不写镜像、Compose 或命令参数；恢复现有数据库时保留原 AES/HMAC 密钥和 SMTP 配置。

启动 Gateway 前，先在本机仓库根目录准备网站文件（本机需要 Node.js / pnpm，服务器只需要 Python 3.12+）：

```sh
uv run --project server python scripts/deploy_website.py publish --host devops --prepare-only
```

保存输出的 release 名称。`--prepare-only` 仍然上传并安装、切换 `current`，只跳过 HTTP 验收；它不是 dry-run，不能证明网站已上线。

创建 Git 根目录并赋予 UID/GID 101 写权限；不需要手工创建任何 bare 仓库。确认 SSL 证书就位后在服务器部署目录启动：

```sh
install -d -m 750 -o 101 -g 101 /dockerdata/git-repos
docker compose --env-file .env.deploy -f compose.deploy.yaml up -d --wait server worker gateway
docker compose --env-file .env.deploy -f compose.deploy.yaml ps
curl --fail https://devops.kevinc.ltd/api/health/ready
```

然后在本机校验刚发布的版本：

```sh
uv run --project server python scripts/deploy_website.py verify --release '<上一步输出的 release>'
```

普通成员即可创建和使用仓库。全新数据库不自带用户；如需系统管理能力，仍由你手动在数据库维护全局 `admin` 角色，注册 API 不能生成系统管理员。可用 `REGISTRATION_ENABLED=false` 关闭公开注册。

后续更新按 `pull` → `run ... alembic upgrade head` → `up -d --wait` 执行。修改 DB 配置后重启所有 server 实例。将 `GIT_OAUTH_ENABLED` 改为 false 并重启可关闭 Git 访问和新授权，不删除仓库或成员数据。

当前直接终止 TLS，把 socket 对端地址传给 FastAPI。前面添加 CDN 时，应在 Nginx 按可信出口网段配置 `set_real_ip_from` / `real_ip_header` / `real_ip_recursive`，再向 DevOps 转发解析后的客户端 IP。

## 前端独立发布

本机项目根目录执行：

```sh
make -C . website-deploy DEPLOY_HOST=devops
# 等价：
uv run --project server python scripts/deploy_website.py publish --host devops
```

脚本执行 `pnpm install --frozen-lockfile` 和 `pnpm build`，将 dist 打包为 `.data/website-releases/<release>.tar.gz`，通过 scp 上传并校验 SHA-256。服务器解包到独立目录，校验入口与资源后原子切换 `current`，最后通过 HTTPS 校验版本标记、入口资源和 API readiness。失败返回非零，不自动回滚。无需重建镜像、重启 Gateway 或触碰数据库。

服务器目录：

```text
/dockerdata/gateway/website/
  current -> releases/<release>
  releases/<release>/       # 完整构建产物和 release.json
  assets/                  # 各版本内容哈希资源，供已打开的页面继续加载
```

必须挂载父目录，不能只挂载 `current`：Docker 会解析挂载时的软链接，导致后续切换不可见。发布保留旧目录和旧 assets，不自动清理，以免影响旧标签页；按磁盘维护策略另行清理。

从包含前端的旧 Gateway 镜像升级时，先备份部署 Compose，并在服务器将旧 assets 复制到新的共享目录，再运行 `publish --prepare-only`。下面的复制仅用于第一次迁移、尚未发布新静态目录时：

```sh
install -d -m 755 /dockerdata/gateway/website/assets
docker cp "$(docker compose --env-file .env.deploy -f compose.deploy.yaml ps -q gateway):/usr/share/nginx/html/assets/." /dockerdata/gateway/website/assets/
```

随后更新 Gateway 镜像和 Compose 的网站挂载，运行 `up -d --no-deps --wait gateway`，再运行 `verify`。后端镜像、连接配置和现有数据保持原部署值。网站文件缺失时 Gateway 启动及容器健康检查会失败，不会误报健康。

## 客户端配置和首次 clone

按 GCM 的通用 OAuth 实现对接，已验证独立 **GCM v2.9.1** 和 **GitHub Desktop 3.6.6** 均能从 clone 拉起 DevOps 登录页，并携带后续授权跳转。2026-10-03 又通过 Desktop 自带 GCM 复用已有授权，在命令行完成测试站点 clone / push / fetch 和 LFS 上传 / 下载；尚未完整记录一次新的邮箱登录到授权、clone 的浏览器流程，也不代表 Desktop 全部 GUI 操作已验收。**安装 GCM、客户端实际启用 GCM、配置 DevOps 域名的 OAuth 参数是三件事。** Git Smart HTTP 的 401 响应不会让所有客户端自动发现本站 OAuth；仅有 keychain helper 也不会打开网页登录。

### GitHub Desktop

GitHub Desktop **3.6.6** 自带 GCM **2.9.0**，但默认没有启用。需要同时完成以下两步：

1. macOS：`GitHub Desktop → Settings → Advanced → Use Git Credential Manager`，勾选后点击 **Save**。Windows 对应 `File → Options → Advanced`。
2. 在终端执行一次本站配置：

```sh
sh gateway/configure-gcm.sh --desktop
```

`--desktop` 只写入本站的 OAuth 参数，不修改 Git 的 credential helper。Desktop 启用上述选项后会调用其自带的 GCM，因此不要求终端中的 `git credential-manager` 可用。

先在网站「工程仓库」创建仓库，再把详情页的 HTTPS 地址粘贴到 `File → Clone Repository → URL`。GitHub Desktop 应打开 DevOps 登录 / 授权页；若仍出现用户名密码输入框，先检查上述开关是否已保存、是否在同一系统账号下运行过配置脚本。不能把命令行 GCM 的验证结果当作 Desktop 的验证结果。

### 命令行 Git

不带参数执行脚本即可：

```sh
sh gateway/configure-gcm.sh
```

脚本会先找 PATH 上的 `git-credential-manager`，找不到时回退到 GitHub Desktop 自带的 GCM（`/Applications/GitHub Desktop.app/.../git-credential-manager`），也可以用 `GCM_PATH=/path/to/git-credential-manager` 显式指定。它只重置本站域名的 helper 并写入 OAuth 参数，其他域名的凭据配置保持不变。

注意本机不要同时保留手动写的 `credential.https://devops.kevinc.ltd.provider` 之类残缺配置：只写 `provider = generic` 而没有 OAuth 端点时，GCM 会退化成用户名密码框。重跑脚本会补齐全部键。

随后执行 `git clone`，地址从仓库详情页复制。

两种客户端均通过 GCM 打开 DevOps 页面，登录并点击“允许访问”，回调本机动态端口后继续 clone。已有 DevOps 登录可直接确认。新仓库的 main 已包含 README 初始提交，clone 后即可查看；历史空仓库仍可能显示 empty repository 提示。之后按仓库保护规则提交和推送。

## 仓库与成员规则

- `repositories`、`repository_members` 使用自增 BIGINT 主键。同一拥有者下未删除的仓库名唯一，不同拥有者可使用相同名称。创建者自动成为拥有者；创建和简介修改接口不能指定拥有者，移交使用独立接口。
- 拥有者具有读写和管理权限；成员分开保存 `access`（`read` / `write`）、`is_admin` 和 `role`（最多 50 字的岗位描述）。岗位可填写“开发人员”“测试人员”等，不参与鉴权。
- 拥有者可管理所有成员、任免仓库管理员。仓库管理员可添加、编辑、移除普通成员，不能管理拥有者、其他管理员或任免管理员。管理权限本身不隐含 Git 写权限。
- 只读成员可以 clone/fetch/pull；读写成员增加 push。当前所有仓库均为私有，只对拥有者和成员可见；全局系统管理员同样遵守仓库成员权限。
- 用户搜索只向仓库管理者开放，仅返回姓名、用户名和 ID，不返回邮箱、手机号或认证信息。只能添加正常账号。
- 创建 API 先分配 ID，再独占创建 bare 目录，配置 `main`、关闭 dumb HTTP、启用 receive-pack，以系统身份提交包含仓库名与 clone 命令的 README，成功后才提交元数据；不覆盖任何已有目录或补写历史仓库。数据库提交结果不明时保留新目录供恢复，不自动删除可能已提交的仓库。
- 元数据与成员变更写入审计。成员变更按仓库加锁后重新检查管理权限；权限降级后不能靠旧网页或旧 OAuth token 继续操作。
- 网页使用 `/repositories/<拥有者用户名>/<仓库名>`；已保存的数字 ID 网页地址会跳转到当前名称地址。内部修改接口继续使用稳定 ID。
- 仅拥有者可以移交仓库给其他正常账号（可为已有成员），必须确认当前完整仓库名。新拥有者已有同名仓库时返回 409，原权限与成员保持不变。成功后新拥有者不再重复占用成员记录；原拥有者成为读写的仓库管理员，其他成员不变。仓库 ID 和 bare 存储目录不变。
- 拥有者移交或用户名改变时，网页和 HTTPS Git 地址随之改变；旧名称地址不提供重定向，团队需更新 `git remote set-url origin <新地址>`。移交、成员和生命周期操作按同一仓库行加锁后重新检查权限。
- 仅拥有者可以归档或取消归档。归档不删除内容或改变成员原有权限，但所有 Git 请求只允许读取，拥有者也不能 push；取消归档恢复原有读写权限。成员管理仍可用，便于及时撤销访问。
- 仅拥有者可以删除仓库，必须确认当前完整仓库名。删除为软删除：元数据、成员历史和 ID 对应的代码目录暂时保留，网页列表、详情、成员操作和 Git 鉴权都拒绝已删除仓库；不承诺自动清理期限，当前没有网页恢复入口。后续在同一拥有者下创建同名仓库会分配新 ID、目录和权限，不继承旧仓库数据。
- 移交、归档、取消归档和删除均记录审计。生命周期变更影响后续 Git 请求，不中断已经获准的在途传输。

| 接口 | 用途 |
|---|---|
| `GET /api/repositories` | 分页查询自己拥有或参与的仓库 |
| `POST /api/repositories` | 创建私有 bare 仓库和 main / README 初始提交，当前用户为拥有者 |
| `GET /api/repositories/{id}` | 仓库详情、当前权限和克隆路径 |
| `GET /api/repositories/by-name/{owner}/{name}` | 按拥有者与仓库名读取详情，同样校验私有访问权限 |
| `PATCH /api/repositories/{id}` | 修改仓库简介 |
| `GET /api/repositories/{id}/branches` | 按名称排序的分支，最多 1,000 个 |
| `GET /api/repositories/{id}/activity` | 私有 ACL；近 12 周所有分支去重后的提交数，按 UTC 周一分桶，超出 10,000 条时标记截断 |
| `GET /api/repositories/{id}/contents?ref=main&path=...&page=1` | 字面 Git 路径；目录每页 200 项并携带当前目录 README，文本预览上限 1 MiB |
| `GET /api/repositories/{id}/image?ref=main&path=...` | 同一私有 ACL 的 README 图片；最大 5 MiB，只接受 PNG / JPEG / GIF / WebP |
| `PUT /api/repositories/{id}/protection` | 仅拥有者，严格布尔 protect_main / protect_push，记录审计 |
| `GET /api/repositories/{id}/lfs/{oid}` | 浏览器会话与仓库 ACL 验证后的附件下载 |
| `GET /api/repositories/{id}/transfer-candidates?q=...` | 拥有者搜索其他正常账号，包含已有成员 |
| `POST /api/repositories/{id}/transfer` | 移交，提交 `new_owner_id` 和 `confirm_name` |
| `PUT /api/repositories/{id}/archive` | 归档或取消归档，提交 `archived` 布尔值 |
| `DELETE /api/repositories/{id}` | 软删除，提交 `confirm_name` |
| `GET /api/repositories/{id}/members` | 查看成员，不暴露联系方式 |
| `GET /api/repositories/{id}/member-candidates?q=...` | 搜索可添加的同事 |
| `POST /api/repositories/{id}/members` | 添加成员 |
| `PATCH /api/repositories/{id}/members/{user_id}` | 修改读写权限、岗位角色和管理员身份 |
| `DELETE /api/repositories/{id}/members/{user_id}` | 移除成员 |

## 从原 Git 联调版本升级

`0007_repositories` 创建正式仓库表并补充 `GIT_REPOSITORIES_ROOT` 配置。它撤销旧 Git 授权和未完成的授权请求，因为此前的用户确认只涵盖固定测试仓库。网页登录会话和人员数据保留；客户端下一次访问需要重新确认新的仓库授权范围。

首次从 `0006_git_oauth_pilot` 升级时，暂停 gateway/server，备份数据库和 Git 目录，把旧的 `/dockerdata/git-repos/1.git` 移到 Git 根目录以外的备份目录，然后执行迁移并启动新镜像。**仅在确认该目录属于原联调仓库且尚无正式仓库记录时归档；以后不能重复移动正常的 `1.git`。** 旧单段 Git URL 不再提供服务。新仓库通过网站/API 创建，不预置特殊仓库。

本地 `uv` 开发时，`GIT_REPOSITORIES_ROOT` 使用 `.data/git-repos` 的绝对路径，目录被 Git 和 Docker 构建忽略；后端需要本机 `git`。容器部署固定使用共享挂载 `/mnt/git-repos`。首次初始化容器配置时，务必覆盖开发默认路径。

`0008_repository_lifecycle` 为现有仓库补上默认未归档、未删除状态，将名称唯一约束改为仅约束未删除记录的索引；已有 ID、代码、成员、会话和 Git 授权保留。已有归档或删除记录时拒绝降级，避免旧版本重新开放这些仓库。升级前备份数据库与 Git 目录，先迁移再启动新镜像。

## 分支与推送保护

Settings 的两个开关默认关闭，仅拥有者可调整；管理员和写入成员不能修改。`protect_main` 禁止创建、更新和删除 `refs/heads/main`，拥有者也不例外；经应用权限、审批和 checks 校验的 PR 合并通过独立的服务端原子引用事务完成。`protect_push` 禁止非快进分支更新、覆盖已有标签和删除任何 ref；普通快进与创建新分支 / 标签允许。它检测实际历史变更，`--force` 推送一个快进提交仍属于安全更新。

Git CGI 使用镜像内只读空目录作为 HOME / XDG_CONFIG_HOME，避免进程降权后继承 `/root` 并读取个人 attributes / excludes；全局 Git 配置仍禁用。

内部鉴权返回可信 `X-Git-Protect-Main` / `X-Git-Protect-Push`，Nginx 传为专用 CGI 环境变量。镜像内包装器固定 `core.hooksPath`，pre-receive 在更新任何 ref 前检查整个推送；混合推送包含受保护更新时全部拒绝，策略缺失时拒绝。`git init --shared` 的默认 `receive.denyNonFastForwards=true` 由包装器覆盖，规则统一由钩子执行，关闭开关后行为一致。客户端不能通过同名 Header 或 `--no-verify` 绕过。

`0009_repository_protection` 补充字段，不移动旧仓库或重新授权；发布时必须同时更新 server / gateway / website，测试服务器运行 `./deploy.sh all --migrate`。任一仓库启用保护时迁移拒绝降级。规则按每次 Git 请求读取，不中断已经进入接收过程的推送。设计参考 [Git pre-receive 文档](https://git-scm.com/docs/githooks#pre-receive)。

## Git LFS

客户端安装 Git LFS 和本站 GCM 配置后，在本地 clone 的仓库执行：

```sh
git lfs install --local
git lfs track "*.zip"
git add .gitattributes
# 添加需要 LFS 管理的文件，再照常 git commit / push。
```

自动发现地址为 `/git/<owner>/<name>.git/info/lfs`，无需额外 Token 或设置 lfs.url。新规则不自动转换已有 Git 历史；已有大文件需另行规划迁移历史，不能无提示改写共享分支。

实现遵循 [Git LFS Batch API](https://github.com/git-lfs/git-lfs/blob/main/docs/api/batch.md) 和 [Basic Transfer](https://github.com/git-lfs/git-lfs/blob/main/docs/api/basic-transfers.md)：

| 路径（相对 info/lfs） | 行为 |
|---|---|
| `POST /objects/batch` | upload / download 协商；最多 100 个对象，JSON 最大 64 KiB；仅 basic / SHA-256 |
| `PUT /objects/{oid}?size=...` | 原始数据流；校验声明大小与 SHA-256，原子发布；上限 5 GiB |
| `GET /objects/{oid}` | 原始附件下载，支持 Range |
| `POST /objects/{oid}/verify` | 确认已验证对象存在且大小一致 |
| `POST /locks/verify` | 鉴权后返回 501，告知客户端未实现文件锁 |

所有请求仅接受 `Basic oauth2:<Git access token>`，沿用 GCM / OAuth 刷新机制，逐次检查授权期限、吊销、账号状态及仓库权限。只读成员可下载；归档禁止上传；软删除拒绝访问；移交后更新 remote。Cookie 不能代替 Git 凭据。仅上述精确机器写路由免 Cookie CSRF，普通 API 不受影响。操作 URL 取 Host 匹配的 `ALLOWED_ORIGINS`，不使用内部 HTTP scheme，也不在 URL 或响应中嵌入 Token。

存储路径是 `<GIT_REPOSITORIES_ROOT>/<id>.git/lfs/objects/<前2位>/<再2位>/<sha256>`。相同对象在不同仓库分别存储；不得按全局 hash 开放跨仓库访问。上传临时文件校验失败 / 中断时删除，结束时重新鉴权并锁仓库才发布；已吊销的授权不能完成在途上传。备份整个 Git 根目录会包含 LFS；仅 pg_dump 不包含对象。当前没有对象垃圾回收、配额、LFS 锁、上传续传或推送前指针完整性检查；不要手动清理可能仍被 Git 历史引用的对象。

## OAuth 接口与边界

| 接口 | 用途 |
|---|---|
| `GET /oauth/authorize` | 创建与本浏览器绑定的授权请求，跳到 DevOps 授权页 |
| `GET /api/oauth/authorization-requests/{request}` | 登录后读取待授权信息 |
| `POST /api/oauth/authorization-requests/{request}/decision` | 确认/拒绝，使用 Cookie + CSRF |
| `POST /oauth/token` | 表单 authorization_code + PKCE，或 refresh_token 换发 |
| `POST /oauth/revoke` | 表单 token，撤销整条授权 |
| `GET /api/me/git-authorizations` | 最近 100 条自己未撤销的授权，过期项仍保留，绝不返回 token |
| `DELETE /api/me/git-authorizations/{id}` | 撤销自己的授权，使用 CSRF |
| `GET /_internal/git/authorize` | Nginx 内部鉴权，外部访问一律 404 |

- 固定 public client `devops-gcm`，无需 client secret，PKCE 仅允许 S256，scope 为 `git:read git:write`。
- callback 仅允许 `http://127.0.0.1:<动态端口>/`，换码时必须完全一致；待授权请求 10 分钟，一次性授权码 60 秒，state 原样返回。
- access/refresh token 使用 48 随机字节的 Base64url（64 字符），按现有策略保存原始值，主键均为 BIGINT 自增长。access 最多 1 小时，新授权有效期 30 天。
- refresh 每次轮换，旧 access 随之失效；旧 refresh 重放撤销整条授权。每次成功刷新将授权到期时间设为本次刷新时间加 30 天，活跃客户端可持续续期；连续 30 天未刷新、到期或撤销后需重新授权。过期或撤销的授权不能通过刷新恢复，失败刷新不延长期限。GCM 在 Git 需要凭据时使用保存的 refresh token 换取新凭据，失败后进入交互授权；服务端不后台定时续签。已有有效授权下次刷新即可续期，无需重新授权。撤销记录从个人列表隐藏，数据库仍保留历史。GCM 自行保存凭据，不要求用户创建或复制 PAT。
- Git 使用 Basic `oauth2:<access_token>`，身份来自 token，不使用 DevOps 密码或 Cookie 回退。退出网页登录不会退出 Git，可在「账号安全」撤销 Git 授权。
- 每次请求检查当前账号状态、token/scope、拥有者或仓库成员关系、路径和读写权限。只有仓库拥有者和读写成员可以 push；只读 OAuth scope 也会拒绝 push。移除成员、降低权限、停用用户或撤销授权会影响后续 Git 请求，不中断已获准的在途传输。
- 无效凭据返回带 Basic challenge 的 401；有凭据但无权返回 403；依赖失败拒绝执行 Git。只允许四种 Smart HTTP 端点，拒绝静态对象、额外 query、伪造身份头和路径遍历。
- OAuth 中精确的 POST `/oauth/token`、`/oauth/revoke`、`/oauth/device/code` 使用协议规则免 Cookie CSRF；LFS 机器写入按上述白名单豁免。仓库 / 协作 API 仅在有效 CLI Bearer 预校验后豁免，路由事务再次鉴权。授权确认及普通网页写操作保留 CSRF。

## 验证

以下命令从仓库根目录执行。后端与前端测试不依赖远端服务器，真实 Git 脚本的完整镜像构建和本地依赖准备见 [脚本 README](../scripts/README.md#真实-git--gateway-集成验证)。

```sh
uv run --project server pytest server/tests
pnpm --dir website test
pnpm --dir website build
uv run --project server python scripts/verify_git_gateway.py
```

最后的脚本需要已构建两份镜像、已完成本地 `pnpm build`、本地 PG/Redis 正在运行。它使用独立临时 schema、普通成员、证书、会话、网站目录和 Docker 网络，通过正式 API 创建仓库，验证静态文件挂载与不重启切换、真实 HTTPS / OAuth / clone / push / pull / 撤销，结束后清理，不发送邮件或访问远端服务器。API 驱动授权不代替 GCM 打开浏览器的验收。

部署后由操作者在网站创建仓库，使用 GCM 完成邮箱登录、授权与首次 clone/push，再验证只读成员不能 push、移除成员后访问被拒绝。

## 排障入口

| 现象 | 检查顺序 |
|---|---|
| Gateway 启动退出 | `start.sh` 的证书可读性、`current/index.html`、`nginx -t`；再看哪个子进程退出 |
| 容器 healthy，但 API 不可用 | 单独请求 `/api/health/ready`，检查 server 及 PG / Redis；Gateway 自身健康不等于 API 就绪 |
| 发布后仍显示旧网页 | 公网 `release.json`、服务器 current、父目录挂载、缓存；不能只查看 dist 的构建时间 |
| JS 404 或收到 HTML | 共享 assets 是否保留、Nginx assets location、首页引用；静态资源不应走 SPA fallback |
| Git 弹用户名密码框 | GCM 是否安装 / 启用、域名 OAuth 配置、Desktop 设置是否保存；DevOps 兼容密码不能代替 OAuth |
| Git 401 | 缺失 / 失效 Token、授权过期或撤销；让 GCM 重新授权，不在日志输出 Token |
| Git 403 | 账号状态、仓库成员关系、read / write scope、归档状态；全局 admin 不绕过 ACL |
| 创建或 push 文件权限错误 | 两个容器 UID/GID 101、Git 卷同源、根目录权限 / 磁盘空间；不要改成全局 0777 |
| 移交后旧地址失效 | 使用新 owner/name 地址并更新 Git remote；当前没有旧名称别名 |

发布前保留当前镜像、Compose、数据库和 Git 数据所需的恢复信息，按变化只更新相关组件。日常网站发布不重建 Gateway；后端 / 网关变更则分别构建和验收。静态网站脚本不执行数据库迁移；测试部署入口仅在显式指定 `--migrate` 时备份并升级，两个入口都不会自动回滚。

参考：[GitHub Desktop 的 GCM 设置](https://github.com/desktop/desktop/blob/release-3.6.6/docs/integrations/gitlab.md)、[Desktop 调用 GCM 的实现](https://github.com/desktop/desktop/blob/release-3.6.6/app/src/lib/git/credential.ts)、[GCM 通用 OAuth](https://github.com/git-ecosystem/git-credential-manager/blob/v2.9.1/docs/generic-oauth.md)、[GCM OAuth 实现](https://github.com/git-ecosystem/git-credential-manager/blob/v2.9.1/src/shared/Core/Authentication/OAuth/OAuth2Client.cs)、[PKCE RFC 7636](https://www.rfc-editor.org/rfc/rfc7636.html)、[Nginx auth_request](https://nginx.org/en/docs/http/ngx_http_auth_request_module.html)、[Git HTTP backend](https://git-scm.com/docs/git-http-backend)。

## 协作任务与推送回执

镜像内 `post-receive` 从内部鉴权传入的 `REMOTE_USER` 取得用户 ID，把分支更新写成 `<id>.git/devops-events/push.*.ready` 原子回执。推送不等待冲突检测，也不调用外部 API。worker 写入数据库持久任务后才移除回执，重复摄取幂等；定时扫描开放 PR 可恢复遗漏的分支同步。

`refs/devops/` 留给 PR 快照和合并恢复，pre-receive 拒绝任何客户端写入这些引用。server / worker / gateway 共用 UID/GID 101 和 Git 挂载。备份完整 Git 根目录，并与数据库备份配套；不要手动删除内部引用或未处理回执。

## Docker Registry 入口

`/v2/` 经 `auth_request /_internal/registry/authorize` 逐请求检查当前 ACL，再将镜像数据流式转发到私有 `registry:5000`。内部鉴权路径外部返回 404，Cookie 不传给 Registry。官方容器不发布端口，存储禁止跳转；不要新增直达存储的入口。部署、原生 docker login 与收权边界见 [Registry README](../registry/README.md)。

跨镜像 mount 在目标有写权限、来源无读权限时，鉴权响应通过 `X-Registry-Upload-Only: 1` 指示 Gateway 丢弃 mount / from 查询参数，启动普通上传。此信号只来自受信任的 `auth_request_set` 上游响应，不能采信客户端同名 Header。正常上传的 `_state` 等参数仍原样转发；来源不会被读取或关联，目标越权请求继续拒绝。

## CI 通信

`/api/ci/worker` 显式转发 WebSocket Upgrade，关闭代理缓冲，空闲超时 120 秒；机器每 10 秒发送心跳。`/api/ci/artifacts/<id>` 的 PUT 使用任务级 Bearer、流式上传和 256 MiB 网关限制，后端再次检查大小与权限。浏览器 Cookie 不能注册 worker 或上传任务产物。`/res/devops-runner/` 是独立只读静态安装包目录，网站发布切换后仍可下载。其他 `/api/` 路由继续使用原认证与 CSRF 规则。
