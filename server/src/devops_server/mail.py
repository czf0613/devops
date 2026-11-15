import smtplib
import ssl
from email.message import EmailMessage
from html import escape

from devops_server.config import Settings


class MailDeliveryError(Exception):
    pass


def code_email_html(code: str, action: str, valid_minutes: int) -> str:
    """Self-contained, inline-styled email; never load external assets or trackers."""
    safe_code, safe_action = escape(code), escape(action)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DevOps {safe_action}验证码</title>
</head>
<body style="margin:0;padding:0;background-color:#f2f5f9;color:#172b3a;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;">
  <div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">
    请完成你的{safe_action}验证，此验证码在 {valid_minutes} 分钟内有效。
  </div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
    style="background-color:#f2f5f9;">
    <tr><td align="center" style="padding:32px 16px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
        style="max-width:520px;background-color:#ffffff;border:1px solid #dce5ed;
          border-radius:16px;overflow:hidden;">
        <tr><td style="padding:24px;background-color:#0c1b29;border-radius:15px 15px 0 0;
          border-top:4px solid #56d8d2;">
          <p style="margin:0;color:#56d8d2;font-size:24px;line-height:32px;font-weight:700;">
            DevOps
          </p>
          <p style="margin:4px 0 0;color:#a7bbc9;font-size:12px;line-height:20px;">
            开发协作平台
          </p>
        </td></tr>
        <tr><td style="padding:28px 24px 24px;">
          <h1 style="margin:0 0 12px;font-size:22px;line-height:32px;color:#172b3a;">
            {safe_action}验证码
          </h1>
          <p style="margin:0 0 24px;font-size:14px;line-height:24px;color:#586c7b;">
            你正在进行{safe_action}操作，请在页面中输入以下验证码。
          </p>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr><td align="center" style="padding:20px 12px;background-color:#eefbf9;
              border:1px solid #c3e9e3;border-radius:10px;">
              <p style="margin:0 0 8px;color:#55756f;font-size:12px;line-height:18px;">
                一次性验证码
              </p>
              <p style="margin:0;color:#12685e;font-family:Consolas,Menlo,monospace;
                font-size:32px;font-weight:700;line-height:44px;letter-spacing:6px;">
                {safe_code}
              </p>
            </td></tr>
          </table>
          <p style="margin:18px 0 0;font-size:13px;line-height:22px;color:#586c7b;">
            <strong style="color:#172b3a;">{valid_minutes} 分钟内有效</strong>
            &nbsp;·&nbsp; 仅可使用一次，请勿分享给他人。
          </p>
        </td></tr>
        <tr><td style="padding:18px 24px;border-top:1px solid #e7edf2;">
          <p style="margin:0;color:#738391;font-size:12px;line-height:21px;">
            如果不是你本人操作，请忽略此邮件。<br>此邮件由 DevOps 自动发送，请勿直接回复。
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


class SMTPMailer:
    def __init__(self, settings: Settings):
        self.settings = settings

    def send_code(self, email: str, code: str, purpose: str) -> None:
        settings = self.settings
        if not settings.smtp_configured:
            raise MailDeliveryError()
        action = {
            "login": "登录",
            "register": "注册",
            "change_email": "更换邮箱",
            "legacy_password": "修改兼容密码",
            "admin_operation": "验证管理员操作",
            "repository_operation": "验证仓库操作",
        }[purpose]
        message = EmailMessage()
        message["From"] = settings.smtp_sender
        message["To"] = email
        message["Subject"] = f"DevOps {action}验证码"
        message.set_content(
            f"你正在进行 DevOps 的{action}操作。\n\n验证码：{code}\n\n"
            f"验证码在 {settings.email_code_seconds // 60} 分钟内有效，仅可使用一次。\n"
            "如果不是你本人操作，请忽略此邮件。"
        )
        message.add_alternative(
            code_email_html(code, action, settings.email_code_seconds // 60), subtype="html"
        )
        self.send_message(message)

    def send_notification(self, email: str, title: str, url: str) -> None:
        message = EmailMessage()
        message["From"] = self.settings.smtp_sender
        message["To"] = email
        message["Subject"] = "DevOps 讨论更新"
        message.set_content(f"你订阅的讨论有新动态：{title}\n\n{url}\n\n可在讨论页面取消邮件订阅。")
        message.add_alternative(
            '<html lang="zh-CN"><body style="font-family:sans-serif;color:#172b3a;">'
            f"<h2>DevOps 讨论更新</h2><p>{escape(title)}</p>"
            f'<p><a href="{escape(url, quote=True)}">查看讨论</a></p>'
            '<p style="color:#586c7b;">可在讨论页面取消邮件订阅。</p></body></html>',
            subtype="html",
        )
        self.send_message(message)

    def send_message(self, message: EmailMessage) -> None:
        settings = self.settings
        if not settings.smtp_configured:
            raise MailDeliveryError()
        try:
            connection = (
                smtplib.SMTP_SSL(
                    settings.smtp_host,
                    settings.smtp_port,
                    timeout=settings.smtp_timeout_seconds,
                    context=ssl.create_default_context(),
                )
                if settings.smtp_transport == "ssl"
                else smtplib.SMTP(
                    settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds
                )
            )
            with connection as smtp:
                if settings.smtp_transport == "starttls":
                    smtp.starttls(context=ssl.create_default_context())
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
                refused = smtp.send_message(message)
                if refused:
                    raise MailDeliveryError()
        except OSError, smtplib.SMTPException:
            # Do not propagate SMTP credentials, server responses or message contents.
            raise MailDeliveryError() from None
