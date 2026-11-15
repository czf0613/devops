from datetime import UTC, datetime, timedelta

import pytest
from conftest import mutate
from sqlalchemy import delete
from sqlalchemy.orm import Session
from test_repositories import add
from test_repositories import workspace as repository_workspace

from devops_server.collaboration_models import (
    DiscussionComment,
    ItemAssignee,
    ItemEvent,
    PullRequest,
    PullReview,
    PullReviewer,
    WorkItem,
)
from devops_server.models import Repository, RepositoryMember

ROOT = "/api/me/dashboard"


@pytest.fixture
def workspace(system):
    return repository_workspace.__wrapped__(system)


def seed_item(db, workspace, number, *, kind="issue", author="owner", **values):
    item = WorkItem(
        repository_id=workspace.repo["id"],
        number=number,
        kind=kind,
        author_id=workspace.people[author][0],
        title=f"Task {number}",
        **values,
    )
    db.add(item)
    db.flush()
    if kind == "pull":
        db.add(
            PullRequest(
                item_id=item.id,
                base_ref="main",
                head_ref=f"work-{number}",
                base_sha="a" * 40,
                head_sha="b" * 40,
            )
        )
    db.add(ItemEvent(item_id=item.id, actor_id=item.author_id, action="created"))
    db.flush()
    return item


def test_acl_precedes_counts_pagination_activity_and_recent_history(workspace):
    reader_id, reader = workspace.people["reader"]
    outsider_id, outsider = workspace.people["outsider"]
    assert add(workspace, "reader").status_code == 201
    with Session(workspace.engine) as db, db.begin():
        for number in range(1, 8):
            item = seed_item(db, workspace, number)
            # Historical relations never grant repository access, even to a global admin.
            for user_id in [reader_id, outsider_id]:
                db.add(ItemAssignee(item_id=item.id, user_id=user_id))
    params = {"kind": "issue", "scope": "assigned", "page_size": 5}
    first = reader.get(ROOT + "/items", params=params).json()
    second = reader.get(ROOT + "/items", params={**params, "page": 2}).json()
    assert (first["total"], len(first["items"]), len(second["items"])) == (7, 5, 2)
    assert not ({r["id"] for r in first["items"]} & {r["id"] for r in second["items"]})
    assert first["items"][0]["repository"]["full_name"] == "owner/example"
    assert "body" not in first["items"][0] and "email" not in str(first)
    assert len(reader.get(ROOT + "/activity").json()["items"]) == 6
    for client in [outsider, reader]:
        if client is reader:
            assert (
                mutate(
                    workspace.people["owner"][1], "DELETE", workspace.path + f"/members/{reader_id}"
                ).status_code
                == 200
            )
        assert client.get(ROOT + "/items", params=params).json()["total"] == 0
        assert client.get(ROOT + "/activity").json()["items"] == []
        assert (
            client.get(ROOT + "/repositories", params={"recent": workspace.repo["id"]}).json()[
                "items"
            ]
            == []
        )


def test_related_scope_only_uses_published_participation_and_distinct_items(workspace):
    reader_id, reader = workspace.people["reader"]
    add(workspace, "reader")
    with Session(workspace.engine) as db, db.begin():
        own = seed_item(db, workspace, 1, author="reader")
        assigned = seed_item(db, workspace, 2)
        commented = seed_item(db, workspace, 3)
        seed_item(db, workspace, 4)
        deleted = seed_item(db, workspace, 5, deleted_at=datetime.now(UTC))
        for item in [own, assigned, deleted]:
            db.add(ItemAssignee(item_id=item.id, user_id=reader_id))
        db.add(DiscussionComment(item_id=commented.id, author_id=reader_id, body="published"))
        pull = seed_item(db, workspace, 6, kind="pull")
        review = PullReview(item_id=pull.id, author_id=reader_id, head_sha="b" * 40)
        db.add(review)
        db.flush()
        db.add(
            DiscussionComment(
                item_id=pull.id, author_id=reader_id, body="private draft", review_id=review.id
            )
        )
        db.add(
            ItemEvent(
                item_id=pull.id,
                actor_id=reader_id,
                action="reviewed",
                data={"review_id": review.id},
            )
        )
        review_id = review.id
    related = reader.get(ROOT + "/items?kind=issue").json()
    assert {item["number"] for item in related["items"]} == {1, 2, 3}
    assert related["total"] == 3
    assert reader.get(ROOT + "/items?kind=issue&scope=authored").json()["total"] == 1
    assert reader.get(ROOT + "/items?kind=pull").json()["total"] == 0
    assert all(
        event["action"] != "reviewed" for event in reader.get(ROOT + "/activity").json()["items"]
    )
    with Session(workspace.engine) as db, db.begin():
        review = db.get(PullReview, review_id)
        review.state = "commented"
        review.submitted_at = datetime.now(UTC)
    assert reader.get(ROOT + "/items?kind=pull").json()["total"] == 1
    assert any(
        event["action"] == "reviewed" for event in reader.get(ROOT + "/activity").json()["items"]
    )


