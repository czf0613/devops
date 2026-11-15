# CLI 工作规则

继承 [DevOps 规则](../AGENTS.md)。使用与兼容范围见 [README](README.md)。

- 仅维护客户端和 SDK。复用服务端仓库 ACL、版本校验、合并任务，不新增管理员绕过或另一套认证。
- `devops-cli` 是 public client，不内置 client secret；Git 继续使用 GCM 的独立授权。默认主机可通过配置、环境和参数覆盖，按完整 origin 隔离凭据，冲突时拒绝。
- 新增 gh 风格参数必须实现对应语义；未支持的命令/字段报错，不静默忽略。JSON 不掺杂进度；API 和 SDK 保持原生字段，CLI 投影单独维护。
- 写请求不自动重试；编辑提交 version，合并提交双端 SHA 并观察任务完成。不得把 202 当作合并成功。
- 系统凭据库失败不隐式回退明文。持有跨进程锁后重新读取凭据；刷新请求前持久化不确定状态，未知结果不得重放旧 refresh token。
- 不把 token 放在命令行、Git remote、错误信息或 URL；仅显式 `auth token` 可输出。SDK 不跟随 HTTP 重定向。
- `files` 仅发布 dist / README，包发布器额外验证白名单；包内不能加入配置、凭据、服务器源代码或安装时执行脚本。
- 版本发布不可覆盖不同字节；CLI、website、server 独立维护版本，不强制相等；CLI 显示版本和网站指南从 CLI 的 package.json 读取，不能另写常量。锁文件必须维护。
- CI 命令与原生 SDK 分层；`workflow run --json` 读取 stdin 输入，其他查询的 `--json` 选择输出字段。运行状态映射为 gh status / conclusion，不伪造时间、工作流 ID 或 attempt；取消是请求，重跑返回新 run ID。产物流式下载须校验路径 / 大小 / SHA-256，不覆盖已有文件。
- 验证：`pnpm test`，随后从仓库根运行 `uv run --project server pytest server/tests/test_cli_http.py server/tests/test_cli_ci_http.py server/tests/test_cli_oauth.py server/tests/test_cli_deploy.py`。包安装检查在临时目录执行，不改用户全局安装/Git 配置。
