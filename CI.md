# CI

DevOps server 保存流水线、调度、授权与结果；独立 [Runner](runner/README.md) 在构建机上执行。现有 Docker 中的 collaboration worker 继续处理推送回执、PR 检查、合并与 CI 超时/保留期清理。两个 worker 的职责不同。

## 开始使用

1. 管理员添加专用构建机并签发一次性注册令牌；Runner 主动通过 WSS 注册、上报环境、领取任务。每台同时执行一个 Job。
2. 仓库默认启用 CI，使用 `default` 资源池，即使暂时没有在线构建机也可排队。已经明确关闭的仓库保持关闭。拥有者在 **Settings → CI / Actions** 调整开关和资源池，不需要在运行页面反复授权。
3. 将 GitHub Actions 格式的 `.yml` / `.yaml` 文件放入 `.github/workflows/`。每个文件独立显示、触发和记录；CI 左侧选择工作流，右侧查看状态、分支与记录。`workflow_dispatch` 工作流提供分支和输入表单。
4. Code 页与 PR 页显示当前完整 SHA 的检查摘要，可展开进入各工作流日志。没有检查、排队和失败均不会显示为通过。

默认资源池是内部可信构建机的共享执行范围，不提供恶意脚本隔离。全局管理员仍不能读取未加入的私有仓库；只有仓库拥有者可以调整资源池，运行仍要求写权限。已显式配置的资源池在所有权移交后失效，新拥有者须重新保存设置；从未单独配置的仓库继续使用平台默认规则。归档、删除、停用用户或撤销写权限会拒绝新调度并取消执行。

## GitHub 工作流格式

例如 `.github/workflows/checks.yml`：

```yaml
name: Build and test
on:
  push:
    branches: [main, 'feature/**']
  pull_request:
    branches: [main]
  workflow_dispatch:
jobs:
  build:
    runs-on: [self-hosted, macos, arm64, 'macos>=15']
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
        with:
          lfs: true
          fetch-depth: 0
      - name: Build
        run: |
          mkdir -p dist
          echo 'Build passed' > dist/result.txt
      - uses: actions/upload-artifact@v4
        with:
          name: build
          path: dist/
  verify:
    needs: build
    runs-on: [self-hosted, macos, arm64]
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: build
          path: artifacts
      - run: cat artifacts/result.txt
```

遵循 [GitHub 工作流语法](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax) 的字段命名与目录约定；以下是当前已实现的执行范围，不是完整 GitHub Actions 服务的替代实现：

| 功能 | 当前支持 |
|---|---|
| 工作流 | 每提交最多 30 个文件；单文件 128 KiB；name、on、env、defaults.run |
| 触发 | push、pull_request、workflow_dispatch；分支包含 / 排除过滤；PR 过滤目标分支；手动输入 string / boolean / number / choice |
| 任务 | runs-on 字符串 / 标签列表 / group + labels；needs；timeout-minutes；静态 strategy.matrix（include / exclude / fail-fast） |
| 步骤 | name、id、run、shell、working-directory、env、continue-on-error；if 为 success() / failure() / always() / cancelled() |
| 表达式 | matrix、inputs 字段替换；步骤可读取 github、runner、env、steps 的字段；GITHUB_ENV / GITHUB_PATH / GITHUB_OUTPUT 文件命令 |
| 内置 Actions | actions/checkout、actions/upload-artifact、actions/download-artifact 的常用参数；使用 DevOps 的任务凭据、源码与产物服务 |
| 尚未实现 | 其他 JavaScript / composite / Docker Actions、setup-*、复杂表达式、Secrets、缓存、services / container、reusable workflows、schedule、concurrency、job outputs、paths / tags 过滤和更多 PR 事件类型 |

不支持的字段或 Action 会产生可见配置失败，不会忽略后伪报成功。内置 Actions 不下载或执行 Marketplace 仓库代码，不调用 GitHub 产物 API；`checkout` 只检出当前固定 SHA，不支持其他仓库、submodules、保存凭据或任意 ref。使用 GitHub 托管镜像名称也不会自动安装该镜像中的工具链，管理员需在专用机准备依赖。`permissions` 仅允许 read / none，不会产生 GitHub Token 或 DevOps 写凭据。

最多 20 个展开后的 Job、每 Job 50 步；默认超时 30 分钟（1–240）。任务依赖全部成功才执行，矩阵默认 fail-fast；设 `strategy.fail-fast: false` 可继续其他矩阵任务。取消会终止原生进程树，不保证执行 always() 清理步骤，应由执行器负责清理临时环境。YAML 使用 1.2 解析，拒绝重复键、引用 / 锚点、自定义 tag 和超深嵌套。工作流和步骤变量不能覆盖 GIT_ / DEVOPS_ / GITHUB_ / RUNNER_ 内部前缀。工作目录和产物路径必须位于本次工作区，排除链接及 `.git`。

GitHub 模式只有执行 `actions/checkout` 时才获取源码；每步独立 shell，默认 macOS/Linux 为 bash，Windows 为 powershell，也可指定 pwsh / sh。产物仅允许下载 `needs` 中已成功任务的文件，`with.name` 选择产物组，`with.path` 决定下载位置。上传支持 `name`、`path`、`if-no-files-found`；下载支持 `name`、`path`。源码 checkout 支持 `fetch-depth`、`lfs`、`persist-credentials: false`。

历史 `.devops/ci.yml` 仅在提交没有 `.github/workflows` 文件时作为兼容入口，继续使用原来的 version / environment / artifacts 格式；无需改写已完成记录。重跑始终使用原 SHA 和原配置快照。

## 构建机环境与匹配

Runner 1.1.0 上报系统版本、CPU 架构、内核、Linux 发行版及 libc；缺失信息显示未知，不猜测 glibc。旧版 worker 可以继续执行旧格式任务，但不能领取新的 GitHub 格式任务。