def test_review_queue_tracks_head_and_renewed_requests(workspace):
    reviewer_id, reviewer = workspace.people["manager"]
    add(workspace, "manager", access="write")
    now = datetime.now(UTC)
    with Session(workspace.engine) as db, db.begin():
        ids = {}
        for number in range(1, 9):
            item = seed_item(
                db, workspace, number, kind="pull", author="manager" if number == 8 else "owner"
            )
            ids[number] = item.id
            db.add(PullReviewer(item_id=item.id, user_id=reviewer_id, created_at=now))
            if number in [2, 3, 4, 5]:
                db.add(
                    PullReview(
                        item_id=item.id,
                        author_id=reviewer_id,
                        head_sha=("a" if number == 3 else "b") * 40,
                        state="commented" if number == 4 else "approved",
                        submitted_at=now + timedelta(seconds=-1 if number == 5 else 1),
                    )
                )
            if number == 6:
                db.query(PullRequest).filter_by(item_id=item.id).one().draft = True
            if number == 7:
                item.state = "closed"
    path = ROOT + "/items?kind=pull&scope=review_requested&page_size=25"
    result = reviewer.get(path).json()
    assert {row["number"] for row in result["items"]} == {1, 3, 4, 5}
    with Session(workspace.engine) as db, db.begin():
        db.query(PullRequest).filter_by(item_id=ids[2]).one().head_sha = "c" * 40
    assert reviewer.get(path).json()["total"] == 5
    with Session(workspace.engine) as db, db.begin():
        db.get(Repository, workspace.repo["id"]).archived = True
    assert reviewer.get(path).json()["total"] == 0


def test_activity_hides_deleted_comments_private_reviews_and_raw_event_data(workspace):
    owner_id, owner = workspace.people["owner"]
    with Session(workspace.engine) as db, db.begin():
        item = seed_item(db, workspace, 1, kind="pull")
        draft = PullReview(item_id=item.id, author_id=owner_id, head_sha="b" * 40)
        db.add(draft)
        db.flush()
        for body, review_id, deleted_at in [
            ("public", None, None),
            ("private", draft.id, None),
            ("deleted", None, datetime.now(UTC)),
        ]:
            comment = DiscussionComment(
                item_id=item.id,
                author_id=owner_id,
                body=body,
                review_id=review_id,
                deleted_at=deleted_at,
            )
            db.add(comment)
            db.flush()
            db.add(
                ItemEvent(
                    item_id=item.id,
                    actor_id=owner_id,
                    action="commented",
                    data={"comment_id": comment.id, "secret": body},
                )
            )
        db.add(
            ItemEvent(
                item_id=item.id,
                actor_id=owner_id,
                action="related",
                data={"title": "inaccessible linked item"},
            )
        )
    events = owner.get(ROOT + "/activity").json()["items"]
    assert [event["action"] for event in events] == ["commented", "created"]
    assert all("data" not in event for event in events)
    assert "secret" not in str(events) and "private" not in str(events)


def test_repository_history_search_transfer_and_soft_deletion(workspace):
    owner_id, owner = workspace.people["owner"]
    reader_id, reader = workspace.people["reader"]
    with Session(workspace.engine) as db, db.begin():
        other = Repository(owner_id=owner_id, name="second")
        db.add(other)
        db.flush()
        other_id = other.id
        seed_item(db, workspace, 1)
    result = owner.get(
        ROOT + "/repositories", params=[("recent", workspace.repo["id"]), ("recent", other_id)]
    ).json()
    assert [row["id"] for row in result["items"]] == [workspace.repo["id"], other_id]
    assert owner.get(ROOT + "/repositories?q=owner/sec").json()["items"][0]["id"] == other_id
    assert owner.get(ROOT + "/repositories?q=%").json()["items"] == []
    # Re-check ownership on every request; stale IDs cannot reveal a former repository.
    with Session(workspace.engine) as db, db.begin():
        db.get(Repository, workspace.repo["id"]).owner_id = reader_id
        db.execute(
            delete(RepositoryMember).where(RepositoryMember.repository_id == workspace.repo["id"])
        )
    assert owner.get(ROOT + "/items?kind=issue&scope=authored").json()["total"] == 0
    assert owner.get(ROOT + "/activity").json()["items"] == []
    recent = reader.get(ROOT + "/repositories", params={"recent": workspace.repo["id"]}).json()[
        "items"
    ]
    assert recent[0]["full_name"] == "reader/example"
    assert recent[0]["url"] == "/repositories/reader/example"
    with Session(workspace.engine) as db, db.begin():
        db.get(Repository, workspace.repo["id"]).deleted_at = datetime.now(UTC)
    assert reader.get(ROOT + "/repositories").json()["items"] == []
    assert reader.get(ROOT + "/activity").json()["items"] == []


def test_state_search_and_invalid_or_unauthenticated_queries(workspace):
    owner = workspace.people["owner"][1]
    with Session(workspace.engine) as db, db.begin():
        seed_item(db, workspace, 1)
        seed_item(db, workspace, 2, state="closed")
        seed_item(db, workspace, 3, kind="pull", state="merged")
    assert owner.get(ROOT + "/items?kind=issue").json()["total"] == 1
    assert owner.get(ROOT + "/items?kind=issue&state=all").json()["total"] == 2
    assert (
        owner.get(ROOT + "/items?kind=issue&state=closed&q=task%202").json()["items"][0]["number"]
        == 2
    )
    assert owner.get(ROOT + "/items?kind=pull&state=merged").json()["total"] == 1
    for suffix in [
        "/items?kind=issue&scope=review_requested",
        "/items?kind=pull&scope=assigned",
        "/items?kind=issue&page_size=26",
        "/repositories?recent=-1",
        "/repositories?recent=9223372036854775808",
        "/repositories?" + "&".join("recent=1" for _ in range(21)),
    ]:
        assert owner.get(ROOT + suffix).status_code == 422
    assert (
        owner.get(
            ROOT + "/items?kind=issue", headers={"Authorization": "Bearer invalid"}
        ).status_code
        == 401
    )
    assert workspace.client().get(ROOT + "/items?kind=issue").status_code == 401
