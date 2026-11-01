"""Independent project, move and version permissions through real SSO HTTP routes."""
# ruff: noqa: F811 - imported pytest fixtures are injected by name.

import pytest

from tests.unit.api.v1.test_platform_sso_editing import create, editor, isolated_auth_configuration  # noqa: F401


async def test_project_permission_does_not_grant_flow_creation(editor):
    client, _, live = editor
    live["actions"] = ["read", "project_create"]
    assert (await client.post("/api/v1/projects/", json={"name": "project-only"})).status_code == 201
    assert (await client.post("/api/v1/flows/", json={"name": "forbidden"})).status_code == 403
    live["actions"] = ["read", "create"]
    assert (await client.post("/api/v1/projects/", json={"name": "forbidden"})).status_code == 403


async def test_version_read_is_independent_and_revocation_does_not_hide_current_flow(editor):
    client, _, live = editor
    flow = await create(client)
    path = f"/api/v1/flows/{flow['id']}"
    assert (await client.get(path + "/versions/")).status_code == 403
    live["actions"] = ["read", "version_read"]
    assert (await client.get(path + "/versions/")).status_code == 200
    live["actions"] = ["read"]
    assert (await client.get(path + "/versions/")).status_code == 403
    assert (await client.get(path)).status_code == 200


async def test_moving_a_personal_flow_requires_both_update_and_move(editor):
    client, _, live = editor
    flow = await create(client)
    live["actions"] = ["read", "project_create"]
    project = await client.post("/api/v1/projects/", json={"name": "move-target"})
    assert project.status_code == 201
    path = f"/api/v1/flows/{flow['id']}"
    body = {"folder_id": project.json()["id"]}
    for actions in (["read", "update"], ["read", "move"]):
        live["actions"] = actions
        assert (await client.patch(path, json=body)).status_code == 403
        assert (await client.get(path)).json()["folder_id"] == flow["folder_id"]
    live["actions"] = ["read", "update", "move"]
    assert (await client.patch(path, json=body)).status_code == 200


@pytest.mark.parametrize("action", ["delete", "export", "publish", "run"])
async def test_unimplemented_actions_are_not_accepted_as_sso_capabilities(editor, action):
    client, _, live = editor
    live["actions"] = ["read", action]
    assert (await client.get("/api/v1/session")).status_code == 503
