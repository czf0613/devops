# Docker Registry

官方 CNCF Distribution 保存镜像；现有 Gateway 的 `/v2/` 接收 Docker 请求，每次通过后端查询数据库中的专用令牌、账号、repo ACL 和 namespace 归属。没有 JWT 权限快照或权限缓存，不需要 DevOps CLI 登录。

## 使用

1. 打开 **工作台 → 镜像管理** 查看本人参与的 repo 关联的全部 namespace，按名称、仓库和绑定状态筛选。拥有者可选择自己的未归档 repo 创建 namespace；也可在 repo 的 **Registry** 页签创建、绑定或转移 namespace。
2. 成员在 **账号安全 → Docker 登录令牌** 创建用户级令牌，选择只读或读写上限以及有效期。一次登录即可访问有权限的所有 repo，无需随项目切换令牌；令牌只显示一次。网站“使用指南”提供完整登录、推送 / 拉取、双平台构建和 CI 示例。
3. 执行页面提供的原生登录命令，在 Password 提示中输入令牌：

```sh
docker login devops.example.com -u example_user
docker tag local-image:latest devops.example.com/example-team/app:v1
docker push devops.example.com/example-team/app:v1
docker pull devops.example.com/example-team/app:v1
```

CI 使用秘密管理系统提供令牌文件，避免写入命令参数、仓库或日志：

```sh
docker login "$REGISTRY_HOST" --username "$DEVOPS_USERNAME" --password-stdin < /run/secrets/docker_registry_token
```

Docker 令牌只关联用户，不接受指定 repo 的创建参数。项目尚未上线，`0014_registry_gc` 升级时直接删除旧项目限定令牌及其范围字段，保留已有用户级令牌。被删除的旧令牌需要在账号安全重新创建；不会自动扩权。

令牌默认 90 天，可选择 1–365 天（网页提供 30 / 90 / 365 天）或永久有效。永久令牌没有到期时间，创建前显示泄露风险并要求明确确认；它仍受撤销、账号状态及当前 repo ACL 限制。每账号最多 50 个有效令牌，永久令牌计入限额。服务端只保存高熵随机令牌的 SHA-256 摘要。API 通过 `days: null` 和 `acknowledge_permanent: true` 创建永久令牌，返回 `expires_at: null`；遗漏 `days` 仍为 90 天。迁移 `0018_registry_permanent_tokens` 保留已有令牌到期时间，存在永久令牌记录时拒绝降级。Docker 自行管理本地凭据；`docker logout` 清除本地登录，网站撤销令牌使全部使用它的客户端在下一次请求失效。退出网页登录不撤销 Docker 令牌。

## 权限与管理

| 操作 | 需要的当前权限 |
|---|---|
| 列出镜像 / 标签、pull | repo 拥有者或成员；令牌属于当前用户 |
| push、上传分块、取消上传 | repo 写权限且未归档，令牌上限为读写 |
| 跨镜像复用 blob（mount） | 目标镜像写权限，同时具有源镜像读权限 |
| 创建、解绑、重新绑定 namespace | repo 拥有者且未归档 |
| 转移 namespace | 同时拥有来源和目标 repo，两者均未删除且未归档 |
| 系统级查询、解绑 / 重新分配 namespace | 全局 admin，通过系统管理的 Registry 管理入口；修改前验证本人邮箱，目标 repo 未删除且未归档 |
| 删除 namespace 及其全部镜像 | 全局 admin，单次邮箱验证码；原子关闭访问，维护窗口 GC 清理全部镜像 |
| 删除 manifest digest | repo 拥有者且未归档；直接 Docker API 还要求读写令牌 |

