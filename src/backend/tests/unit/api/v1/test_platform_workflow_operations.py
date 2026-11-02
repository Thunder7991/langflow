"""Exercise expanded platform permissions through real routes and owner storage."""

# ruff: noqa: F811 - pytest uses the imported editor fixture.

from uuid import UUID, uuid4

import pytest

from tests.unit.api.v1.test_platform_sso_editing import GRAPH, create, editor, isolated_auth_configuration  # noqa: F401


async def test_delete_is_independent_from_editing_and_enforced_by_api(editor):
    client, _, live = editor
    flow = await create(client)
    assert (await client.delete(f"/api/v1/flows/{flow['id']}")).status_code == 403
    live["actions"] = ["read", "delete"]
    response = await client.delete(f"/api/v1/flows/{flow['id']}")
    assert response.status_code == 200, response.text
    assert (await client.get(f"/api/v1/flows/{flow['id']}")).status_code == 404


async def test_project_rename_and_export_have_independent_permissions(editor):
    client, _, live = editor
    live["actions"] += ["project_create"]
    response = await client.post("/api/v1/projects/", json={"name": uuid4().hex})
    assert response.status_code == 201, response.text
    project = response.json()
    response = await client.post(
        "/api/v1/flows/", json={"name": "exportable", "data": GRAPH, "folder_id": project["id"]}
    )
    assert response.status_code == 201, response.text
    live["actions"] = ["read", "project_update", "project_export"]
    response = await client.patch(f"/api/v1/projects/{project['id']}", json={"name": "renamed"})
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "renamed"
    assert (await client.get(f"/api/v1/projects/download/{project['id']}")).status_code == 200
    response = await client.patch(f"/api/v1/projects/{project['id']}", json={"auth_settings": {"enabled": True}})
    assert response.status_code == 403, response.text


async def test_nonempty_project_delete_requires_flow_delete_too(editor):
    client, _, live = editor
    live["actions"].append("project_create")
    project = (await client.post("/api/v1/projects/", json={"name": uuid4().hex})).json()
    flow = (
        await client.post("/api/v1/flows/", json={"name": "keep", "data": GRAPH, "folder_id": project["id"]})
    ).json()
    live["actions"] = ["read", "project_delete"]
    assert (await client.delete(f"/api/v1/projects/{project['id']}")).status_code == 403
    assert (await client.get(f"/api/v1/flows/{flow['id']}")).status_code == 200
    live["actions"].append("delete")
    response = await client.delete(f"/api/v1/projects/{project['id']}")
    assert response.status_code == 204, response.text
    assert (await client.get(f"/api/v1/flows/{flow['id']}")).status_code == 404


async def test_duplicate_and_import_create_new_owned_private_flows(editor):
    client, actor, live = editor
    flow = await create(client)
    live["actions"] = ["read", "create", "duplicate", "import", "export"]
    response = await client.post(f"/api/v2/platform-workflow/flows/{flow['id']}/duplicate", json={})
    assert response.status_code == 201, response.text
    copied = response.json()
    assert copied["id"] != flow["id"]
    assert copied["user_id"] == str(actor.id)
    assert copied["access_type"] == "PRIVATE"
    response = await client.post("/api/v2/platform-workflow/import", json={"flows": [flow]})
    assert response.status_code == 201, response.text
    assert response.json()[0]["id"] not in {flow["id"], copied["id"]}
    live["actions"] = ["read", "create"]
    assert (await client.post("/api/v2/platform-workflow/import", json={"flows": [flow]})).status_code == 403


async def test_project_import_matches_role_menu_dependencies_and_does_not_grant_flow_import(editor):
    client, actor, live = editor
    flow = await create(client)
    body = {"flows": [flow], "project_name": uuid4().hex}
    live["actions"] = ["read", "create", "project_create", "project_import"]
    response = await client.post("/api/v2/platform-workflow/projects/import", json=body)
    assert response.status_code == 201, response.text
    assert response.json()["flows"][0]["user_id"] == str(actor.id)
    assert response.json()["flows"][0]["folder_id"] == response.json()["project"]["id"]
    assert (await client.post("/api/v2/platform-workflow/import", json={"flows": [flow]})).status_code == 403
    live["actions"].remove("project_create")
    before = (await client.get("/api/v1/projects/")).json()
    assert (await client.post("/api/v2/platform-workflow/projects/import", json=body)).status_code == 403
    assert (await client.get("/api/v1/projects/")).json() == before