| 要求 | runs-on 示例 / 语义 |
|---|---|
| 默认环境 | `[self-hosted]` 使用 default 池内任意支持的 64 位构建机 |
| 系统与架构 | `[self-hosted, macos, arm64]`；仅 x64 / arm64，拒绝 32 位 |
| 最低系统版本 | `[macos, 'macos>=15']`，可匹配 macOS 15、26 等；不推断产物的最低运行系统 |
| 指定系统版本 | `macos-15` 匹配 15.x；`'macos=15.7'` 匹配 15.7.x；`macos-15-intel` 指定 x64 |
| Linux 发行版 | `ubuntu-24.04` 指定 Ubuntu 24.04 x64；后缀 `-arm` 指定 arm64 |
| Linux 内核 / libc | `[linux, x64, 'kernel>=5.15', 'glibc>=2.31']`；`musl` / `glibc` 严格分开，也可指定 `'musl>=1.2'` |
| Windows | `windows-10` / `windows-11` 区分客户端；`windows-2019` / `windows-2022` / `windows-2025` 匹配对应 Server 内核 build，不混同 NT 10.0 的所有系统 |
| 自定义资源池 | `runs-on: { group: build-pool, labels: [self-hosted, linux, x64] }`，须在仓库 Settings 中选中该池 |

不带版本的系统标签不限制版本；`macos-latest` / `ubuntu-latest` / `windows-latest` 表示满足系统类别的本机 worker，而非请求云端镜像（windows-latest 要求 NT 10.0.17763 及以上）。精确标签按所写版本前缀匹配，最低版本按数字段比较；未上报版本的 worker 不满足版本约束。自定义工具链标签仍须全部匹配。

构建宿主满足条件，不代表二进制一定能在更新系统运行。macOS 的 deployment target、SDK 和架构，Linux 构建使用的 libc 基线，Windows SDK / runtime 都应在项目构建参数和目标系统测试中确定；CI 不自动修改这些参数。

## 状态与持久化

Run 保存事件、完整提交 SHA、触发者与不可变配置；Job 保存依赖及运行要求；Attempt 保存机器、租约、步骤、日志序号和最终结果。调度通过 PostgreSQL 事务和锁协调多个 server 实例；每台机器和每个 Job 最多一个活跃 Attempt。

WebSocket v1 使用 hello/welcome、heartbeat、job.offer/accept/start/started、job.log/step/complete、ack/log.ack、job.cancel/finished。服务器在事务提交后才发送 ACK 或开始消息。worker 以落盘 outbox 重试；日志以 Attempt + sequence 去重；连接 generation 拒绝旧连接。正常心跳间隔 10 秒，租约 90 秒。已开始任务的 worker 崩溃、租约到期或身份撤销会变成 lost，由成员明确重跑，避免重复产生发布等外部副作用。

取消是持久请求，服务器通过 WebSocket 推送；离线 worker 因租约确认超时自行停止。原生模式不构成对恶意脚本的隔离保证。多个 worker 可跨操作系统调度；当前真机验证仅 macOS arm64。

任务持有现有可吊销 Git OAuth 体系下的特殊 `devops-ci` grant，限定一个仓库与一个执行时段，仅 git:read。Git/LFS 和产物请求同时检查活跃任务、worker、仓库池授权及实时 ACL，不能借此访问通用 CLI/管理接口。worker 长期令牌不能直接读仓库。

PR 在当前 head SHA 上生成保留名称 `ci/<workflow-file>/<job>`（旧格式仍为 `ci/<job>`） 的检查，成功、失败、取消均回写。拥有者可在现有 merge policy 中将其设为 required check；不能通过手工检查接口写入 ci/ 名称。新 SHA 的结果独立，重跑后旧任务结果不能覆盖新检查。首版仅同仓库 PR。

日志每次执行 10 MiB，单个产物 256 MiB，每次合计 1 GiB / 100 个；日志与产物保留 30 天。文件位于既有 Git 持久卷的 `.ci-data/<attempt-id>/`，到期由后台清理，运行元数据继续保留。`refs/devops/ci/<run-id>` 固定源提交以支持后续重跑。备份数据库与整个 Git 持久卷；不要只备份 bare repo 文件夹。暂未提供 CI 历史清理或存储配额面板。

## 验证与发布

CLI 1.1.0 提供 gh 风格的 `devops workflow list/view/run`、`devops run list/view/watch/cancel/rerun/download` 和 `devops pr checks`；复用现有 CLI OAuth 与仓库 ACL，支持工作流输入、JSON 查询、日志和校验后的产物下载。安装、示例与兼容边界见 [CLI README](cli/README.md#ci工作流运行与-pr-检查)。

```sh
uv run --project server pytest server/tests/test_ci.py
uv run --project runner pytest runner/tests
make -C . check
uv run --project server python scripts/deploy_runner.py build
./deploy.sh server gateway runner website --migrate
```

真实 HTTPS Gateway 验证复用 [集成脚本](scripts/README.md#真实-git--gateway-集成验证)，并启动本机 Runner；需先 `uv sync --project runner --locked`。测试包含合成仓库、push/manual、固定 SHA 与 LFS 检出、步骤日志、依赖产物、取消及崩溃后 lost，不连接真实 SMTP。

runner 独立 wheel、版本与锁文件；server/worker 共用服务镜像，Gateway 只转发 WSS / HTTP 并提供 wheel 静态下载，网站仍单独构建发布。迁移 `0019_ci` 保留既有认证和协作数据；存在 CI worker 或运行记录时拒绝破坏性降级。开发或测试部署授权按当前会话判断，不能从本文推断以后允许操作服务器。
