# 开发、发布与验证脚本

规则见 [AGENTS.md](AGENTS.md)。以下命令除明确说明外从仓库根目录执行；运行前识别目标是本机还是部署服务器。

| 脚本 | 用途 | 副作用 |
|---|---|---|
| [init_env.py](init_env.py) | 补齐本地 PG / Redis 连接文件 | 写 `.env`，生成缺失连接密码，保留已有值并设置 0600 |
| [redis-start.sh](redis-start.sh) | 本地 / 部署 Compose 的 Redis 入口 | 启动需认证、AOF、单 DB 的 Redis，参数含服务环境中的密码 |
| [deploy.py](deploy.py) / [../deploy.sh](../deploy.sh) | 选择 server / gateway / registry / cli / runner / website，更新 kdevops | 本机构建 amd64 镜像、save/load 传输、迁移 / 配置 / 起服务；website 调用独立静态发布器 |
| [deploy_runner.py](deploy_runner.py) | 生成独立 worker wheel 并发布不可变版本 | 本机打包；publish 写测试站静态目录并核对公网 SHA-256 |
| [deploy_cli.py](deploy_cli.py) | 生成白名单 npm 包并发布不可变版本 | 本机构建；publish 写测试站静态目录并核对公网 SHA-256 |
| [deploy_website.py](deploy_website.py) | 本机构建并独立发布网站；单独验收已有 release | `publish` 写本地产物并通过 SSH / scp 写服务器；`verify` 只发 HTTP 请求 |
| [verify_git_gateway.py](verify_git_gateway.py) | 本机 Docker 的真实 HTTPS / Git 集成 | 创建临时 schema、Redis 键、容器、网络、卷和测试仓库，结束清理 |

`../gateway/configure-gcm.sh` 属于客户端设置工具，会修改当前用户的全局 Git 域名配置；不属于服务部署或只读验证。

## 本地初始化

```sh
make -C . env
make -C . setup
```

`env` 只处理连接文件。`setup` 还安装依赖、启动数据库、执行迁移、初始化业务配置。不要把连接初始化当作重置现有密码的工具；已有数据库卷的密码必须与连接配置一致。旧 `.env` 业务项由后端 `config_cli init` 导入后移除，不由 `init_env.py` 负责迁移。

## kdevops 部署

目标是 `ssh kdevops`（Debian 13 x64）上的 `/dockerdata/devops/deploy`。服务器访问不到 Docker Hub，所以所有镜像都在本机按 `linux/amd64` 构建，用 `docker save` → `gzip` → SSH → `docker load` 传输，再按 image ID 双向核对；整个过程不使用远端 registry，也不执行 `docker push` / `docker pull`。

连接信息在本地 `deploy.env`（git 忽略，模板见 `deploy.env.example`）：阿里云 RDS PostgreSQL、Tair（Redis 7.0 兼容，仅 DB 0）和 SMTP。`scripts/deploy.py` 每次把它写成远端 0600 的 `.env.deploy`，并补上 server / gateway 两个镜像标签，所以 `compose.deploy.yaml` 里没有数据库容器，只有 server / worker / registry / registry-gc / gateway。

本机需要 uv / Python 3.14、Docker Buildx（支持 `linux/amd64` 模拟构建）、Git、SSH；发布 website 还需要 Node.js / pnpm。

```sh
./deploy.sh all                 # 首次部署或完整更新
./deploy.sh server              # 只更新后端与 worker
./deploy.sh website             # 只发布网站
./deploy.sh all --dry-run       # 只看计划，不构建也不连服务器
```

`deploy.sh` 只负责定位目录并转发参数，逻辑在 `scripts/deploy.py`：

1. 本机按 `linux/amd64` 构建 server / gateway，标签为 `devops.kevinc.ltd/core/devops[-gateway]:<短提交>[-dirty]-<UTC时间>`；同时按 digest 拉取官方 `registry:3.1.2` 再打成本地标签，供 registry 服务使用。
2. 一次 `docker save` 流经 SSH 交给服务器 `docker load`，逐个核对两端 image ID；任何不一致立即停止。
3. 建立 `/dockerdata/devops/{deploy,git-repos,registry}` 与 `/dockerdata/gateway/{cer,website}`，`git-repos` 归 UID 101；写入 `.env.deploy`、`compose.deploy.yaml` 和 `registry/` 配置。
4. server 组件先执行 `alembic upgrade head`，再 `config_cli init` 与 `config_cli set`（生产配置：`ALLOWED_ORIGINS=https://devops.kevinc.ltd`、关闭注册、开启 Git OAuth 与 Registry、写入 SMTP），最后按 `ADMIN_EMAIL` 创建或补授管理员。
5. 启动 server / worker / registry / registry-gc，发布网站后再启动 gateway；用 `up --wait` 和服务器内部 HTTPS 请求核对 readiness 与网站 release。
6. 任一步失败返回非零、不自动回滚；数据库是托管 RDS，升级前依赖 RDS 快照，不再在部署脚本里 `pg_dump` 到本机或服务器磁盘。