async def test_versions_cannot_restore_untrusted_graph_or_bypass_permissions(editor):
    client, _, live = editor
    flow = await create(client)
    live["actions"] += ["version_read", "version_create", "version_restore", "version_delete"]
    response = await client.post(f"/api/v1/flows/{flow['id']}/versions/", json={"description": "checkpoint"})
    assert response.status_code == 201, response.text
    version = response.json()
    from langflow.services.database.models.flow_version.model import FlowVersion
    from langflow.services.deps import session_scope

    async with session_scope() as session:
        stored = await session.get(FlowVersion, UUID(version["id"]))
        stored.data = {**GRAPH, "nodes": [{"code": "import os"}]}
        session.add(stored)
    response = await client.post(
        f"/api/v1/flows/{flow['id']}/versions/{version['id']}/activate",
        json={},
        headers={"If-Match": flow["version_token"]},
    )
    assert response.status_code == 403, response.text
    unchanged = (await client.get(f"/api/v1/flows/{flow['id']}")).json()
    assert unchanged["data"] == flow["data"]
    assert unchanged["version_token"] == flow["version_token"]
    live["actions"] = ["read", "version_read"]
    assert (await client.delete(f"/api/v1/flows/{flow['id']}/versions/{version['id']}")).status_code == 403
    live["actions"] += ["version_delete"]
    assert (await client.delete(f"/api/v1/flows/{flow['id']}/versions/{version['id']}")).status_code == 204


async def test_new_operations_do_not_allow_native_auth_or_custom_execution(editor):
    client, _, live = editor
    live["actions"] = ["read", "run", "stop", "publish", "share", "api_access", "import", "create"]
    assert (await client.post("/api/v1/api_key/", json={})).status_code == 403
    assert (await client.post("/api/v2/workflows", json={})).status_code == 403
    malicious = {"name": "untrusted", "data": {**GRAPH, "nodes": [{"code": "import os"}]}}
    assert (await client.post("/api/v2/platform-workflow/import", json={"flows": [malicious]})).status_code == 403


async def test_general_run_accepts_saved_graph_input_and_checks_version(editor):
    from tests.unit.api.v2.test_platform_dry_run import text_flow

    client, _, live = editor
    flow = await text_flow(client, "saved")
    live["actions"] = ["read", "run"]
    path = f"/api/v2/platform-workflow/flows/{flow['id']}/run"
    assert (await client.post(path, json={})).status_code == 428
    response = await client.post(path, json={"input": "runtime"}, headers={"If-Match": flow["version_token"]})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "succeeded"
    assert {"node_id": "TextOutput-b5", "text": "runtime"} in response.json()["outputs"]
    restored = (await client.get(f"/api/v1/flows/{flow['id']}")).json()
    assert restored["data"] == flow["data"]
    assert (
        await client.post(path, json={"data": flow["data"]}, headers={"If-Match": flow["version_token"]})
    ).status_code == 422


async def test_general_model_graph_uses_platform_bridge_and_server_edge_types(editor, monkeypatch):
    from langflow.services.auth import platform_models

    from tests.unit.api.v2.test_platform_models import CONNECTION, model_flow

    client, _, live = editor
    calls = []

    async def gateway(action, body, _settings):
        if action == "catalog":
            return {"connections": [{"id": CONNECTION, "name": "test", "provider": "openai_compatible"}], "models": []}
        assert action == "invoke"
        assert body["connectionId"] == CONNECTION
        assert body["input"] == "saved question"
        assert body["executionId"] != body["runId"]
        calls.append(body)
        return {"runId": body["runId"], "text": "platform answer"}

    monkeypatch.setattr(platform_models, "call", gateway)
    flow = await model_flow(client)
    live["actions"] = ["read", "run"]
    response = await client.post(
        f"/api/v2/platform-workflow/flows/{flow['id']}/run",
        json={},
        headers={"If-Match": flow["version_token"]},
    )
    assert response.status_code == 200, response.text
    assert {"node_id": "TextOutput-b5", "text": "platform answer"} in response.json()["outputs"]
    assert len(calls) == 1


