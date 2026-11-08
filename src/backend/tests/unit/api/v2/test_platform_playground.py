"""Playground keeps real SSO, graph execution and publication isolation."""
# ruff: noqa: F811 - shared authenticated fixtures are injected by pytest.

import asyncio
import json
from uuid import UUID, uuid4

import pytest

from tests.unit.api.v1.test_platform_sso_editing import editor, isolated_auth_configuration  # noqa: F401
from tests.unit.api.v2.test_platform_dry_run import text_flow
from tests.unit.api.v2.test_platform_publications import publish, save_text


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


async def test_chat_input_and_real_node_events_do_not_mutate_saved_draft(editor):
    client, _, live = editor
    live["actions"] += ["dry_run", "stop"]
    flow = await text_flow(client, "saved text")
    run_id = str(uuid4())
    response = await client.post(
        f"/api/v2/platform-playground/{flow['id']}",
        json={"mode": "draft", "input": "hello from chat", "run_id": run_id},
        headers={"If-Match": flow["version_token"]},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    received = events(response)
    assert received[0]["type"] == "run_started"
    assert received[0]["version_token"] == flow["version_token"]
    starts = [item["node_id"] for item in received if item["type"] == "node_started"]
    finishes = [item["node_id"] for item in received if item["type"] == "node_finished"]
    assert starts == ["TextInput-b5", "TextOutput-b5"]
    assert finishes == starts
    assert received[-1]["type"] == "run_finished"
    assert received[-1]["outputs"] == [{"node_id": "TextOutput-b5", "text": "hello from chat"}]
    assert all(item["run_id"] == run_id for item in received)
    unchanged = (await client.get(f"/api/v1/flows/{flow['id']}")).json()
    assert unchanged["data"] == flow["data"]
    assert unchanged["version_token"] == flow["version_token"]


async def test_playground_runs_pinned_publication_after_draft_changes(editor):
    client, _, live = editor
    live["actions"] += ["publish", "run"]
    flow = await text_flow(client, "V1")
    release = (await publish(client, flow)).json()["current"]
    await save_text(client, flow, "V2")
    response = await client.post(
        f"/api/v2/platform-playground/{flow['id']}",
        json={"mode": "published", "input": "business input", "run_id": str(uuid4())},
        headers={"If-Match": release["id"]},
    )
    assert response.status_code == 200, response.text
    final = events(response)[-1]
    assert final["type"] == "run_finished"
    assert final["publication_id"] == release["id"]
    assert final["publication_version"] == 1
    assert final["outputs"][0]["text"] == "business input"


@pytest.mark.parametrize("mode", ["draft", "published"])
async def test_playground_requires_mode_permission_and_owned_flow(editor, mode):
    client, _, _ = editor
    flow = await text_flow(client, "text")
    response = await client.post(
        f"/api/v2/platform-playground/{flow['id']}",
        json={"mode": mode, "input": "denied", "run_id": str(uuid4())},
        headers={"If-Match": flow["version_token"]},
    )
    assert response.status_code == 403


async def test_stale_missing_version_and_runtime_overrides_are_rejected_before_stream(editor):
    client, _, live = editor
    live["actions"] += ["dry_run", "run"]
    flow = await text_flow(client, "text")
    url = f"/api/v2/platform-playground/{flow['id']}"
    body = {"mode": "draft", "input": "text", "run_id": str(uuid4())}
    assert (await client.post(url, json=body)).status_code == 428
    assert (await client.post(url, json=body, headers={"If-Match": str(uuid4())})).status_code == 409
    assert (
        await client.post(url, json={**body, "data": {}}, headers={"If-Match": flow["version_token"]})
    ).status_code == 422
    body["mode"] = "published"
    assert (await client.post(url, json=body, headers={"If-Match": flow["version_token"]})).status_code == 409


async def test_revocation_during_node_execution_never_delivers_output(editor, monkeypatch):
    client, _, live = editor
    live["actions"] += ["dry_run"]
    flow = await text_flow(client, "private result")
    from langflow.services.auth import platform_execution

    original = platform_execution.execute

    async def revoked(*args, **kwargs):
        result = await original(*args, **kwargs)
        live["active"] = False
        return result

    monkeypatch.setattr(platform_execution, "execute", revoked)
    response = await client.post(
        f"/api/v2/platform-playground/{flow['id']}",
        json={"mode": "draft", "input": "private result", "run_id": str(uuid4())},
        headers={"If-Match": flow["version_token"]},
    )
    assert response.status_code == 200, response.text
    received = events(response)
    assert received[-1]["type"] == "run_error"
    assert received[-1]["status_code"] == 401
    assert received[-1]["code"] == "PLATFORM_SSO_REQUIRED"
    assert all("outputs" not in item for item in received)


async def test_completed_run_id_cannot_be_reused_for_second_draft_execution(editor):
    client, _, live = editor
    live["actions"] += ["dry_run"]
    flow = await text_flow(client, "text")
    url = f"/api/v2/platform-playground/{flow['id']}"
    body = {"mode": "draft", "input": "text", "run_id": str(uuid4())}
    headers = {"If-Match": flow["version_token"]}
    first = await client.post(url, json=body, headers=headers)
    assert first.status_code == 200, first.text
    second = await client.post(url, json=body, headers=headers)
    assert second.status_code == 409, second.text


@pytest.mark.parametrize("mode", ["draft", "published"])
async def test_playground_stop_cancels_active_execution_and_cleans_worker_state(editor, monkeypatch, mode):
    from langflow.services.auth import platform_execution
    from langflow.services.auth.platform_dry_run import ACTIVE_USERS

    client, actor, live = editor
    live["actions"] += ["dry_run", "publish", "run", "stop"]
    flow = await text_flow(client, "text")
    version = flow["version_token"] if mode == "draft" else (await publish(client, flow)).json()["current"]["id"]
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def waiting(*_args, **_kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(platform_execution, "execute", waiting)
    run_id = str(uuid4())
    task = asyncio.create_task(
        client.post(
            f"/api/v2/platform-playground/{flow['id']}",
            json={"mode": mode, "input": "hello", "run_id": run_id},
            headers={"If-Match": version},
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    stopped = await client.post(f"/api/v2/platform-workflow/flows/{flow['id']}/runs/{run_id}/stop", json={})
    assert stopped.status_code == 200, stopped.text
    response = await asyncio.wait_for(task, timeout=5)
    assert events(response)[-1]["type"] == "run_cancelled"
    assert cancelled.is_set()
    assert (str(actor.id), flow["id"], run_id) not in platform_execution.ACTIVE
    assert str(actor.id) not in ACTIVE_USERS


async def test_playground_foreign_flow_and_oversized_input_are_rejected_before_stream(editor):
    from langflow.services.database.models.flow.model import Flow
    from langflow.services.database.models.user.model import User
    from langflow.services.deps import session_scope

    client, _, live = editor
    live["actions"] += ["dry_run"]
    flow = await text_flow(client, "text")
    path = f"/api/v2/platform-playground/{flow['id']}"
    body = {"mode": "draft", "input": "汉" * 30000, "run_id": str(uuid4())}
    assert (await client.post(path, json=body, headers={"If-Match": flow["version_token"]})).status_code == 422
    async with session_scope() as session:
        other = User(username=f"other-{uuid4().hex}", password="unused", is_active=True)  # noqa: S106
        session.add(other)
        await session.flush()
        record = await session.get(Flow, UUID(flow["id"]))
        record.user_id = other.id
    body["input"] = "hello"
    assert (await client.post(path, json=body, headers={"If-Match": flow["version_token"]})).status_code == 404


async def test_closing_stream_cancels_worker_even_when_model_cancel_bridge_fails(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from fastapi import Request
    from langflow.api.v2 import platform_playground
    from langflow.services.auth import platform_execution

    actor, flow_id, run_id = uuid4(), uuid4(), uuid4()
    key = (str(actor), str(flow_id), str(run_id))
    cancelled = asyncio.Event()
    bridge_called = asyncio.Event()

    async def model_call(*_args):
        bridge_called.set()
        raise RuntimeError

    request = Request({"type": "http", "headers": [(b"if-match", str(uuid4()).encode())], "query_string": b""})
    request.state.platform_workflow = {
        "user_id": str(actor),
        "actions": ["read", "dry_run"],
        "host_origin": "http://localhost:5999",
        "model_call": model_call,
    }

    async def execute():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def handler(*_args):
        task = asyncio.create_task(execute())
        platform_execution.ACTIVE[key] = task
        platform_execution.ACTIVE_MODELS.add(key)
        platform_execution.emit("run_started", version_token=str(uuid4()))
        try:
            await task
        finally:
            platform_execution.ACTIVE.pop(key, None)
            platform_execution.ACTIVE_MODELS.discard(key)

    monkeypatch.setattr(platform_playground, "dry_run", handler)

    @asynccontextmanager
    async def session():
        yield None

    monkeypatch.setattr(platform_playground, "session_scope", session)
    response = await platform_playground.playground(
        flow_id,
        platform_playground.PlaygroundBody(mode="draft", run_id=run_id),
        request,
        SimpleNamespace(id=actor),
    )
    await anext(response.body_iterator)
    await asyncio.sleep(0)
    await response.body_iterator.aclose()
    assert bridge_called.is_set()
    assert cancelled.is_set()
    assert key not in platform_execution.ACTIVE