Gateway 使用 host 网络直接绑定宿主机 443（TCP + UDP，IPv4 + IPv6），所以 `$remote_addr` 就是真实客户端地址；server 与 registry 只发布到 `127.0.0.1`，公网无法直连。宿主机的公网 `/128` IPv6 由 nginx 的 `listen [::]:443` 直接承接，不需要给 Docker bridge 分配前缀。

Compose 为每个服务显式设置 `json-file` + `50m × 3` 日志轮转（每容器上限约 150 MB），不依赖宿主机 `/etc/docker/daemon.json`；宿主 daemon 也配置了相同默认值。

证书固定读取 `/dockerdata/gateway/cer/kevinc.pem` 与 `kevinc.key`；网站 release、共享 assets 和 `/res/` 下载都落在 `/dockerdata/gateway/website`。首次启用 HTTP/3 前，安全组需同时放行 TCP 443 与 UDP 443，并用 `scripts/verify_http3.py` 验证真实 QUIC 握手。

## 网站独立发布

本机需要 Python 3.14 / uv、Node.js / pnpm、Git、ssh、scp。目标 Linux 服务器需要 Python **3.12+** 标准库、可写网站目录及 Docker 挂载准备；无需安装 Node.js 或 pnpm。

已配置服务器时：

```sh
make -C . website-deploy DEPLOY_HOST=kdevops
```

等价及自定义参数：

```sh
uv run --project server python scripts/deploy_website.py publish \
  --host kdevops --root /dockerdata/gateway/website --url https://devops.kevinc.ltd
```

| 参数 / 子命令 | 含义 |
|---|---|
| `publish --host` | SSH 目标，默认 `devops` |
| `publish --root` | 服务器网站父目录，默认 `/dockerdata/gateway/website` |
| `publish --url` | 发布后验收的公开 origin，默认 `https://devops.kevinc.ltd` |
| `publish --prepare-only` | 仍然上传、安装并切换 current，只跳过 HTTP 验收；不是 dry-run |
| `verify --release <release> --url <origin>` | 校验指定版本标记、首页引用的入口 assets 和 API readiness |
| `install --root --archive --checksum --release` | 服务器内部解包 / 切换入口，由 publish 调用；不是一般操作者入口 |

流程：

1. 本机执行 `pnpm install --frozen-lockfile` 和 `pnpm build`。
2. 生成 UTC 时间、短 Git HEAD 和随机后缀构成的 release 名；打包到 `.data/website-releases/`。
3. 验证非空首页和其 assets，拒绝构建目录软链接，写入 `release.json`；上传压缩包与同版本 Python 安装脚本到远端临时目录。
4. 服务器核对 SHA-256，获取目录发布锁，在 staging 安全解包；拒绝路径穿越、链接、缺失资源、版本不匹配等输入。
5. 合并哈希资源到共享 `assets/`。同名不同内容的资源冲突会拒绝，避免破坏旧页面。
6. 将完整版本放入 `releases/` 并原子替换 `current`；通过 HTTPS 校验 release、入口资源和后端依赖 readiness。

目录约定：

```text
/dockerdata/gateway/website/
  .publish.lock
  assets/                         各版本共享的内容哈希资源
  releases/<release>/             完整静态产物与 release.json
  current -> releases/<release>   Nginx 入口
```

Gateway 必须只读挂载父目录到 `/usr/share/nginx/html`，Nginx root 为 `.../current`；不能挂载 `current` 软链接本身。目录 0755、文件 0644，让 Gateway 的运行用户可读。发布不重启 Gateway、不改数据库或后端。

release 名中包含 HEAD，但脚本不要求工作区干净；它不是“产物必然来自该提交”的证明。可追溯发布应先提交功能变更，并在验收时记录代码版本和实际 release。

## 首次安装与失败处理

Gateway 启动前先 `publish --prepare-only`，保存输出 release，准备 Compose 挂载并启动 Gateway 后，再执行：

```sh
uv run --project server python scripts/deploy_website.py verify --release '<实际 release>'
```

