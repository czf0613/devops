"""Extract references from Markdown tokens, never substitute arbitrary rendered HTML."""

import re
import time
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from sqlalchemy import select
from sqlalchemy.orm import Session

from devops_server.collaboration_git import GitFailure, resolve_commit
from devops_server.collaboration_models import (
    ContentReference,
    DiscussionComment,
    ItemRedirect,
    WorkItem,
)
from devops_server.collaboration_service import item_url, repo_url, summary, visible
from devops_server.models import Repository, User
from devops_server.repositories import repository_access

MARKDOWN = MarkdownIt("commonmark", {"html": False}).disable("text_join")
REFERENCE = re.compile(
    r"(?<![\w/#@\\])(?:https?://[^\s<>\[\]()]+|"
    r"[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9._-]*(?:#[1-9][0-9]*|@[0-9a-fA-F]{7,40})|"
    r"#[1-9][0-9]*|@[a-z0-9][a-z0-9_-]*|[0-9a-fA-F]{7,40})(?![\w])"
)
CLOSING = re.compile(r"\b(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s+$", re.I)
ITEM_PATH = re.compile(r"^/repositories/([^/]+)/([^/]+)/(issue|issues|pr|pull)/(\d+)(?:/.*)?$")
COMMIT_PATH = re.compile(r"^/repositories/([^/]+)/([^/]+)/commit/([0-9a-fA-F]{7,40})$")


def candidates(body: str) -> list[tuple[str, bool]]:
    result = []
    for block in MARKDOWN.parse(body):
        link_depth = 0
        closing_link = False
        for token in block.children or []:
            if token.type == "link_open":
                link_depth += 1
                href = token.attrGet("href")
                if href:
                    result.append((href, closing_link))
            elif token.type == "link_close":
                link_depth -= 1
            elif token.type == "text" and not link_depth:
                closing_link = bool(CLOSING.search(token.content))
                for match in REFERENCE.finditer(token.content):
                    result.append(
                        (
                            match.group().rstrip(".,;:!?。；，"),
                            bool(CLOSING.search(token.content[: match.start()])),
                        )
                    )
    # A bounded number of references also bounds DB and Git work on untrusted Markdown.
    return list(dict.fromkeys(result))[:100]


def resolve(
    db: Session, repository: Repository, body: str, user_id: int, root: str, origins: list[str]
) -> list[dict]:
    results = []
    repo_cache = {(repository.owner_id, repository.name): repository}
    commit_cache = {}
    commit_count = 0
    deadline = time.monotonic() + 10
    for raw, closing in candidates(body):
        if time.monotonic() >= deadline:
            break
        if not (raw.startswith("/") or REFERENCE.fullmatch(raw)):
            continue
        target_repo = repository
        value = raw
        target_kind = ""
        owner_name = repository_name = None
        if raw.startswith(("https://", "http://", "/")):
            parts = urlsplit(raw)
            if (parts.scheme or parts.netloc) and f"{parts.scheme}://{parts.netloc}" not in origins:
                continue
            path = unquote(parts.path)
            if match := ITEM_PATH.fullmatch(path):
                owner_name, repository_name, _, number = match.groups()
                target_kind, value = "item", number
            elif match := COMMIT_PATH.fullmatch(path):
                owner_name, repository_name, value = match.groups()
                target_kind = "commit"
            else:
                continue
        elif "/" in raw:
            namespace, value = re.split(r"[#@]", raw, maxsplit=1)
            owner_name, repository_name = namespace.split("/", 1)
            target_kind = "item" if "#" in raw else "commit"
        elif raw.startswith("#"):
            target_kind, value = "item", raw[1:]
        elif raw.startswith("@"):
            target_kind, value = "user", raw[1:]
        else:
            target_kind = "commit"
        if owner_name:
            key = (owner_name, repository_name)
            if key not in repo_cache:
                repo_cache[key] = db.scalar(
                    select(Repository)
                    .join(User, Repository.owner_id == User.id)
                    .where(
                        User.username == owner_name,
                        Repository.name == repository_name,
                        Repository.deleted_at.is_(None),
                    )
                )
            target_repo = repo_cache[key]
        if not target_repo or not repository_access(db, target_repo, user_id):
            continue
        result = {
            "raw": raw,
            "repository_id": target_repo.id,
            "kind": target_kind,
            "closing": closing,
            "item_id": None,
            "sha": None,
            "user_id": None,
        }
        if target_kind == "item":
            if len(value) > 18:
                continue
            target = db.scalar(
                select(WorkItem).where(
                    WorkItem.repository_id == target_repo.id,
                    WorkItem.number == int(value),
                    WorkItem.deleted_at.is_(None),
                )
            )
            if not target:
                redirect = db.scalar(
                    select(ItemRedirect).where(
                        ItemRedirect.repository_id == target_repo.id,
                        ItemRedirect.number == int(value),
                    )
                )
                target = db.get(WorkItem, redirect.item_id) if redirect else None
            if not visible(db, target, user_id):
                continue
            result["repository_id"] = target.repository_id
            result.update(
                key=f"item:{target.id}",
                item_id=target.id,
                url=item_url(db, target),
                title=target.title,
                state=target.state,
                item_kind=target.kind,
            )
        elif target_kind == "user":
            user = db.scalar(select(User).where(User.username == value, User.status == "active"))
            if not user or not repository_access(db, repository, user.id):
                continue
            # Member cards are inside the repository, never an unrestricted account directory.
            result.update(
                key=f"user:{user.id}",
                user_id=user.id,
                title=user.display_name,
                url=f"{repo_url(db, repository)}/settings#member-{user.id}",
            )
        else:
            key = (target_repo.id, value.lower())
            if key not in commit_cache:
                commit_count += 1
                if commit_count > 20:
                    continue
                try:
                    commit_cache[key] = resolve_commit(root, target_repo.id, value)
                except GitFailure:
                    commit_cache[key] = None
            sha = commit_cache[key]
            if not sha:
                continue
            result.update(
                key=f"commit:{target_repo.id}:{sha}",
                sha=sha,
                title=sha,
                url=f"{repo_url(db, target_repo)}/commit/{sha}",
            )
        results.append(result)
    return results


