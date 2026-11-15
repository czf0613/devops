# 人工管理员 SQL

[`create_admin.example.sql`](create_admin.example.sql) 是手动创建全局管理员的示例。应用启动、迁移和 `make setup` 都不会执行它，使管理员授予与普通注册分离。

使用前完成数据库迁移和 SMTP 配置，替换示例邮箱为受控且能收信的真实地址。账号无需密码，与普通成员一样通过邮箱验证码登录。

## 新建管理员

先复制示例到不受 Git 管理的临时文件并编辑邮箱，再用目标数据库的 psql 执行。以下命令在 **`devops` 目录、本地开发数据库** 中运行：

```sh
docker compose exec -T postgres sh -c \
  'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < /tmp/devops-create-admin.sql
```

`/tmp/devops-create-admin.sql` 是操作者已编辑好的副本，不要原样执行 `replace-me@example.com` 示例。脚本使用事务；重复用户名或邮箱会失败，不覆盖已有账号。

服务器使用自己的 `.env.deploy` 和 `compose.deploy.yaml`，选择目标后再执行相应命令，不把本机 `.env` 复制过去。真实邮箱、连接信息和填写后的脚本不要提交到 Git。

## 给已有账号授予管理员

先核实用户名、邮箱归属和账号状态，再在目标数据库执行：

```sql
INSERT INTO user_roles (user_id, role_id)
SELECT users.id, roles.id
FROM users CROSS JOIN roles
WHERE users.username = 'existing_username' AND roles.code = 'admin'
ON CONFLICT (user_id, role_id) DO NOTHING;
```

仅创建 `users` 行不会授予管理员。应用每次请求读取当前角色；角色 API 不允许分配 / 移除 admin，管理界面保留已有 admin。全局管理员没有私有仓库的隐式访问权限。

直接 SQL 绕过应用输入校验、审计和最后一个有效管理员保护，人工变更须自行核对前提。用户名 / 邮箱使用规范化小写值，修改已有资料时维护 `updated_at`，不能移除唯一可用管理入口。
