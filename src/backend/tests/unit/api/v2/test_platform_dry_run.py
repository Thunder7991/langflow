"""Platform dry runs exercise real SSO, saved versions and the local text graph."""

# ruff: noqa: F811 - pytest injects the imported editor fixture by its name.

import json
from uuid import UUID, uuid4

import pytest
from langflow.services.database.models.flow.model import Flow
from langflow.services.deps import session_scope

from tests.unit.api.v1.test_platform_sso_editing import (
    builtin,
    editor,  # noqa: F401
    isolated_auth_configuration,  # noqa: F401
)


async def text_flow(client, text="B5 真实文本执行"):
    nodes = []
    for name in ["TextInput", "TextOutput"]:
        node = await builtin(client, name)
        node["template"]["input_value"]["value"] = text if name == "TextInput" else ""
        nodes.append(
            {
                "id": f"{name}-b5",
                "type": "genericNode",
                "position": {"x": 0, "y": 0},
                "data": {"id": f"{name}-b5", "type": name, "node": node},
            }
        )
    edge = {
        "id": "text-edge",
        "source": "TextInput-b5",
        "target": "TextOutput-b5",
        "sourceHandle": json.dumps({"id": "TextInput-b5", "name": "text", "output_types": ["Message"]}),
        "targetHandle": json.dumps(
            {"id": "TextOutput-b5", "fieldName": "input_value", "type": "str", "inputTypes": ["Message"]}
        ),
    }
    response = await client.post("/api/v1/flows/", json={"name": "b5-text", "data": {"nodes": nodes, "edges": [edge]}})
    assert response.status_code == 201, response.text
    return response.json()


def path(flow):
    return f"/api/v2/platform-flows/{flow['id']}/dry-run"


async def run(client, flow, **kwargs):
    return await client.post(path(flow), json={}, headers={"If-Match": flow["version_token"]}, **kwargs)


async def test_saved_text_graph_executes_without_changing_flow(editor):
    client, _, live = editor
    flow = await text_flow(client)
    live["actions"] = ["read", "dry_run"]
    response = await run(client, flow)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "succeeded"
    assert result["version_token"] == flow["version_token"]
    assert result["outputs"] == [{"node_id": "TextOutput-b5", "text": "B5 真实文本执行"}]
    restored = (await client.get(f"/api/v1/flows/{flow['id']}")).json()
    assert restored["data"] == flow["data"]
    assert restored["version_token"] == flow["version_token"]


@pytest.mark.parametrize("text", ["", "0", "换行\n中文"])
async def test_text_results_preserve_empty_zero_and_unicode(editor, text):
    client, _, live = editor
    flow = await text_flow(client, text)
    live["actions"].append("dry_run")
    response = await run(client, flow)
    assert response.status_code == 200, response.text
    assert response.json()["outputs"] == [{"node_id": "TextOutput-b5", "text": text}]


async def test_editing_does_not_grant_execution_and_old_run_routes_remain_closed(editor):
    client, _, live = editor
    flow = await text_flow(client)
    assert (await run(client, flow)).status_code == 403
    live["actions"].append("dry_run")
    for url in [f"/api/v1/run/{flow['id']}", "/api/v2/workflows", "/api/v1/build/flow", f"{path(flow)}/stop"]:
        assert (await client.post(url, json={})).status_code == 403
    live["actions"] = ["read"]
    assert (await run(client, flow)).status_code == 403


async def test_precondition_and_extra_payload_cannot_run_a_different_graph(editor):
    client, _, live = editor
    flow = await text_flow(client)
    live["actions"].append("dry_run")
    assert (await client.post(path(flow), json={})).status_code == 428
    assert (
        await client.post(path(flow), json={}, headers={"If-Match": '"00000000-0000-4000-8000-000000000001"'})
    ).status_code == 409
    response = await client.post(
        path(flow), json={"data": flow["data"], "user_id": "forged"}, headers={"If-Match": flow["version_token"]}
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("attack", "expected"),
    [
        ("code", 403),
        ("global", 403),
        ("file", 403),
        ("model", 403),
        ("cycle", 422),
        ("handle", 422),
        ("malformed", 422),
        ("long_text", 422),
    ],
)
async def test_unsafe_saved_graph_is_refused_before_execution(editor, monkeypatch, attack, expected):
    from langflow.services.auth import platform_dry_run

    client, _, live = editor
    flow = await text_flow(client)
    data = flow["data"]
    template = data["nodes"][0]["data"]["node"]["template"]
    if attack == "code":
        template["code"]["value"] = "raise RuntimeError('must never run')"
    elif attack == "global":
        template["use_global_variable"]["value"] = True
    elif attack == "file":
        template["extra_file"] = {"type": "file", "value": ["secret.txt"], "file_path": ["another/secret.txt"]}
    elif attack == "model":
        data["nodes"][0]["data"]["type"] = "OpenAIModel"
    elif attack == "cycle":
        data["edges"][0]["target"] = "TextInput-b5"
    elif attack == "handle":
        data["edges"][0]["targetHandle"] = json.dumps({"id": "TextOutput-b5", "fieldName": "code"})
    elif attack == "malformed":
        data["nodes"][0]["data"]["node"] = None
    elif attack == "long_text":
        template["input_value"]["value"] = "中" * 22000
    async with session_scope() as session:
        row = await session.get(Flow, UUID(flow["id"]))
        row.data = data
        session.add(row)

    async def forbidden_execution(*_args, **_kwargs):
        pytest.fail("Invalid saved graph reached the runtime")

    monkeypatch.setattr(platform_dry_run, "execute_text_pair", forbidden_execution)
    live["actions"].append("dry_run")
    response = await run(client, flow)
    assert response.status_code == expected, response.text


