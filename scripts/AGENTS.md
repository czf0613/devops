# 脚本修改规则

继承 [DevOps AGENTS.md](../AGENTS.md)，参数与操作说明见 [README.md](README.md)。

- 明确命令是只读、创建隔离本地资源，还是写远端。`publish --prepare-only` 仍会上传并切换，不能标成演练模式。
- `deploy.sh` 只转发参数，`deploy.py` 只处理 kdevops。保留组件选择、先本机构建再 `docker save` / `ssh` / `docker load`、两端 image ID 核对、健康检查与真正无发布副作用的 `--dry-run`。
- 发布默认只构建 `linux/amd64`；官方 `registry:3.1.2` 先按 digest 校验本地副本再传输，不能改为在服务器上 `docker pull`。Compose 由仓库版本生成，不再就地改写远端旧文件；保留 Gateway 的 TCP 443 与 UDP 443 映射，未知结构必须拒绝。
- 本地集成必须使用原生架构镜像，拒绝误用异构旧镜像。HTTP/3 探针验证 TLS 和 h3 ALPN，不得关闭证书校验或回退 TCP 后宣称通过。
- 后端发布先执行 `alembic upgrade head`，再初始化 / 更新运行时配置；数据库是托管 RDS，升级依赖 RDS 快照，脚本不再本地 `pg_dump`。失败不自动回滚、降级、清理数据或触发未选中容器重启。
- `deploy_website.py` 的服务器部分仅依赖 Python 3.12+ 标准库；不能引入服务器 Node.js、pnpm 或额外 pip 依赖。
- 保留校验和、目录锁、路径 / 软硬链接检查、入口资源校验、同名资源内容冲突检查和原子切换。
- 保留旧 assets 以支持已打开页面。不能隐式删除旧 release、清空共享目录或在失败时自动回滚。
- 外部命令使用参数数组；确需远端 shell 的部分用正确 shell quoting，禁止拼接未转义路径或输出凭据。
- 验收必须读实际 release 和入口资源、检查 API readiness；安装成功不能代替线上验证。
- 集成测试继续只允许本地 PG / Redis、随机命名、合成账号、finally 清理。不得使用共享服务器 / 真实邮箱或 `FLUSHDB`。
- Git 集成测试需本机 Git LFS，使用 --local 安装钩子；覆盖强推 / 删除 / 混合推送拒绝和 LFS 实际上传下载，不仅用 API mock 代替协议验收。
- 静态挂载测试保留 Linux 卷和当前安装脚本，防止 macOS 文件系统差异让结果失真。
- `redis-start.sh` 的 DB 0、认证和 AOF 设置需与 Compose / 后端一致；不要为了排障打印启动环境。

修改后运行相应 Ruff 和 `test_website_deploy.py`；涉及容器挂载或 Git 协议时再运行真实集成脚本。新增参数同步 Makefile、模块 README 和 Gateway 文档；不要为文档核验执行真正发布命令。

- server 组件包括同镜像的 worker；两者共用镜像标签与 Git 挂载。核对 server / worker / gateway 的实际镜像与健康状态，gateway 必须在网站发布后再启动。