async def test_general_prompt_graph_can_branch_to_multiple_outputs(editor):
    import json
    from copy import deepcopy

    from tests.unit.api.v1.test_platform_sso_editing import builtin
    from tests.unit.api.v2.test_platform_dry_run import text_flow

    client, _, live = editor
    flow = await text_flow(client, "hello")
    data = deepcopy(flow["data"])
    prompt = await builtin(client, "Prompt Template")
    prompt["template"]["template"]["value"] = "Result: {message}"
    prompt["template"]["message"] = {"type": "str", "value": "", "input_types": ["Message"]}
    data["nodes"].append(
        {"id": "Prompt-1", "type": "genericNode", "data": {"id": "Prompt-1", "type": "Prompt Template", "node": prompt}}
    )
    second = deepcopy(data["nodes"][1])
    second["id"] = second["data"]["id"] = "TextOutput-second"
    data["nodes"].append(second)

    def edge(source, output, target, field):
        return {
            "source": source,
            "target": target,
            "sourceHandle": json.dumps({"id": source, "name": output}),
            "targetHandle": json.dumps({"id": target, "fieldName": field}),
        }

    data["edges"] = [
        edge("TextInput-b5", "text", "Prompt-1", "message"),
        edge("Prompt-1", "prompt", "TextOutput-b5", "input_value"),
        edge("Prompt-1", "prompt", "TextOutput-second", "input_value"),
    ]
    response = await client.patch(
        f"/api/v1/flows/{flow['id']}", json={"data": data}, headers={"If-Match": flow["version_token"]}
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    live["actions"] = ["read", "run"]
    result = await client.post(
        f"/api/v2/platform-workflow/flows/{flow['id']}/run", json={}, headers={"If-Match": saved["version_token"]}
    )
    assert result.status_code == 200, result.text
    outputs = result.json()["outputs"]
    assert {"node_id": "TextOutput-b5", "text": "Result: hello"} in outputs
    assert {"node_id": "TextOutput-second", "text": "Result: hello"} in outputs


@pytest.mark.parametrize(
    "data", [{"nodes": None}, {"nodes": [None]}, {"nodes": [{"data": {"node": None}}]}, {"nodes": [], "edges": {}}]
)
def test_malformed_graph_is_rejected_as_input_error(data):
    from fastapi import HTTPException
    from langflow.services.auth.platform_execution import prepared_graph

    with pytest.raises(HTTPException) as error:
        prepared_graph(data)
    assert error.value.status_code == 422


async def test_publish_can_be_reversed_and_does_not_prevent_owner_editing(editor):
    client, _, live = editor
    flow = await create(client)
    live["actions"] += ["publish", "share", "api_access"]
    path = f"/api/v2/platform-workflow/flows/{flow['id']}"
    response = await client.post(f"{path}/publish", json={"published": True})
    assert response.status_code == 200, response.text
    assert response.json()["access_type"] == "PUBLIC"
    assert (await client.patch(f"/api/v1/flows/{flow['id']}", json={"name": "published-edit"})).status_code == 200
    assert (await client.get(f"{path}/share")).json()["authentication"] == "platform_sso"
    assert (await client.get(f"{path}/api")).json()["required_action"] == "run"
    response = await client.post(f"{path}/publish", json={"published": False})
    assert response.json()["access_type"] == "PRIVATE"


async def test_stop_cancels_the_owned_server_task_without_granting_other_operations(editor, monkeypatch):
    import asyncio

    from langflow.services.auth import platform_execution

    from tests.unit.api.v2.test_platform_dry_run import text_flow

    client, _, live = editor
    flow = await text_flow(client)
    live["actions"] = ["read", "run", "stop"]
    entered = asyncio.Event()

    async def blocked(*_args, **_kwargs):
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr(platform_execution, "execute", blocked)
    run_id = str(uuid4())
    path = f"/api/v2/platform-workflow/flows/{flow['id']}"
    task = asyncio.create_task(
        client.post(f"{path}/run", json={"run_id": run_id}, headers={"If-Match": flow["version_token"]})
    )
    await asyncio.wait_for(entered.wait(), 5)
    live["actions"] = ["read", "run"]
    assert (await client.post(f"{path}/runs/{run_id}/stop", json={})).status_code == 403
    live["actions"].append("stop")
    response = await client.post(f"{path}/runs/{run_id}/stop", json={})
    assert response.status_code == 200, response.text
    assert (await asyncio.wait_for(task, 5)).json()["status"] == "cancelled"
    assert not platform_execution.ACTIVE


async def test_full_action_grants_do_not_allow_other_users_resources(editor):
    from langflow.services.auth.platform_actions import ACTIONS
    from langflow.services.database.models.flow.model import Flow
    from langflow.services.database.models.user.model import User
    from langflow.services.deps import session_scope

    client, _, live = editor
    async with session_scope() as session:
        foreign = User(username=uuid4().hex, password="test-only", is_active=True)  # noqa: S106
        session.add(foreign)
        await session.flush()
        flow = Flow(name="foreign", user_id=foreign.id, data=GRAPH)
        session.add(flow)
        await session.flush()
        foreign_id = str(flow.id)
    live["actions"] = list(ACTIONS)
    for action in ["export", "share", "api"]:
        assert (await client.get(f"/api/v2/platform-workflow/flows/{foreign_id}/{action}")).status_code == 404
    assert (await client.delete(f"/api/v1/flows/{foreign_id}")).status_code == 404
    assert (await client.request("DELETE", "/api/v1/flows/", json=[foreign_id])).status_code == 404