从旧镜像内置网站迁移时，先备份 Compose / 当前镜像记录 / 旧网站，再保留旧哈希 assets。具体顺序见 [Gateway README](../gateway/README.md)，不要在已经使用共享静态目录的部署上重复执行旧版迁移步骤。

安装校验失败不会切换 current；共享目录可能已留下部分可复用资源。HTTP 验收发生在切换后，因此 HTTP 失败时 current 可能已更新。任何失败都返回非零，**不会自动回滚**。先检查当前 release、入口资源、Gateway 挂载和 API readiness，再决定修复 / 重新发布。

旧 release、共享 assets 和本地压缩包保留，不自动清理。不要只清空旧 assets：旧标签页可能仍需懒加载它们。磁盘清理和显式恢复策略尚未实现。

## CLI 安装包发布

CLI 发布使用 `deploy_cli.py`：`build` 只生成本机 `.data/cli-releases/<version>.tgz`，`publish` 通过固定测试 SSH 别名 `devops` 发布到网站父目录的 `res/devops-cli/`。验证 npm 包文件白名单、精确运行依赖版本、SHA-256 和无安装脚本；服务器持有发布锁，拒绝链接和同版本内容冲突，写入原子文件及 `.sha256`。旧版本保留，网站 current 切换不影响下载；服务器无需 Node.js。`all` 在镜像完成后先发布 CLI、再发布网站指南。首次公开路径需要先更新 Gateway；`--dry-run` 不构建、上传或连接服务器。

发布单测：`uv run --project server pytest server/tests/test_cli_deploy.py`。下载后安装不需要私有仓库权限，运行依赖仍从公共 npm registry 下载。完整使用方式见 [CLI README](../cli/README.md)。

## 真实 Git / Gateway 集成验证

前提：Docker、Git、Git LFS、openssl、uv / Python、本地 PostgreSQL / Redis 已启动、配置已初始化。脚本只允许本地数据库连接，使用 `:latest` 两份镜像，因此必须先构建当前代码，避免误测旧镜像：

```sh
make -C . setup
docker buildx build --load -t devops.kevinc.ltd/core/devops:latest server
pnpm --dir website build
python3 scripts/deploy_cli.py build
docker buildx build --load -t devops.kevinc.ltd/core/devops-gateway:latest -f gateway/Dockerfile .
uv run --project server python scripts/verify_git_gateway.py
```

上述本地构建默认使用 Docker 的原生架构（不要设置指向异构平台的 `DOCKER_DEFAULT_PLATFORM`）；脚本会在启动前确认两份镜像与本机 Docker 架构一致，ARM Mac 使用 arm64，避免误用遗留 amd64 镜像。`--load` 用于单架构本地验收，双架构发布使用前文 `--push`。

脚本建立临时证书、Docker 网络、两份服务、Linux 网站卷、独立 PG schema、Redis 测试前缀和合成用户。同时映射 loopback TCP / UDP 临时端口，用开发依赖 aioquic 完成验证证书的真实 HTTP/3 请求，校验首页 / API readiness / 内部路径拒绝 / Git 未授权挑战；失败不会降级 TCP 伪装通过。测试通过真实 HTTPS / OAuth / Git 链路创建仓库，核对 main 初始 README、克隆地址和 Code API 一致，再 clone、2 MiB chunked push / pull，检查未授权挑战、内部 / 静态 Git 路径拒绝、授权撤销、移交、归档、删除及静态版本不重启切换。刷新重放、读写成员矩阵和成员移除等更细的 ACL 场景由 `test_git_oauth.py` / `test_repositories.py` 覆盖。不会连接 `ssh kdevops` 或发送邮件。

另覆盖临近到期授权经 HTTPS 刷新后续期 30 天、旧 access 失效及新凭据执行真实 Git / LFS 操作，以及真实代码与提交趋势读取、已撤销授权从个人列表隐藏、main 保护、非快进 / 删除 / 标签替换拒绝、混合 refs 整体拒绝、客户端伪造策略 Header、关闭保护后恢复写入；通过 Git LFS 客户端上传 3 MiB 合成对象、verify、clone / pull，检查归档、移交和吊销后的 LFS 权限。仅在临时 checkout `git lfs install --local`，不改用户全局配置。

网站卷使用 Linux 文件系统，由容器中的 Python 安装脚本切换软链接；这是为了匹配服务器语义，避免 Docker Desktop 的 macOS 共享目录在宿主替换软链接后出现 EINVAL。不要把这项验证简化成只请求静态健康页。

正常异常退出由 finally 清理本次命名空间；进程被强杀时可能留有 `devops-integration-*` 资源，先核对标识再只清理本次创建的对象，不能批量清空 Docker / 数据库。API 驱动授权不代替 GCM 浏览器登录验收。

