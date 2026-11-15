# DevOps CLI 1.1.0

通过浏览器授权后管理 DevOps 仓库、Issue、Pull request 和 CI。命令入口为 `devops`，同时提供 TypeScript SDK。源码仓库可以保持私有；用户只需下载公开的 npm tarball。

## 安装

需要 Node.js 22.12+，推荐 Node.js 24 LTS。无需 GitHub/npm 账号或源码仓库权限。

```sh
npm install --global https://devops.kevinc.ltd/res/devops-cli/1.1.0.tgz
devops --version
devops auth login --web
devops auth status
```

运行依赖来自 npm 公共源。下载包同时提供 `1.1.0.tgz.sha256`。如全局目录不可写，请用 Node.js 版本管理器配置用户目录；无需更改网站权限。升级时安装新版本 URL；卸载用 `npm uninstall --global @kevinc/devops-cli`。

## 服务地址与仓库

默认地址为 `https://devops.kevinc.ltd`。服务地址必须是 HTTPS origin，支持端口；仅本机 loopback 允许 HTTP，不能包含子路径、用户名或密码。

```sh
devops auth login --hostname https://example.com:8443 --web
devops issue list --hostname https://example.com:8443 -R owner/repo
devops repo set-default https://example.com:8443/git/owner/repo.git
```

地址优先级：显式仓库 URL / HOST/OWNER/REPO 中的地址（若与显式 hostname 冲突则拒绝）、`--hostname`、`DEVOPS_HOST`、最近登录保存的地址、默认地址。仓库优先级：命令参数、`-R`、`DEVOPS_REPO`、本地 `git config devops.repo`、唯一的 DevOps HTTPS Git remote。多个不同仓库 remote 时必须指定，避免操作错误仓库。

## 授权与 Agent

- `auth login --web`：S256 PKCE + 随机 state + 本地 127.0.0.1 临时端口回调。`--no-browser` 仅打印授权 URL，便于手动选择浏览器。本站只接受邮箱验证码登录。
- `auth login --device`：远端终端显示设备代码，在另一台设备访问授权网页并核对。
- 默认存入 macOS Keychain、Windows Credential Manager 或 Linux Secret Service。Linux 需要可用的 Secret Service；不可用时不会自动降级。
- 无凭据库的环境可**显式**使用 `auth login --device --insecure-storage`，凭据保存为权限 0600 的明文文件。Windows 文件访问权限由用户目录 ACL 控制。
- `auth login --scopes api:read` 可创建只读授权。默认权限为 `api:read api:write`，仓库 ACL 始终生效，全局 admin 不隐式拥有私有仓库权限。
- `auth switch --user alice` 切换同一服务已保存的账号；`auth refresh` 轮换当前凭据。不同地址（包括端口）分别保存。
- `auth logout` 仅删除本地凭据；`auth revoke` 撤销服务端授权并删除本地凭据；网页「账号安全」也可撤销。
- 自动化可以设置 `DEVOPS_TOKEN` 和 `DEVOPS_HOST`。环境令牌仅发送到匹配的 origin，覆盖已保存凭据且不自动刷新。通过安全的环境变量注入，勿写入命令参数或日志。
- `auth login --with-token` 从 stdin 接收现有 CLI access token，不具备 refresh token；到期后需重新登录。`auth token` 显式输出秘密令牌，慎用。
- 配置默认在 `$XDG_CONFIG_HOME/devops` 或 `~/.config/devops`；`DEVOPS_CONFIG_DIR` 可覆盖。不要把该目录提交到 Git。
- access token 有效 1 小时；授权刷新后续期至 30 天。跨进程锁串行轮换；网络结果不确定时标记并要求重新登录，绝不重放旧 refresh token。
- CLI 授权与 Git/GCM 授权独立。clone/checkout 调用系统 Git，通过 GCM 获取 Git 授权，不把 API token 伪装成 Git 密码。

GitHub Desktop：在 macOS Settings / Preferences → Advanced 或 Windows File → Options → Advanced 勾选 **Use Git Credential Manager**，随后运行：

```sh
devops auth setup-git --desktop
```

普通终端 Git 已安装 GCM 时运行 `devops auth setup-git`。只写当前服务的 `credential.<origin>.*` 全局配置；`--desktop` 保留 Desktop 自带 helper。

## 命令与输出

```sh
devops repo list --limit 100 --json nameWithOwner,url
devops repo view owner/repo --web
devops repo create example --private --description "Example"
devops issue create -R owner/repo --title "Bug" --body-file issue.md
devops issue list -R owner/repo --state open --json number,title,state,author
devops issue view 12 -R owner/repo --comments
devops issue edit 12 -R owner/repo --body-file -
devops issue comment 12 -R owner/repo --body "Confirmed"
devops issue close 12 -R owner/repo --reason "not planned"
devops pr create -R owner/repo --head feature --base main --title "Feature" --body-file pr.md
devops pr diff 13 -R owner/repo
devops pr review 13 -R owner/repo --approve
devops pr checks 13 -R owner/repo
devops pr merge 13 -R owner/repo --squash --match-head-commit FULL_SHA
devops api /api/repositories --paginate --slurp
```

