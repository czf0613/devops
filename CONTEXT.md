# 项目上下文与交接

更新日期：**2026-10-12（Asia/Shanghai）**。当前阶段为网页登录滑动续期与 Issue #22 状态同步。最新发布与验收见文末记录；本文件保留历史快照，不能用旧段落推断当前运行状态。DevOps 仅以桌面为开发和验收目标，不安排手机适配或窄屏检查。

先读 [README.md](README.md) 获取运行入口，再读 [AGENTS.md](AGENTS.md) 的工作规则和 [CODEMAP.md](CODEMAP.md) 的代码导航。需求跟踪沿用历史 Issue #22（已归档在旧仓库），其正文已在本次整理中从早期设计更新为实际实现与后续待办。这里记录阶段状态，不替代当前代码、数据库迁移或现场检查。

## 当前交付范围

当前受 Git 管理的业务实现集中在 `仓库根目录`，是独立部署的 DevOps 开发协作平台。运行链路为浏览器或 Git 客户端 → HTTPS Gateway → FastAPI / Git CGI；PostgreSQL 存账号、权限和配置，Redis DB 0 存共享限流状态。

| 领域 | 已实现 | 当前边界 |
|---|---|---|
| 账号 | 邮箱验证码注册 / 登录、资料、换邮箱、登录设备、全局人员管理、审计 | 不支持用户名密码登录；没有删除用户 API |
| 邮件 | DevOps 品牌 HTML 验证码邮件及纯文本备用正文，登录 / 注册 / 换邮箱 / 兼容密码 / 管理员操作共用模板 | 不加载外部资源；未做真实邮箱客户端兼容矩阵验收 |
| 兼容密码 | 本人邮箱验证后设置 / 清除；管理员只能清除；bcrypt + AES-GCM | 不是本站或 Git 登录密码；旧系统同步尚未接入 |
| 工程仓库 | 私有仓库、成员管理、列表提交趋势，Code / Settings、分支、目录、文本预览、目录 README 渲染；新仓库 main 初始化 README | 不补写既有仓库历史；提交详情 / PR diff 已支持，任意网页分支操作尚未实现 |
| 仓库生命周期 | 拥有者逐次邮箱验证后移交、归档 / 取消归档、软删除，CLI 同样验证；全局管理员可在邮箱验证后强制调整成员、权限、拥有者及生命周期 | 系统管理仅提供元数据与显式操作，不隐式授予代码访问；不提供恢复界面、物理清理或旧名称重定向 |
| Git | GCM 浏览器 OAuth、PKCE、HTTPS clone / fetch / pull / push、授权吊销 | 需要配置客户端；无 SSH、PAT 或密码回退 |
| Issue / PR | 独立路径、共享编号、子项、评论 / 附件 / 引用、审阅、异步冲突检测和三种合并 | 同仓库 PR；无 Projects、跨仓库 PR 或网页冲突编辑器 |
| 保护 | 仅拥有者调整 main / 推送保护，服务端钩子执行 | 两项默认关闭，所有人适用；main 拒绝直接推送，应用授权 PR 合并使用独立服务端路径 |
| LFS | Batch / Basic / verify，5 GiB 对象上限，Git OAuth、按仓库隔离 | 无 LFS 锁 / 配额 / 上传续传 / 垃圾回收 / 推送前指针完整性校验 |
| Registry | 原生 Docker 登录、限时或显式确认的永久用户令牌、repo 多 namespace、镜像 / 标签 / digest 管理，逐请求 DB ACL；管理员逐次邮箱验证管理绑定、删除整组 namespace、人工窗口 GC | 单 namespace 单 repo；整组删除立即停止访问，下次 GC 清理；名称保留；无自动只读或自动 GC，不中断已放行的单次传输 |
| CI | WSS 独立 worker、固定提交、GitHub 格式工作流、push / PR / 手动触发、原生执行、DAG / 有界矩阵、步骤日志、产物、取消和重跑、Code / PR checks | 可信专用机器；仅 macOS arm64 真机验证；一次性隔离、Secrets、缓存待后续 |
| 网关 | TCP 443 的 HTTP/1.1 / HTTP/2 + UDP 443 的 HTTP/3，保留 Brotli、同一 TLS / ACL | IPv6 外网验收受客户端网络条件限制；不启用 TLS 0-RTT |
| 网站 | DevOps 产品名称、深色主题、可折叠侧栏、工作台二级菜单、两行列表简介、紧凑仓库页头、统一成员面板、角色预设 / 自定义、固定设备列表区域、成功提示自动关闭 | 浏览器视觉验收与 jsdom 单测是不同验证层级 |
| CLI / SDK | TypeScript + Node.js CLI、PKCE / device 授权、系统凭据库、多 origin、多账号、gh 风格仓库 / Issue / PR / workflow / run 命令、JSON、原生 API SDK | 非 gh 全量兼容；jq 依赖系统可执行文件；仅整次 CI 重跑，未实现 release / fork / template |
| 发布 | 测试服务器按组件 Buildx 构建 arm64 + amd64 / push / pull / up，按原生架构核验镜像；网站独立上传；可选备份和迁移 | 当前一键入口只针对 `ssh kdevops`；没有自动回滚或历史资源清理 |

## 已确定的行为与设计变化

### 登录与数据

- 当前账号以必填、规范化、唯一的邮箱接收验证码登录。用户名是唯一身份和仓库命名空间，不是登录凭据。
- 资料及可选密码材料实际在 `users` 表；没有独立 `user_credentials` / profile 表。不要直接套用早期讨论的拆表方案。
- 会话是数据库中的随机原始 Token，不是 JWT。默认 7 天滑动有效期，成功访问需要网页登录的接口时按分钟节流延长至本次活动后 7 天，并在事务提交成功后同步会话和现有 CSRF Cookie 期限；临近到期不足一分钟时立即尝试续期。每次请求检查会话、账号状态、密码版本和当前角色，已失效会话不能恢复，退出不会重新签发 Cookie。超过期限未活动需重新邮箱登录。
- 本人和管理员的登录设备列表仅展示有效会话，统计与分页排除已退出、过期、密码版本失配或账号停用的记录；保留数据库历史。退出成功后界面移除设备，末页变空时回到有效页。
- 兼容密码变更推进仍有效会话的版本，保持邮箱登录设备在线；不会恢复已吊销、过期或版本失配的会话。改邮箱或停用账号会吊销网页登录会话。
- 全局 `admin` 必须手动通过数据库授予或移除。应用不会生成初始管理员；注册及管理端创建账号只得到普通成员身份。
- `config` 表保存原始 JSONB，包括敏感配置。`is_secret` 是展示标记，不是加密。`.env` 只有数据库 / Redis 连接字段；业务配置变更需要重启后端。
- 用户密码仍使用 bcrypt / AES-GCM。早期“加密配置表”“Argon2id”“UUIDv7 公共 ID”等讨论不代表当前实现。

### 仓库权限

| 操作 | 拥有者 | 仓库管理员 | 普通仓库成员 | 无仓库关系的全局管理员 |
|---|---|---|---|---|
| 读取仓库信息、成员、Git 内容 | 可以 | 可以 | 可以 | 不可以 |
| push，仓库未归档时 | 可以 | 按 `access` | 按 `access` | 不可以 |
| 修改简介、添加普通成员 | 可以 | 可以 | 不可以 | 不可以 |
| 编辑 / 移除普通成员 | 可以 | 可以 | 不可以 | 不可以 |
| 任免、编辑、移除仓库管理员 | 可以 | 不可以 | 不可以 | 不可以 |
| 移交、归档 / 取消归档、删除 | 可以 | 不可以 | 不可以 | 不可以 |
| 调整 main / 推送保护 | 可以 | 不可以 | 不可以 | 不可以 |

上表是普通仓库 / Git / 内容接口的权限。全局管理员另有 `/api/admin/repositories` 元数据管理入口，可查看成员并在每次邮箱验证后强制修改成员、权限、拥有者、归档或软删除；该入口不返回代码、Issue / PR 内容。Registry 系统管理的绑定、解绑、整组删除和 GC 同样逐次验证当前管理员邮箱；不能用它绕过正常 Docker ACL。

岗位 `role` 是最多 50 字的分工描述；`access` 是 `read` / `write`；`is_admin` 是仓库管理资格。三者独立。归档覆盖所有人的有效 Git 写权限，保存的成员权限不变；归档期间仍可维护简介和成员。

拥有者不占 `repository_members` 行，成员列表中的拥有者条目由服务生成。移交必须确认当前完整仓库名；新拥有者必须是正常账号，同名冲突返回 409。原拥有者转为读写仓库管理员，新拥有者原成员行移除，其他成员保留。

网页规范地址为 `/repositories/<owner>/<name>`，旧数字 ID 网页入口会跳到当前名称地址；内部修改 API 继续使用 ID。Git 地址为 `/git/<owner>/<name>.git`，物理目录为 `<GIT_REPOSITORIES_ROOT>/<id>.git`。移交或用户名变化不会移动代码目录，但会改变名称地址；旧名称不保留别名，团队需要更新 remote。

删除使用 `deleted_at`，不物理删除 Git 数据。未删除仓库才受 `(owner_id, name)` 唯一性约束；重新创建同名仓库获得新 ID 和独立目录。已删除仓库对网页 API 和 Git 均不可访问。生命周期和权限变更影响后续 Git 请求，不中断已获准的在途传输。

仓库列表使用可换行的条目，简介最多显示两行。右侧趋势通过独立、同一私有 ACL 的 `GET /api/repositories/{id}/activity` 获取，不阻塞列表元数据。统计含本周的近 12 个 UTC 周（周一开始），按 committer 时间聚合所有本地分支可达提交；共享历史去重，不包含仅标签可达或未来时间的提交。使用 `--since-as-filter` 避免非单调提交时间截断遍历，最多计入 10,000 条，超限显示“至少”，Git 命令仍受 10 秒超时限制。空仓库显示零，读取失败显示不可用，不伪造曲线；窄屏将图表移至条目下方。

Code 默认入口；页签采用 `/code`、`/issue`、`/pr`、`/settings`，旧 `tab=settings` 兼容跳转，简介只在 Settings 显示，`ref` / `path` / `page` 保存浏览位置。普通文件按 Git 对象读取且转义预览，符号链接不跟随。目录每页 200 项，文本最多预览 1 MiB，分支选择器最多 1,000 项。

目录 README 从完整目录中选取，与文件树使用同一提交，不受分页影响；在文件树下通过 markdown-it 渲染并经 DOMPurify 清洗，原始 HTML 不执行。相对链接保持当前分支与目录；仓库图片走同一私有 ACL 的 image API，最多 5 MiB，只接受 PNG / JPEG / GIF / WebP，不支持 SVG 或自动读取 LFS 图片。切换分支 / 目录后清理旧 README，二进制、超限和 LFS README 显示文件入口。

新建仓库以固定系统身份在 main 写入一条 README 初始提交，内容包含完整仓库名和受信任站点地址的 clone 命令；创建失败只清理本次独占创建的目录，不修改已有仓库或历史。共享 AppNotice 的成功提示默认 5 秒关闭，可手动关闭并同步清空页面消息；错误 / 信息提示及显式 duration=0 的成功提示保持显示。侧栏展开状态保存在浏览器本地。

Code 页的刷新和 HTTPS 克隆均使用 AppButton。克隆弹层保留完整可换行地址，右侧图标复制；支持外部点击 / 焦点离开关闭、Esc 关闭后恢复焦点，复制成功反馈 2 秒后清理。验证码邮件使用纯文本 + HTML 的 multipart/alternative，品牌、用途、连续验证码、有效期和非本人操作提示共用模板；HTML 采用内联样式 / 表格，动态内容转义，不依赖外部图片或字体。

保护默认关闭；main 保护拒绝该分支的创建 / 更新 / 删除，推送保护拒绝非快进、删除 refs 和替换已有标签。`0009_repository_protection` 增加字段；Gateway wrapper 固定镜像内钩子，同时覆盖 `git init --shared` 自带的禁止非快进设置，避免 UI 关闭而底层仍禁止。规则按请求读取，混合推送整体拒绝。

