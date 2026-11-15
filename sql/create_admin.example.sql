-- 先执行数据库迁移，再将邮箱替换为你可收信的真实邮箱。
-- 此文件仅为手动操作示例，应用和 make setup 都不会执行它。
-- 不需要密码。管理员与普通成员一样，只能通过邮箱验证码登录。
BEGIN;

WITH created AS (
    INSERT INTO users (username, display_name, email)
    VALUES ('admin', '管理员', lower('replace-me@example.com'))
    RETURNING id
)
INSERT INTO user_roles (user_id, role_id)
SELECT id, (SELECT id FROM roles WHERE code = 'admin')
FROM created;

COMMIT;