## HTTP/3 只读验收

在有权访问目标站点的环境执行（默认目标是测试站点）：

```sh
uv run --project server python scripts/verify_http3.py
make -C . http3-check
make -C . http3-check HTTP3_ARGS="--ipv4"  # 或 --ipv6 单独检查地址族
# 本地临时环境可显式指定 origin 与 CA 文件
uv run --project server python scripts/verify_http3.py --url https://127.0.0.1:8443 --ca-file /path/to/sc.pem
```

此脚本依次尝试域名解析出的地址，也可用互斥的 `--ipv4` / `--ipv6` 指定地址族；每次都直接通过 UDP / QUIC 请求首页和 API readiness，校验证书与 `h3` ALPN，不使用 Cookie、不写业务数据、没有 HTTP/2 回退。超时先检查 UDP 443、防火墙、Docker 映射和本机网络；仅看到 `Alt-Svc` 不能证明 HTTP/3 链路可用。aioquic 只属于开发依赖，不进入 server 运行镜像。

## 脚本自身检查

```sh
uv run --project server ruff check scripts
uv run --project server ruff format --check scripts
uv run --project server pytest server/tests/test_website_deploy.py -q
uv run --project server pytest server/tests/test_deploy.py -q
```

发布测试仅在临时目录检查合法压缩包、校验和、路径 / 链接拒绝、资源冲突、旧版本保留和 current 切换，不触碰部署服务器。`deploy_website.py --help` 或各子命令 `--help` 可只读查看参数；`verify_git_gateway.py` 没有 dry-run，调用它即开始创建本地集成环境。

`test_deploy.py` 使用临时 Compose 和命令替身验证组件选择、未选中服务保留、备份 / 迁移顺序、失败时停止、镜像与健康检查、双架构索引 / manifest / config ID 及 containerd 运行描述符、旧 platform / UDP 映射迁移、dry-run 和网站发布复用。真实发布验收另行执行，不能把命令替身测试当作已经上线。

协作集成还验证真实 Gateway 推送回执、Issue / 子 Issue 共享编号、提交反向引用、后台原生 Git 冲突检测、受保护 main 上的显式 PR 合并、关键词关闭 Issue 及 worker 健康。所有账号和仓库仍在随机 schema / 临时目录内，测试结束清理，不创建或修改 GitHub Issue / PR。

Registry 已启用时，server 发布同时补齐 / 更新 `registry-gc`，核验同一原生镜像及独立心跳。该进程不继承 server 的 Git 挂载，不开放端口；固定挂载结构有异常时拒绝覆盖。部署与 GC 共用 Registry 数据父目录的 `.gc.lock`，回收执行中拒绝部署。真实 Gateway 验证包含 `verify_registry_gc.py`，使用临时 Linux Registry 卷、合成双平台镜像和独立后台进程，验证预检 / 回收后保留的引用仍可读取。

整组 namespace 删除验收还覆盖嵌套镜像路径、别名标签、双平台索引与证明：管理员单次邮箱验证后立即拒绝访问，人工维护窗口先清空该 namespace 的标签再执行 GC，验证独占 blob 已物理删除、其他 namespace 的共享层仍存在。测试使用隔离邮箱替身，不向真实用户发送验证码。

## CI Runner 发布与集成

新增 `runner` 独立组件：`./deploy.sh runner` 或 `uv run --project server python scripts/deploy_runner.py publish`。`build` 仅生成本地 `.data/runner-releases/` 下 wheel 和 SHA-256；publish 验证 wheel 文件白名单，通过固定 SSH 别名部署到 `/res/devops-runner/`，同版本不同内容拒绝覆盖，并核对公开 HTTPS 下载及 checksum。目标服务器只需要标准库。首次需要同时发布支持该静态路径的 Gateway；`all` 包含 runner。

CI 完整发布：`./deploy.sh server gateway runner website --migrate`。现有 server/worker 挂载的 Git 根目录内保存 `.ci-data`，不新增数据库连接来源或宿主 Docker socket 挂载。网站构建时从 runner 的 pyproject.toml 读取下载版本。

真实 Gateway harness 现调用 `verify_ci.py`：执行前需 `uv sync --project runner --locked`，使用本机 Python worker 和临时 CA 验证 WSS 注册、真实 Git 检出、push/manual、步骤、依赖产物、取消及进程重启后 lost。验证期间不要同时重建 website/dist，静态切换测试需要固定输入。当前仅 macOS arm64 进行了 worker 真机验收。
