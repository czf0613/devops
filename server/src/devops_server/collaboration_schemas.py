from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StringConstraints, field_validator, model_validator

from devops_server.schemas import Input

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
Body = Annotated[str, StringConstraints(max_length=65536)]
Positive = Annotated[int, Field(gt=0)]
Kind = Literal["issue", "pull"]
CatalogKind = Literal["label", "type", "milestone", "template"]
Method = Literal["merge", "squash", "rebase"]


class CreateItem(Input):
    title: Title
    body: Body = ""
    assignee_ids: list[Positive] = Field(default_factory=list, max_length=20)
    catalog_ids: list[Positive] = Field(default_factory=list, max_length=50)
    parent_id: Positive | None = None
    template_id: Positive | None = None
    fields: dict[str, str | list[str] | bool] = Field(default_factory=dict, max_length=30)


class UpdateItem(Input):
    version: Positive
    title: Title | None = None
    body: Body | None = None
    state: Literal["open", "closed"] | None = None
    close_reason: Literal["completed", "not_planned", "duplicate"] | None = None
    assignee_ids: list[Positive] | None = Field(default=None, max_length=20)
    catalog_ids: list[Positive] | None = Field(default=None, max_length=50)
    pinned: StrictBool | None = None
    locked: StrictBool | None = None

    @field_validator("title", "body", "state", "assignee_ids", "catalog_ids", "pinned", "locked")
    @classmethod
    def not_null(cls, value):
        if value is None:
            raise ValueError("此字段不能为 null")
        return value


class BulkItems(Input):
    numbers: list[Positive] = Field(min_length=1, max_length=100)
    state: Literal["open", "closed"] | None = None
    close_reason: Literal["completed", "not_planned"] = "completed"
    assignee_ids: list[Positive] | None = Field(default=None, max_length=20)
    catalog_ids: list[Positive] | None = Field(default=None, max_length=50)


class CommentInput(Input):
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=65536)]
    thread_id: Positive | None = None
    review_id: Positive | None = None


class MarkdownInput(Input):
    body: Body
    item_id: Positive | None = None
    comment_id: Positive | None = None


class EditComment(Input):
    version: Positive
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=65536)]


class RelationInput(Input):
    target_id: Positive
    kind: Literal["parent", "blocks", "duplicate", "development"]
    position: Annotated[int, Field(ge=0, le=100)] = 0


class TransferItem(Input):
    repository_id: Positive


class ChildOrder(Input):
    ids: list[Positive] = Field(min_length=1, max_length=100)


class SubscribeInput(Input):
    watching: StrictBool
    email: StrictBool = False


class ReactInput(Input):
    emoji: Literal["+1", "-1", "laugh", "hooray", "confused", "heart", "rocket", "eyes"]
    comment_id: Positive | None = None
    active: StrictBool = True


class FormField(Input):
    id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,49}$")]
    label: Title
    type: Literal["input", "textarea", "select", "checkbox"] = "textarea"
    required: StrictBool = False
    options: list[Title] = Field(default_factory=list, max_length=30)
    description: Annotated[str, StringConstraints(max_length=2000)] = ""


class CatalogData(Input):
    color: Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")] = "#36c8c4"
    due_at: datetime | None = None
    state: Literal["open", "closed"] = "open"
    item_kind: Kind = "issue"
    body: Body = ""
    fields: list[FormField] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def unique_fields(self):
        if len({field.id for field in self.fields}) != len(self.fields):
            raise ValueError("表单字段标识不能重复")
        return self


class CatalogInput(Input):
    kind: CatalogKind
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    description: Annotated[str, StringConstraints(max_length=2000)] = ""
    data: CatalogData = Field(default_factory=CatalogData)


class FilterInput(Input):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    query: dict[str, str] = Field(max_length=12)

    @field_validator("query")
    @classmethod
    def allowed_query(cls, value):
        allowed = {
            "kind",
            "state",
            "q",
            "author",
            "assignee",
            "label",
            "type",
            "milestone",
            "sort",
            "updated_after",
        }
        if set(value) - allowed or any(len(part) > 256 for part in value.values()):
            raise ValueError("筛选参数无效")
        return value


class MergePolicy(Input):
    methods: list[Method] = Field(
        default_factory=lambda: ["merge", "squash", "rebase"], min_length=1, max_length=3
    )
    required_approvals: Annotated[int, Field(ge=0, le=10)] = 0
    dismiss_stale_approvals: StrictBool = True
    require_resolved_threads: StrictBool = True
    required_checks: list[Title] = Field(default_factory=list, max_length=30)


class CreatePull(CreateItem):
    base_ref: Annotated[str, StringConstraints(min_length=1, max_length=1024)] = "main"
    head_ref: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    draft: StrictBool = False
    reviewer_ids: list[Positive] = Field(default_factory=list, max_length=20)


class UpdatePull(Input):
    version: Positive
    draft: StrictBool | None = None
    base_ref: Annotated[str, StringConstraints(min_length=1, max_length=1024)] | None = None
    reviewer_ids: list[Positive] | None = Field(default=None, max_length=20)


class ReviewInput(Input):
    head_sha: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    state: Literal["pending", "commented", "approved", "changes_requested"] = "pending"
    body: Body = ""


class ThreadInput(CommentInput):
    path: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    side: Literal["left", "right"]
    start_line: Annotated[int, Field(ge=1, le=10000000)]
    end_line: Annotated[int, Field(ge=1, le=10000000)]
    base_sha: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    head_sha: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]

    @model_validator(mode="after")
    def line_range(self):
        if self.end_line < self.start_line or self.end_line - self.start_line > 1000:
            raise ValueError("评论行范围无效")
        return self


class MergeInput(Input):
    base_sha: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    head_sha: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    method: Method = "merge"
    message: Annotated[str, StringConstraints(max_length=65536)] = ""
    delete_branch: StrictBool = False


class CheckInput(Input):
    head_sha: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    name: Title
    status: Literal["queued", "running", "success", "failure", "cancelled"]
    details: Annotated[str, StringConstraints(max_length=10000)] = ""
    external_url: Annotated[str, StringConstraints(max_length=2000)] | None = None

    @field_validator("external_url")
    @classmethod
    def safe_url(cls, value):
        if value is not None and not value.startswith(("https://", "http://")):
            raise ValueError("检查链接必须使用 HTTP 或 HTTPS")
        return value