首版提供：

| 类别 | 命令 |
|---|---|
| auth | login、status、refresh、switch、token、logout、revoke、setup-git |
| repo | list、view、create、clone、edit（描述）、archive、unarchive、delete（软删除）、set-default |
| issue | list、view、create、edit、comment、close、reopen、lock、unlock、delete |
| pr | list、view、create、edit（通用字段）、comment、close、reopen、lock、unlock、ready、review、checks、diff、checkout、merge |
| workflow | list / ls、view、run |
| run | list / ls、view、watch、cancel、rerun、download |
| api | 原生 API 请求、JSON 参数/stdin、分页、jq |

兼容 gh 的常用命令名、参数、`-R`、`--body-file`、`--json field1,field2`、`--jq`，JSON 使用 camelCase 和 OPEN/CLOSED/MERGED。用于输出的 `--json` 不带字段可离线列出当前命令支持的字段。普通输出供人阅读，进度/错误在 stderr，JSON 在 stdout。列表自动分页至 `--limit`。不保证 gh 的全部命令与 GraphQL schema；不支持的选项明确报错，`--template`、release、fork 等尚未实现。`--jq` 使用系统 jq。`api` 和 SDK 返回原生 DevOps snake_case 字段。

编辑提交读取到的 version，409 不重试覆盖。合并提交 base/head SHA，并等待 worker 完成；超时返回任务编号和退出码 8，不能当作合并成功。checkout 创建新本地分支，不覆盖已有分支。归档/删除需 `--yes`。

退出码：0 成功；1 普通失败；2 取消授权；4 需要认证；8 检查/合并仍在等待。stdout 管道被下游关闭时的处理遵循 Node.js。

归档、取消归档和删除仓库现在每次都需要本人邮箱验证码。交互终端执行 `devops repo archive OWNER/REPO --yes` 会发送邮件并提示输入验证码；`--yes` 仅确认动作，不跳过邮箱验证。非交互调用先执行 `devops repo archive OWNER/REPO --request-code`，再通过标准输入传入验证码并使用 `--yes --code-stdin`。`--request-code` 只发验证码，不修改仓库；验证码不接受命令行参数，不保存到配置或输出到日志。SDK / 通用 API 调用同样必须提交 `code`，可向 `POST /api/repositories/{id}/operation-code` 申请，继续使用当前 CLI OAuth 授权与仓库拥有者权限。

## CI：工作流、运行与 PR 检查

