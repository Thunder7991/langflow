"""Independent DB sessions serialize publication, draft writes and delete guards."""

# ruff: noqa: F811 - imported database fixture is injected by pytest.

import asyncio
import json
import sys
from copy import deepcopy
from importlib import import_module
from inspect import getsource
from uuid import uuid4

import pytest
from fastapi import HTTPException
from langflow.api.v2.platform_workflow import PublishBody, publish
from langflow.services.database.models.flow.model import Flow
from langflow.services.database.models.user.model import User
from langflow.services.database.models.workflow_publication import WorkflowPublication
from lfx.custom.utils import build_custom_component_template
from sqlalchemy.exc import DBAPIError
from sqlmodel import select, update
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.requests import Request

from tests.unit.services.database.models.flow_version.test_delete_attachment_concurrency import db_engine  # noqa: F401


@pytest.fixture
def event_loop_policy():
    return asyncio.WindowsSelectorEventLoopPolicy() if sys.platform == "win32" else asyncio.DefaultEventLoopPolicy()


def graph():
    nodes = []
    for kind, module, name in (
        ("TextInput", "text", "TextInputComponent"),
        ("TextOutput", "text_output", "TextOutputComponent"),
    ):
        imported = import_module(f"lfx.components.input_output.{module}")
        template, _ = build_custom_component_template(getattr(imported, name)(_code=getsource(imported)))
        template["template"]["input_value"]["value"] = "published" if kind == "TextInput" else ""
        nodes.append({"id": kind, "type": "genericNode", "data": {"id": kind, "type": kind, "node": template}})
    return {
        "nodes": nodes,
        "edges": [
            {
                "source": "TextInput",
                "target": "TextOutput",
                "sourceHandle": json.dumps({"id": "TextInput", "name": "text"}),
                "targetHandle": json.dumps({"id": "TextOutput", "fieldName": "input_value"}),
            }
        ],
    }


@pytest.mark.parametrize("db_engine", ["sqlite", "postgres", "postgres-repeatable-read"], indirect=True)
@pytest.mark.usefixtures("client")
async def test_first_publication_race_cannot_create_two_versions_from_same_state(db_engine, monkeypatch, request):
    monkeypatch.setenv("LANGFLOW_AUTHZ_ENABLED", "false")
    if request.node.callspec.params["db_engine"] == "postgres-repeatable-read":
        db_engine = db_engine.execution_options(isolation_level="REPEATABLE READ")
    actor = User(username=uuid4().hex, password="test-only", is_active=True)  # noqa: S106
    async with AsyncSession(db_engine, expire_on_commit=False) as seed:
        seed.add(actor)
        await seed.flush()
        flow = Flow(name="publication race", user_id=actor.id, data=graph(), version_token=uuid4())
        seed.add(flow)
        await seed.commit()

    async def recheck():
        return ["read", "publish"]

    call = Request(
        {
            "type": "http",
            "headers": [(b"if-match", str(flow.version_token).encode())],
            "state": {
                "platform_workflow": {"user_id": str(actor.id), "actions": ["read", "publish"], "recheck": recheck},
            },
        }
    )
    body = PublishBody(published=True, publication_token=None)
    async with AsyncSession(db_engine, expire_on_commit=False) as first:
        state = await publish(flow.id, body, call, actor, first)
        assert state["current"]["version_number"] == 1
        async with AsyncSession(db_engine) as second:
            with pytest.raises(HTTPException) as blocked:
                await publish(flow.id, body, call, actor, second)
            assert blocked.value.status_code in {409, 503}
            await second.rollback()
        await first.commit()
    async with AsyncSession(db_engine) as duplicate:
        with pytest.raises(HTTPException) as stale:
            await publish(flow.id, body, call, actor, duplicate)
        assert stale.value.status_code == 409
        await duplicate.rollback()
        releases = (
            await duplicate.exec(select(WorkflowPublication).where(WorkflowPublication.flow_id == flow.id))
        ).all()
        assert len(releases) == 1
        assert releases[0].data == flow.data


@pytest.mark.parametrize("db_engine", ["sqlite", "postgres"], indirect=True)
async def test_concurrent_draft_write_cannot_change_the_selected_publication_snapshot(db_engine, monkeypatch):
    from langflow.services.auth.platform_publications import lock_flow

    monkeypatch.setenv("LANGFLOW_AUTHZ_ENABLED", "false")
    actor = User(username=uuid4().hex, password="test-only", is_active=True)  # noqa: S106
    async with AsyncSession(db_engine, expire_on_commit=False) as seed:
        seed.add(actor)
        await seed.flush()
        flow = Flow(name="save race", user_id=actor.id, data=graph(), version_token=uuid4())
        seed.add(flow)
        await seed.commit()
    async with AsyncSession(db_engine) as publisher:
        locked = await publisher.get(Flow, flow.id)
        await lock_flow(publisher, locked)
        changed = deepcopy(flow.data)
        changed["nodes"][0]["data"]["node"]["template"]["input_value"]["value"] = "new draft"
        async with AsyncSession(db_engine) as writer:
            # The native save uses this Flow UPDATE, including when it runs on another worker.
            if writer.get_bind().dialect.name == "postgresql":
                from sqlalchemy import text

                await writer.exec(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError):
                await writer.exec(update(Flow).where(Flow.id == flow.id).values(data=changed))
            await writer.rollback()
        assert locked.data == flow.data
        await publisher.rollback()


