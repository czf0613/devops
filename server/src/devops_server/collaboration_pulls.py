"""Same-repository pull requests, reviews and explicit merge policy."""

import re
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from devops_server import collaboration_git as git
from devops_server import collaboration_service as service
from devops_server.collaboration_issues import create_item, synchronize
from devops_server.collaboration_models import (
    CollaborationJob,
    DiscussionComment,
    MergeIntent,
    PullCheck,
    PullRequest,
    PullReview,
    PullReviewer,
    ReviewThread,
    ViewedFile,
    WorkItem,
)
from devops_server.collaboration_schemas import (
    CheckInput,
    CommentInput,
    CreatePull,
    MergeInput,
    MergePolicy,
    ReviewInput,
    ThreadInput,
    UpdatePull,
)
from devops_server.dependencies import Database
from devops_server.dependencies import RepositoryAuthenticated as Authenticated
from devops_server.models import User
from devops_server.repositories import (
    repository_access,
    require_owner,
    visible_repository,
)

router = APIRouter(prefix="/api/repositories", tags=["pull-requests"])


@router.post("/{repository_id}/pulls/{number}/retry-checks")
def retry_checks(repository_id: int, number: int, actor: Authenticated, db: Database):
    repo, _, item = service.get_item(db, repository_id, number, actor.user.id, write=True)
    pull = find_pull(db, item)
    if item.state != "open":
        raise HTTPException(409, "PR 已关闭")
    checks = merge_checks(db, repo, pull)
    for check in checks.values():
        job = db.scalar(
            select(CollaborationJob)
            .where(CollaborationJob.id == check["job_id"])
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job.status == "failed":
            job.status, job.attempts, job.result = "queued", 0, {}
            job.available_at = service.now()
    return {"ok": True}


def enqueue(db, key, kind, payload):
    db.execute(
        insert(CollaborationJob)
        .values(key=key, kind=kind, payload=payload, available_at=service.now())
        .on_conflict_do_nothing(index_elements=["key"])
    )
    return db.scalar(select(CollaborationJob).where(CollaborationJob.key == key))


def merge_checks(db, repo, pull):
    checks = {}
    for method in MergePolicy.model_validate(repo.merge_policy).methods:
        key = f"merge-check:v1:{repo.id}:{pull.base_sha}:{pull.head_sha}:{method}"
        job = enqueue(
            db,
            key,
            "merge-check",
            {
                "repository_id": repo.id,
                "base": pull.base_sha,
                "head": pull.head_sha,
                "method": method,
            },
        )
        checks[method] = {
            "status": job.result.get("status", "error")
            if job.status == "complete"
            else "error"
            if job.status == "failed"
            else "checking",
            "conflicts": job.result.get("conflicts", []),
            "job_id": job.id,
            "error": job.result.get("error"),
        }
    return checks


def find_pull(db, item):
    if item.kind != "pull":
        raise HTTPException(404, "PR 不存在")
    pull = db.scalar(
        select(PullRequest)
        .where(PullRequest.item_id == item.id)
        .execution_options(populate_existing=True)
    )
    if pull is None:
        raise HTTPException(404, "PR 不存在")
    return pull


def refresh(db, root, repo, item, pull):
    if item.state != "open":
        return {"base_missing": False, "head_missing": False}
    source = git.repository_directory(root, repo.id)
    missing = {"base_missing": False, "head_missing": False}
    changes = {}
    for side in ("base", "head"):
        try:
            sha = git.branch_sha(source, getattr(pull, f"{side}_ref"))
        except git.GitFailure as error:
            if error.status != 404:
                raise
            missing[f"{side}_missing"] = True
            continue
        if sha != getattr(pull, f"{side}_sha"):
            changes[side] = sha
            setattr(pull, f"{side}_sha", sha)
    if changes:
        git.preserve(source, item.id, pull.base_sha, pull.head_sha)
        item.version += 1
        service.event(db, item, None, "synchronized", changes)
    return missing


def review_out(db, review):
    return {
        "id": review.id,
        "author": service.person(db, review.author_id),
        "body": review.body,
        "head_sha": review.head_sha,
        "state": review.state,
        "created_at": review.created_at,
        "submitted_at": review.submitted_at,
    }


def review_summary(db, repo, item, pull):
    policy = MergePolicy.model_validate(repo.merge_policy)
    latest = {}
    for review in db.scalars(
        select(PullReview)
        .where(
            PullReview.item_id == item.id,
            PullReview.state.in_(["approved", "changes_requested", "dismissed"]),
        )
        .order_by(PullReview.id)
    ):
        latest[review.author_id] = review
    approvals = 0
    blockers = []
    for user_id, review in latest.items():
        user = db.get(User, user_id, populate_existing=True)
        if (
            not user
            or user.status != "active"
            or user_id == item.author_id
            or repository_access(db, repo, user_id) != "write"
        ):
            continue
        if (
            policy.dismiss_stale_approvals
            and review.state == "approved"
            and review.head_sha != pull.head_sha
        ):
            continue
        if review.state == "approved":
            approvals += 1
        elif review.state == "changes_requested":
            blockers.append(user_id)
    unresolved = []
    for thread in db.scalars(
        select(ReviewThread).where(
            ReviewThread.item_id == item.id, ReviewThread.resolved_at.is_(None)
        )
    ):
        # Pending review threads cannot block a merge until the reviewer submits them.
        comments = list(
            db.scalars(
                select(DiscussionComment).where(
                    DiscussionComment.thread_id == thread.id, DiscussionComment.deleted_at.is_(None)
                )
            )
        )
        if any(
            not comment.review_id or db.get(PullReview, comment.review_id).state != "pending"
            for comment in comments
        ):
            unresolved.append(thread.id)
    checks = list(
        db.scalars(
            select(PullCheck).where(
                PullCheck.item_id == item.id, PullCheck.head_sha == pull.head_sha
            )
        )
    )
    passing = {check.name for check in checks if check.status == "success"}
    reasons = []
    if pull.draft:
        reasons.append("PR 仍为草稿")
    if approvals < policy.required_approvals:
        reasons.append(f"需要 {policy.required_approvals} 个批准，当前 {approvals} 个")
    if blockers:
        reasons.append("审阅者要求修改")
    if policy.require_resolved_threads and unresolved:
        reasons.append(f"还有 {len(unresolved)} 个未解决的讨论")
    if missing_checks := set(policy.required_checks) - passing:
        reasons.append("必要检查尚未通过：" + "、".join(sorted(missing_checks)))
    if repo.archived:
        reasons.append("仓库已归档")
    return {
        "approvals": approvals,
        "required_approvals": policy.required_approvals,
        "unresolved_threads": len(unresolved),
        "reasons": reasons,
        "checks": [
            {
                "id": c.id,
                "name": c.name,
                "status": c.status,
                "details": c.details,
                "external_url": c.external_url,
                "head_sha": c.head_sha,
            }
            for c in checks
        ],
        "checks_configured": bool(checks or policy.required_checks),
    }


def pull_out(db, repo, item, pull, user_id, missing=None):
    result = {
        "id": pull.id,
        "base_ref": pull.base_ref,
        "head_ref": pull.head_ref,
        "base_sha": pull.base_sha,
        "head_sha": pull.head_sha,
        "draft": pull.draft,
        "merged_sha": pull.merged_sha,
        "merge_method": pull.merge_method,
        "merged_by": service.person(db, pull.merged_by),
        "reviewers": [
            service.person(db, uid)
            for uid in db.scalars(
                select(PullReviewer.user_id).where(PullReviewer.item_id == item.id)
            )
        ],
        "reviews": [
            review_out(db, r)
            for r in db.scalars(
                select(PullReview).where(PullReview.item_id == item.id).order_by(PullReview.id)
            )
            if r.state != "pending" or r.author_id == user_id
        ],
        "policy": MergePolicy.model_validate(repo.merge_policy).model_dump(),
        "can_merge": repository_access(db, repo, user_id) == "write",
        **review_summary(db, repo, item, pull),
        **(missing or {}),
    }
    result["merge_checks"] = merge_checks(db, repo, pull) if item.state == "open" else {}
    intent = db.scalar(
        select(MergeIntent)
        .where(MergeIntent.item_id == item.id)
        .order_by(MergeIntent.id.desc())
        .limit(1)
    )
    result["merge_intent"] = (
        {
            "id": intent.id,
            "state": intent.state,
            "error": intent.error,
            "result_sha": intent.result_sha,
        }
        if intent
        else None
    )
    return result


@router.get("/{repository_id}/compare")
def compare(
    repository_id: int,
    request: Request,
    actor: Authenticated,
    db: Database,
    base: Annotated[str, Query(min_length=1, max_length=1024)],
    head: Annotated[str, Query(min_length=1, max_length=1024)],
    path: Annotated[str | None, Query(max_length=4096)] = None,
    page: Annotated[int, Query(ge=1, le=1000)] = 1,
):
    service.repository(db, repository_id, actor.user.id)
    root = request.app.state.settings.git_repositories_root
    try:
        source = git.repository_directory(root, repository_id)
        return git.compare(
            root,
            repository_id,
            git.branch_sha(source, base),
            git.branch_sha(source, head),
            path=path,
            page=page,
        )
    except git.GitFailure as error:
        raise git.api_failure(error) from None


@router.post("/{repository_id}/pulls", status_code=201)
def create_pull(
    repository_id: int, body: CreatePull, request: Request, actor: Authenticated, db: Database
):
    repo, member = service.repository(db, repository_id, actor.user.id, write=True)
    if repository_access(db, repo, actor.user.id) != "write":
        raise HTTPException(403, "创建 PR 需要仓库写入权限")
    if body.parent_id or body.base_ref == body.head_ref:
        raise HTTPException(422, "请选择不同的源分支和目标分支")
    service.validate_people(db, repo, body.reviewer_ids)
    existing = db.scalar(
        select(PullRequest)
        .join(WorkItem, WorkItem.id == PullRequest.item_id)
        .where(
            WorkItem.repository_id == repo.id,
            WorkItem.state == "open",
            WorkItem.deleted_at.is_(None),
            PullRequest.base_ref == body.base_ref,
            PullRequest.head_ref == body.head_ref,
        )
    )
    if existing:
        raise HTTPException(409, "这两个分支已有未关闭的 PR")
    root = request.app.state.settings.git_repositories_root
    try:
        source = git.repository_directory(root, repo.id)
        base, head = git.branch_sha(source, body.base_ref), git.branch_sha(source, body.head_ref)
        comparison = git.compare(root, repo.id, base, head)
        if not comparison["commits"]:
            raise HTTPException(422, "源分支没有需要合并的提交")
        item = create_item(db, request, repo, member, actor, body, "pull")
        pull = PullRequest(
            item_id=item.id,
            base_ref=body.base_ref,
            head_ref=body.head_ref,
            base_sha=base,
            head_sha=head,
            draft=body.draft,
        )
        db.add(pull)
        db.add_all(PullReviewer(item_id=item.id, user_id=uid) for uid in set(body.reviewer_ids))
        git.preserve(source, item.id, base, head)
        db.flush()
        if body.reviewer_ids:
            service.event(
                db, item, actor.user.id, "review_requested", recipients=set(body.reviewer_ids)
            )
        from devops_server.ci_service import pull_event

        pull_event(db, request.app.state.settings, repo, item, pull)
        return {
            **service.item_out(db, item, actor.user.id, detail=True),
            "pull": pull_out(db, repo, item, pull, actor.user.id),
        }
    except git.GitFailure as error:
        raise git.api_failure(error) from None


@router.get("/{repository_id}/pulls/{number}")
def get_pull(repository_id: int, number: int, request: Request, actor: Authenticated, db: Database):
    repo, _ = visible_repository(db, repository_id, actor.user.id, lock=True)
    _, _, item = service.get_item(db, repository_id, number, actor.user.id)
    pull = find_pull(db, item)
    try:
        missing = refresh(db, request.app.state.settings.git_repositories_root, repo, item, pull)
    except git.GitFailure as error:
        raise git.api_failure(error) from None
    return pull_out(db, repo, item, pull, actor.user.id, missing)


@router.patch("/{repository_id}/pulls/{number}")
def edit_pull(
    repository_id: int,
    number: int,
    body: UpdatePull,
    request: Request,
    actor: Authenticated,
    db: Database,
):
    repo, member, item = service.get_item(db, repository_id, number, actor.user.id, write=True)
    service.can_edit(repo, member, item, actor.user.id)
    pull = find_pull(db, item)
    if item.state != "open" or item.version != body.version:
        raise HTTPException(409, "PR 状态已变化，请刷新后重试")
    if body.draft is not None:
        pull.draft = body.draft
    if body.reviewer_ids is not None:
        service.validate_people(db, repo, body.reviewer_ids)
        db.execute(delete(PullReviewer).where(PullReviewer.item_id == item.id))
        db.add_all(PullReviewer(item_id=item.id, user_id=uid) for uid in set(body.reviewer_ids))
    try:
        if body.base_ref:
            if body.base_ref == pull.head_ref:
                raise HTTPException(422, "目标分支不能与源分支相同")
            source = git.repository_directory(
                request.app.state.settings.git_repositories_root, repo.id
            )
            if db.scalar(
                select(PullRequest.id)
                .join(WorkItem, WorkItem.id == PullRequest.item_id)
                .where(
                    WorkItem.repository_id == repo.id,
                    WorkItem.state == "open",
                    WorkItem.deleted_at.is_(None),
                    PullRequest.item_id != item.id,
                    PullRequest.base_ref == body.base_ref,
                    PullRequest.head_ref == pull.head_ref,
                )
            ):
                raise HTTPException(409, "这两个分支已有未关闭的 PR")
            pull.base_sha = git.branch_sha(source, body.base_ref)
            pull.base_ref = body.base_ref
            git.preserve(source, item.id, pull.base_sha, pull.head_sha)
    except git.GitFailure as error:
        raise git.api_failure(error) from None
    item.version += 1
    service.event(db, item, actor.user.id, "pull_updated", recipients=set(body.reviewer_ids or []))
    return pull_out(db, repo, item, pull, actor.user.id)


@router.get("/{repository_id}/pulls/{number}/diff")
def pull_diff(
    repository_id: int,
    number: int,
    request: Request,
    actor: Authenticated,
    db: Database,
    path: Annotated[str | None, Query(max_length=4096)] = None,
    page: Annotated[int, Query(ge=1, le=1000)] = 1,
):
    _, _, item = service.get_item(db, repository_id, number, actor.user.id)
    pull = find_pull(db, item)
    try:
        result = git.compare(
            request.app.state.settings.git_repositories_root,
            repository_id,
            pull.base_sha,
            pull.head_sha,
            path=path,
            page=page,
        )
    except git.GitFailure as error:
        raise git.api_failure(error) from None
    viewed = set(
        db.scalars(
            select(ViewedFile.path).where(
                ViewedFile.item_id == item.id,
                ViewedFile.user_id == actor.user.id,
                ViewedFile.head_sha == pull.head_sha,
            )
        )
    )
    for entry in result.get("files", []):
        entry["viewed"] = entry["path"] in viewed
    return result


@router.put("/{repository_id}/pulls/{number}/viewed")
def viewed(
    repository_id: int,
    number: int,
    actor: Authenticated,
    db: Database,
    path: Annotated[str, Query(min_length=1, max_length=4096)],
    head: Annotated[str, Query(pattern=r"^[0-9a-f]{40}$")],
    active: bool = True,
):
    _, _, item = service.get_item(db, repository_id, number, actor.user.id)
    pull = find_pull(db, item)
    if head != pull.head_sha:
        raise HTTPException(409, "PR 已更新，请刷新差异")
    if active:
        db.execute(
            insert(ViewedFile)
            .values(item_id=item.id, user_id=actor.user.id, path=path, head_sha=head)
            .on_conflict_do_update(
                index_elements=["item_id", "user_id", "path"],
                set_={"head_sha": head},
            )
        )
    else:
        db.execute(
            delete(ViewedFile).where(
                ViewedFile.item_id == item.id,
                ViewedFile.user_id == actor.user.id,
                ViewedFile.path == path,
            )
        )
    return {"ok": True}


def review_submission(db, request, repo, item, pull, actor_id, body, review=None):
    if body.head_sha != pull.head_sha or item.state != "open":
        raise HTTPException(409, "PR 已更新或关闭，请刷新后提交审阅")
    if body.state in {"approved", "changes_requested"}:
        if item.author_id == actor_id:
            raise HTTPException(422, "不能批准或要求修改自己的 PR")
        if repository_access(db, repo, actor_id) != "write":
            raise HTTPException(403, "批准或要求修改需要仓库写入权限")
    if review is None:
        review = db.scalar(
            select(PullReview).where(
                PullReview.item_id == item.id,
                PullReview.author_id == actor_id,
                PullReview.state == "pending",
            )
        )
        if review is None:
            review = PullReview(item_id=item.id, author_id=actor_id, head_sha=pull.head_sha)
            db.add(review)
    review.state, review.body, review.head_sha = body.state, body.body, pull.head_sha
    db.flush()
    if body.state != "pending":
        review.submitted_at = service.now()
        mentioned = set()
        for comment in db.scalars(
            select(DiscussionComment).where(
                DiscussionComment.review_id == review.id, DiscussionComment.deleted_at.is_(None)
            )
        ):
            mentioned |= synchronize(request, db, item, actor_id, comment)
        if body.body:
            comment = DiscussionComment(item_id=item.id, author_id=actor_id, body=body.body)
            db.add(comment)
            db.flush()
            mentioned |= synchronize(request, db, item, actor_id, comment)
            service.event(db, item, actor_id, "commented", {"comment_id": comment.id}, notify=False)
        service.event(
            db,
            item,
            actor_id,
            "reviewed",
            {"review_id": review.id, "state": review.state},
            recipients=mentioned,
        )
    return review


@router.post("/{repository_id}/pulls/{number}/reviews", status_code=201)
def review(
    repository_id: int,
    number: int,
    body: ReviewInput,
    request: Request,
    actor: Authenticated,
    db: Database,
):
    repo, member, item = service.get_item(db, repository_id, number, actor.user.id, write=True)
    service.conversation(repo, member, item, actor.user.id)
    pull = find_pull(db, item)
    return review_out(db, review_submission(db, request, repo, item, pull, actor.user.id, body))


@router.post("/{repository_id}/pulls/{number}/reviews/{review_id}/dismiss")
def dismiss_review(
    repository_id: int, number: int, review_id: int, actor: Authenticated, db: Database
):
    repo, member, item = service.get_item(db, repository_id, number, actor.user.id, write=True)
    if not service.triage(repo, member, actor.user.id):
        raise HTTPException(403, "需要仓库写入或管理权限")
    record = db.get(PullReview, review_id)
    if (
        not record
        or record.item_id != item.id
        or record.state not in {"approved", "changes_requested"}
    ):
        raise HTTPException(404, "审阅不存在")
    record.state = "dismissed"
    service.event(db, item, actor.user.id, "review_dismissed", {"review_id": record.id})
    return {"ok": True}


def thread_comments(db, thread, user_id):
    return [
        comment
        for comment in db.scalars(
            select(DiscussionComment)
            .where(DiscussionComment.thread_id == thread.id, DiscussionComment.deleted_at.is_(None))
            .order_by(DiscussionComment.id)
        )
        if not comment.review_id
        or db.get(PullReview, comment.review_id).state != "pending"
        or comment.author_id == user_id
    ]


@router.get("/{repository_id}/pulls/{number}/threads")
def threads(repository_id: int, number: int, actor: Authenticated, db: Database):
    _, _, item = service.get_item(db, repository_id, number, actor.user.id)
    pull = find_pull(db, item)
    result = []
    for thread in db.scalars(
        select(ReviewThread).where(ReviewThread.item_id == item.id).order_by(ReviewThread.id)
    ):
        comments = thread_comments(db, thread, actor.user.id)
        if not comments:
            continue
        result.append(
            {
                "id": thread.id,
                "path": thread.path,
                "side": thread.side,
                "start_line": thread.start_line,
                "end_line": thread.end_line,
                "base_sha": thread.base_sha,
                "head_sha": thread.head_sha,
                "outdated": thread.head_sha != pull.head_sha or thread.base_sha != pull.base_sha,
                "resolved": thread.resolved_at is not None,
                "resolved_by": service.person(db, thread.resolved_by),
                "comments": [
                    service.comment_out(db, comment, actor.user.id) for comment in comments
                ],
            }
        )
    return result


def diff_lines(patch, side):
    valid = set()
    line = None
    for row in patch.splitlines():
        if match := re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", row):
            line = int(match[1 if side == "left" else 2])
        elif line is not None and row[:1] in {" ", "-" if side == "left" else "+"}:
            valid.add(line)
            line += 1
    return valid


@router.post("/{repository_id}/pulls/{number}/threads", status_code=201)
def add_thread(
    repository_id: int,
    number: int,
    body: ThreadInput,
    request: Request,
    actor: Authenticated,
    db: Database,
):
    repo, member, item = service.get_item(db, repository_id, number, actor.user.id, write=True)
    service.conversation(repo, member, item, actor.user.id)
    pull = find_pull(db, item)
    if body.base_sha != pull.base_sha or body.head_sha != pull.head_sha or item.state != "open":
        raise HTTPException(409, "PR 已变化，请刷新后评论")
    try:
        result = git.compare(
            request.app.state.settings.git_repositories_root,
            repo.id,
            pull.base_sha,
            pull.head_sha,
            path=body.path,
        )
    except git.GitFailure as error:
        raise git.api_failure(error) from None
    valid = diff_lines(result["patch"], body.side)
    if any(line not in valid for line in range(body.start_line, body.end_line + 1)):
        raise HTTPException(422, "所选行不在当前差异中")
    review = None
    if body.review_id:
        review = db.get(PullReview, body.review_id)
        if (
            not review
            or review.item_id != item.id
            or review.author_id != actor.user.id
            or review.state != "pending"
        ):
            raise HTTPException(404, "审阅草稿不存在")
    thread = ReviewThread(
        item_id=item.id,
        author_id=actor.user.id,
        path=body.path,
        base_sha=pull.base_sha,
        head_sha=pull.head_sha,
        side=body.side,
        start_line=body.start_line,
        end_line=body.end_line,
    )
    db.add(thread)
    db.flush()
    comment = DiscussionComment(
        item_id=item.id,
        author_id=actor.user.id,
        body=body.body,
        thread_id=thread.id,
        review_id=review.id if review else None,
    )
    db.add(comment)
    db.flush()
    if not review:
        mentioned = synchronize(request, db, item, actor.user.id, comment)
        service.event(
            db, item, actor.user.id, "commented", {"comment_id": comment.id}, recipients=mentioned
        )
    return {"id": thread.id, "comment": service.comment_out(db, comment, actor.user.id)}


@router.post("/{repository_id}/pulls/{number}/threads/{thread_id}/comments", status_code=201)
def reply_thread(
    repository_id: int,
    number: int,
    thread_id: int,
    body: CommentInput,
    request: Request,
    actor: Authenticated,
    db: Database,
):
    repo, member, item = service.get_item(db, repository_id, number, actor.user.id, write=True)
    service.conversation(repo, member, item, actor.user.id)
    thread = db.get(ReviewThread, thread_id)
    if not thread or thread.item_id != item.id or not thread_comments(db, thread, actor.user.id):
        raise HTTPException(404, "讨论不存在")
    if body.thread_id or body.review_id:
        raise HTTPException(422, "回复不能附带其他讨论或审阅")
    existing_comments = thread_comments(db, thread, actor.user.id)
    pending_id = next(
        (
            comment.review_id
            for comment in existing_comments
            if comment.review_id and db.get(PullReview, comment.review_id).state == "pending"
        ),
        None,
    )
    published = any(
        not comment.review_id or db.get(PullReview, comment.review_id).state != "pending"
        for comment in existing_comments
    )
    comment = DiscussionComment(
        item_id=item.id,
        author_id=actor.user.id,
        body=body.body,
        thread_id=thread.id,
        review_id=None if published else pending_id,
    )
    db.add(comment)
    db.flush()
    if not comment.review_id:
        mentioned = synchronize(request, db, item, actor.user.id, comment)
        service.event(
            db, item, actor.user.id, "commented", {"comment_id": comment.id}, recipients=mentioned
        )
    return service.comment_out(db, comment, actor.user.id)


@router.put("/{repository_id}/pulls/{number}/threads/{thread_id}/resolved")
def resolve_thread(
    repository_id: int,
    number: int,
    thread_id: int,
    actor: Authenticated,
    db: Database,
    resolved: bool = True,
):
    repo, member, item = service.get_item(db, repository_id, number, actor.user.id, write=True)
    thread = db.get(ReviewThread, thread_id)
    if not thread or thread.item_id != item.id or not thread_comments(db, thread, actor.user.id):
        raise HTTPException(404, "讨论不存在")
    if actor.user.id not in {item.author_id, thread.author_id} and not service.triage(
        repo, member, actor.user.id
    ):
        raise HTTPException(403, "不能解决此讨论")
    if all(
        comment.review_id and db.get(PullReview, comment.review_id).state == "pending"
        for comment in thread_comments(db, thread, actor.user.id)
    ):
        raise HTTPException(409, "请先提交审阅再解决讨论")
    thread.resolved_at = service.now() if resolved else None
    thread.resolved_by = actor.user.id if resolved else None
    service.event(
        db,
        item,
        actor.user.id,
        "thread_resolved" if resolved else "thread_reopened",
        {"thread_id": thread.id},
    )
    return {"ok": True}


@router.put("/{repository_id}/merge-policy")
def policy(repository_id: int, body: MergePolicy, actor: Authenticated, db: Database):
    repo, _ = service.repository(db, repository_id, actor.user.id, write=True)
    require_owner(repo, actor.user.id)
    repo.merge_policy = body.model_dump()
    return repo.merge_policy


@router.get("/{repository_id}/merge-policy")
def get_policy(repository_id: int, actor: Authenticated, db: Database):
    repo, _ = service.repository(db, repository_id, actor.user.id)
    return MergePolicy.model_validate(repo.merge_policy).model_dump()


@router.put("/{repository_id}/pulls/{number}/checks")
def record_check(
    repository_id: int, number: int, body: CheckInput, actor: Authenticated, db: Database
):
    repo, _, item = service.get_item(db, repository_id, number, actor.user.id, write=True)
    # Only the repository owner can record external/manual checks. They are not a CI runner.
    require_owner(repo, actor.user.id)
    if body.name.startswith("ci/"):
        raise HTTPException(422, "ci/ 为 CI 执行结果保留名称")
    pull = find_pull(db, item)
    if body.head_sha != pull.head_sha:
        raise HTTPException(409, "检查对应的提交已过期")
    record = db.scalar(
        select(PullCheck).where(
            PullCheck.item_id == item.id,
            PullCheck.head_sha == body.head_sha,
            PullCheck.name == body.name,
        )
    )
    if record is None:
        record = PullCheck(item_id=item.id, **body.model_dump())
        db.add(record)
    else:
        for key, value in body.model_dump().items():
            setattr(record, key, value)
    service.event(
        db, item, actor.user.id, "check_updated", {"name": body.name, "status": body.status}
    )
    return {"ok": True}


@router.post("/{repository_id}/pulls/{number}/merge", status_code=202)
def merge(
    repository_id: int,
    number: int,
    body: MergeInput,
    request: Request,
    actor: Authenticated,
    db: Database,
):
    repo, _, item = service.get_item(db, repository_id, number, actor.user.id, write=True)
    if repository_access(db, repo, actor.user.id) != "write":
        raise HTTPException(403, "合并需要仓库写入权限")
    pull = find_pull(db, item)
    if item.state != "open":
        raise HTTPException(409, "PR 已关闭或合并")
    try:
        missing = refresh(db, request.app.state.settings.git_repositories_root, repo, item, pull)
    except git.GitFailure as error:
        raise git.api_failure(error) from None
    if any(missing.values()) or (body.base_sha, body.head_sha) != (pull.base_sha, pull.head_sha):
        raise HTTPException(409, "分支已更新或删除，请刷新后重新确认")
    reasons = review_summary(db, repo, item, pull)["reasons"]
    if reasons:
        raise HTTPException(409, "；".join(reasons))
    if body.method not in MergePolicy.model_validate(repo.merge_policy).methods:
        raise HTTPException(409, "仓库未启用此合并方式")
    check = merge_checks(db, repo, pull)[body.method]
    if check["status"] != "clean":
        raise HTTPException(409, "合并检测尚未通过")
    active = db.scalar(
        select(MergeIntent).where(
            MergeIntent.item_id == item.id,
            MergeIntent.state.in_(["pending", "prepared", "applied"]),
        )
    )
    if active:
        return {"id": active.id, "state": active.state}
    if body.delete_branch and (
        repo.protect_push or (repo.protect_main and pull.head_ref == "main")
    ):
        raise HTTPException(409, "分支保护不允许删除源分支")
    intent = MergeIntent(
        item_id=item.id,
        actor_id=actor.user.id,
        session_id=actor.session.id if actor.session else None,
        oauth_grant_id=actor.grant.id if actor.grant else None,
        base_sha=pull.base_sha,
        head_sha=pull.head_sha,
        method=body.method,
        message=body.message or f"Merge pull request #{item.number}: {item.title}",
        delete_branch=body.delete_branch,
    )
    db.add(intent)
    db.flush()
    enqueue(db, f"merge:{intent.id}", "merge", {"intent_id": intent.id})
    return {"id": intent.id, "state": intent.state}


@router.get("/{repository_id}/pulls/{number}/merges/{intent_id}")
def merge_status(
    repository_id: int, number: int, intent_id: int, actor: Authenticated, db: Database
):
    _, _, item = service.get_item(db, repository_id, number, actor.user.id)
    intent = db.get(MergeIntent, intent_id)
    if not intent or intent.item_id != item.id:
        raise HTTPException(404, "合并任务不存在")
    return {
        "id": intent.id,
        "state": intent.state,
        "result_sha": intent.result_sha,
        "error": intent.error,
    }