mount 的来源无权访问、已解绑或删除时，不读取或关联来源 blob；只要目标仍可写，Gateway 会去除 mount / from 参数，按普通上传返回 202，让 Docker / Buildx 上传本地持有的内容。这样旧构建缓存不会因来源收权而阻断向其他有权 namespace 推送。行为依据 [OCI mount 回退约定](https://github.com/opencontainers/distribution-spec/blob/main/spec.md#mounting-a-blob-from-another-repository)；来源直接读取和目标越权写入仍拒绝。

全局 admin 可显式管理 namespace 归属，但不隐式获得镜像读写或 repo 内容访问权。岗位 role、repo `is_admin` 不额外授予镜像权限。归档仅可读，软删除拒绝读写，停用账号拒绝其全部 Docker 请求。Docker API 不接受 Cookie、Git / CLI OAuth Token 或旧系统兼容密码。

一个 repo 可关联多个 namespace，每个 namespace 同时归属一个 repo。namespace 全站唯一，由小写字母 / 数字 / 中间短横线组成，最多 63 字符；镜像名为 `namespace/image`，image 可包含多层路径。解绑保留数据、归属和名称，其他用户不能抢注同名 namespace 获取旧镜像。源 repo 软删除后仍保留名称。**系统管理 → Registry 管理** 可筛选已绑定 / 未绑定（含原 repo 已删除），按 namespace / repo 搜索，在确认成员权限影响后解绑或分配到其他 repo。未绑定时展示上次归属，普通用户不能抢占；管理员操作有审计记录，并通过版本号检测并发修改。管理入口仅列出用于归属选择的 repo 名称，不返回其内容或成员。

系统 Registry 管理中的绑定、解绑、删除和 GC 每次都需要当前管理员的 `admin_operation` 邮箱验证码，不能复用登录验证码或此前已消费的验证码。只读列表、筛选和查询不要求验证码。

**删除 namespace 包含其中全部镜像**，已绑定的 namespace 会同时解绑。提交时先原子关闭访问；名称与删除记录永久保留，不能重新绑定或同名抢注，Docker 后续请求立即拒绝。页面的“已删除 / 清理记录”显示待 GC 或已清理。物理数据在下一次人工维护窗口 GC 时处理，而不是在浏览器请求内逐个清空；已放行的上传可能完成，维护人员仍须等待在途写入结束。GC 失败保留待清理状态，排查后重新验证邮箱并发起任务。

页面支持查看镜像、标签、digest、多平台索引的平台信息和复制按 digest 拉取命令。删除针对整个 manifest：**所有指向同一 digest 的标签都会删除**，引用它的多平台索引可能无法拉取。目前不支持只删一个别名标签，删除操作不直接执行物理垃圾回收；管理员可在人工维护窗口手动发起 GC。列表每页 20 项，目录 / 标签扫描上限各 5000 项，超限报错，不返回伪完整列表。

内部 catalog 分页沿用官方返回的最后条目作游标；文件系统存储按目录层级遍历，不假定完整镜像名称的字典序。分页重复项或循环视为失败，不能为了继续 GC 返回截断目录。

权限变更提交后，新请求即使用新权限，不要求重新登录。已通过鉴权的单次传输可能完成，无法收回已下载的数据；上传分块、完成请求和后续下载分别鉴权。本版不保证中途强制断流。

个人镜像管理与 repo Registry 页签复用镜像 / 标签浏览、按 digest 删除和创建 namespace 表单；只读成员可以查看和复制拉取命令，只有拥有者可修改，归档时禁止修改。未绑定 namespace 展示原归属但不能读取镜像，拥有者重新绑定后恢复访问。

namespace 名称保留是应用的保护策略：现有 GC 按名称路径重复清理已删除 namespace，且删除前已授权的在途上传可能稍后结束；立即同名重建会混淆旧数据与新镜像。即使存储已清理，旧 CI / 部署配置仍会继续使用同一地址。因此本版不自动释放名称，若以后开放需独立设计维护窗口内的显式释放及重建流程。

## 双平台镜像

使用同一个用户令牌和原生 Docker Buildx：

```sh
docker buildx build --platform linux/amd64,linux/arm64 \
  -t devops.example.com/example-team/app:v1 --push .
docker buildx imagetools inspect devops.example.com/example-team/app:v1
docker pull --platform linux/amd64 devops.example.com/example-team/app:v1
docker pull --platform linux/arm64 devops.example.com/example-team/app:v1
```

Gateway 同样逐请求检查多平台索引、子 manifest 和 blob 的权限。网页将 amd64 / arm64 与 Buildx 的构建证明分别展示，不把证明记录的 `unknown/unknown` 当作运行平台。Buildx 的默认证明行为见 [Docker 官方文档](https://docs.docker.com/build/metadata/attestations/)。

## 手动空间回收

全局管理员在 **系统管理 → Registry 管理 → 空间回收** 发起任务。此版不自动停止写入、不切换只读状态：维护人员须提前暂停 CI 构建推送、所有 `docker push` 与网页 / API 镜像删除，确认在途写请求已经结束，并保持到任务结束。确认弹窗说明并发写入可能损坏镜像、清理范围及不可撤销性；输入当前管理员邮箱验证码并确认维护窗口后才可提交。

任务先通过固定 Distribution 3.1.2 的内部 DELETE tag API 清理所有已删除 namespace 的标签；按完整 namespace 前缀匹配，复查目录和标签，有异常则停止。随后执行 `garbage-collect --delete-untagged`，先 `--dry-run` 预检，再正式回收。其他 namespace 保留有标签的镜像及其引用链（含 amd64 / arm64 子镜像、索引引用的构建证明和共享层）；允许回收无标签且不再被保留镜像引用的旧版本。只通过 digest 使用的历史版本需要保留一个标签。namespace 是否绑定不参与 GC 判定，解绑不会自动删除其中的有标签镜像；管理员明确删除的 namespace 才会清空所有标签。只删除标签根，不直接删除全局 blob，共享层由官方 GC 判断是否仍被使用。仅整个 GC 成功后才标记 namespace 已清理；每次手动 GC 复查全部删除记录，处理删除前可能已放行的迟到上传。

页面显示排队 / 预检 / 回收 / 完成 / 失败、候选数量、执行时间与前后存储占用，离开页面不终止任务。占用使用数据目录 `du -sk`，反映已分配文件空间变化，不等同于整个磁盘的可用容量（例如存储快照仍可能占空间）。共享层不会重复回收。

- 只有浏览器全局管理员、CSRF 及单次邮箱验证通过后可以提交；执行前重新检查发起人会话和 admin 权限。全局管理仍不提供私有镜像读取旁路。
- 同时只允许一个任务；确认后 60 秒未启动则取消，避免迟到的任务越过维护窗口。中断 / 失败不自动重跑，可能已有部分数据被回收，须排查后重新确认窗口。
- 独立 `registry-gc` 进程使用同一 server 镜像和官方 Registry 二进制，仅挂载 Registry 数据及只读配置，没有公开端口、Docker socket 或 SSH 凭据。存储以 root 身份访问，容器根文件系统只读、丢弃 capabilities；Web server 不挂载镜像数据。
- 数据目录父级 `.gc.lock` 在整个任务期间持有，并传给执行子进程；部署器共用该锁，避免后台回收与 server / Registry 发布并发。进程异常退出后先等待存量执行进程退出，再将中断记录标为失败。
- 未就绪时不能提交新任务。排查 `registry-gc` 的健康状态、数据库连接、固定配置和数据挂载；不要通过直接删除 blob 目录修复。

标签清理依据：[固定 3.1.2 官方 DELETE tag 实现](https://github.com/distribution/distribution/blob/v3.1.2/registry/handlers/manifests.go#L405-L435)。此内部维护能力不扩大面向 Docker 客户端的 Gateway 白名单。

协议依据：[官方 GC](https://distribution.github.io/distribution/about/garbage-collection/)、[固定 3.1.2 引用遍历实现](https://github.com/distribution/distribution/blob/v3.1.2/registry/storage/garbagecollect.go)。官方要求 GC 期间无写入；本版由人工维护窗口落实。

## 部署与存储

首次启用测试环境：

```sh
./deploy.sh server gateway registry website --migrate
```

`registry` 必须与 `server` 一起选择。部署器安装已知的私有服务结构及本目录配置，通过 stdin 将数据库 `REGISTRY_ENABLED` 设为 true，后端 / worker / registry-gc 随新镜像重启。非标准 Registry 挂载或暴露端口会被拒绝，须先核对，不盲目覆盖。

- 官方镜像版本 / 索引摘要固定在 `compose.deploy.yaml` 和 `scripts/deploy.py`，支持 arm64 / amd64；发布快照与核验方式集中在根目录 `CONTEXT.md`。
- Registry 不发布宿主机端口，仅在受信任的 Compose 内部网络提供 `registry:5000`；不能通过其他入口暴露它或存储，否则会绕过 ACL。
- 数据：`/dockerdata/devops/registry/data`。上传状态签名密钥：父目录 `.http-secret`，首次启动生成并设置 0600，随数据备份。它不是用户凭据，不写 `.env`。仅支持单实例文件系统存储。
- 业务配置仍在 PG `config`。`REGISTRY_INTERNAL_URL` 默认 `http://registry:5000`，用于后端元数据访问；Gateway upstream 保持同一目标。客户端不能指定上游。
- 禁止存储重定向，上传 Location 使用相对路径；镜像数据经 Nginx 流式转发，不经 FastAPI。单次请求上限 5 GiB，传输空闲超时 900 秒。鉴权不可用时拒绝访问。
- Registry 访问日志关闭，Gateway 不记录查询参数、Authorization 或 Cookie。后端记录凭据 / namespace / manifest 管理及写请求授权；授权事件不等于存储已完成写入。
- PG 与 Registry 文件系统不是同一事务；操作失败可刷新核对。恢复须兼顾 namespace 归属与镜像存储，删除 manifest 不表示磁盘空间已释放。

## 验证

后端：`server/tests/test_registry.py`、`test_registry_gc.py`、`test_migrations.py`、`test_deploy.py`；前端：`website/tests/registry.test.ts`、`registry-gc.test.ts`。真实网关复用 `scripts/verify_git_gateway.py`，其中 `verify_registry.py` 验证 OCI 上传 / 拉取、metadata、mount 双侧权限、解绑 / 重新绑定、归档只读、按 digest 删除和同令牌即时撤销；`verify_registry_gc.py` 还通过真实后台任务验证物理回收、双平台引用、共享层、标签别名、构建证明及解绑 namespace 保留。使用临时 schema / 容器及合成数据，结束清理。

依据：[Distribution HTTP API](https://distribution.github.io/distribution/spec/api/)、[Registry 配置](https://distribution.github.io/distribution/about/configuration/)、[Nginx 子请求鉴权](https://docs.nginx.com/nginx/admin-guide/security-controls/configuring-subrequest-authentication/)。