@pytest.mark.parametrize("db_engine", ["postgres"], indirect=True)
async def test_repeatable_read_cannot_publish_a_stale_draft_after_a_concurrent_save(db_engine):
    from langflow.services.auth.platform_publications import lock_flow

    engine = db_engine.execution_options(isolation_level="REPEATABLE READ")
    async with AsyncSession(engine, expire_on_commit=False) as seed:
        flow = Flow(name="stale draft", data={"nodes": [], "edges": []}, version_token=uuid4())
        seed.add(flow)
        await seed.commit()
    async with AsyncSession(engine) as reader:
        stale = await reader.get(Flow, flow.id)
        async with AsyncSession(engine) as writer:
            await writer.exec(update(Flow).where(Flow.id == flow.id).values(version_token=uuid4()))
            await writer.commit()
        with pytest.raises(HTTPException) as refused:
            await lock_flow(reader, stale)
        assert refused.value.status_code == 503
        await reader.rollback()


@pytest.mark.parametrize("db_engine", ["postgres"], indirect=True)
async def test_publication_migration_on_postgres_preserves_existing_flows(db_engine):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from langflow.services.database.models.workflow_publication import WorkflowPublicationRun, WorkflowPublicationState
    from sqlalchemy import inspect

    async with db_engine.begin() as connection:
        for table in (WorkflowPublicationRun, WorkflowPublicationState, WorkflowPublication):
            await connection.run_sync(table.__table__.drop)
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        flow = Flow(name="legacy PUBLIC", access_type="PUBLIC", data={"nodes": [], "edges": []})
        session.add(flow)
        await session.commit()

    def migrate(connection):
        with Operations.context(MigrationContext.configure(connection)):
            import_module("langflow.alembic.versions.f1b2c3d4e5a6_workflow_publications").upgrade()
        inspector = inspect(connection)
        assert inspector.get_foreign_keys("workflow_publication")[0]["options"]["ondelete"] == "RESTRICT"
        assert {"flow_id", "version_number"} in [
            set(item["column_names"]) for item in inspector.get_unique_constraints("workflow_publication")
        ]

    async with db_engine.begin() as connection:
        await connection.run_sync(migrate)
    async with AsyncSession(db_engine) as session:
        assert (await session.get(Flow, flow.id)).access_type == "PUBLIC"
        assert await session.get(WorkflowPublicationState, flow.id) is None


@pytest.mark.parametrize("db_engine", ["postgres"], indirect=True)
@pytest.mark.usefixtures("client")
async def test_same_worker_rejects_a_second_run_while_admission_is_committing(db_engine, monkeypatch):
    from langflow.api import utils
    from langflow.api.v2.platform_workflow import RunBody, run
    from langflow.services.database.models.workflow_publication import WorkflowPublicationState

    monkeypatch.setenv("LANGFLOW_AUTHZ_ENABLED", "false")
    actor = User(username=uuid4().hex, password="unused", is_active=True)  # noqa: S106
    async with AsyncSession(db_engine, expire_on_commit=False) as seed:
        seed.add(actor)
        await seed.flush()
        flows = [Flow(name=f"run-{i}", user_id=actor.id, data=graph(), version_token=uuid4()) for i in range(2)]
        seed.add_all(flows)
        await seed.flush()
        for flow in flows:
            publication = WorkflowPublication(
                flow_id=flow.id,
                version_number=1,
                source_version_token=flow.version_token,
                data=flow.data,
                digest="test",
                input_schema={"nodes": ["TextInput"]},
                output_schema={"nodes": ["TextOutput"]},
                dependencies={},
                created_by=actor.id,
            )
            seed.add(publication)
            await seed.flush()
            seed.add(WorkflowPublicationState(flow_id=flow.id, publication_id=publication.id, latest_version=1))
        await seed.commit()

    async def recheck():
        return ["read", "run"]

    request = Request(
        {
            "type": "http",
            "headers": [],
            "state": {"platform_workflow": {"user_id": str(actor.id), "actions": ["read", "run"], "recheck": recheck}},
        }
    )
    committing, proceed = asyncio.Event(), asyncio.Event()
    original_commit = utils.release_db_transaction

    async def paused_commit(session):
        committing.set()
        await proceed.wait()
        await original_commit(session)

    monkeypatch.setattr(utils, "release_db_transaction", paused_commit)
    async with AsyncSession(db_engine, expire_on_commit=False) as first, AsyncSession(db_engine) as second:
        first_task = asyncio.create_task(run(flows[0].id, RunBody(), request, actor, first))
        try:
            await asyncio.wait_for(committing.wait(), 10)
            with pytest.raises(HTTPException) as rejected:
                await asyncio.wait_for(run(flows[1].id, RunBody(), request, actor, second), 5)
            assert rejected.value.status_code == 429
        finally:
            proceed.set()
            await second.rollback()
            result = await asyncio.wait_for(first_task, 10)
        assert result["status"] == "succeeded"
