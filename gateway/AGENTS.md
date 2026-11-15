# Gateway 修改规则

继承 [DevOps AGENTS.md](../AGENTS.md)。本目录处理 HTTPS、OAuth 代理、Git Smart HTTP 和 GCM 配置；部署方法见 [README.md](README.md)。

- Dockerfile 仅提供 Nginx、Git、fcgiwrap 和进程监督，不安装 Node.js、不构建网站、不 COPY dist。
- 保留定制主配置的 Brotli 模块加载，不让基础镜像默认站点重新开放额外入口。
- 网站父目录只读挂载；首页从 current 获取，assets 从共享父目录获取。资源不存在返回 404，不能回退 HTML 假装 JS 加载成功。
- `/assets/` 与 `/res/` 使用一年 `immutable` 缓存（文件名含内容哈希或版本号，旧 release 的 assets 保留）；SPA 入口、健康检查和 API 保持 `no-store`。HTTP 80 只允许 301 到 `https://devops.kevinc.ltd`，不提供内容或证书例外路径。
- Gateway 对外暴露 TCP 80（仅 301）与 TCP / UDP 443；FastAPI、PG、Redis 和 FastCGI 保持内部访问，证书只读挂载。
- Git CGI 只允许四种 Smart HTTP 方法 / URI 组合，后端再次校验原始方法、URI、权限和归档状态。LFS 使用独立 API 代理路由，不进入 CGI；禁止 dumb HTTP 或静态读取仓库 config / hooks / objects / lfs。
- CGI 固定通过镜像内 `git-http-backend.sh` 注入 core.hooksPath；pre-receive 对新旧仓库一致生效。仅使用内部鉴权返回的保护变量，缺失或格式错误必须拒绝。客户端 --force / --no-verify 或同名 Header 不能绕过。
- 保留 LFS 流式代理、5 GiB 上限及应用端哈希校验；LFS URL 用配置的 ALLOWED_ORIGINS，不因 --no-proxy-headers 返回内部 HTTP 地址。
- GET 鉴权子请求前冻结原请求信息；用户提供的身份头不能直接传到 CGI。只有后端返回的正整数用户 ID / 仓库 ID 才能选择 Git backend，否则拒绝执行。
- 仓库路径由后端返回的 ID 构成，不使用拥有者 / 仓库名拼物理路径。后端 app 和 CGI nginx 的 UID/GID 101 保持一致。
- OAuth 只代理精确协议路径；`/oauth/consent` 是 SPA 页面。不要捕获整个 `/oauth/` 导致页面被 API 吞掉。
- 日志不记录查询字符串、Cookie、Authorization 或 OAuth code；代理覆盖来源 IP 头，不能直接信任外部伪造值。
- HTTP/3 需要 QUIC 编译模块、UDP 443 映射、Alt-Svc 与真实证书 / ALPN 验证；HTTP/3 的 :authority 必须正确转为代理 Host 并保留端口（当前使用 Nginx 1.29.3+ 变量）；保留 HTTP/1.1 / HTTP/2，保持 ssl_early_data off。不能用 Alt-Svc 响应头代替实际 UDP 握手验收。
- 保留上传缓冲和容量限制；改变大文件 / chunked 行为需真实 Git 验证。CGI 或 Nginx 任一子进程退出，容器必须退出让 Docker 重启。
- 容器健康包括网站和 CGI 存活；部署验收还需要 API readiness、实际 release 和关键请求验证。

修改 Nginx / Dockerfile / start.sh / Git OAuth 时构建当前两份镜像，再运行 `verify_git_gateway.py`，不能仅依赖 `nginx -t`。客户端配置脚本修改全局 Git 设置，运行前确认本次范围；`--desktop` 不替用户开启 Desktop 的 GCM 选项。

- `refs/devops/` 是服务端 PR 快照与合并恢复引用，客户端不得写入。post-receive 只记录内部鉴权身份及分支更新到原子回执，不能执行仓库代码或阻塞等待冲突检测；保持 worker 同 UID / 挂载。