def synchronize(
    db: Session,
    item: WorkItem,
    user_id: int,
    root: str,
    origins: list[str],
    *,
    comment: DiscussionComment | None = None,
) -> set[int]:
    source_key = f"comment:{comment.id}" if comment else f"item:{item.id}"
    existing = {
        (r.target_key, r.kind): r
        for r in db.scalars(
            select(ContentReference).where(ContentReference.source_key == source_key)
        )
    }
    for entry in existing.values():
        entry.active = False
    body = comment.body if comment else item.body
    context = comment if comment else item
    repo = db.get(Repository, context.body_repository_id or item.repository_id)
    references = resolve(db, repo, body, user_id, root, origins)
    mentioned = set()
    for target in references:
        if target["item_id"] == item.id:
            continue
        kind = (
            "closing"
            if (
                target["closing"]
                and target["item_id"]
                and target.get("item_kind") == "issue"
                and item.kind == "pull"
                and not comment
            )
            else "mention"
        )
        key = (target["key"], kind)
        entry = existing.get(key)
        if not entry:
            entry = ContentReference(
                source_key=source_key,
                source_item_id=item.id,
                source_comment_id=comment.id if comment else None,
                source_repository_id=repo.id,
                target_key=target["key"],
                target_repository_id=target["repository_id"],
                target_item_id=target["item_id"],
                target_sha=target["sha"],
                target_user_id=target["user_id"],
                kind=kind,
            )
            db.add(entry)
            existing[key] = entry
        entry.active = True
        if target["user_id"]:
            mentioned.add(target["user_id"])
    return mentioned


def backlinks(
    db: Session,
    user_id: int,
    *,
    item_id: int | None = None,
    repository_id: int | None = None,
    sha: str | None = None,
) -> list[dict]:
    query = select(ContentReference).where(ContentReference.active.is_(True))
    if item_id:
        query = query.where(ContentReference.target_item_id == item_id)
    else:
        query = query.where(
            ContentReference.target_repository_id == repository_id,
            ContentReference.target_sha == sha,
        )
    result = []
    for reference in db.scalars(query.order_by(ContentReference.created_at.desc()).limit(500)):
        if reference.source_sha:
            source_repo = db.get(Repository, reference.source_repository_id)
            if not source_repo or not repository_access(db, source_repo, user_id):
                continue
            url = f"{repo_url(db, source_repo)}/commit/{reference.source_sha}"
            result.append(
                {
                    "id": reference.id,
                    "kind": reference.kind,
                    "created_at": reference.created_at,
                    "source": {
                        "kind": "commit",
                        "title": f"提交 {reference.source_sha[:8]}",
                        "url": url,
                    },
                    "url": url,
                }
            )
            continue
        item = db.get(WorkItem, reference.source_item_id) if reference.source_item_id else None
        if not visible(db, item, user_id):
            continue
        comment = (
            db.get(DiscussionComment, reference.source_comment_id)
            if reference.source_comment_id
            else None
        )
        if comment and comment.deleted_at is not None:
            continue
        result.append(
            {
                "id": reference.id,
                "kind": reference.kind,
                "created_at": reference.created_at,
                "source": summary(db, item),
                "url": item_url(db, item) + (f"#issuecomment-{comment.id}" if comment else ""),
            }
        )
    return result
