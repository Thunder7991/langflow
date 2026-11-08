"""Publication isolation through real authenticated routes and database transactions."""

# ruff: noqa: F811 - pytest injects the imported editor fixture.

import asyncio
import json
from copy import deepcopy
from uuid import UUID, uuid4

import pytest

from tests.unit.api.v1.test_platform_sso_editing import editor, isolated_auth_configuration  # noqa: F401
from tests.unit.api.v2.test_platform_dry_run import text_flow


def base(flow):
    return f"/api/v2/platform-workflow/flows/{flow['id']}"


async def publish(client, flow, token=None):
    return await client.post(
        f"{base(flow)}/publish",
        json={"published": True, "publication_token": token},
        headers={"If-Match": flow["version_token"]},
    )


async def save_text(client, flow, text):
    data = deepcopy(flow["data"])
    data["nodes"][0]["data"]["node"]["template"]["input_value"]["value"] = text
    response = await client.patch(
        f"/api/v1/flows/{flow['id']}", json={"data": data}, headers={"If-Match": flow["version_token"]}
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("access_type", ["PRIVATE", "PUBLIC"])
async def test_unpublished_and_legacy_public_flows_reject_business_run(editor, access_type):
    from langflow.services.database.models.flow.model import Flow
    from langflow.services.deps import session_scope

    client, _, live = editor
    flow = await text_flow(client, "draft")
    async with session_scope() as session:
        stored = await session.get(Flow, UUID(flow["id"]))
        stored.access_type = access_type
        session.add(stored)
    live["actions"] += ["run"]
    response = await client.post(f"{base(flow)}/run", json={}, headers={"If-Match": flow["version_token"]})
    assert response.status_code == 409, response.text


async def test_publish_save_republish_and_rollback_preserve_draft_and_versions(editor):
    client, _, live = editor
    flow = await text_flow(client, "V1")
    live["actions"] += ["publish", "run", "dry_run"]
    response = await publish(client, flow)
    assert response.status_code == 200, response.text
    first = response.json()
    assert first["status"] == "published"
    assert first["current"]["version_number"] == 1
    draft = await save_text(client, flow, "V2")
    state = (await client.get(f"{base(flow)}/publication")).json()
    assert state["has_unpublished_changes"] is True
    run = await client.post(f"{base(flow)}/run", json={})
    assert run.status_code == 200, run.text
    assert run.json()["outputs"] == [{"node_id": "TextOutput-b5", "text": "V1"}]
    assert run.json()["publication_id"] == first["current"]["id"]
    debug = await client.post(
        f"/api/v2/platform-flows/{flow['id']}/dry-run", json={}, headers={"If-Match": draft["version_token"]}
    )
    assert debug.json()["outputs"] == [{"node_id": "TextOutput-b5", "text": "V2"}]
    second = await publish(client, draft, first["publication_token"])
    assert second.status_code == 200, second.text
    second = second.json()
    assert second["current"]["version_number"] == 2
    assert (await client.post(f"{base(flow)}/run", json={})).json()["outputs"][0]["text"] == "V2"
    rollback = await client.post(
        f"{base(flow)}/publications/{first['current']['id']}/activate",
        json={"publication_token": second["publication_token"]},
    )
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["current"]["id"] == first["current"]["id"]
    assert rollback.json()["has_unpublished_changes"] is True
    unchanged = (await client.get(f"/api/v1/flows/{flow['id']}")).json()
    assert unchanged["data"] == draft["data"]
    assert unchanged["version_token"] == draft["version_token"]
    assert (await client.post(f"{base(flow)}/run", json={})).json()["outputs"][0]["text"] == "V1"
    history = await client.get(f"{base(flow)}/publications")
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 2
    from langflow.services.database.models.audit_event.model import AuditEvent
    from langflow.services.database.models.workflow_publication import WorkflowPublication
    from langflow.services.deps import session_scope
    from sqlmodel import select

    async with session_scope() as session:
        retained = await session.get(WorkflowPublication, UUID(first["current"]["id"]))
        assert retained.data == flow["data"]
        assert retained.source_version_token == UUID(flow["version_token"])
        events = (
            await session.exec(
                select(AuditEvent).where(
                    AuditEvent.resource_id == UUID(flow["id"]), AuditEvent.action == "workflow:publish"
                )
            )
        ).all()
        assert [event.operation for event in events].count("publish") == 2
        assert [event.operation for event in events].count("rollback") == 1


async def test_stale_publication_and_draft_tokens_cannot_change_online_version(editor):
    client, _, live = editor
    flow = await text_flow(client)
    live["actions"] += ["publish"]
    first = (await publish(client, flow)).json()
    assert (await publish(client, flow)).status_code == 409
    draft = await save_text(client, flow, "new draft")
    assert (await publish(client, flow, first["publication_token"])).status_code == 409
    assert (await client.get(f"{base(flow)}/publication")).json()["current"] == first["current"]
    assert (await publish(client, draft, first["publication_token"])).status_code == 200


async def test_publish_validation_failure_preserves_online_version(editor):
    client, _, live = editor
    flow = await text_flow(client)
    live["actions"] += ["publish"]
    first = (await publish(client, flow)).json()
    data = deepcopy(flow["data"])
    data["edges"] = []
    draft = await client.patch(
        f"/api/v1/flows/{flow['id']}", json={"data": data}, headers={"If-Match": flow["version_token"]}
    )
    assert draft.status_code == 200, draft.text
    response = await publish(client, draft.json(), first["publication_token"])
    assert response.status_code == 422, response.text
    state = (await client.get(f"{base(flow)}/publication")).json()
    assert state["current"] == first["current"]
    assert state["publication_token"] == first["publication_token"]


async def test_offline_permissions_and_delete_preserve_publication_history(editor):
    client, _, live = editor
    flow = await text_flow(client)
    live["actions"] += ["publish", "run", "delete"]
    state = (await publish(client, flow)).json()
    assert (await client.delete(f"/api/v1/flows/{flow['id']}")).status_code == 409
    live["actions"].remove("publish")
    body = {"published": False, "publication_token": state["publication_token"]}
    assert (await client.post(f"{base(flow)}/publish", json=body)).status_code == 403
    live["actions"].append("publish")
    off = await client.post(f"{base(flow)}/publish", json=body)
    assert off.status_code == 200, off.text
    assert off.json()["status"] == "offline"
    assert (await client.post(f"{base(flow)}/run", json={})).status_code == 409
    assert (await client.delete(f"/api/v1/flows/{flow['id']}")).status_code == 409
    assert (await client.get(f"{base(flow)}/publications")).json()["total"] == 1


async def test_run_id_replay_does_not_execute_again_and_graph_override_is_rejected(editor):
    client, _, live = editor
    flow = await text_flow(client)
    live["actions"] += ["publish", "run"]
    assert (await publish(client, flow)).status_code == 200
    body = {"run_id": str(uuid4()), "input": "runtime"}
    response = await client.post(f"{base(flow)}/run", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["outputs"][0]["text"] == "runtime"
    replay = await client.post(f"{base(flow)}/run", json=body)
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "workflow_run_accepted"
    assert (await client.post(f"{base(flow)}/run", json={"data": flow["data"]})).status_code == 422
    record = await client.get(f"{base(flow)}/runs/{body['run_id']}")
    assert record.status_code == 200, record.text
    assert record.json()["status"] == "succeeded"
    assert record.json()["publication_id"] == response.json()["publication_id"]


async def test_inflight_run_keeps_snapshot_after_publish_and_offline(editor, monkeypatch):
    from langflow.services.auth import platform_execution

    client, _, live = editor
    flow = await text_flow(client, "V1")
    live["actions"] += ["publish", "run"]
    first = (await publish(client, flow)).json()
    entered, proceed = asyncio.Event(), asyncio.Event()
    original = platform_execution.execute

    async def paused(graph, **kwargs):
        entered.set()
        await proceed.wait()
        return await original(graph, **kwargs)

    monkeypatch.setattr(platform_execution, "execute", paused)
    task = asyncio.create_task(client.post(f"{base(flow)}/run", json={}))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        draft = await save_text(client, flow, "V2")
        second = (await publish(client, draft, first["publication_token"])).json()
        off = await client.post(
            f"{base(flow)}/publish", json={"published": False, "publication_token": second["publication_token"]}
        )
        assert off.status_code == 200, off.text
    finally:
        proceed.set()
    response = await task
    assert response.status_code == 200, response.text
    assert response.json()["publication_id"] == first["current"]["id"]
    assert response.json()["outputs"][0]["text"] == "V1"


async def test_draft_history_cleanup_and_batch_delete_cannot_destroy_publications(editor):
    client, _, live = editor
    flow = await text_flow(client, "retained")
    live["actions"] += ["publish", "run", "delete", "version_create", "version_read", "version_delete"]
    state = (await publish(client, flow)).json()
    snapshot = await client.post(f"/api/v1/flows/{flow['id']}/versions/", json={})
    assert snapshot.status_code == 201, snapshot.text
    assert (await client.delete(f"/api/v1/flows/{flow['id']}/versions/{snapshot.json()['id']}")).status_code == 204
    assert (await client.request("DELETE", "/api/v1/flows/", json=[flow["id"]])).status_code == 409
    run = await client.post(f"{base(flow)}/run", json={})
    assert run.status_code == 200, run.text
    assert run.json()["publication_id"] == state["current"]["id"]
    assert run.json()["outputs"][0]["text"] == "retained"


async def test_missing_output_is_failure_and_does_not_create_a_success_record(editor, monkeypatch):
    from langflow.services.auth import platform_execution

    client, _, live = editor
    flow = await text_flow(client)
    live["actions"] += ["publish", "run"]
    assert (await publish(client, flow)).status_code == 200

    async def empty(*_args, **_kwargs):
        return []

    monkeypatch.setattr(platform_execution, "execute", empty)
    run_id = str(uuid4())
    response = await client.post(f"{base(flow)}/run", json={"run_id": run_id})
    assert response.status_code == 422, response.text
    record = await client.get(f"{base(flow)}/runs/{run_id}")
    assert record.json()["status"] == "failed"


async def test_publication_input_contract_and_api_example_match_a_graph_without_text_input(editor):
    from tests.unit.api.v1.test_platform_sso_editing import builtin

    client, _, live = editor
    flow = await text_flow(client)
    data = deepcopy(flow["data"])
    prompt = await builtin(client, "Prompt Template")
    prompt["template"]["template"]["value"] = "固定业务结果"
    data["nodes"][0]["data"]["type"] = "Prompt Template"
    data["nodes"][0]["data"]["node"] = prompt
    data["edges"][0]["sourceHandle"] = json.dumps({"id": data["nodes"][0]["id"], "name": "prompt"})
    saved = await client.patch(
        f"/api/v1/flows/{flow['id']}", json={"data": data}, headers={"If-Match": flow["version_token"]}
    )
    assert saved.status_code == 200, saved.text
    flow = saved.json()
    live["actions"] += ["publish", "run", "api_access"]
    publication = await publish(client, flow)
    assert publication.status_code == 200, publication.text
    assert publication.json()["current"]["input_schema"]["nodes"] == []
    assert "input" not in publication.json()["current"]["input_schema"]
    configuration = await client.get(f"{base(flow)}/api")
    assert configuration.status_code == 200, configuration.text
    assert "input" not in configuration.json()["body"]
    refused = await client.post(f"{base(flow)}/run", json={"input": "cannot override"})
    assert refused.status_code == 422, refused.text
    executed = await client.post(f"{base(flow)}/run", json=configuration.json()["body"])
    assert executed.status_code == 200, executed.text
    assert executed.json()["outputs"][0]["text"] == "固定业务结果"


async def test_publications_and_run_records_cannot_cross_flow_or_owner(editor):
    from langflow.services.auth.platform_actions import ACTIONS
    from langflow.services.database.models.flow.model import Flow
    from langflow.services.database.models.user.model import User
    from langflow.services.deps import session_scope

    client, _, live = editor
    live["actions"] = list(ACTIONS)
    first, second = await text_flow(client), await text_flow(client)
    published_first = (await publish(client, first)).json()
    published_second = (await publish(client, second)).json()
    run_id = str(uuid4())
    assert (await client.post(f"{base(first)}/run", json={"run_id": run_id})).status_code == 200
    assert (await client.get(f"{base(second)}/runs/{run_id}")).status_code == 404
    wrong_release = await client.post(
        f"{base(second)}/publications/{published_first['current']['id']}/activate",
        json={"publication_token": published_second["publication_token"]},
    )
    assert wrong_release.status_code == 404, wrong_release.text
    async with session_scope() as session:
        foreign = User(username=uuid4().hex, password="unused", is_active=True)  # noqa: S106
        session.add(foreign)
        await session.flush()
        stored = await session.get(Flow, UUID(first["id"]))
        stored.user_id = foreign.id
        session.add(stored)
    for method, suffix, body in (
        ("GET", "publication", None),
        ("GET", "publications", None),
        ("GET", f"runs/{run_id}", None),
        ("POST", "run", {}),
        ("POST", "publish", {"published": False, "publication_token": published_first["publication_token"]}),
        (
            "POST",
            f"publications/{published_first['current']['id']}/activate",
            {"publication_token": published_first["publication_token"]},
        ),
    ):
        response = await client.request(method, f"{base(first)}/{suffix}", json=body)
        assert response.status_code == 404, response.text


async def test_project_deletion_preserves_the_published_flow_and_project(editor):
    client, _, live = editor
    live["actions"] += ["publish", "delete", "move", "project_create", "project_delete"]
    project = await client.post("/api/v1/projects/", json={"name": f"publication-{uuid4().hex}"})
    assert project.status_code == 201, project.text
    flow = await text_flow(client)
    moved = await client.patch(
        f"/api/v1/flows/{flow['id']}",
        json={"folder_id": project.json()["id"]},
        headers={"If-Match": flow["version_token"]},
    )
    assert moved.status_code == 200, moved.text
    flow = moved.json()
    assert (await publish(client, flow)).status_code == 200
    removed = await client.delete(f"/api/v1/projects/{project.json()['id']}")
    assert removed.status_code == 409, removed.text
    assert (await client.get(f"{base(flow)}/publication")).json()["status"] == "published"
    assert (await client.get(f"/api/v1/flows/{flow['id']}")).json()["folder_id"] == project.json()["id"]


def test_publication_migration_preserves_legacy_flows_and_creates_restrictive_foreign_keys(tmp_path):
    from importlib import import_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from langflow.services.database.models.flow.model import Flow
    from langflow.services.database.models.workflow_publication import (
        WorkflowPublication,
        WorkflowPublicationRun,
        WorkflowPublicationState,
    )
    from sqlalchemy import create_engine, inspect
    from sqlmodel import Session, SQLModel

    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    try:
        SQLModel.metadata.create_all(engine)
        WorkflowPublicationRun.__table__.drop(engine)
        WorkflowPublicationState.__table__.drop(engine)
        WorkflowPublication.__table__.drop(engine)
        with Session(engine) as session:
            flow = Flow(name="legacy", data={"nodes": [], "edges": []}, access_type="PUBLIC")
            session.add(flow)
            session.commit()
            flow_id = flow.id
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                import_module("langflow.alembic.versions.f1b2c3d4e5a6_workflow_publications").upgrade()
            inspector = inspect(connection)
            assert {"workflow_publication", "workflow_publication_state", "workflow_publication_run"}.issubset(
                set(inspector.get_table_names())
            )
            assert inspector.get_foreign_keys("workflow_publication")[0]["options"]["ondelete"] == "RESTRICT"
        with Session(engine) as session:
            assert session.get(Flow, flow_id).name == "legacy"
            assert session.get(WorkflowPublicationState, flow_id) is None
    finally:
        engine.dispose()
