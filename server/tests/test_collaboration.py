from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import mutate
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_repositories import add
from test_repositories import workspace as repository_workspace

from devops_server.collaboration_models import ItemRelation, WorkItem
from devops_server.collaboration_references import candidates
from devops_server.models import Repository


@pytest.fixture
def workspace(system):
    return repository_workspace.__wrapped__(system)


def create(workspace, title="Issue", **extra):
    response = mutate(
        workspace.people["owner"][1], "POST", workspace.path + "/issues", {"title": title, **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_shared_numbers_permission_edit_conflicts_and_close_reason(workspace):
    owner = workspace.people["owner"][1]
    reader = workspace.people["reader"][1]
    outsider = workspace.people["outsider"][1]
    assert add(workspace, "reader").status_code == 201
    first = create(workspace, "Implement", body="A **safe** body")
    second = create(workspace, "Follow-up")
    assert [first["number"], second["number"]] == [1, 2]
    path = workspace.path + "/items/1"
    assert outsider.get(path).status_code == 404
    assert mutate(outsider, "POST", workspace.path + "/issues", {"title": "No"}).status_code == 404
    assert mutate(reader, "PATCH", path, {"version": 1, "title": "No"}).status_code == 403
    own = mutate(reader, "POST", workspace.path + "/issues", {"title": "Reader issue"})
    assert own.status_code == 201
    assert (
        mutate(
            reader,
            "POST",
            workspace.path + "/issues",
            {"title": "No", "assignee_ids": [workspace.people["owner"][0]]},
        ).status_code
        == 403
    )
    result = mutate(
        owner,
        "PATCH",
        path,
        {"version": 1, "title": "Changed", "state": "closed", "close_reason": "not_planned"},
    )
    assert result.status_code == 200, result.text
    assert result.json()["version"] == 2 and result.json()["close_reason"] == "not_planned"
    assert mutate(owner, "PATCH", path, {"version": 1, "body": "Lost update"}).status_code == 409
    assert owner.get(path + "/history").json()["items"][0]["title"] == "Implement"
    assert owner.get(workspace.path + "/items?state=open").json()["total"] == 2
    assert owner.get(workspace.path + "/items?state=closed").json()["total"] == 1


def test_comments_references_reactions_and_private_backlinks(workspace):
    owner = workspace.people["owner"][1]
    target = create(workspace, "Target")
    source = create(workspace, "Source", body="#1 and `#1` and \\#1")
    path = workspace.path + "/items/2"
    backlinks = owner.get(workspace.path + "/items/1").json()["backlinks"]
    assert len(backlinks) == 1 and backlinks[0]["source"]["id"] == source["id"]
    comment = mutate(owner, "POST", path + "/comments", {"body": "See #1"}).json()
    assert "issuecomment-" in comment["url"]
    resolved = mutate(owner, "POST", workspace.path + "/markdown/resolve", {"body": "#1 `#2` \\#2"})
    assert [r["item_id"] for r in resolved.json()] == [target["id"]]
    react = {"emoji": "+1", "comment_id": comment["id"]}
    for _ in range(2):
        assert mutate(owner, "PUT", path + "/reaction", react).json() == [
            {"emoji": "+1", "count": 1, "mine": True}
        ]
    assert (
        mutate(
            owner,
            "PATCH",
            path + f"/comments/{comment['id']}",
            {"version": 1, "body": "No reference"},
        ).status_code
        == 200
    )
    assert len(owner.get(workspace.path + "/items/1").json()["backlinks"]) == 1
    assert mutate(owner, "DELETE", path + f"/comments/{comment['id']}").status_code == 200
    timeline = owner.get(path + "/timeline").json()
    assert all("comment" not in entry for entry in timeline["items"])


def test_hierarchy_cycle_depth_limit_and_independent_states(workspace):
    owner = workspace.people["owner"][1]
    root = create(workspace, "Root")
    nodes = [root]
    for depth in range(7):
        nodes.append(create(workspace, f"Child {depth}", parent_id=nodes[-1]["id"]))
    denied = mutate(
        owner,
        "POST",
        workspace.path + "/issues",
        {"title": "Too deep", "parent_id": nodes[-1]["id"]},
    )
    assert denied.status_code == 409
    cycle = mutate(
        owner,
        "POST",
        workspace.path + "/items/8/relations",
        {"target_id": root["id"], "kind": "parent"},
    )
    assert cycle.status_code == 409
    assert (
        mutate(
            owner, "PATCH", workspace.path + "/items/1", {"version": 1, "state": "closed"}
        ).status_code
        == 200
    )
    assert owner.get(workspace.path + "/items/2").json()["state"] == "open"
    with Session(workspace.engine) as db:
        assert len(list(db.scalars(select(ItemRelation)))) == 7
        assert len(list(db.scalars(select(WorkItem)))) == 8
        assert db.get(Repository, workspace.repo["id"]).next_item_number == 9


def test_private_cross_repository_transfer_and_old_url(workspace):
    owner = workspace.people["owner"][1]
    reader = workspace.people["reader"][1]
    assert add(workspace, "reader").status_code == 201
    issue = create(workspace)
    another = mutate(owner, "POST", "/api/repositories", {"name": "another"}).json()
    moved = mutate(
        owner, "POST", workspace.path + "/items/1/transfer", {"repository_id": another["id"]}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["id"] == issue["id"]
    assert owner.get(workspace.path + "/items/1").json()["url"].endswith("/another/issue/1")
    assert reader.get(workspace.path + "/items/1").status_code == 404
    assert (
        mutate(
            owner, "PATCH", workspace.path + "/items/1", {"version": 2, "title": "old"}
        ).status_code
        == 409
    )


def test_metadata_templates_search_and_bulk(workspace):
    owner = workspace.people["owner"][1]
    label = mutate(
        owner, "POST", workspace.path + "/catalog", {"kind": "label", "name": "bug"}
    ).json()
    template = mutate(
        owner,
        "POST",
        workspace.path + "/catalog",
        {
            "kind": "template",
            "name": "Bug report",
            "data": {"fields": [{"id": "steps", "label": "Steps", "required": True}]},
        },
    ).json()
    response = mutate(
        owner,
        "POST",
        workspace.path + "/issues",
        {"title": "Missing", "template_id": template["id"]},
    )
    assert response.status_code == 422
    item = create(
        workspace,
        "Unicode 中文",
        template_id=template["id"],
        fields={"steps": "Open page"},
        catalog_ids=[label["id"]],
    )
    assert "### Steps\n\nOpen page" in item["body"]
    listing = owner.get(workspace.path + f"/items?q=中文&label={label['id']}").json()
    assert listing["total"] == 1
    result = mutate(
        owner, "POST", workspace.path + "/items/bulk", {"numbers": [1], "state": "closed"}
    )
    assert result.status_code == 200, result.text
    assert owner.get(workspace.path + "/items/1").json()["state"] == "closed"


def test_parallel_number_allocation(workspace):
    # Independent sessions exercise PostgreSQL row locks without sharing browser clients.
    from devops_server.collaboration_service import next_number
    from devops_server.repositories import visible_repository

    def allocate(_):
        with Session(workspace.engine) as db:
            repo, _ = visible_repository(
                db, workspace.repo["id"], workspace.people["owner"][0], lock=True
            )
            number = next_number(repo)
            db.add(
                WorkItem(
                    repository_id=repo.id,
                    number=number,
                    author_id=repo.owner_id,
                    kind="issue",
                    title="Concurrent",
                )
            )
            db.commit()
            return number

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sorted(pool.map(allocate, range(8))) == list(range(1, 9))


def test_markdown_reference_parser_skips_code_links_and_escapes():
    text = "#1 `#2` \\#3 [#4](https://example.com)\n\n```\n#5\n```\nCloses #6"
    parsed = candidates(text)
    assert ("#1", False) in parsed and ("#6", True) in parsed
    assert not any(raw in {"#2", "#3", "#4", "#5"} for raw, _ in parsed)


def test_transfer_preserves_relative_body_and_comment_references(workspace):
    owner = workspace.people["owner"][1]
    target = create(workspace, "Original target")
    source = create(workspace, "Transfer source", body="See #1")
    comment = mutate(
        owner, "POST", workspace.path + "/items/2/comments", {"body": "Also #1"}
    ).json()
    destination = mutate(owner, "POST", "/api/repositories", {"name": "destination"}).json()
    destination_path = f"/api/repositories/{destination['id']}"
    mutate(owner, "POST", destination_path + "/issues", {"title": "Different #1"})
    moved = mutate(
        owner, "POST", workspace.path + "/items/2/transfer", {"repository_id": destination["id"]}
    ).json()
    for body, extra in [("See #1", {}), ("Also #1", {"comment_id": comment["id"]})]:
        links = mutate(
            owner,
            "POST",
            destination_path + "/markdown/resolve",
            {"body": body, "item_id": source["id"], **extra},
        ).json()
        assert links[0]["item_id"] == target["id"]
    links = mutate(owner, "POST", workspace.path + "/markdown/resolve", {"body": "#2"}).json()
    assert links[0]["url"] == moved["url"]
    assert (
        mutate(
            owner,
            "PATCH",
            destination_path + "/items/2",
            {"version": moved["version"], "body": "#1"},
        ).status_code
        == 200
    )
    links = mutate(
        owner,
        "POST",
        destination_path + "/markdown/resolve",
        {"body": "#1", "item_id": source["id"]},
    ).json()
    assert links[0]["item_id"] != target["id"]


def test_child_order_requires_complete_set_and_permissions_on_both_repositories(workspace):
    owner, reader = workspace.people["owner"][1], workspace.people["reader"][1]
    assert add(workspace, "reader", access="write").status_code == 201
    parent = create(workspace)
    first = create(workspace, parent_id=parent["id"])
    second = create(workspace, parent_id=parent["id"])
    path = workspace.path + "/items/1/children/order"
    assert mutate(owner, "PUT", path, {"ids": [second["id"]]}).status_code == 409
    assert mutate(owner, "PUT", path, {"ids": [second["id"], first["id"]]}).status_code == 200
    result = owner.get(workspace.path + "/items/1").json()
    assert [edge["target_id"] for edge in result["relations"]["items"]] == [
        second["id"],
        first["id"],
    ]
    another = mutate(owner, "POST", "/api/repositories", {"name": "private"}).json()
    mutate(owner, "POST", workspace.path + "/items/2/transfer", {"repository_id": another["id"]})
    assert mutate(reader, "PUT", path, {"ids": [first["id"], second["id"]]}).status_code == 404
    visible = reader.get(workspace.path + "/items/1").json()["relations"]
    assert visible["children_total"] == 1


def test_relative_markdown_links_and_unrelated_links_are_safe(workspace):
    owner = workspace.people["owner"][1]
    item = create(workspace)
    body = (
        f"[issue]({item['url']}#issuecomment-12) [heading](#section) "
        "[file](../file.md) [mail](mailto:test@example.org) ![file](/api/attachments/1)"
    )
    result = mutate(owner, "POST", workspace.path + "/markdown/resolve", {"body": body})
    assert result.status_code == 200, result.text
    assert [entry["item_id"] for entry in result.json()] == [item["id"]]
    assert candidates("Closes [issue](https://example.org/repositories/a/b/issue/1)")[0][1] is True
