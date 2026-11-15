# DevOps Runner

独立安装在专用构建机上的 CI worker。使用 WebSocket 主动连接 DevOps，无需开放构建机入站端口。首次版本使用本机执行器，适用于内部可信仓库；构建脚本拥有运行用户的本机权限。每个 worker 同时执行一个 Job，多台构建机提供并行能力。

平台实现覆盖 macOS、Linux 和 Windows 的 arm64 / x64。当前实际验收平台是 macOS arm64；其余五种组合仍需真机验证，不能视为已通过。Linux / macOS 使用进程组及独立监督进程，Windows 使用带 kill-on-close 的 Job Object；后台服务运行也需分别验收。

## 安装

需安装 Git、uv；使用 LFS 时安装 Git LFS；安装编译工具链由构建机管理员负责。Runner 使用 Python 3.14，uv 可自动准备。管理员在 DevOps 的“构建机”页面获取对应版本的下载地址与 SHA-256。

```sh
uv tool install --python 3.14 'https://devops.example.com/res/devops-runner/devops_runner-1.1.0-py3-none-any.whl'
devops-runner --version
devops-runner doctor
```

本地开发：

```sh
uv sync --project runner --locked
uv run --project runner devops-runner doctor
uv run --project runner pytest runner/tests
```

## 注册与运行

系统管理员添加 worker 名称和资源池，获得 15 分钟有效的一次性注册令牌。在构建机执行下列命令，按提示输入令牌，不要放在命令参数、脚本或仓库中：

```sh
devops-runner register --server https://devops.example.com --label xcode
devops-runner run
```

可在子命令前指定 `--config /absolute/path/config.json`；注册支持 `--work-dir /absolute/build-root`、重复 `--label` 和非交互 `--token-stdin`。默认配置为 `~/.devops-runner/config.json`，工作目录为 `~/.devops-runner/work`。本地配置保存长期身份凭据，POSIX 文件权限为 0600；Windows 需用专用账号并限制目录 ACL。服务器只保存 worker 令牌摘要。配置存在时拒绝覆盖；重新签发后用新配置路径注册。

长期令牌由本机生成，注册前先持久保存，再把摘要交给服务器。注册响应丢失时先尝试 `run`；若未成功消费注册令牌，管理员重新签发。不要并行运行使用同一配置的多个进程；同一工作目录有独占锁，服务器还用连接代次隔离旧连接。

生产连接要求 HTTPS/WSS；HTTP 仅允许 loopback 开发。内部 CA 可用 `SSL_CERT_FILE` 指向 CA PEM 文件，WebSocket、HTTP 产物及 Git 检出共用它，始终验证证书。不需要设置数据库、Redis 或服务器共享目录。

WSS、产物和 Git 共用系统 HTTP(S) 代理发现（包括 HTTP_PROXY / HTTPS_PROXY）及 NO_PROXY，loopback 始终直连。代理必须提供 HTTP CONNECT；仅有 SOCKS 入口时请提供对应的 HTTP 代理端口。代理地址不写入仓库或打印到日志。

“暂停接单”完成当前任务后停止领取；“恢复接单”继续领取；“撤销”立即让原身份失效，并使执行中记录失联。SIGTERM / Ctrl-C 尝试停止本机任务、上报结果后退出。重启能重放已完成消息；未完成执行标记为 `lost`，不会自动重跑，避免重复发布等副作用。

## 后台运行

先以前台方式注册、运行并验收，再交给操作系统的服务管理器。服务必须与注册时使用同一专用账号，并提供构建工具链所需 PATH。凭据只引用配置文件，不放在启动参数的令牌字段中。

Linux 可创建用户级 `~/.config/systemd/user/devops-runner.service`（替换示例绝对路径）：

```ini
[Unit]
Description=DevOps CI worker
After=network-online.target
[Service]
ExecStart=/home/ci/.local/bin/devops-runner --config /home/ci/.devops-runner/config.json run
Restart=on-failure
RestartSec=10
TimeoutStopSec=20
KillMode=control-group
[Install]
WantedBy=default.target
```

使用 `systemctl --user daemon-reload` 和 `systemctl --user enable --now devops-runner`；是否允许用户退出后运行由管理员配置 linger。

macOS 可建立 `~/Library/LaunchAgents/com.devops.runner.plist`，`ProgramArguments` 设置为 runner 的绝对路径、`--config`、配置绝对路径、`run`，使用 `RunAtLoad=true`、`KeepAlive=true` 和 `ThrottleInterval=10`。明确配置 `EnvironmentVariables.PATH`、标准输出和错误日志路径，然后通过 `launchctl bootstrap "gui/$(id -u)" /absolute/path/com.devops.runner.plist` 加载（替换为实际 plist 路径）。停止服务先 `bootout`，避免 KeepAlive 重新启动。

Windows 可在任务计划程序中创建“计算机启动时”或“用户登录时”任务，用专用账号运行 `devops-runner.exe --config C:\ci\config.json run`，设置工作目录、失败重试和“已有任务运行时不启动新实例”。原生 Windows Service 包装、系统级安装器和自动升级暂未提供；进程树取消逻辑需要后续 Windows 真机确认。

## 执行与扩展

`Executor` 定义 prepare / checkout / execute / collect / cancel / cleanup 生命周期。`NativeExecutor` 实现宿主机工作目录与进程管理；以后添加 Docker、Windows container、OpenSandbox 或 Tart 时沿用 Job 的 `environment.executor` 和同一协议。当前配置只接受 `native`，不把未实现的隔离类型标成可用。

源码按服务器保存的完整 SHA 检出；临时 Git/LFS 令牌只能读本次仓库，不能推送、调用通用管理 API 或访问其他仓库，执行结束或权限改变后失效。令牌通过 Git 子进程环境传递，不写入 clone URL、Git config 或构建步骤环境。submodule 首版不自动拉取。

运行脚本默认 macOS/Linux 为 bash，Windows 为 Windows PowerShell；可指定 sh、bash、pwsh、powershell。bash 开启 `-e -o pipefail`，PowerShell 使用终止错误及原生命令退出码。每一步独立 shell；GitHub 格式可使用工作流 / Job / step 的 `env`、`working-directory`，通过 GITHUB_ENV / GITHUB_PATH / GITHUB_OUTPUT 传递后续步骤数据。版本、架构和 libc 匹配规则见 CI 说明。

本地 journal 保存消息和日志，收到 ACK 后删除；断线继续短时间执行，超过租约确认窗口自行终止。日志最多 10 MiB，超限任务失败；产物每个 256 MiB，每次执行合计 1 GiB / 100 个。服务端日志与产物保留 30 天，之后只保留运行元数据。工作目录清理失败时停止接单，管理员处理后重启。

完整流水线语法与服务端行为见 [CI 说明](../CI.md)。