LFS 数据保存在 `<id>.git/lfs/objects`，备份 Git 根目录一并包含。每次协议请求使用 Git OAuth；上传完成后再次鉴权，不能以在途上传绕过归档或吊销。没有新增 LFS 数据表，不全局共享相同 hash 的对象；授权不含浏览器 Cookie 回退。协议与限制见 [Gateway README](gateway/README.md#git-lfs)。

### Git 与静态网站

Git OAuth 使用 public client `devops-gcm`；CLI 使用独立 public client `devops-cli`，两者权限和令牌互不混用。Git / 本机 CLI，使用 S256 PKCE 和本机 loopback 回调。授权覆盖用户当前有权访问的仓库，API / Gateway 每次重新检查实际权限。退出网页登录与撤销 Git 授权相互独立。详细时效、刷新和接口见 [Gateway README](gateway/README.md)。

access 最多有效 1 小时，GCM 在 Git 需要新凭据时通过 refresh 自动换取；刷新轮换旧凭据，旧 refresh 重放会撤销整条授权。新授权有效 30 天，每次成功刷新将期限更新为本次刷新后 30 天，活跃客户端可以持续续期；连续 30 天未刷新或撤销后需重新网页授权，服务端不后台续签。授权行锁获取后重新检查当前时间，避免等待期间过期的授权被恢复；拒绝路径不延长期限，续期与凭据轮换在同一事务提交。已有有效授权下次成功刷新即可续期，无需数据库迁移。个人授权列表在最近 100 条上限之前过滤已撤销记录，过期但未撤销的记录仍显示；数据库保留历史，撤销成功后界面立即移除对应行。侧栏不再展示系统状态入口，健康 API 与 `/status` 直接访问保留。

产品展示名称为 `DevOps`，包括页面标题、品牌、邮件文案与新仓库初始提交身份；既有 Git 历史不改写。OAuth client、Cookie、侧栏本地存储键、内部请求头与镜像 / Compose 标识统一使用 `devops` 前缀，不能为改名让现有客户端和登录失效。

Gateway 只运行 Nginx、fcgiwrap、Git 和进程监督。网站在本机构建，服务器只接收静态产物；服务器安装脚本需要 Python 3.12+，不需要 Node.js。挂载网站父目录，`current` 在内部原子切换，旧哈希资源供已打开页面继续使用。

测试服务器更新入口是 `./deploy.sh`，选择 server / gateway / website 或 all。server / gateway 默认 Buildx 构建并推送 `linux/arm64,linux/amd64`，本地集成则只使用原生架构，ARM Mac 不再强制运行 amd64；发布中的 amd64 构建仍需模拟或原生 amd64 builder 节点。所有选中镜像都完成双平台校验后才连接服务器，远端 image 固定索引摘要，移除选中服务的旧 platform 固定值，选中 Gateway 时补齐 UDP 443，其他配置不覆盖。按远端 Docker 架构核对 manifest / config ID；containerd 运行容器还校验 ImageManifestDescriptor 的平台和摘要，不能只比较父索引 ID。等待 healthy 并检查公网 API。默认只检查迁移 head；`--migrate` 才先 pg_dump 再升级。网站复用独立发布器，未选中的数据库容器不重启。参数与失败行为见 [脚本 README](scripts/README.md#测试服务器一键部署)。

Gateway 启用 QUIC Retry 和 `Alt-Svc`，保留 TCP 协议与相同权限检查，关闭 early data。HTTP/3 客户端可能只有 `:authority`，代理在 `$http_host` 为空时使用 `$host$is_request_port$request_port`（需要 Nginx 1.29.3+），否则 API 上游会因缺失 Host 返回 400。该回归由真实 HTTP/3 首页、API 与拒绝路径验证覆盖。`verify_http3.py` 使用仅开发依赖 aioquic，验证证书及 h3 ALPN，不回退 TCP；按 DNS 地址逐一尝试，可用 `--ipv4` / `--ipv6` 单测地址族。

## 2026-10-03 测试部署验收后快照

这是本轮先执行 `./deploy.sh server website`，随后通过 `./deploy.sh website` 发布授权说明精简后的快照；已用 `ssh kdevops`、发布器的公网版本 / readiness 检查和 Safari 桌面核验。Gateway、PostgreSQL、Redis 容器未更换，没有执行数据库迁移；不是永久配置，也不能据此推断下次发布目标。

| 项目 | 观察值 |
|---|---|
| 网站 | `https://devops.kevinc.ltd` |
| 服务器部署目录 | `/dockerdata/devops/deploy` |
| Compose 项目 | `devops` |
| 后端镜像 | `devops.kevinc.ltd/core/devops:test-cd6a2c7-dirty-20261003T102644Z-b25087@sha256:6e304da088485bab8b85fb4e84a410b84be3b0393e44e87c980c683ad88835f3` |
| Gateway 镜像 | `devops.kevinc.ltd/core/devops-gateway:test-fb702e3-dirty-20261003T094600Z-235515@sha256:7651675b2914b6f000aa37936122104ec40178b1ea5ac0b6f1a5ef011f680276` |
| PostgreSQL / Redis | `postgres:18.6-alpine` / `redis:8.8.3-alpine` |
| 容器状态 | server / gateway / postgres / redis 均 running + healthy |
| 镜像平台 | 发布索引均包含 linux/arm64、linux/amd64；当前服务器原生运行 amd64 |
| Gateway 端口 | TCP / UDP 443；本轮未变更，HTTP/3 / HTTP/2 / HTTP/1.1 的公网协议验收见此前记录 |
| 数据库迁移 | `0009_repository_protection (head)` |
| 网站 release | `20261003T102916Z-cd6a2c7-002d02` |
| 网站软链接 | `current -> releases/20261003T102916Z-cd6a2c7-002d02` |

最近一次后端发布前的 Compose 保存在 `/dockerdata/devops/deploy/.deploy-backups/test-j582fhm1/compose.deploy.yaml`，本次设备列表调整前的网站 release 为 `20261003T101255Z-cd6a2c7-0e3baa`，历史静态产物保留。本次只替换 server 并切换网站；Gateway / PostgreSQL / Redis 容器 ID 保持原样，数据库仍为 0009。后端核对了多架构索引、服务器 amd64 manifest、实际运行健康及 `users.py` / `services.py` 源码 SHA-256 与本地验证版本一致。此前 OAuth 续期发布备份为 `test-0remt3nt`，双架构 / HTTP/3 发布备份为 `test-l1s580ag`，品牌 / 邮件发布备份为 `test-4moyu9pu`，更早仓库列表发布备份为 `test-qy1ghua9`，均不是本次恢复点。

此前三组件升级时的 Compose 与数据库备份保存在 `/dockerdata/devops/deploy/.deploy-backups/test-03jwd1gi/`；该次执行 `--migrate`，先生成 `database.dump`（0600），再升级至 0009。这是此前升级备份，不是本轮新生成的数据库备份。

已有升级备份记录：仓库生命周期升级目录 `/dockerdata/devops/backups/repo-lifecycle-20261002-KW7O5z`；静态部署切换目录 `/dockerdata/devops/backups/static-site-20261003-yedj0at5`。前者包含数据库和 Git 备份，后者用于 Compose / 镜像与旧网站恢复。使用前重新检查存在性和内容，不能把网站备份当作数据库备份。

后续核验在服务器部署目录执行：

```sh
docker compose --env-file .env.deploy -f compose.deploy.yaml ps
docker compose --env-file .env.deploy -f compose.deploy.yaml exec -T server alembic current
readlink /dockerdata/gateway/website/current
cat /dockerdata/gateway/website/current/release.json
curl --fail https://devops.kevinc.ltd/api/health/ready
```

不要输出完整 `docker inspect` 或渲染后的 Compose，它们可能包含连接凭据；检查时只选取需要的镜像、挂载、状态字段。Gateway 容器 healthy 不等于后端依赖 ready；两者分别核验。

## 验证证据与限制

- 登录设备过滤后的 `make -C . check` 通过：171 项后端 / 脚本测试、52 项前端测试、Ruff、Alembic check、类型检查与生产构建。覆盖本人 / 管理员入口、失效条件、过滤后统计与分页、数据库历史保留，以及退出后的移除、末页回退和当前设备退出。Safari 仅做桌面验收，原已退出设备消失，仅余当前有效设备，数量由 2 条变为 1 条；未吊销真实会话或授权。
- 本次首次镜像拉取返回 `unexpected media type application/octet-stream`，发布器在切换 Compose 前停止，原容器与网站保持正常。同一镜像摘要重试拉取成功后，通过现有发布器的远端流程继续完成镜像 / 迁移版本 / 健康校验与服务更新，再发布网站；未修改发布脚本或仓库配置。首次仓库响应异常的原因尚未定位。
- 授权面板说明精简后，48 项前端测试、类型检查、生产构建与网站发布验收通过。Safari 桌面确认标题下两段说明已移除、授权记录与操作布局正常；本次未检查窄屏，后续验证规范同步改为仅桌面。
- 本轮 Git 授权续期与导航调整的 `make -C . check` 通过：168 项后端 / 脚本测试、48 项前端测试、Ruff、Alembic check 与生产构建；加强撤销断言后 26 项 OAuth 测试再次通过。覆盖创建 30 天、跨原到期日连续续期、到期瞬间拒绝、无效客户端 / scope / Token 不续期、停用 / 撤销不恢复、并发重放撤销，以及等待行锁期间到期的拒绝路径。
- 本轮按原生架构重建本地后端和 Gateway 后，真实 HTTP/3 / HTTPS / Git / LFS 集成通过。新增隔离 schema 内的临近到期授权，经真实 `/oauth/token` 刷新后续期 30 天，旧 access 被拒绝，新凭据继续完成 clone / push / pull 与 LFS 上传 / 下载；没有操作真实用户授权做失效测试。
- 说明精简前的 Safari 验收已重新加载测试站点，桌面和 391 × 844 窄屏确认侧栏不再显示系统状态、当时的续期说明完整显示且正常换行。既有会话与授权保持有效；现有授权在下一次客户端成功刷新时才更新到期日期。未撤销用户授权、未发送邮件、未修改仓库，结束已恢复桌面及原导航展开状态。
- 功能实现阶段已运行后端 / 脚本 pytest、前端 Vitest、Ruff、生产构建及真实 HTTPS / Git 集成验证。测试入口、隔离方式和复现命令见 [CODEMAP.md](CODEMAP.md) 与 [脚本文档](scripts/README.md)。测试数量不作为长期验收约束。
- Git 集成脚本走真实 Nginx / CGI / Git 和 API 驱动的 OAuth，覆盖 clone / push / pull、保护规则、混合推送拒绝、策略头伪造拒绝、3 MiB LFS 上传 / 校验 / clone / pull、生命周期与吊销及静态切换；它不自动执行 GCM 的浏览器邮箱登录流程。读写成员矩阵、刷新重放等细节由后端测试覆盖。
- 仓库写操作已有行锁与权限重读；当前仓库测试验证顺序变更，尚无专门的多线程并发用例。后续修改并发路径时应补充对应验证，不能把存在锁写成已经过并发验收。
- 既有客户端记录验证指定版本的 GCM / GitHub Desktop 能拉起登录并保留授权跳转。当日此前通过 Desktop 自带 GCM 复用已有授权，在命令行完成真实测试站点的 clone / push / fetch 和 LFS 上传 / 下载；未修改全局 helper，没有重新走邮箱验证码授权，也没有验收 Desktop 全部 GUI 操作。详见 Gateway 文档中的版本和验证边界。
- 此前品牌 / 邮件优化的 `make -C . check` 通过：140 项后端 / 脚本测试、48 项前端测试、Ruff、Alembic check 与生产构建。包含提交趋势 / 授权过滤，以及四种验证码用途的纯文本 + HTML、动态有效期和 HTML 转义验证。克隆卡片新增复制 / 关闭 / 焦点测试；Safari 实测补齐鼠标点击后未聚焦时的 Esc 处理，再次通过 48 项前端测试和生产构建。
- 仓库列表优化阶段重建本地后端和 Gateway 镜像后，完整真实 HTTPS / Git / LFS 集成复跑通过，含 API 创建的 main / README 与 clone 一致、提交趋势、撤销授权不再出现在列表。首次运行曾在普通快进 push 收到一次 HTTP 500；第二次完整复跑未复现，尚未定位根因，不应写成已修复。若再次出现，应保留失败现场的 Gateway / server 日志并排查；日志可能含授权数据，不直接公开。
- 经用户授权，在本地 `/Users/czf/Desktop/works/test_repo` 对应仓库创建并推送验收分支 `codex/ui-readme-check-20261003`，最终提交 `b5b974c5be43c917faf1eeee32ad3d0272aa8f0e`。包含根目录 / docs README、中文路径、PNG 和 155,648 字节 LFS 样本；独立重新克隆并校验 LFS 内容一致。分支在本地及远端保留供复查，原工作区仍停留在干净的 test 分支，原提交 `1656d93ff2e56f67a8bab52a0492fe2e38c4eef2` 不变。
- Safari 已对照真实验收分支检查文件树、当前目录 README 渲染、相对目录 / 中文链接、图片及 LFS 文件大小；切回原 test 分支不会残留 README。页头没有简介，侧栏折叠刷新后保持，共享成功提示能自动消失、重复出现和手动关闭。真实仓库的两个保护开关仍为关闭，未改成员或生命周期；保护拒绝路径由隔离集成测试覆盖。
- 仓库列表优化阶段 Safari 重新加载测试站点，桌面和 391 × 844 窄屏均检查通过：长简介两行省略、元信息换行、趋势图和分页没有横向溢出。`test_repo_2` 显示 1 次提交，`test_repo` 显示 5 次；账号安全页仅显示现有 1 条有效授权，原已撤销行消失，30 天固定到期说明可见。品牌和标签页标题为 DevOps，既有会话保持登录。没有撤销用户的有效授权或修改仓库设置，验收结束已恢复桌面和展开侧栏。

- 此前品牌 / 克隆弹层 / 邮件优化已在 Safari 桌面及 391 × 844 窄屏检查：刷新与 HTTPS 克隆按钮同高，弹层完整显示地址，复制反馈、外部关闭和 Esc 恢复焦点正常。Safari 鼠标点击按钮不自动聚焦的 Esc 路径已修正并加入回归测试。HTML 邮件使用合成验证码在本地网页预览，桌面与窄屏均无溢出；SMTP 替身确认纯文本 + HTML 内容，未发送真实测试邮件，也未宣称已覆盖所有邮箱客户端。浏览器已恢复桌面和原侧栏状态。

- 此前双架构 / HTTP/3 的 `make -C . check` 通过：161 项后端 / 脚本测试、48 项前端测试、Ruff、Alembic check 与生产构建。新增部署测试包含双架构完整性、摘要固定、原生平台选择、经典 / containerd ID 差异、错误运行 manifest / 平台拒绝、旧 platform / UDP 映射迁移和失败保持原服务。
- 两份本地镜像均以 arm64 原生重建，完整真实 HTTP/3 / Git / LFS 集成通过；双架构发布及测试服务器 amd64 启动通过。公网 IPv4 的首页和 API 经验证证书的 HTTP/3 返回 200，HTTP/2 / HTTP/1.1 同样 200；服务器用系统 curl 的 `--http3-only --ipv6` 检查自身域名和 IPv6 loopback 均为 200。本机到该域名 IPv6 的 TCP / QUIC 都超时，因此不宣称已完成外部 IPv6 网络验收；未修改本机网络或云防火墙。默认探针遇到不可达 DNS 地址后继续使用其他地址的 HTTP/3，不以 TCP 成功冒充 HTTP/3。

## 后续尚未交付

以下为现状边界，具体优先级由下一次需求决定。DevOps 面向专业人员的桌面开发工作，后续不安排手机适配或窄屏验收：

- 独立完整分支历史列表、任意网页分支创建 / 删除、跨仓库 PR / fork、Projects、CI/CD Runner 与构建制品。
- Docker / OCI 仓库、SSH Git、通用 OIDC / SSO、更多 OAuth 客户端类型。
- DevOps → 旧系统的单向凭据同步、持久化重试 / 补偿、批量密钥轮换和重加密。
- 已删除仓库恢复流程、物理数据清理策略、仓库改名及旧名称重定向。
- 静态历史版本 / assets 清理和发布失败后的显式恢复工具；现有发布失败不会自动回滚。
- 完整的真实客户端登录 → 授权 → clone / push 验收记录，以及更多桌面长文本场景的浏览器视觉覆盖。

下次交接应更新实际完成项、验证层级、部署版本和未解决问题；不要把用户一次性的服务器 / 浏览器授权当作跨任务永久授权。

## 2026-10-03 Issue / PR 实施

用户确认实施，并明确要求页签 / 详情使用路径（`/issue/1`、`/pr/1`、`/settings`），不使用 query 选择标签。已加入 Issue / PR 共用编号、子项、元数据、模板、评论 / 附件 / 历史 / 通知、Markdown 双向引用、原生 Git PR 比较 / 审阅 / 冲突检测 / 三种合并及持久恢复。范围与稳定行为集中于 [协作说明](COLLABORATION.md)，实施核对见 [实施记录](COLLABORATION_PLAN.md)。

当前迁移为 `0010_collaboration`。运行新增 worker，server 部署会同步发布同镜像 worker；Gateway 新增 post-receive 回执并保护内部引用。完整 Git 根目录与数据库需要配套备份。此前上方 0009 发布快照是旧版本，不能作为本次实施后的运行状态；新的发布 / 验收结果在本节后续记录。

此轮仅使用隔离账号 / 仓库做写入测试；GitHub 参考页面始终只读。未做窄屏 / 手机验收。

实施代码的完整 `make -C . check` 已通过：194 项后端 / 脚本测试、59 项前端测试、Ruff、Alembic 模型一致性、类型检查及生产构建；随后新增的协作数据降级保护测试独立通过，当前后端共 195 项。覆盖编号并发、ACL 拒绝、子项循环 / 深度、私有审阅草稿、附件发布、引用移交上下文、三种真实 Git 合并、冲突、过期审批 / 会话失效、分支竞争和 SQL 中断后的合并恢复。

使用最终原生架构 server / gateway 镜像及网站构建，`verify_git_gateway.py` 完整通过：真实 HTTP/3 / HTTPS / OAuth / Git / LFS、推送回执、提交反向引用、后台冲突检测、受保护 main 的应用 PR 合并、关联 Issue 自动关闭及 worker 健康。测试保留子 Issue 不级联关闭，并验证客户端不能写内部恢复引用。本段是本地隔离验收，测试服务器发布与 Safari 桌面结果另行记录。

首次真实发布已备份并升级数据库至 0010，API / Gateway 健康；worker 的业务心跳正常，但原健康探针重复导入整套后端，在测试服务器上超过 5 秒，发布器因此阻止网站切换。改为独立标准库 `collaboration_health` 探针，保持无心跳 / 过期心跳拒绝，兼容更新旧 Compose 中的已知探针。54 项部署 / worker / 原生合并相关测试通过，新容器三种心跳状态共 0.37 秒；新增 2 项测试后后端总数为 197。首次发布的 Compose 与迁移前数据库备份在 `/dockerdata/devops/deploy/.deploy-backups/test-7gasxjhl/`，后续发布不自动回滚或降级。

### 本轮最终测试环境快照

已通过现有发布器完成 API / worker / Gateway / website 发布。API / worker 的健康探针修复通过 `./deploy.sh server website` 发布；后续 Safari 发现的页面问题仅通过 `./deploy.sh website` 更新。所有业务实现及验收修复均推送到 `codex/devops-bootstrap`，主实现提交为 `b531252`，最终前端实现提交为 `6b22d51`。

| 项目 | 2026-10-03 验收值 |
|---|---|
| server / worker 镜像 | `devops.kevinc.ltd/core/devops:test-3ec5699-20261003T142004Z-4db5d0@sha256:361fb90496d1b7a0f76f7a5a0f54048159d3f73cd7b7d6eb9c191f153b3bd04f` |
| Gateway 镜像 | `devops.kevinc.ltd/core/devops-gateway:test-b531252-20261003T141515Z-452780@sha256:64fad34352e3409e50b7d9272d1d0f552ad3e6ca02af06a61e3960821bafeb62` |
| 网站 release | `20261003T143021Z-6b22d51-b17a06` |
| 数据库版本 | `0010_collaboration (head)` |
| 容器健康 | server / worker / gateway / postgres / redis 均 running + healthy，重启次数均为 0 |
| 平台与运行版本 | 发布索引含 arm64 / amd64，服务器实际 amd64 manifest 与预期一致；server / worker 的 11 个 collaboration 模块及 Gateway 两个钩子的 SHA-256 与本地一致 |
| 挂载 | server / worker 共享 `/dockerdata/git-repos:/mnt/git-repos`；Gateway Git 可写、证书与网站只读，网站继续独立发布 |

迁移前备份 `test-7gasxjhl/database.dump` 已核实存在、44,742 字节、权限 0600；健康探针修复前的 Compose 另存于 `test-87gf9n4h/`。PostgreSQL / Redis 仍为此前 `be74ffaa0bb2` / `6231b6ad2742` 容器，未重建。按本文件上方命令可重新核验版本；worker 另可执行 `docker compose --env-file .env.deploy -f compose.deploy.yaml exec -T worker python -m devops_server.collaboration_health`。

最终前端 61 项测试、类型检查和生产构建通过。Safari 桌面只读验证了旧 `?tab=settings` 跳转、新仓库导航、Issue / PR 空列表与创建表单、已有分支的 2 提交 / 6 文件比较、统一 / 并排差异、中文文件切换和真实 SHA 的提交详情。修复页头样式归属、差异代码宽度、缺少默认 main 时的初始报错，以及 query / hash 变化重建仓库页面导致状态丢失的问题；新增回归验证草稿保留和同仓库旧根地址跳转。切换差异文件后分支选择现已保留。GitHub 参考标签页未修改，真实仓库未新增 Issue / PR、未提交评论或合并；没有做窄屏验证。

网站发布器已核验入口资源、release 与公网 readiness；更新后的 Gateway 经证书验证的公网 IPv4 HTTP/3 首页 / API 返回 200。此轮没有做大型仓库压力测试，也没有进行真实 SMTP 发送；有数据的 Issue / 子项 / 审阅 / 合并写流程使用本地隔离测试验收。范围仍不含跨仓库 PR / fork、Projects、CI 执行器和网页冲突编辑器。

## 2026-10-04 Issue / PR 桌面 UX 验收

修复关联搜索、评论 / 关闭操作的对齐；标题、正文、评论、负责人 / 标签 / 类型 / 里程碑在原位置编辑，提供保存、取消、Esc 和焦点返回。关联面板提供顶部关闭与底部取消，下拉框展开时先消费 Esc。输入暂存覆盖新建、编辑、评论、审阅、线程回复、行评论及合并说明；按账号与编辑位置隔离，编辑版本 / 比较 SHA 随草稿保存，失败保留、成功清除。补齐 PR 新提交的审阅确认、合并后草稿清除、异步初始化期间禁用输入，以及预览发表评论后回到编写。稳定行为和附件期限见 [协作说明](COLLABORATION.md)。

代码提交为 `e360619`、`fda391a`、`df444da`；最终网站 release 为 `20261003T163418Z-df444da-cc25b3`（release 时间使用 UTC）。本轮仅通过 `./deploy.sh website` 发布静态网站，没有后端 / Gateway 镜像或迁移变更。发布器核验入口、资源及公网 readiness，现场确认 server / worker / gateway / postgres / redis 均 running + healthy；容器 ID 仍分别为 `500fcceceee1` / `79a6c25cc8af` / `58bd6b4e6468` / `be74ffaa0bb2` / `6231b6ad2742`。可使用前述 readlink、release.json、Compose ps 与 readiness 命令重新核验。

- 前端完整 75 项测试、类型检查与生产构建通过；最后仅调整提交后预览复位，相关 23 项协作测试和生产构建再次通过。新增测试覆盖账号 / 位置隔离、期限和存储失败、刷新恢复、取消清除、并发版本拒绝、独立字段保存、陈旧搜索响应、下拉 Esc、行评论轮询和审阅 SHA 变化。后端代码未改，本轮没有重跑整个后端套件。
- Safari 只读对照 GitHub Issue / PR 的标题、正文编辑与差异交互，未保存或提交任何 GitHub 业务记录。本轮测试站点经授权允许写入，仅使用 `czf0613/test_repo_2` 的专用验收记录；原 Issue #1 未修改，未做窄屏检查。
- Issue #2、子 Issue #3：新建 / 评论 / 编辑草稿在刷新或跳转后恢复；标题 / 正文原地编辑与取消、负责人取消 / 保存、关联搜索和双向引用、关闭并评论 / 重开、评论编辑、文本附件上传、Markdown 预览、PR / 提交链接、任务勾选持久化均已实际操作。列表中文搜索、刷新保留筛选及关闭列表的已合并状态也已核验。
- PR #4：使用 `codex/ux-clean-20261004` → `codex/ux-base-20261004`，验证行评论暂存 / 刷新 / 发布、审阅说明、线程回复草稿、未解决讨论阻止合并、解决后允许合并、草稿 / 可审阅切换、合并说明恢复。实际合并完成后 fetch 确认目标提交 `086ae73`，含预期双亲和文件内容。
- PR #5：使用 `codex/ux-conflict-20261004` → 同一验收目标分支，先显示 `ux-check.txt` 冲突并禁用合并；本地解决冲突并推送 `4d827fd` 后页面自动变为无冲突。未提交的审阅文字保持，提示检查新提交并禁用提交；刷新仍保留草稿和提示，检查最新差异并确认后成功提交。已验证关联 Issue 保存、Esc 逐层关闭、取消后的焦点和审阅正文只展示一次。

验收记录、附件和三条 `codex/ux-*-20261004` 分支保留便于复查：#2 / #3 开放，#4 已合并，#5 开放且无冲突。只合并到专用验收目标分支，测试仓库 main 未变更。隔离克隆在忽略目录 `.data/ux-acceptance-20261004`，未纳入源码提交；Git 操作单次指定 Desktop 自带 GCM 复用既有授权，没有改变全局 helper 或用户授权。本轮未做大型仓库压力测试、真实 SMTP、多用户同时操作或所有管理 / 删除功能的浏览器遍历，不将上述验证宣称为全部功能零缺陷。

### 2026-10-04 子 Issue 布局与列表筛选优化

代码提交 `e52da4b`。子 Issue 的展开内容改为只呈现下一层子项，不再递归复制标题、父关联和创建 / 关联面板；状态、标题、排序与解除按钮在同一行，展开请求显示加载状态并防止重复请求。同步修正讨论 Markdown 任务框的行内对齐与重复列表符号。Issue / PR 的作者、标签、类型、里程碑、负责人和排序收进列表表头的紧凑菜单；成员 / 元数据弹层可搜索，日期及保存的筛选放入“更多筛选”，已选条件可单独移除，后退同步搜索框。仍使用现有服务端过滤参数，未增加 GitHub 完整查询语法。

前端完整 82 项 Vitest、类型检查与生产构建通过，新增覆盖多层子项、展开失败重试 / 防重复、筛选键盘与焦点、无匹配选项、条件组合 / 单项清除、后退、保存的筛选与日期应用。Safari 桌面实测 #2 展开 #3 的布局及正文任务列表；Issue 筛选弹层、作者搜索、作者加关键词的结果、清除条件、后退、空匹配与 Esc；PR 排序、已关闭列表以及刷新保留状态。多层有后代的树与保存筛选应用由组件测试覆盖；本轮浏览器没有新增或修改测试 Issue / PR，GitHub 参考页面保持只读，未做窄屏验证。

通过 `./deploy.sh website` 发布网站 `20261004T030424Z-e52da4b-fbbeda`，已核验 release.json、current、入口资源和公网 readiness。server / worker / gateway / postgres / redis 仍为上节容器且均 running + healthy，网站挂载保持只读。本轮无后端 / 镜像 / 迁移变更，未重跑后端套件；最新运行版本可按上方命令重新核验。

同日后续紧凑布局调整（`c4fddf9`）：去除 Issue / PR 列表重复标题，将创建按钮与搜索、更多筛选放在同一行。5 项列表 / 关联回归测试、类型检查、生产构建通过，Safari 桌面已检查两个页面的实际布局和创建链接。发布网站 `20261004T033530Z-c4fddf9-9cc22b`；current / release.json、入口资源及 readiness 已核验，五个服务仍 running + healthy。仅更新前端，未改后端或业务记录。

### 2026-10-04 局部导航、通知与仓库初始化

本轮修改保留在 `codex/devops-bootstrap` 工作区，基线为 `bfed8d7`；依用户本轮指示未 commit / push DevOps 源码。差异文件、行号和筛选更新保留页面位置；同一 PR 的子页复用详情及编辑器，差异加载期间保留区域高度，过期响应不覆盖当前文件。通知筛选与标题同一行。局部导航不重复查询会话，网络 / 5xx 错误保留当前页并可重试；首次会话确认失败时暂不挂载业务内容，明确 401 仍退出到登录，权限继续由 API 检查。

新仓库 README 增加已有本地仓库导入步骤：保留 origin、添加 devops 远端、合并初始化历史、解决 README 冲突后推送；main 受保护时推送新分支并创建 PR。只影响此后新建仓库，不改已有 Git 内容。临时 Git 测试执行了模板命令，验证无冲突 / README 冲突两种情况以及本地和远端历史均保留。真实 Docker / HTTPS / Git 集成再次确认保护 main 时直接推送被拒、PR 合并成功、合并后保护仍开启且继续拒绝直接推送。

验证：完整 `make -C . check` 的 199 项后端测试、Ruff、Alembic check 通过；最终前端 94 项 Vitest、类型检查与生产构建通过。Safari 桌面在原测试仓库的新建 PR 页复现并验证修复：切换两个文件保持停留差异区，正文及标题保留，刷新能恢复草稿；PR #5 的正文编辑切换到文件变更 / 检查再返回仍保留，取消后恢复原文。通知按钮同排。仅使用浏览器本地临时草稿并已清理，没有提交或新增 Issue / PR，没有修改 GitHub 数据；未安排窄屏检查。

最终网站 release 为 `20261004T041357Z-bfed8d7-26e9da`，使用本轮未提交代码构建。server / worker 镜像为 `test-bfed8d7-dirty-20261004T040600Z-ff0a57`，固定索引摘要 `sha256:1e8ea0fd14c43ecf19009822ed2ee5fa08928b9c58c9e58a3cd812d3f54df738`，已验证双架构及实际 amd64 manifest / config。首次拉取摘要遇到 `application/octet-stream` 类型错误，未替换运行服务；按同一标签预拉取并核对摘要后，复用原部署脚本的远端流程继续成功，没有降低镜像或健康校验。Compose 备份为 `/dockerdata/devops/deploy/.deploy-backups/test-h42vs8ud/compose.deploy.yaml`。

server / worker 容器现为 `4f33823d08b1` / `f526553024e3`；Gateway / PostgreSQL / Redis 仍为 `58bd6b4e6468` / `be74ffaa0bb2` / `6231b6ad2742`，均 running + healthy。数据库仍为 `0010_collaboration`，本轮未迁移。运行容器 `repository_storage.py` SHA-256 与本地一致；网站入口资源、release 和公网 readiness 已核验。后续可按前述 Compose / release.json / readiness 命令重新核验，不能仅凭基线提交号判断本轮未提交代码的部署内容。

### 2026-10-04 Settings 协作设置 UX

标签、Issue 类型、里程碑和模板改为带数量的紧凑分类列表，新增按需打开，编辑在条目原位置进行，提供上下两处取消、Esc 和焦点恢复。颜色改为小色盘 / 十六进制值 / 实时预览；里程碑日期与状态并排，未改日期时保留原时间；模板提供安全 Markdown 预览、字段增删和排序，自动生成不重复的字段标识。长表单收起后只在原操作区已离开视口时将其移回可见位置。草稿复用 useDraft，按账号、仓库、分类和条目隔离，切换分类或刷新后重新打开编辑器可恢复，失败保留、成功或取消清除。

PR 合并规则按允许方式和前置要求分组，新增未保存提示、取消修改和草稿恢复；至少保留一种合并方式。Catalog 与规则独立加载，初始规则读取失败时不能保存默认值，成功写入 Catalog 使用响应更新列表，避免二次读取失败造成重复创建。后端权限、数据模型与合并规则未改变。

完整前端 105 项 Vitest、类型检查和生产构建通过；Safari 发现长模板收起的定位问题后补充测试，最终 12 项设置回归及类型检查 / 生产构建再次通过。Safari 桌面在 `czf0613/test_repo_2` 实测四类临时条目的新增与删除、类型取消 / 刷新草稿、颜色同步、里程碑日期 / 已关闭状态、模板 Markdown / 单选字段 / 排序 / 修改适用类型，以及长表单取消后的定位。临时条目与本轮草稿均已清理；既有 Issue / PR 和 GitHub 数据未修改。合并规则只测试本地草稿及取消，未向服务器写入规则；保存失败 / 规范化、只读 / 归档及加载拒绝路径由组件测试覆盖。本轮无后端变更，未重跑后端套件，未做窄屏检查。

最终通过 `./deploy.sh website` 发布 `20261004T054044Z-bfed8d7-fa59cb`，已核验 current / release.json、入口资源及公网 readiness；五个运行容器与上节相同且均 healthy，网站继续只读挂载，未更新后端镜像或迁移。更改保留在原分支工作区，未 commit / push。


## 2026-10-04 CLI 与使用指南交付

- `cli/` 发布 `@kevinc/devops-cli` 0.1.0，Node.js 22.12+，推荐 24 LTS；通过 `npm install --global https://devops.kevinc.ltd/res/devops-cli/0.1.0.tgz` 安装，无需源码仓库权限。SDK 导出 `@kevinc/devops-cli/sdk`。完整支持范围、配置和退出码见 [CLI README](cli/README.md)。
- CLI public client 与 GCM 分离，新增设备码、API read/write scope、实时 ACL 与授权绑定的后台合并。迁移 `0011_cli_oauth` 保留旧 GCM 授权，增加 client_id 和设备码表；CLI 合并历史阻止丢失来源的降级。
- Gateway 新增公开 `/res/devops-cli/` 和精确设备码协议代理。CLI 包存于网站父目录 `res/devops-cli/`，原子发布、SHA-256、无目录列表/写入/链接、版本不可覆盖；独立于 website current。
- 网站侧栏 `/guide` 提供 GitHub Desktop GCM 开关示意图、本站 OAuth 配置命令和 CLI 安装 / 登录示例。域名与端口从网页 origin 生成。Safari 已检查桌面版布局、示意图、安装链接及命令。继续检查授权页时 Safari 切换到其他工作页面，已停止界面操作并关闭临时本机监听器；未给真实账号新增 CLI 授权。
- 本次已执行测试环境发布（未进行源码提交或 Git push）：server / worker / gateway 使用 `test-c2d29d3-dirty-20261004T080623Z-67eac9`。server 索引为 `sha256:191047cd09e328f85e9e5f0684bb0cc022262043d10e2d1530749dc6ee4e60fd`，gateway 索引为 `sha256:4158aacb8d5711941b8bc3e81f6b98e12f35cc03e883ac0ef00521c3e2a19d8c`；均包含 arm64 + amd64，测试服务器实际 amd64 实例已验证镜像与健康状态。升级前备份位于 `/dockerdata/devops/deploy/.deploy-backups/test-pq5y9nt9`，实际 migration head 为 `0011_cli_oauth`。
- 网站 release 为 `20261004T080821Z-c2d29d3-e7d128`。CLI 公网包 SHA-256：`6a292d7d3c2d004f20dac4ffdf1d8558cae0c7ca205241d5e708a54941a1cd21`。部署包含未提交工作区，不能把标签中的提交号当成完整源码快照。
- 验证通过：后端全量 219 项、网站 110 项、CLI 10 项；Ruff check / format、TypeScript 构建；本地真实 HTTP CLI 操作；macOS 系统凭据库合成数据读写；公开 tarball 独立安装及 SDK 导入；原生 Docker Git/LFS/HTTP/3/保护/异步 PR 合并与网站版本切换后包下载。
- 测试站独立合成账号 `clitest_4afff3fd05` 验证公网安装、PKCE、设备码授权、Git clone/push、Issue 创建/编辑/评论、PR diff/squash 合并、自动关闭 Issue 和只读 scope 拒绝写入。测试仓库 ID 3 已软删除，测试账号停用、其会话与所有授权均撤销；保留审计和软删除历史，没有发送真实邮件或修改真实用户的仓库。
- 仍需区分：Linux / Windows 已有原生凭据库依赖与安装代码，本次没有在这两个桌面系统上实际登录验证；Safari 本次完成指南视觉检查，正向授权完整链路由独立测试账号 HTTP 验证，不宣称已完成真实用户的 Safari 点击授权。
- 重新核验：读取测试站 `/release.json`、`/api/health/ready`，下载包并核对 `.sha256`；SSH 检查选中容器实际镜像/健康和 `alembic current --check-heads`。后续发布继续按当次用户授权执行，不把本次许可作为永久授权。

## 2026-10-04 个人协作 Dashboard

- 本轮开始前已按用户要求将 CLI / 使用指南等既有更改独立提交为 `9488f3e feat(devops): add CLI, browser authorization and installation guide`，位于原分支 `codex/devops-bootstrap`，当时未 Git push。本节记录 Dashboard 功能完成及测试站发布时的状态；该阶段 Dashboard 变更尚未提交或推送，后续源码状态以 Git 分支和远端为准。
- 首页改为个人协作工作台：左栏最近仓库与搜索；中间开放 PR / Issue 各 5 条、相关 / 创建 / 待审阅或分配筛选及完整列表入口；右侧最近 3 条协作动态和最多 3 条未读通知。保留现有深色、青色强调和账号 / 管理菜单，移除大面积账号欢迎卡。
- 完整列表位于 `/work/pulls`、`/work/issues`，支持标题搜索、状态与分页，筛选保存到 query。最近访问只按账号在本机保存编号，由服务端重新核验名称与权限。页面各区域独立加载、错误重试，忽略旧响应；账号切换清除旧内容，窗口恢复焦点时重新查询。
- 新增 Cookie 会话接口 `/api/me/dashboard/items`、`/activity`、`/repositories`，复用现有协作表与 `visible_repositories`，在计数、排序和分页前过滤私有仓库 ACL。待审阅匹配当前 PR head 和最近请求时间；公开参与及动态不包含私有审阅草稿、已删除评论或原始事件 JSON。没有新增模型、迁移或依赖。
- 本地验证：后端 225 项、前端 118 项、CLI 10 项通过；Ruff check / format、TypeScript / Vite 生产构建通过。完整检查的迁移阶段最初发现本机开发库仍在 `0010_collaboration`；执行此前已有的 `0011_cli_oauth` 升级后，重新运行 Alembic check 与生产构建均通过。这次未改变 Git 协议或 Gateway，未重跑 Docker Git 集成。
- Safari 使用真实账号验证：跨仓库开放列表、待审阅空态、已关闭 Issue、标题搜索与后退恢复、最近仓库访问排序和搜索、新建仓库入口。没有更改已有 Issue / PR、成员权限或 GitHub 内容。另用本地合成数据检查多行列表、长标题、独立通知区域及浏览器错误，1280px 桌面无横向溢出或控制台错误；临时服务和预览 tab 已关闭。最终将动态预览从 4 条收紧为 3 条后，用户已切换 Safari 到 GitHub，停止后续界面操作；该末版已完成生产构建、公网文件一致性和 readiness 核验，未另拍末版 Safari 截图。
- 测试环境 server / worker 已发布双架构索引 `devops.kevinc.ltd/core/devops:test-9488f3e-dirty-20261004T091755Z-f486e1@sha256:1f182aaf09b130f2c134b0af779bb93eeba1f7a7cf7da21fd096a9a3a733d271`，部署器核验原生 amd64 实际镜像及 healthy，随后只读检查 server / worker / gateway 均 running + healthy。远端 migration head 已是 `0011_cli_oauth`，本轮没有修改测试站 schema；Gateway、CLI 下载包及只读网站挂载保持现状。
- 最终网站 release：`20261004T092132Z-9488f3e-7aa3ed`。已校验公网 `/release.json`、入口 assets、最终 HomeView chunk 与本机构建的 SHA-256 一致及 `/api/health/ready`。构建包含未提交工作区，标签中的 HEAD 不等于完整源码快照。重新核验继续使用网站发布器 verify、SSH 镜像 / 健康检查与 Alembic current；本次服务器 / 浏览器授权不固化为后续授权。


## 2026-10-05 Registry 集成交付

- 用户明确授权实施、测试环境部署、Safari 网页操作和数据写入测试。本次保持原分支 `codex/devops-bootstrap`，源码未 commit、未 Git push；发布镜像包含工作区变更。
- 新增官方 Registry 3.1.2 私有容器，现有 HTTPS `/v2/` 经 Gateway 子请求鉴权，复用用户与 repo ACL。原生 `docker login` 使用网页创建的专用令牌，无 DevOps CLI 前置登录；只保存令牌摘要。浏览器管理仍用 Cookie + CSRF，Docker 协议不接受 Cookie / Git OAuth / 兼容密码。权限不缓存，数据库变更提交后下一请求即生效。
- repo 新增 Registry 页签：创建 / 解绑 / 恢复 / 转移 namespace，查看镜像与标签、复制 digest 拉取命令、仅拥有者删除 manifest。一个 repo 支持多个 namespace，namespace 单一归属且解绑保留名称与数据。详情与限制见 [Registry README](registry/README.md)。
- 迁移 `0012_registry` 已在本机和测试环境升级；新增 namespace 与凭据表，并为已初始化配置补齐默认项。测试站已启用 REGISTRY_ENABLED。存在 Registry 历史时拒绝降级。
- `make -C . check` 全部通过：后端 248 项、前端 122 项、CLI 10 项，Ruff check / format、Alembic check、TypeScript / Vite 构建通过。文档链接 / 代码围栏及 `git diff --check` 通过。
- 本机真实 Docker Gateway 集成通过：新 Registry 的 OCI 上传 / 拉取、目录与标签、mount 双侧授权、解绑 / 恢复、归档只读、删除 digest 和即时撤销；原 Git / LFS / OAuth / HTTP/3 / PR 合并与静态切换回归通过。沿用临时 schema、合成账号和临时容器。
- 测试站原生 Docker CLI 验收通过：独立临时 DOCKER_CONFIG、`docker login --password-stdin`、scratch 合成镜像 build / push / pull、跨镜像层复用。保持同一登录，改 read 后新标签写入立即拒绝、pull 仍可用；恢复 write 后新标签立即推送成功；解绑拒绝 pull，恢复即成功；撤销令牌立即拒绝 login 与 pull。相同已存在 manifest 可能被 Docker 跳过写入，因此写权限拒绝采用全新标签验证。
- Safari 已验证新 repo 的 Registry tab、多个 namespace 创建、空 namespace 解绑 / 恢复、真实镜像和标签列表、复制拉取命令、删除确认影响提示与取消；未在真实账号下生成 Docker 凭据。新建的验收 repo `czf0613/registry-acceptance-20261005`、两个 namespace 及少量合成 smoke / clone 镜像保留供查看。临时合成账号已停用、会话和令牌全部撤销、repo 成员关系移除；未改动原有用户凭据或既有 repo 权限。

初次集成的测试环境快照（`ssh kdevops`，`/dockerdata/devops/deploy`；后续更新见下节）：

| 组件 | 已核验的发布 |
|---|---|
| server / worker | `test-951a9f9-dirty-20261005T074513Z-99685a`，索引 `sha256:39c05479bdaa4e20a8ea4054c1c16a0d1cb0131f24113b828e4d883ec94e3f37` |
| gateway | `test-951a9f9-dirty-20261005T074028Z-d203e8`，索引 `sha256:e17ff91e8564848b7cf78976df0e9f3e8a10ca00231e7c4e20a4530115ace456` |
| Registry | 官方 `registry:3.1.2@sha256:ddf754342cfc8acc51a56d5d0ab6af06826461864460636d8bd5c546dab2a7b8` |
| website | `20261005T074215Z-951a9f9-590be3` |
| PostgreSQL / Redis | 保持原镜像与挂载；迁移 head 为 `0012_registry` |

发布器核验双架构索引及服务器实际 amd64 镜像，六个容器均 running + healthy，公网 readiness 为 ready；Registry 没有宿主机端口，配置只读挂载、数据独立持久化。首次迁移前 Compose / PG 备份在 `.deploy-backups/test-6690i1fb`，最后后端更新前 Compose 在 `.deploy-backups/test-px1loi4w`，均位于远端部署目录。网站 release.json 和入口资源已核验。以后重新检查请读取实际容器 Config.Image / 健康、`alembic current --check-heads`、`/release.json` 和 `/api/health/ready`，不能只凭本段快照。

当前限制：即时收权针对新请求，已放行的单次传输可完成；删除同 digest 会移除所有相关标签，可能破坏多平台索引引用，且不会立刻回收磁盘空间。Registry 为单实例文件系统存储；数据库与镜像存储应配套备份恢复。


## 2026-10-05 Registry 使用体验与系统管理优化

- 延续本轮已授权的实施、测试环境部署、Safari 和数据写入验收；保持 `codex/devops-bootstrap`，源码未提交、未 Git push。
- AppInput / AppSelect 增加 actions 插槽，输入框、下拉控件与相邻按钮同行等高，标签和说明独立。镜像标签操作与标签标题对齐。AppNotice 保持 inline 默认模式，新增共享堆叠 floating 浮层；Registry / Docker 令牌的复制成功与失败提示不占页面空间。Safari 已比较复制前后画面与滚动位置，未发生位移。
- Docker 令牌只在账号安全创建；新令牌仅关联用户，一次原生 docker login 可用于有权限的多个 repo。repo 页只提供账号安全链接。已有项目限定令牌保留原限制直到撤销 / 到期，避免静默扩权；API 拒绝新建 repo 限定令牌。
- 系统管理新增 Registry 管理（`/admin/registry`）：namespace / repo 搜索、绑定状态筛选、解绑、重新分配与原 repo 删除后的找回。管理员仅获得此项显式管理能力，Docker 和镜像内容仍检查成员 ACL。修改按 repo ID 排序锁定，再检查 namespace 版本，写入审计。迁移 `0013_registry_management` 增加版本号，不重写旧归属或凭据。
- 检查通过：`make -C . check` 的后端 253 项、前端 126 项、CLI 10 项，Ruff / Alembic / TS / Vite；最后的复制失败浮层补充后再次通过全部 126 项前端测试和生产构建。真实本机 Gateway harness 的 Registry / Git / LFS / OAuth / HTTP/3 / PR / worker 回归通过。
- 测试站原生 Buildx 实测：独立临时 DOCKER_CONFIG 和用户级令牌，一条 `buildx build --platform linux/amd64,linux/arm64 --push` 同时推送到两个不同 repo；保留默认 provenance，两份构建证明保存成功。分别 `docker pull --platform` 后核验 architecture，并从未执行的临时容器复制各自镜像层内的架构标记文件验证；也通过 index digest 拉取。移除一个 repo 的成员资格后立即拒绝该项目，但同一令牌继续访问第二个项目；撤销令牌后第二个项目也立即拒绝。
- 合成 `multiarch:v1` 留在原验收 namespace，index digest 为 `sha256:78f34031b824077720818a51fcaa3cc030d3ecb85a53cd71364f17f50f5b87bb`。网页正确展示 linux/amd64、linux/arm64 和 2 份构建证明；unknown/unknown 证明条目不算作运行平台。此次验证覆盖构建、存储、推送和拉取，不宣称执行了业务应用。
- Safari 已核验 repo 中无令牌创建入口、账号安全表单、Registry 管理菜单、未绑定 / 原 repo 已删除筛选、目标 repo 搜索，以及空验收 namespace 的解绑 / 原归属恢复；已核对两条管理审计。未修改真实账号的凭据。新增临时合成账号已停用、会话与令牌撤销、成员关系清零，第二个合成 repo 软删除；其 namespace 保持未绑定，供管理页展示。临时本机 Docker 配置、容器、镜像引用已清理。

本次更新后核验的测试环境（2026-10-05）：

| 项目 | 当前发布 |
|---|---|
| server / worker | `test-951a9f9-dirty-20261005T095033Z-a52d74`，索引 `sha256:4c2f4ea2c5608c7f88b9becb8c68a40c06292c7cbd5a13a984aeb0d80b9c4e51` |
| website | `20261005T095655Z-951a9f9-5617f6` |
| Alembic head | `0013_registry_management` |
| 升级前 Compose / PG 备份 | 远端部署目录 `.deploy-backups/test-1nicd9le` |

Gateway、官方 Registry、PG / Redis 镜像与原挂载保持不变；六个容器健康，Registry 无宿主机端口。发布器已核对实际 amd64 manifest / config、网站入口资源与 readiness，公网 release.json 与 API ready 已核验。重新核验方式同上，不能将本次授权或此快照当作未来部署依据。即时收权与删除 / GC 的限制继续见 [Registry README](registry/README.md)。

## 2026-10-05 人工维护窗口 GC、令牌清理与弹窗修复

- 用户确认本版由人工控制维护窗口，仅在管理员发起前提醒，不实现自动只读或写请求拦截。沿用本轮已授权的测试环境部署、Safari 和数据写入验证；原分支未提交、未 Git push。
- `0014_registry_gc` 删除所有旧 repo 限定 Docker 令牌及范围列，保留用户级令牌；Git OAuth 和网页登录会话不受此清理影响。新增 GC 历史 / 进程心跳表。
- 系统管理 Registry 页面新增空间回收提醒、确认、进度及历史；专用进程先 dry-run 再执行固定官方 3.1.2 `--delete-untagged`。保留有标签镜像及其引用链，namespace 解绑不参与 GC 判断；无标签且未被保留引用的 digest 历史可被回收，长期保留需留标签。
- GC 与 server / Registry 部署共用文件锁；活动任务有数据库唯一约束，执行前重查管理员会话 / 角色。确认 60 秒后尚未启动则取消；进程中断记录失败，不自动重跑。API 不执行任意命令、不挂载镜像数据，独立进程无 Docker socket / SSH 权限。
- 通用确认弹窗支持长 digest 换行、分段说明、正文垂直滚动、固定可见操作区及无滚动焦点恢复；删除版本弹窗明确逻辑删除与后续物理回收的区别。
- 本地验证通过：后端 264 项、前端 129 项，Ruff / Alembic check / TypeScript / Vite；真实 Gateway harness 的 GC、原 Registry / Git / LFS / OAuth / HTTP/3 / PR / worker 回归通过。真实 GC 用隔离 Linux 卷回收超过 1 MiB 独占数据，同时验证双平台子清单、共享层、别名标签、Buildx 风格证明及未绑定 namespace 保留；没有运行镜像内业务程序。
- 测试环境真实 Buildx 验收通过：使用合成账号和临时 Docker 配置推送 amd64 / arm64（默认 provenance），删除一个测试索引后由管理员 API 发起 GC，数据目录占用从 4,497,408 降至 2,183,168 字节（减少约 2.21 MiB）。保留镜像分别通过两个平台的 docker pull、镜像内架构标记检查，以及按 index digest 拉取；两份构建证明保留。第二次 GC 清理本次测试镜像图，完成后数据目录 954,368 字节。测试镜像已删除，合成账号停用、admin 角色移除、会话 / 令牌撤销、成员关系清零，合成 repo 软删除且 namespace 保持未绑定。
- Safari 核验删除弹窗完整 digest 换行、无横向滚动、操作按钮可见；GC 维护提醒与取消、成功历史 / 空间减少数值显示正常。未通过真实账号创建令牌或删除演示镜像；Safari 停留在 Registry 管理回收记录。
- 构建曾遇 Docker Hub 认证服务直连超时，改用本次进程的本机代理后完成双架构构建 / 发布，官方 Registry 摘要保持一致。Web 安装后另用标准验证器核验 release、index、4 个入口资源和 API readiness。


此次测试环境快照（2026-10-05，后续操作仍须重新核验）：

| 项目 | 已核验发布 |
|---|---|
| server / worker / registry-gc | `test-951a9f9-dirty-20261005T103432Z-989e08`，索引 `sha256:265b946e91dfe3d43da34f713402d34efeb594f55f92c38b0f59428ac4f10b7b` |
| 服务器 amd64 manifest / config | `sha256:63d070874103351d067bf1535c335485284d3a5e344e7a1f59011c57d36baea9` / `sha256:038d2ceacee294299d941d2dcc46dd035595c2c93dc0066f5ad5cc6e98ab2022` |
| website | `20261005T103629Z-951a9f9-d2378a` |
| Alembic head | `0014_registry_gc` |
| 升级前 Compose / PG 备份 | `/dockerdata/devops/deploy/.deploy-backups/test-c5xpn7o4` |

七个服务均 running + healthy；GC 二进制现场核验为官方 3.1.2，心跳正常，两次记录均 succeeded。Gateway / Registry / PG / Redis 沿用上一快照的镜像和挂载；server 仍只有 Git 数据挂载，新增 GC 容器挂载 Registry 数据和只读配置，无公开端口。公网 readiness / release 已核验，重新确认方法沿用前述实际容器镜像 / 健康、迁移 head 和公共端点查询。源码仍未提交、未 Git push。

## 2026-10-05 管理员仓库操作与整组 namespace 删除

- 延续本轮授权，完成实现、测试环境发布和 Safari 检查；保留原分支及既有未提交 Registry 工作，源码未 commit、未 Git push。管理员操作使用独立合成账号验收，未向真实邮箱发测试邮件。
- 系统管理新增仓库列表 / 详情，可强制添加、编辑、移除成员，分别修改读写、岗位、仓库管理员资格，以及更换拥有者、归档 / 取消归档、软删除。管理接口只返回必要元数据，普通仓库 / Git / Registry 的私有内容 ACL 不因全局 admin 身份放开。移交后原拥有者保留读写和仓库管理员权限；删除仍保留 ID 对应的 Git 文件与历史。
- 每次管理员仓库写操作，以及 Registry 的绑定、解绑、删除、GC 都消费一条绑定当前管理员邮箱 / 用户的 `admin_operation` 验证码。复用邮箱挑战、Redis 限流、有效期、失败次数和邮件模板；成功消费与业务操作同事务，提交时重查账号、邮箱、会话和 admin 角色。没有临时全局免验证窗口，审计不记录验证码。仓库行锁和 `expected_updated_at` 拒绝过期表单，普通成员管理也推进仓库版本时间。
- 用户确认删除 namespace 时包括全部镜像：原子标记删除并立即拒绝后续访问，名称永久保留、不可恢复或重新绑定；页面提供“已删除 / 清理记录”。下次人工维护窗口由 GC worker 清理这些 namespace 的全部标签（包括嵌套路径），复查无新增标签后预检 / 回收，成功才记 `purged_at`。删除前已放行的上传可能完成，仍需人工停止写入并等待在途请求结束；不新增自动只读状态。清理中断保留待处理状态，由管理员排查后重新验证邮箱发起。
- `0015_admin_management` 增加管理员挑战约束和 namespace 删除标记，`0016_registry_namespace_purge` 增加回收完成状态；删除记录或管理员挑战历史阻止不安全降级。
- 验收发现并修复 Buildx 复用旧来源层的问题：来源收权时禁止 mount，但目标有写权限则通过可信鉴权信号去掉 mount / from，让客户端普通上传本地数据。真实网关验证来源直接访问仍拒绝、目标在上传前无该 blob、客户端伪造信号不能越权，合法 mount 仍正常。
- 现场 GC 在实际回收前发现 catalog 校验误判：官方文件系统驱动按目录层级枚举，共同前缀的 namespace 不一定按完整名称字符串排序。`RegistryClient.pages` 对 catalog 保留上游最后条目游标，使用跨页去重和次数上限拒绝循环，不再臆定字典序；标签仍验证有序。补充跨页 / 重复项测试，真实回收用 `gc-deleted` 与 `gc-deleted-keep` 验证前缀隔离。
- 本地 `make -C . check` 通过后端 286、前端 137、CLI 10 项，Ruff / Alembic check / TS / Vite 均通过。最后补充 GC 页面状态联动后，重跑相关前端 28 项和生产构建通过；mount 修复后 Registry 26 项通过；目录修复后 Registry / GC 43 项及 Ruff 通过，重建本机原生镜像并再次通过完整真实 Gateway harness（Registry / GC / Git / LFS / OAuth / HTTP/3 / PR / worker）。整组回收包含双平台、嵌套镜像、别名标签和共享层，验证独占 blob 消失、存活引用保留。
- Safari 已检查仓库列表、成员搜索 / 权限表单、仓库删除邮箱弹窗、整组 namespace 删除提示，以及 GC 维护与邮箱确认弹窗；正文换行和操作区域正常。只打开并取消确认，没有通过真实管理员发信、授予成员权限或删除演示数据。
- 最终测试站合成数据验收通过：缺失、错误、重复使用验证码均拒绝；管理员成员增删改、归档 / 取消归档、移交和软删除成功且审计齐全，普通私有内容仍按成员 ACL 拒绝访问。真实 Buildx 推送默认 provenance 的 amd64 / arm64 镜像到嵌套路径，删除整个 namespace 后立即拒绝访问；GC 第 4 次记录成功，目录占用从 4,857,856 降到 1,904,640 字节，共享层所在的保留 namespace 仍能分别拉取两个平台。随后删除此次保留测试 namespace 并再次 GC，目录降到 1,441,792 字节，清理完成状态正确。3 个临时账号已停用、全局角色移除、会话 / 令牌撤销、成员关系清除，测试 repo 软删除并验证物理 Git 目录仍在；前两次验收遗留的测试 namespace 也已回收，失败历史保留。邮箱挑战通过合成数据和邮件替身验证，未测试真实 SMTP 投递。
- 首次远端按摘要拉取遇到 `application/octet-stream` 错误，部署器在备份 / 迁移 / 替换容器前停止，旧服务健康；按原标签重新拉取并校验同一摘要后复用部署流程完成升级。没有更改网络全局配置或跳过镜像核验。

本次测试环境快照（2026-10-05，后续操作仍须重新核验）：

| 项目 | 已核验发布 |
|---|---|
| server / worker / registry-gc | `test-951a9f9-dirty-20261005T122010Z-8f49b6`，索引 `sha256:5ef9fef78dc920306f02414e86d3444c1fa6d2120972b72b5e7f01532ed79231` |
| gateway | `test-951a9f9-dirty-20261005T121210Z-c9b6c3`，索引 `sha256:235728144b6eeff3ebf11385cf4c9b8cd72ef01b9eba2211fb35ebba76c032f4` |
| website | `20261005T120554Z-951a9f9-b40dbe` |
| Alembic head | `0016_registry_namespace_purge` |
| 升级前 Compose / PG 备份 | `/dockerdata/devops/deploy/.deploy-backups/test-cr50aui2` |
| 最后一次后端更新前 Compose | `/dockerdata/devops/deploy/.deploy-backups/test-q_ahiwk3` |

发布器核验双架构索引和服务器实际 amd64 镜像，七个服务 running + healthy；官方 Registry / PG / Redis 镜像及原挂载不变。网站标准验证器核验 release、index、4 个入口资源和 API readiness，公网 release / ready 另行读取确认。重新核验应读取实际容器镜像 / 健康、`alembic current --check-heads` 和公共端点，不能用此快照代替现场状态或后续部署授权。


## 2026-10-05 个人镜像管理与仓库生命周期验证

- 延续本轮实施、测试服务器和 Safari 验收授权，保留原分支及全部已有未提交工作；未 commit、未 Git push。
- namespace 名称保留规则保持：这是应用的保守策略，不是 Registry 的固有限制。当前 GC 会按名称路径重复清理已删除记录，删除前放行的上传可能稍后结束；同名重建会混淆数据与清理目标，旧 CI / 部署地址也会指向新的镜像。若以后开放复用，需要另行设计维护窗口内的显式释放及重建流程。
- 管理员仓库详情增加刷新区 / 表格间距，添加成员在成员面板中就地展开，更换拥有者在生命周期面板中展开，不再把添加卡片放到页面末尾。
- 仓库拥有者的归档 / 取消归档、移交和软删除逐次验证当前邮箱，使用独立 `repository_operation` 用途。迁移 `0017_repository_verification` 增加用途和用户绑定约束，存在记录时拒绝降级。与业务修改同一事务消费，等待验证码锁后重查用户、邮箱、Cookie 会话或 CLI Bearer，保留原 ACL、完整名称确认和软删除语义。
- CLI 0.1.1 配套更新：交互模式发信后提示输入；非交互模式先 `--request-code`，再 `--yes --code-stdin`，code 不进入命令行 / 持久存储或日志。`--yes` 不能跳过邮箱验证。发信入口位于 repo API 的 `operation-code`，检查拥有者后复用现有挑战 / 限流 / SMTP，不扩大 CLI 对账号接口的访问范围。
- “工作台 → 镜像管理”位于工程仓库下，路由 `/images`；本人相关 namespace 支持搜索、绑定状态筛选、分页和原 repo 跳转。拥有者可为本人未归档 repo 创建 namespace，解绑项可重新绑定；`RegistryImages` 与 `RegistryNamespaceCreate` 由此页及 repo Registry 页签共用。列表计数 / 分页前执行仓库 ACL，不为全局 admin 暗中放开私有镜像；归档只读，收权 / 删除后列表和原接口立即拒绝。账号、namespace、筛选变化防止旧异步响应覆盖。
- 完整本地检查通过后端 296、前端 142、CLI 11 项，以及 Ruff / Alembic check / TS / Vite。补充邮箱用途与账号切换弹窗测试后，相关前端 38 项再次通过；最后布局整理随发布重新生产构建。重新构建原生 server、打包 CLI 后完整真实 Gateway harness 通过，覆盖 Registry / GC 双平台和共享引用、HTTP/3、Git / LFS、PR / worker，以及带新验证码的归档 / 移交 / 软删除。
- 测试站合成数据验收通过：普通成员可查看相关 namespace，移除成员后立即消失；全局 admin 未加入 repo 时不可读取；归档期间禁止创建，移交后创建权限跟随新拥有者。生命周期操作缺失、错误、重放验证码均被拒绝，归档 / 取消归档、移交和软删除成功且审计齐全，物理 Git 目录仍保留。3 个临时账号已停用、角色 / 成员关系清除、会话撤销，测试 repo 软删除，2 个空 namespace 的删除记录保留；本轮未发起测试站 GC，未测试真实 SMTP 投递。
- Safari 已确认管理员添加成员表单紧跟操作按钮、位于成员表格之前，刷新区域有独立间距；个人镜像页可切换 namespace、查看双平台镜像标签与证明条目，创建表单使用相同组件。仓库归档弹窗要求当前邮箱验证码，空验证码时禁止执行；仅打开并取消表单，没有对真实账号发信或改变演示仓库权限 / 生命周期。
- 最后修正个人镜像页筛选栏的通用表单相邻间距继承，独立发布网站后 Safari 复核搜索输入、搜索按钮和绑定下拉框已对齐。

本次测试环境快照（2026-10-05，后续操作仍须重新核验）：

| 项目 | 已核验发布 |
|---|---|
| server / worker / registry-gc | `test-951a9f9-dirty-20261005T124808Z-6b6f4f`，索引 `sha256:bb1e0dcb6890ce3472cd97f7ae939fcf992eeb759ddbb53b6e879d91d50b219a` |
| 服务器 amd64 manifest / config | `sha256:8003c610a3701a4ceeb324d14a1776fd07e17eb0b17f6a6e7a432f761e11ca41` / `sha256:31b18b3f4bc38a98755bd6ab5806a964da57c679bb8d742820358dcbc5cd7ed3` |
| website | `20261005T125652Z-951a9f9-21f491` |
| CLI | `0.1.1`，归档 SHA-256 `83579c664af4eb346e14c4c896bb1568f68fad038ed39cede902cdf305b805e8` |
| Alembic head | `0017_repository_verification` |
| 升级前 Compose / PG 备份 | `/dockerdata/devops/deploy/.deploy-backups/test-b16a908u` |

发布器已校验双平台索引和服务器实际 amd64 镜像，七个服务 running + healthy；网站发布器核验最终 release、index、4 个入口资源和 API readiness，公网 `/api/health/ready` 与 `/release.json` 另行读取正常。Gateway / Registry / PG / Redis 沿用上一快照的镜像和挂载；源码未提交、未 Git push。重新核验仍以实际容器镜像 / 健康、迁移 head 和公共端点为准。


## 2026-10-05 永久令牌与 Registry 使用体验

- 按用户要求先提交此前全部 Registry、管理功能和相关文档，独立 checkpoint 为 `672e585`；提交前核对全部新增文件并通过 `git diff --cached --check`，随后工作区为空。沿用当前分支，本轮后续改动未提交，未 Git push。
- Docker 登录令牌新增“永久有效”，默认仍为 90 天。选择永久时显示泄露风险及当前 ACL 说明，勾选确认后才可创建；API 要求 `days: null` 与严格布尔 `acknowledge_permanent: true`，不使用超远未来日期。创建成功、改选期限和账号切换清除确认。列表显示“永久有效”，保留撤销入口；永久令牌计入 50 个有效令牌限额，仍逐请求校验撤销、账号、仓库和 namespace 权限。
- 新迁移 `0018_registry_permanent_tokens` 允许到期列为空，保留已有令牌原到期时间。存在永久令牌记录（含已撤销历史）时拒绝回退非空约束，不修改旧迁移或伪造期限。
- 使用指南新增完整 Docker Registry 部分：namespace 和账号令牌入口、原生登录、tag / push / pull、amd64 / arm64 Buildx 构建与检查、CI 标准输入、退出 / 撤销和权限排障。命令取当前域名 / 端口和登录用户名，并链接 Docker 官方说明；复制提示浮于页面上方。GitHub Desktop 与 CLI 卡片在同一网格行自动等高。
- 用户截图的 repo 只有拥有者；此前该行没有任何操作说明，普通成员的操作还位于表格最右。现在操作列紧跟姓名，普通成员和仓库管理员可就地编辑 / 移除；拥有者行明确展示更换拥有者入口、聚焦移交表单，并解释先移交后才能降低原拥有者权限。仅有拥有者时给出添加成员引导。保留拥有者隐式读写 / 管理权限、逐次邮箱验证、版本冲突检查，以及系统管理员不隐式读取私有内容的边界。
- 本地 Registry / 管理 / 迁移专项 65 项通过；完整 `make -C . check` 通过后端 301、前端 147、CLI 11 项，Ruff / 格式 / Alembic check / TS / Vite 均通过。真实 Gateway harness 使用永久令牌完成 OCI 上传 / 拉取与撤销，并通过既有 GC 双平台 / 共享引用、Git / LFS / PR / worker / HTTP/3 验证。没有使用真实 SMTP 发信。
- 测试站合成数据验收通过：永久令牌可用原生 `docker login --password-stdin` 登录，有限期限仍保留到期时间，未确认风险的永久申请被拒绝。管理员逐次验证邮箱后完成成员添加、读写 / 管理 / 岗位修改、移除、所有权移交及原拥有者降权；相同 Docker 令牌的后续请求立即服从新权限，撤销后返回 401。非成员全局管理员仍不能读取私有仓库内容，审计记录齐全。
- Safari 复核永久令牌警告及未勾选确认时的禁用状态、管理员成员行内编辑与邮箱确认弹窗、两张指南卡片等高及新增 Registry 指南。现场发现并修正通用样式导致的确认框尺寸和命令区宽度问题，相关前端 10 项与最终生产构建再次通过；复制命令的浮层提示不改变页面滚动位置。未通过真实管理员发送测试邮件、创建令牌或提交权限变更。
- 3 个临时账号已停用，全局角色 / 成员关系清除，会话与 Docker 令牌全部撤销，测试 repo 软删除；空 namespace 标记删除、保留下一次维护 GC 清理记录。本轮没有在测试站推送镜像或执行 GC；真实镜像上传、回收和共享引用验证在本地隔离 Gateway harness 中完成。

本次测试环境快照（2026-10-05，后续操作仍须重新核验）：

| 项目 | 已核验发布 |
|---|---|
| server / worker / registry-gc | `test-672e585-dirty-20261005T135552Z-0e51dd`，索引 `sha256:2a2866f71efbeb11b7ecf1df898a11a852b75373be18c07a9cf5c69b7a726fd7` |
| 服务器 amd64 manifest / config | `sha256:66eb13bbfb4090335bac6663ec3ea4d2c7d61e37573440ef20bf6636ac2746df` / `sha256:814284db4652ddc939f0af344abe8f0f505c5dea01555c165b8a575e3cf64763` |
| website | `20261005T140515Z-672e585-a6e52f` |
| Alembic head | `0018_registry_permanent_tokens` |
| 升级前 Compose / PG 备份 | `/dockerdata/devops/deploy/.deploy-backups/test-9m2_y0u1` |

发布器已校验双平台索引和服务器实际 amd64 镜像，网站发布器核验最终 release、index、4 个入口资源和 API readiness；七个服务 running + healthy，公网 `/api/health/ready` 与最终 `/release.json` 另行读取正常。Gateway / Registry / PG / Redis 及挂载沿用之前的已核验配置；CLI 保持 0.1.1。重新核验仍应查询实际容器、迁移 head 和公共端点。本轮修改未提交，未 Git push。

## 2026-10-05 成员操作栏与登录指南精简

- 复用 `RepositoryMemberForm` 的操作插槽，管理员仓库详情和普通仓库设置均将“保存成员设置”“取消编辑”放在同一行；取消只关闭编辑，不提交权限变更。
- 指南命令与复制按钮垂直居中；长命令保持一行、可横向滚动。CLI 示例拆为逐条复制，每个按钮只复制一条完整命令。Docker Registry 指南按本轮要求精简为准备账号令牌与原生 `docker login` 两步，移除后续 Docker 操作教程；两张 Git / CLI 卡片继续等高。
- 相关前端 30 项测试和 TypeScript / Vite 生产构建通过。Safari 复核两个成员编辑入口的按钮位置及取消行为、指南卡片布局、命令居中和精简内容；实际复制 Issue 列表命令并核对为完整单行。浏览器未提交权限变更、创建令牌或发送验证邮件。
- 沿用本轮测试部署授权，仅发布 website：`20261005T142548Z-672e585-df28be`。发布器核验 release、index、4 个入口资源和 API readiness；另行核对公网 release / readiness，以及七个服务 running + healthy、容器 ID 和镜像与发布前一致。本轮未修改后端或迁移，未重跑后端检查；此前未提交工作全部保留，未新增 commit、未 Git push。

## 2026-10-05 组件 1.0.0 与网页版本展示

- 按用户要求先提交此前所有更改，独立 checkpoint 为 `6fa23a1`，提交后工作区为空；本节的后续版本调整未提交，未 Git push。
- server、website、CLI 本次分别设为 `1.0.0`。按用户澄清，三者独立维护和发布版本，不自动同步、不要求相等。server 的版本定义在 `pyproject.toml`，uv 更新锁文件；FastAPI / OpenAPI 从安装包元数据读取。网站和 CLI 分别读取自己的 `package.json`，移除运行代码中的重复版本常量。
- 左侧栏底部显示网站构建版本 `DevOps v1.0.0` 和当年版权 `© 2026 kevinc.ltd`；年份随页面加载动态生成。指南单独读取 CLI 包版本生成安装 / 下载 / 校验和地址，CLI 更新无需提升网站或 server 版本；回归测试使用与网站不同的 CLI 版本，防止再次混用。
- 完整 `make -C . check` 已通过；最终按独立版本规则收敛后，后端健康 / 部署专项 60 项、指南 / 外壳前端 9 项、Ruff 与 TypeScript / Vite 构建再次通过。没有修改认证、权限、数据库结构或 Git 协议，本轮未重跑真实 Gateway harness。
- 本轮发布 server（含 worker / registry-gc）、CLI 和 website。后端容器更新完成后，为落实版本独立规则中止本地后续流程，再单独发布 CLI / website；没有回滚或执行迁移。发布器已核对双平台索引、服务器 amd64 镜像与健康；最终七个服务 running + healthy，Gateway / Registry / PG / Redis 的容器 ID、镜像及所有挂载保持原配置。
- 公网 OpenAPI 和三份后端运行包均为 `1.0.0`，readiness 正常。公开 CLI 下载和校验和与本地产物一致，在临时目录从该公开包安装后 `devops --version` 返回 `1.0.0`，未修改本机全局 CLI / Git 设置。Safari 已确认侧栏版本 / 版权，以及 CLI 1.0.0 下载地址；未执行用户数据写入或发送邮件。

测试环境快照（2026-10-05，后续操作须重新核验）：

| 项目 | 已核验发布 |
|---|---|
| server / worker / registry-gc | `test-6fa23a1-dirty-20261005T144812Z-ba2641`，索引 `sha256:993e6ebb8d50b4c69e73c6077170bc3f4a68bda332f387ae4a9b014b51d0bb85` |
| 服务器 amd64 manifest / config | `sha256:ed2f5ad1b88eead08e04d098614d51b92e14f0024227774f6b1f875c9a3031c7` / `sha256:a9f2f6a4bae688668b6c8c51e67699241643b4ee491736ca25767c905f367ca5` |
| website | `20261005T145345Z-6fa23a1-e5980d` |
| CLI | `1.0.0`，归档 SHA-256 `779d87cc206a59d8c956bc0057de3f83568dda21007f69c218ee751cb9f61c9e` |
| Alembic head | `0018_registry_permanent_tokens`（仅检查，未迁移） |
| 更新前 Compose 备份 | `/dockerdata/devops/deploy/.deploy-backups/test-v6v2j0q4` |

重新核验读取实际容器版本 / 健康、OpenAPI `info.version`、公网 release 与 CLI 校验和；本快照不代替后续部署授权。

## 2026-10-06 独立 CI worker

- 按本轮授权实施，并在专用合成仓库中进行测试环境读写、原生 macOS arm64 worker 和 Safari 验收。沿用 `codex/devops-bootstrap`，开始时工作区为空，基线 `a24bfc6`；本轮没有提交或 Git push。
- server 通过 WSS 管理外部 worker，PostgreSQL 持久化注册、调度、不可变提交 / 配置、Attempt 租约及结果。支持 push、同仓库 PR、手动触发、DAG、步骤日志、依赖产物、取消、明确重跑和 PR `ci/<job>` checks。已有 collaboration worker 负责事件处理及回收；服务端不执行构建脚本。
- 独立 Runner 使用 Python 3.14 / uv / wheel，首版为 NativeExecutor。Executor 生命周期、环境类型、worker capability 及平台进程监督为后续容器 / VM 执行器保留扩展位置；当前不能作为恶意代码沙盒。每台一次执行一个 Job，通过增加构建机扩展并行度。
- 一次性注册令牌、长期身份摘要、连接代次、持久 outbox / ACK、日志去重和租约共同处理重连。已开始任务失联为 lost，不自动重复执行；取消、超时或失联同时收敛正在运行的步骤状态。源码凭据复用可吊销的任务级 Git OAuth，仅允许读取本次仓库和声明的依赖产物，执行结束即失效。
- 仓库拥有者明确授权资源池；全局 admin 不隐式读取私有仓库，移交使旧授权失效。worker 管理逐次邮箱验证、审计；日志 / 产物沿用仓库 ACL。迁移 `0019_ci` 保留原认证与业务数据，存在 CI 记录时拒绝破坏性降级。日志 / 产物落在既有 Git 卷的 `.ci-data`，保留 30 天，运行元数据继续保存。
- 实际安装包验收发现并修复系统代理发现、loopback 绕过代理的一致性，以及隔离宿主 Git 配置后缺少仓库 LFS filters 的问题。Git 凭据与代理只通过临时子进程环境传递；LFS filters 只安装在本次临时 checkout，不改本机全局 Git 配置。网页修正了通用 pre 宽度约束对安装指引和执行日志的影响。
- 完整 `make -C . check` 通过后端 321、前端 152、CLI 11 项及 Ruff / Alembic check / TypeScript / Vite；随后队列筛选、任务终态、代理及 LFS 修正分别补跑 CI 后端 17 项、Runner 8 项、前端 152 项和相关静态检查。跨平台实现的真机验收仅 macOS arm64；macOS x64、Windows x64 / arm64、Linux x64 / arm64 及后台服务安装方式仍待具备机器后验证。一次性隔离、Secrets、缓存、矩阵与第三方 Actions 兼容尚未实现。
- 最终真实 Gateway harness 使用当前原生 Docker 镜像与本机 Runner，完整通过 Git / LFS / HTTP/3 / Registry / GC / 协作回归。CI 新增实际 LFS 文件内容断言、错误代理环境下的 loopback WSS / Git / 产物直连、取消步骤终态及崩溃后 lost 验证；结束清理临时 schema、容器和文件。双架构 server 镜像发布不代表 Linux worker 真机已验收。
- 测试站从公开 wheel 安装 Runner 1.0.3，实测通过代理的 WSS 注册 / 重连、HTTPS Git / LFS、两阶段产物传递、下载校验、PR 自动检查、固定提交重跑、取消，以及必要 CI 检查通过后合并触发新合并 SHA 的 push 流水线。最终成功记录为合成仓库 ID 12 的 Run #8，取消终态记录为 #7；早期失败记录保留用于说明回归过程。Safari 复核构建机管理 / 安装指引、运行列表 / 详情 / 日志，以及 PR 检查详情链接。
- 验收结束已停止本机 Runner，撤销测试 worker、禁用合成仓库 CI、停用临时账号并清除其角色、吊销该账号全部会话和 Git 授权；没有改动真实账号凭据。保留合成仓库、PR、运行 / 日志 / 产物及审计，已有测试成员可继续查看；本机专用配置、checkout、工作目录和安装测试 venv 已清理。未连接真实 SMTP，未安装系统常驻服务。

测试环境快照（2026-10-06，后续操作须重新核验）：

| 项目 | 已核验发布 |
|---|---|
| server / worker / registry-gc | `test-a24bfc6-dirty-20261005T164900Z-f6135a`，索引 `sha256:db6dbf66e2af2d7b250da72cd25736ae0839f5edbc77936c95de1c61ce0e6b3c` |
| 服务器 amd64 manifest / config | `sha256:93b45f211e9826d0871a77f57e9a5d120df8de31f42255db4f10aa31a7ee874b` / `sha256:f3142ee9f1b57641fa2655264511491edc1be7042ee4171374ae9cd9b6f2246d` |
| gateway | `test-a24bfc6-dirty-20261005T162849Z-05f545`，索引 `sha256:34a0c577e4e8c45fe892b0a9c06deff6ab0780dd1fed1a8ed3a1443149b470ae` |
| website | `20261005T165524Z-a24bfc6-729264` |
| Runner | `1.0.3`，wheel SHA-256 `f6a7643d2d064323aadc4d947d800c08e539381684c95d2f0e24f9a3ac50a673` |
| Alembic head | `0019_ci` |
| CI 迁移前 Compose / PG 备份 | `/dockerdata/devops/deploy/.deploy-backups/test-fs9udg_5` |
| 最后一次后端更新前 Compose | `/dockerdata/devops/deploy/.deploy-backups/test-n3meg76q` |

发布器核验双架构索引、服务器实际 amd64 镜像与 healthy；现场再次核对七个服务 running + healthy、迁移 head、公共 readiness / release，以及运行服务的五个 CI 模块源码 hash 与工作区一致。网站仍由本机独立构建发布；CLI、Registry、PostgreSQL、Redis 版本和原有挂载未调整。本轮只发布测试环境，源码未提交、未 Git push；该快照不代替后续操作授权。

## 2026-10-06 GitHub 工作流、CI 界面与构建机环境

- 仓库未配置 CI 时默认启用并使用 `default` 资源池；已明确关闭的仓库保持关闭。显式池授权仍属于仓库拥有者，移交后需重新保存。CI 页采用工作流侧栏、运行列表、状态筛选和手动运行表单；开关与可展开的资源池卡片移入 Settings，沿用现有权限和 API。
- 优先读取提交中的 `.github/workflows/*.yml` / `*.yaml`，各文件独立触发与保存不可变快照。已实现 GitHub 字段格式、push / PR / workflow_dispatch、分支过滤、手动输入、静态矩阵、needs、步骤环境变量及常用文件命令；内置 checkout / upload-artifact / download-artifact 连接 DevOps 自有服务。没有 GitHub 工作流文件时保留 `.devops/ci.yml` 兼容入口，历史记录仍可筛选与重跑。
- 当前不是完整 GitHub Actions runtime：setup-*、其他第三方 Actions、Secrets、缓存、容器 / 服务、复用工作流、复杂表达式等仍未实现，不支持项产生可见配置失败。原生构建机需自行准备工具链；详细执行范围和示例集中在 [CI.md](CI.md)。旧 worker 仍可运行旧格式任务，新格式要求 runtime 2，避免派给不认识新步骤的旧版本。
- Code 与 PR 展示当前完整 SHA 的 CI 摘要和工作流详情链接，区分无检查、等待、执行、成功和失败；异步响应及轮询按仓库 / SHA / 账号隔离。PR 检查使用 `ci/<workflow-file>/<job>`，旧格式名称保持兼容。
- Runner 1.1.0 上报 macOS / Windows 版本、64 位 CPU 架构、Linux 发行版 / 内核 / libc 类型与版本；支持精确版本前缀及最低版本约束，缺失版本不满足约束。Windows 客户端与 Server 按 edition / build 区分，glibc 与 musl 严格分开。调度要求不等于产物的运行兼容承诺，项目仍须配置 SDK、deployment target 和 libc 基线。
- Run #8 的 `/root/.config/git/attributes` 是远端 Git CGI 输出的 warning；该 Run 实际成功。Gateway 现在为 CGI 设置专用 HOME / XDG_CONFIG_HOME，不继承 root 配置路径。另在原生 macOS 联调发现仅剩僵尸进程时 `killpg` 返回 EPERM，已在 Darwin 核对进程组后处理，活进程组的权限错误仍报错；连续 40 次真实进程清理回归通过。
- 完整 `make -C . check` 通过后端 328、Runner 13、前端 154、CLI 11 项及 Ruff / Alembic check / TypeScript / Vite；随后历史工作流筛选修复通过 CI 后端专项 23 项。真实 Gateway harness 通过 Git / LFS / HTTP/3 / Registry / GC / 协作及 GitHub 格式 CI 的检出、产物、取消、重启恢复，新日志无 `/root/` 警告。
- 真机范围仍为 macOS arm64；macOS x64、Windows x64 / arm64、Linux x64 / arm64 待设备具备后验收。其他平台的环境识别、约束和拒绝路径已做模拟测试，不能据此宣称完成真机验证。原生执行器边界和未来一次性隔离接口保持不变。
- 测试站从公开 wheel 安装 Runner 1.1.0，新建合成仓库 ID 13，先确认默认启用 / default 池，再使用独立验收池。push #9 / #10、带输入的手动运行 #11、PR #1 对应运行 #12，以及 Safari 表单触发的 #14 均成功；矩阵任务真实检出 LFS、上传 / 下载依赖产物，校验输入内容与产物一致，日志无 `/root/` 警告。真实 macOS 26.6.2 满足 `macos>=15`，精确要求 `macos-15` 的 #13 无 Attempt、保持排队后由验收脚本取消。
- Safari 只读参考 GitHub Actions；在 DevOps 验证工作流侧栏 / 筛选、Settings 资源池展开、手动分支和输入提交、构建机版本信息、Code 当前 SHA 摘要及 PR 三项自动检查详情。原截图仓库已有 enabled=false / pools=[] 显式记录，沿用关闭状态；未改变该仓库配置。
- 验收后停止并撤销临时 worker、关闭合成仓库 CI、停用临时账号、清除其角色并吊销全部会话与 Git grant；再次查询无有效凭据或活跃 Attempt，本机配置、工作目录和安装测试 venv 已删除。保留合成仓库 / PR / 运行记录供原测试成员查看，用户创建的 `test` 构建机配置未改变；没有安装常驻服务或发送真实 SMTP 邮件。

测试环境快照（2026-10-06，后续操作须重新核验）：

| 项目 | 已核验发布 |
|---|---|
| server / worker / registry-gc | `test-a24bfc6-dirty-20261006T053725Z-08cba3`，索引 `sha256:875eeb6698d57bce19fbff28e0a1f38fbbdbd3c8155aaa325482895c92aac5c8` |
| 服务器实际 amd64 manifest | `sha256:76f9dbc71d4e73f0eb9aad5f95abca05655789872da4a11a024ee6647864e08c` |
| gateway | `test-a24bfc6-dirty-20261006T053138Z-b7064f`，索引 `sha256:813e669cdcb0bd4afe4cbbd341de5f0802069ffb056e08485a2dc8bfe9b97479` |
| website | `20261006T053716Z-a24bfc6-05e602` |
| Runner | `1.1.0`，wheel SHA-256 `6d6be6ef90bd1d935c13578512a81cc261b11738939249a76536f312645a3405` |
| Alembic head | `0019_ci`（仅检查，本次未新增迁移） |
| server / gateway 更新前 Compose | `/dockerdata/devops/deploy/.deploy-backups/test-9q4c4r4t` |
| 最后一次后端更新前 Compose | `/dockerdata/devops/deploy/.deploy-backups/test-ty3o0zvg` |

发布器核对双架构镜像和实际运行版本；现场读取七个服务均 running + healthy，公共 readiness / release 正常。网站继续本机构建独立发布，Registry / PostgreSQL / Redis 镜像及原挂载沿用既有配置。本轮源码未提交、未 Git push；测试仓库内的合成提交属于已授权的 CI 验收。重新核验应读取实际容器、迁移 head、公共端点及下载包校验和，本快照不代替后续部署授权。


## 2026-10-06 CLI CI 命令

- CLI 1.1.0 新增 `workflow list/view/run` 与 `run list/view/watch/cancel/rerun/download`，命令、参数和 JSON 字段按 gh 常用约定提供；原生 SDK 增加类型化 CI 方法，继续使用 DevOps HTTP 路由和 snake_case 数据。没有新增认证、数据库迁移或服务端业务改动。
- 支持工作流名称 / 文件选择、分支、`-f/-F` 与 JSON stdin 输入、带分页的运行筛选、status / conclusion 投影、job 日志、终态等待、取消和整次重跑。产物流式下载并校验大小 / SHA-256，拒绝路径越界、符号链接与覆盖已有文件；取消输出是请求确认，重跑输出新 run URL。
- `pr checks` 改为 gh 的 `--json fields` / `--jq` 和 state / bucket / link 等字段，并支持 required / watch / fail-fast。原 1.0.0 裸 `--json` 的原生数组输出不再保留，可通过 `devops api ... --jq .checks` 获取原生数据。未支持的 gh 参数和字段直接报错，详细范围见 [CLI README](cli/README.md#ci工作流运行与-pr-检查)。当前不支持逐工作流启停、部分重跑、失败步骤日志筛选、数值工作流 ID 或 tag ref。
- 验证通过 CLI 23 项、真实 HTTP / OAuth / CLI 发布器后端专项 21 项、指南 2 项、Ruff、TypeScript / Vite 构建及临时目录 npm 安装。后端测试使用本机隔离 schema、合成账号和邮件替身，覆盖只读 OAuth、仓库读取权限、无关系管理员及授权撤销的拒绝路径。
- 从公开 tarball 安装 CLI 1.1.0，连接测试站与本机 macOS 26.6.2 arm64 Runner 1.1.0，合成仓库 ID 14 完成工作流源码读取、JSON 输入触发 #15、日志、二进制产物校验、筛选、固定快照重跑 #16、主动失败及非零退出 #17、排队取消 #18、PR #1 自动检查 #19。#17 是验收脚本主动 exit 7，用于验证错误传播；#18 的 Windows 标签只用于保持排队，没有执行 Windows 构建。只读 CLI 令牌写请求被拒绝。真机范围仍仅 macOS arm64，本轮 Node.js 为 22.21.1。
- 仅发布 CLI 静态包和网站指南，沿用既有 Gateway 只读静态挂载。CLI `1.1.0.tgz` SHA-256 为 `204dff7976416c8902a72734a0cadf9700062b47df6807a72e3353a9b8e79d4e`；网站 release 为 `20261006T062502Z-a24bfc6-7a4aa9`。发布器核对公网 tarball 校验和、网站 release / 入口 assets 与 API readiness。其他容器发布版本见上一节；本轮未更新镜像、配置或迁移。
- 沿用分支 `codex/devops-bootstrap` 并保留先前 CI 工作区改动；源码未提交、未 Git push。合成仓库内的测试提交属于本轮已授权验收。重新核验应读取公开校验和、`release.json`、readiness 与实际容器状态，本记录不代替未来部署授权。
- 验收结束停止并撤销本机临时 worker，关闭合成仓库 CI，停用合成账号、移除其角色并撤销会话 / grants；本机临时 CLI 安装、Runner venv、源码、配置和工作目录已删除。保留验收仓库、PR 和运行记录，用户的 `test` worker 未改变；未发送真实 SMTP 邮件，未改全局 Git / npm 安装或安装常驻服务。本轮未新增 Safari 视觉验收。


## 2026-10-12 网页会话滑动续期

- 网页会话沿用数据库中的随机原始 Token，`SESSION_DAYS` 默认 7 天；有效网页登录请求按分钟节流更新最近活动和期限，临近到期不足一分钟时立即尝试续期。数据库事务提交成功后在实际响应上续期会话及原 CSRF Cookie，保留 Token 值与多标签页兼容。超过有效期未活动需重新邮箱登录。
- 续期持有会话行锁并在等待后重查到期、撤销、账号状态和密码版本；退出不会重新发放 Cookie，失效会话不能恢复。无需新增迁移，已有有效会话可直接受益。Git / CLI OAuth 仍在成功刷新时续期至本次刷新后 30 天，网页活动不会替代客户端刷新。
- 新增 `test_session_renewal.py` 的 18 个用例，覆盖跨原到期日连续续期、分钟节流 / 临期、Cookie 属性及缓存 CSRF、过期 / 吊销 / 停用 / 版本失配、无权限与公开接口、退出、提交失败、直接 Response、并发续期和等待锁期间失效。先运行认证相关 86 项，再运行完整 `make -C . check`：后端 348、CLI 23、Runner 13、前端 154 项全部通过；Ruff、Alembic check、类型检查和生产构建通过。
- 测试使用本机独立 Compose 项目 `devops-session-20261012`、随机 schema / Redis 前缀与邮件替身，保留原本停止的本地容器及其数据。本次未发送真实邮件或用真实账号做失效 / 吊销试验。6 份修改文档的 185 个本地链接及 `git diff --check` 已核对。
- 发布：`./deploy.sh server` 完成双架构构建 / 镜像推送并更新测试站 server、worker、registry-gc；运行镜像为 `devops.kevinc.ltd/core/devops:test-d51132e6-dirty-20261012T140513Z-61e321@sha256:0936e959c3101455468db699d460e7ed7900f59899803dc000d5435ce21ea735`。发布器核对原生 amd64 平台 / 镜像，全部 7 个容器 running + healthy，公网 readiness 返回数据库 / Redis connected；`dependencies.py`、`security.py`、`main.py` 的运行版本 SHA-256 与本地测试代码一致。Gateway、PostgreSQL、Redis、Registry 原容器 ID 保持不变；无迁移、业务配置或前端发布，数据库仍为 `0019_ci`。
- Safari 桌面沿用已有登录：登录时间保持 2026-10-12 21:55，到期时间从 2026-10-19 21:55 依次变为 22:10、22:11（Asia/Shanghai）。个人资料页导航及不改变字段值的保存操作成功，随后回到账号安全页；未退出或吊销真实用户会话 / OAuth，未发送验证码。跨 7 天和各类失效场景由隔离测试覆盖，没有等待真实 7 天。
- 验收后清理本次创建的独立本地 Compose 容器 / 数据卷及原本不存在的 `.env`，保留原有停止的本地容器 / 数据。沿用 `codex/devops-bootstrap`；部署验收时源码尚未提交或 Git push，保留无关的未跟踪 `information_aggregation/`。
- Issue #22 正文已按当前实现更新并逐字读回核对，标题及 OPEN 状态保持不变。补充网页滑动续期、Git / CLI 授权刷新续期、Issue / PR / 原生合并、Registry / GC、CLI / SDK、CI Runner / 日志 / 产物及管理功能；保留完整分支历史、网页分支操作、SSH / OIDC、CI 隔离 / Secrets 等实际未完成项。

## 2026-11-15：迁移到 kdevops 与 devops.kevinc.ltd

- 仓库从旧 monorepo 的 `devops/` 模块独立；工作副本 `/Users/czf/Desktop/utils/devops`，本次改动尚未提交。
- 部署主机 `ssh kdevops`（Debian 13 x64），站点 `https://devops.kevinc.ltd`；证书来自 `/dockerdata/gateway/cer/kevinc.pem` 与 `kevinc.key`（CN=kevinc.ltd，SAN `*.kevinc.ltd`）。
- 连接信息：阿里云 RDS PostgreSQL 18.6（`devops` 库）与 Tair（Redis 7.0 兼容，DB 0）；Compose 不再包含数据库容器。部署目录 `/dockerdata/devops/deploy`，Git 仓库 `/dockerdata/devops/git-repos`，Registry 数据 `/dockerdata/devops/registry`，网站与 `/res/` 在 `/dockerdata/gateway/website`。
- 镜像不再经过 `nas.kevinc.ltd:30009`：本机 `buildx --platform linux/amd64` 构建，`docker save` → SSH → `docker load`，两端 image ID 一致；Gateway 基础镜像改为 Docker Hub `nginx:1.29-alpine`，Brotli 由构建阶段编译的 `ngx_brotli` 动态模块提供。
- 本次发布：server / worker / registry-gc = `devops.kevinc.ltd/core/devops:48d6acf-dirty-20261115T140024Z`；gateway = `devops.kevinc.ltd/core/devops-gateway:48d6acf-dirty-20261115T140024Z`；registry = `registry:3.1.2`；网站 release `20261115T140215Z-48d6acf-fa6e0e`；CLI 1.1.0、Runner 1.1.0 已发布。
- 运行时配置：`production`、`ALLOWED_ORIGINS=https://devops.kevinc.ltd`、注册关闭、Git OAuth 与 Registry 打开、SMTP `smtp.exmail.qq.com:465`（`ssl`）、Cookie `devops_session` / `devops_csrf`；管理员 `admin` 绑定 `1354016594@qq.com`。
- 验收：5 个容器 running + healthy；公网 HTTPS 返回 200（HTTP/2），HTTP/3 QUIC 握手与证书校验通过；`/api/health/ready` 返回数据库与 Redis connected；网站资源与 `/res/` 下载的 SHA-256 一致；SMTP 账号登录成功但未发送真实验证码邮件。
- 已知边界：旧 registry 的历史镜像未迁移；本文件早前段落仍是旧系统时期的快照，未逐条改写。
- 宿主机 `/etc/docker/daemon.json` 关闭 `userland-proxy` 并给 json-file 日志加上轮转：之前发布端口经 Docker 用户态代理转发，源地址被改写成网桥网关 `172.18.0.1`，登录 IP 记错；关闭后 iptables DNAT 保留真实源地址，Nginx 仍按原样转发 `X-Forwarded-For` / `X-Real-IP`。修复前产生的旧会话记录仍是 `172.18.0.1`，重新登录即可。
- 内部标识统一改为 `devops` 前缀：Nginx upstream / 变量 / 配置文件、Git 保护环境变量、镜像内路径、Cookie、OAuth client、密码 AAD、Redis 限流前缀、测试命名空间、发布临时目录；仓库与线上资源中已无旧品牌字样。
- Gateway 改回 host 网络（`network_mode: host`）：nginx 直接绑定宿主机 TCP / UDP 443 的 IPv4 与 IPv6（`listen [::]:443 ssl` / `listen [::]:443 quic`），不再经过 Docker 端口发布，`$remote_addr` 就是真实客户端（v4 与 v6 都验证过）。server 只发布 `127.0.0.1:8000`，registry 只发布 `127.0.0.1:5000`，公网仍不可直连；worker 改为显式定义，避免通过 `extends` 继承 8000 端口造成冲突。nginx upstream 改为 `127.0.0.1`，移除 Docker DNS resolver。
- IPv6 验收：域名 AAAA 指向实例 `/128`，公网 IPv6 的 TCP / UDP 443 均可达；HTTP/2 与 HTTP/3 在 v4 / v6 下都返回 200，证书校验通过。注意该 IPv6 是 SLAAC 动态地址，实例重建或地址变化后需要同步 AAAA 记录。
