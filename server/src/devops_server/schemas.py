import re
from datetime import datetime
from typing import Annotated, Any, Literal

from email_validator import EmailNotValidError, validate_email
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)


def normalize_email(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("请输入有效邮箱")
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        raise ValueError("请输入有效邮箱") from None


def normalize_username(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("用户名格式不正确")
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,31}", value):
        raise ValueError("用户名为 3–32 位，以字母开头，可包含数字、下划线、点和短横线")
    return value


def normalize_phone(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\+[1-9][0-9]{0,2}\.[0-9]{4,14}", value):
        raise ValueError("手机号格式应为 +86.13912345678")
    if len(value.replace("+", "").replace(".", "")) > 15:
        raise ValueError("国家码与电话号码合计不能超过 15 位")
    return value


Email = Annotated[str, BeforeValidator(normalize_email)]
Username = Annotated[str, BeforeValidator(normalize_username)]
Phone = Annotated[str | None, BeforeValidator(normalize_phone)]
Code = Annotated[str, Field(pattern=r"^[0-9]{6}$")]
Name = Annotated[str, StringConstraints(min_length=1, max_length=80, strip_whitespace=True)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class EmailCodeRequest(Input):
    email: Email
    purpose: Literal[
        "login",
        "register",
        "change_email",
        "legacy_password",
        "admin_operation",
        "repository_operation",
    ]


class AdminVerification(Input):
    code: Code


class LoginRequest(Input):
    email: Email
    code: Code


class RegisterRequest(LoginRequest):
    username: Username
    display_name: Name
    phone: Phone = None


class CreateUserRequest(Input):
    email: Email
    username: Username
    display_name: Name
    phone: Phone = None


class ProfilePatch(Input):
    username: Username | None = None
    display_name: Name | None = None
    phone: Phone = None

    @field_validator("username", "display_name", mode="before")
    @classmethod
    def not_null(cls, value):
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError("此字段不能为空")
        return value.strip() if isinstance(value, str) else value


class AdminUserPatch(ProfilePatch):
    email: Email | None = None
    status: Literal["active", "disabled"] | None = None

    @field_validator("email", "status", mode="before")
    @classmethod
    def not_null_admin(cls, value):
        if value is None:
            raise ValueError("此字段不能为空")
        return value


class PasswordRequest(Input):
    password: str | None
    code: Code

    @field_validator("password")
    @classmethod
    def valid_password(cls, value):
        if value is not None and not 8 <= len(value.encode("utf-8")) <= 72:
            raise ValueError("兼容密码必须为 8–72 个 UTF-8 字节")
        return value


class ClearPasswordRequest(Input):
    password: None


class RoleAssignment(Input):
    roles: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(max_length=20)


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    email: str
    phone: str | None
    status: str
    roles: list[str]
    has_legacy_password: bool
    created_at: datetime
    updated_at: datetime


class SessionOut(BaseModel):
    id: int
    user_id: int
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None
    revoke_reason: str | None
    login_ip: str | None
    user_agent: str | None
    current: bool
    active: bool


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    actor_user_id: int | None
    target_type: str | None
    target_id: int | None
    action: str
    result: str
    request_id: str | None
    details: dict[str, Any]