async def test_other_owner_and_wrong_tenant_cannot_execute_existing_flow(editor, monkeypatch):
    from langflow.services.auth import platform_sso
    from langflow.services.database.models.user.model import User

    client, _, live = editor
    flow = await text_flow(client)
    async with session_scope() as session:
        other = User(username=f"b5-other-{uuid4().hex}", password="unused", is_active=True)  # noqa: S106
        session.add(other)
        await session.flush()
        row = await session.get(Flow, UUID(flow["id"]))
        row.user_id = other.id
        session.add(row)
    live["actions"].append("dry_run")
    assert (await run(client, flow)).status_code == 404
    original = platform_sso.platform_call

    async def wrong_tenant(*args):
        return {**await original(*args), "tenantId": "other-tenant"}

    monkeypatch.setattr(platform_sso, "platform_call", wrong_tenant)
    assert (await run(client, flow)).status_code == 401


async def test_csrf_and_stream_options_remain_closed(editor):
    client, _, live = editor
    flow = await text_flow(client)
    live["actions"].append("dry_run")
    assert (
        await client.post(
            path(flow), json={}, headers={"Origin": "https://evil.example", "If-Match": flow["version_token"]}
        )
    ).status_code == 403
    assert (
        await client.post(path(flow) + "?stream=true", json={}, headers={"If-Match": flow["version_token"]})
    ).status_code == 422


async def test_runtime_failure_and_timeout_release_the_user_slot(editor, monkeypatch):
    import asyncio

    from langflow.services.auth import platform_dry_run

    client, _, live = editor
    flow = await text_flow(client)
    live["actions"].append("dry_run")
    original = platform_dry_run.execute_text_pair

    async def fail(*_args, **_kwargs):
        message = "internal secret trace must not be returned"
        raise RuntimeError(message)

    monkeypatch.setattr(platform_dry_run, "execute_text_pair", fail)
    response = await run(client, flow)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "platform_dry_run_failed"
    assert "internal secret" not in response.text

    async def slow(*_args, **_kwargs):
        await asyncio.sleep(1)
        return "late"

    monkeypatch.setattr(platform_dry_run, "execute_text_pair", slow)
    monkeypatch.setattr(platform_dry_run, "TIMEOUT_SECONDS", 0.01)
    assert (await run(client, flow)).status_code == 504
    monkeypatch.setattr(platform_dry_run, "execute_text_pair", original)
    monkeypatch.setattr(platform_dry_run, "TIMEOUT_SECONDS", 10)
    assert (await run(client, flow)).status_code == 200


@pytest.mark.parametrize("session_expired", [False, True])
async def test_revoked_access_does_not_return_completed_private_output(editor, monkeypatch, session_expired):
    from langflow.services.auth import platform_dry_run

    client, _, live = editor
    flow = await text_flow(client)
    live["actions"].append("dry_run")
    original = platform_dry_run.execute_text_pair

    async def revoke(*args, **kwargs):
        value = await original(*args, **kwargs)
        if session_expired:
            live["active"] = False
        else:
            live["actions"] = ["read"]
        return value

    monkeypatch.setattr(platform_dry_run, "execute_text_pair", revoke)
    response = await run(client, flow)
    assert response.status_code == (401 if session_expired else 403)
    assert "B5 真实文本执行" not in response.text
    if session_expired:
        assert response.json()["code"] == "PLATFORM_SSO_REQUIRED"
        assert response.headers["x-workflow-actions"] == ""


async def test_overlapping_requests_for_one_user_are_not_queued(editor, monkeypatch):
    import asyncio

    from langflow.services.auth import platform_dry_run

    client, _, live = editor
    flow = await text_flow(client)
    live["actions"].append("dry_run")
    started, release = asyncio.Event(), asyncio.Event()
    original = platform_dry_run.execute_text_pair

    async def wait_for_release(*args, **kwargs):
        started.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(platform_dry_run, "execute_text_pair", wait_for_release)
    first = asyncio.create_task(run(client, flow))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        assert (await run(client, flow)).status_code == 429
    finally:
        release.set()
        response = await first
    assert response.status_code == 200, response.text