命令和参数参照 [gh workflow](https://cli.github.com/manual/gh_workflow)、[gh run](https://cli.github.com/manual/gh_run) 和 [gh pr checks](https://cli.github.com/manual/gh_pr_checks)。所有命令复用当前 CLI OAuth、`-R` 和仓库定位，不单独保存 CI 凭据。读取只需 `api:read` 和仓库读取权限；触发、取消和重跑同时需要 `api:write` 与仓库写入权限。

```sh
devops workflow list -R owner/example --json name,path,state
devops workflow view checks.yml -R owner/example --ref main --yaml
devops workflow run checks.yml -R owner/example --ref main -f message=hello
devops workflow run checks.yml -R owner/example -F message=@message.txt
echo '{"message":"hello"}' | devops workflow run checks.yml -R owner/example --json

devops run list -R owner/example -w checks.yml -b main -e workflow_dispatch -L 20 \
  --json databaseId,workflowName,headSha,status,conclusion,url
devops run view 42 -R owner/example --json status,conclusion,jobs
devops run view 42 -R owner/example --log
devops run view 42 -R owner/example --job 123 --log
devops run watch 42 -R owner/example --interval 3 --exit-status
devops run download 42 -R owner/example --name build --dir ./artifacts
devops run cancel 42 -R owner/example
devops run rerun 42 -R owner/example

devops pr checks 7 -R owner/example --json name,state,bucket,link
devops pr checks 7 -R owner/example --required --watch --interval 3
```

- 工作流从 `.github/workflows/*.yml` / `*.yaml` 读取，支持名称、文件名或完整路径选择；同名时必须提供路径。交互终端可省略工作流 / run 参数并选择，脚本必须明确指定。`run download` 总是需要 run ID。工作流配置能力见 [CI 文档](../CI.md)。
- `workflow run --json` 和 gh 一样表示从 stdin 读取输入对象，不能与 `-f/-F` 混用。`-F key=@file` 读文件，`@-` 读 stdin；`-f` 保留字面文本。根据工作流输入定义检查并转换 boolean / number，string 保留文本。成功触发 / 重跑后 stdout 返回可继续传给 `run view/watch` 的 DevOps run URL。
- `workflow list --all` 包含仓库关闭 CI 时的工作流；暂不支持逐个工作流启停。`--ref` 目前仅支持分支，不支持 tag。`workflow list` 默认上限 50；`run list` 默认 20，并在匹配 branch / commit / event / status / workflow 后应用上限。`--commit` 使用完整 40 位 SHA。
- run JSON 将原生状态投影为 `queued` / `in_progress` / `completed`，结束原因放在 `conclusion`，原生 `manual` 事件投影为 `workflow_dispatch`；工作机失联映射为 failure。`run view --verbose` 展示步骤，`--log` 按执行读取分页日志。进度写 stderr，JSON / 日志写 stdout；日志剔除终端控制字符。
- `run watch` 等待完成，Ctrl-C 只停止观察；`watch/view --exit-status` 在已完成但失败、取消或超时的运行上返回 1，未完成的 `view` 返回 0。取消返回的是已提交请求，可用 watch 确认终态。重跑使用原 SHA 与原工作流快照，DevOps 会创建**新的 run ID**，并返回新链接。
- 产物按 `actions/upload-artifact` 的 name 选择，`-n` / `-p` 可重复；`-p` 支持 `*` / `?`，不能与 `-n` 混用。单个显式 `--name` 将内容写入目标目录，其余情况按产物名建立子目录。逐文件流式下载并校验大小 / SHA-256，默认最多等待 5 分钟；拒绝越界路径、符号链接、Windows 保留名称及覆盖已有文件，失败时移除当前未完成文件。
- `pr checks` 输出当前 PR head SHA 的检查；`--required` 按仓库策略过滤。JSON 的 `state` 使用大写状态，`bucket` 为 pass / fail / pending / skipping / cancel；失败退出 1、仍等待退出 8、没有已上报检查退出 1。`--watch --fail-fast` 在首次失败时结束观察。**1.1.0 调整**：原 `pr checks --json` 的原生输出改为 gh 字段选择，旧脚本可用 `devops api /api/repositories/REPOSITORY_ID/pulls/NUMBER --jq .checks` 保留原生格式。

支持的 JSON 字段可用各命令裸 `--json` 查看。没有工作流数值 ID、run attempt / startedAt / updatedAt 等原生数据，因此不伪造这些 gh 字段；`pr checks` 当前提供 name / state / bucket / description / link。暂不支持 `workflow enable/disable`、`run delete`、`rerun --failed/--job/--debug`、`view --attempt/--log-failed`、按 actor / 日期筛选、跨 run 下载产物。`--job` 需要同时指定或选择所属 run；PR checks 需要显式 PR 编号 / URL。未知命令、参数或 JSON 字段直接报错。

## SDK

在项目中安装同一个 URL（去掉 `--global`），然后：

```ts
import { DevOpsClient } from '@kevinc/devops-cli/sdk'

const client = new DevOpsClient({
  hostname: 'https://devops.kevinc.ltd',
  token: () => process.env.DEVOPS_TOKEN!,
})
const repo = await client.repository('owner', 'example')
const issues = await client.list(`/api/repositories/${repo.id}/items?kind=issue`, 100)
await client.createIssue(repo.id, { title: 'From SDK', body: 'Example' })
const workflows = await client.workflows(repo.id, 'main')
const run = await client.runWorkflow(repo.id, {
  ref: 'main', workflow: '.github/workflows/checks.yml', inputs: { message: 'hello' },
})
const detail = await client.run(repo.id, run.id) // 原生 state / workflow_path 等字段
const recent = await client.runs(repo.id, { workflow: '.github/workflows/checks.yml', limit: 20 })
```

SDK 提供类型化的仓库 / Issue / PR / CI 方法、通用 `request<T>`、`pages<T>`、`list<T>` 和携带 HTTP status / requestId 的 `APIError`。CI 还提供 `ciConfig`、`workflowSource`、`cancelRun`、`rerun`、`runLogs`（after 游标）和 `downloadArtifact`（流式 Response，调用方负责消费 / 取消及完整性校验）。它不读取浏览器 Cookie，不重试写请求，不跟随重定向。token provider 可由集成方管理生命周期；网页会话和管理用户接口不接受 CLI token。

## 开发与发布

```sh
pnpm --dir cli install --frozen-lockfile
pnpm --dir cli test
uv run --project server pytest server/tests/test_cli_oauth.py server/tests/test_cli_http.py server/tests/test_cli_ci_http.py server/tests/test_cli_deploy.py
python3 scripts/deploy_cli.py build
python3 scripts/deploy.py cli --dry-run
```

发布入口统一为 `deploy.sh cli`（固定测试环境），完整发布可使用 `all --migrate`。安装包只允许 package.json、README 和 dist 下 JS/d.ts；不包括 server、website、源码仓库、配置或凭据。相同版本不同内容会拒绝发布，历史包不随网站切换删除。CLI 独立维护 `package.json` 的版本；`devops --version` 直接读取已安装包的版本。网站指南从 CLI 包元数据读取下载版本；更新指南无需提升网站或 server 的版本。
