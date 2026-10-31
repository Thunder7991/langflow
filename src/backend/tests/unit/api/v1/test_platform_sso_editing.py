"""SSO editing through real flow routes, SQLite, owner queries and version checks."""

from uuid import UUID, uuid4

import pytest
from langflow.services.auth import platform_sso
from langflow.services.database.models.flow.model import Flow
from langflow.services.database.models.folder.model import Folder
from langflow.services.deps import session_scope
from sqlmodel import select

ORIGIN = "http://localhost:7860"
GRAPH = {"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}}


@pytest.fixture(autouse=True)
def isolated_auth_configuration(monkeypatch):
    monkeypatch.setenv("LANGFLOW_PLATFORM_SSO_ENABLED", "false")
    monkeypatch.setenv("LANGFLOW_ADD_PROJECTS_TO_MCP_SERVERS", "false")
    monkeypatch.setenv("LANGFLOW_MCP_COMPOSER_ENABLED", "false")
    monkeypatch.setenv("LANGFLOW_SKIP_MCP_AUTO_INIT", "true")


@pytest.fixture
async def editor(client, active_user, monkeypatch):
    settings = platform_sso.PlatformSsoSettings(
        enabled=True,
        platform_url="http://localhost:3000",
        client_id="flow-a",
        client_secret="test-editing-service-secret-with-32-characters",  # noqa: S106
        tenant_id="tenant-a",
        public_url=ORIGIN,
        host_origin="http://localhost:5999",
        secure=False,
    )
    live = {"actions": ["read", "create", "update"], "active": True}

    async def status(action, body, _settings):
        assert action == "status"
        assert body == {"grant": "g" * 43}
        from fastapi import HTTPException

        if not live["active"]:
            raise HTTPException(401, "revoked")
        return {
            "userId": "platform-user-a",
            "username": active_user.username,
            "tenantId": "tenant-a",
            "clientId": "flow-a",
            "sessionId": "session-a",
            "expiresAt": "2099-01-01T00:00:00Z",
            "accessLevel": "viewer",
            "authorizationVersion": 1,
            "resourceScope": "personal",
            "actions": live["actions"],
        }

    monkeypatch.setattr(platform_sso, "platform_call", status)
    app = client._transport.app  # Dedicated test ASGI transport; no live listener or development DB.
    import inspect

    # LifespanManager's outer state wrapper replaces scope.state. Install inside it,
    # at the same layer as production middleware, so request authorization survives.
    real_app = inspect.getclosurevars(app).nonlocals["app"]
    middleware = platform_sso.PlatformSsoMiddleware(real_app, settings)

    async def wrapped(scope, receive, send):
        scope.setdefault("app", real_app)
        return await middleware(scope, receive, send)

    client._transport.app = wrapped
    client.headers["Origin"] = ORIGIN
    client.cookies.set(
        "platform_sso",
        platform_sso.encode_cookie(
            {
                "sub": str(active_user.id),
                "type": "platform_sso",
                "grant": "g" * 43,
                "userId": "platform-user-a",
                "sessionId": "session-a",
                "tenantId": "tenant-a",
                "clientId": "flow-a",
            },
            3600,
        ),
    )
    yield client, active_user, live
    client._transport.app = app


async def create(client):
    response = await client.post("/api/v1/flows/", json={"name": f"b2-{uuid4().hex}", "data": GRAPH})
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_save_reload_and_stale_save_preserves_graph(editor):
    client, actor, _ = editor
    flow = await create(client)
    assert flow["user_id"] == str(actor.id)
    saved = await client.patch(
        f"/api/v1/flows/{flow['id']}",
        json={"name": "saved-name", "data": {**GRAPH, "viewport": {"x": 2}}},
        headers={"If-Match": flow["version_token"]},
    )
    assert saved.status_code == 200, saved.text
    reloaded = await client.get(f"/api/v1/flows/{flow['id']}")
    assert reloaded.json()["name"] == "saved-name"
    assert reloaded.json()["data"]["viewport"] == {"x": 2}
    stale = await client.patch(
        f"/api/v1/flows/{flow['id']}", json={"data": GRAPH}, headers={"If-Match": flow["version_token"]}
    )
    assert stale.status_code == 409
    assert (await client.get(f"/api/v1/flows/{flow['id']}")).json()["data"]["viewport"] == {"x": 2}


async def test_revoked_update_and_missing_precondition_do_not_write(editor):
    client, _, live = editor
    flow = await create(client)
    path = f"/api/v1/flows/{flow['id']}"
    assert (await client.patch(path, json={"data": GRAPH})).status_code == 428
    live["actions"] = ["read", "create"]
    denied = await client.patch(path, json={"name": "not-saved"})
    assert denied.status_code == 403
    assert (await client.get(path)).json()["name"] == flow["name"]
    live["actions"] = ["read", "update"]
    assert (await client.post("/api/v1/flows/", json={"name": "not-created", "data": GRAPH})).status_code == 403
    assert (await client.patch(path, json={"name": "renamed"})).status_code == 200
    live["active"] = False
    assert (await client.patch(path, json={"name": "revoked"})).status_code == 401


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("user_id", "11111111-1111-4111-8111-111111111111"),
        ("mcp_enabled", True),
        ("access_type", "PUBLIC"),
        ("webhook", True),
        ("fs_path", "unwanted.json"),
        ("is_component", True),
        ("a2a_enabled", True),
        ("flow_type", "agent"),
        ("workspace_id", str(uuid4())),
    ],
)
async def test_create_rejects_identity_and_exposure_fields_without_rows(editor, field, value):
    client, _, _ = editor
    name = f"rejected-{uuid4().hex}"
    response = await client.post("/api/v1/flows/", json={"name": name, "data": GRAPH, field: value})
    assert response.status_code in {403, 422}, response.text
    async with session_scope() as session:
        assert (await session.exec(select(Flow).where(Flow.name == name))).first() is None


async def test_foreign_project_rejected_instead_of_falling_back(editor):
    client, actor, _ = editor
    from langflow.services.database.models.user.model import User

    async with session_scope() as session:
        foreign = User(username=f"other-{uuid4().hex}", password="unused", is_active=True)  # noqa: S106
        session.add(foreign)
        await session.flush()
        folder = Folder(name="foreign", user_id=foreign.id)
        session.add(folder)
        await session.flush()
        folder_id = str(folder.id)
    response = await client.post(
        "/api/v1/flows/", json={"name": "foreign-target", "data": GRAPH, "folder_id": folder_id}
    )
    assert response.status_code == 404, response.text
    flow = await create(client)
    response = await client.patch(f"/api/v1/flows/{flow['id']}", json={"folder_id": folder_id})
    assert response.status_code == 404
    async with session_scope() as session:
        assert (await session.get(Flow, UUID(flow["id"]))).user_id == actor.id


async def test_unsupported_side_effects_stay_blocked_and_ui_permissions_match(editor):
    client, _, live = editor
    flow = await create(client)
    for method, path in (
        ("delete", f"/api/v1/flows/{flow['id']}"),
        ("post", "/api/v2/workflows"),
        ("put", f"/api/v1/flows/{flow['id']}"),
        ("post", "/api/v2/files"),
    ):
        assert (await client.request(method, path, json={})).status_code == 403
    live["actions"] = ["read"]
    result = await client.post(
        "/api/v1/authz/me/permissions", json={"resource_type": "flow", "resource_ids": [flow["id"]]}
    )
    assert result.status_code == 200, result.text
    assert result.json()["permissions"][flow["id"]] == ["read"]


async def test_initial_project_create_does_not_register_mcp(editor, monkeypatch):
    client, actor, _ = editor
    from langflow.services.database.models.mcp_server.model import MCPServer
    from langflow.services.deps import get_settings_service

    monkeypatch.setattr(get_settings_service().settings, "add_projects_to_mcp_servers", True)

    async with session_scope() as session:
        before = len((await session.exec(select(MCPServer).where(MCPServer.user_id == actor.id))).all())
    response = await client.post(
        "/api/v1/projects/", json={"name": f"personal-{uuid4().hex}", "parent_id": None, "description": ""}
    )
    assert response.status_code == 201, response.text
    assert response.json()["auth_settings"] is None
    # 即使实例原生MCP自动注册配置开启, 平台初始化也不得产生注册副作用。
    async with session_scope() as session:
        assert len((await session.exec(select(MCPServer).where(MCPServer.user_id == actor.id))).all()) == before


async def test_builtin_node_create_edit_and_reload(editor):
    import copy

    client, _, _ = editor
    catalog = await client.get("/api/v1/all")
    assert catalog.status_code == 200, catalog.text[:200]
    template = next(
        group["ChatInput"] for group in catalog.json().values() if isinstance(group, dict) and "ChatInput" in group
    )
    node = {
        "id": "ChatInput-b2",
        "type": "genericNode",
        "position": {"x": 0, "y": 0},
        "data": {"id": "ChatInput-b2", "type": "ChatInput", "node": copy.deepcopy(template)},
    }
    graph = {**GRAPH, "nodes": [node]}
    created = await client.post("/api/v1/flows/", json={"name": "builtin-b2", "data": graph, "mcp_enabled": False})
    assert created.status_code == 201, created.text[:300]
    flow = created.json()
    graph["nodes"][0]["data"]["node"]["template"]["input_value"]["value"] = "B2 saved text"
    result = await client.patch(
        f"/api/v1/flows/{flow['id']}", json={"data": graph}, headers={"If-Match": flow["version_token"]}
    )
    assert result.status_code == 200, result.text[:300]
    restored = (await client.get(f"/api/v1/flows/{flow['id']}")).json()
    assert restored["data"]["nodes"][0]["data"]["node"]["template"]["input_value"]["value"] == "B2 saved text"


async def test_create_persists_trusted_code_in_the_handler_body(editor, monkeypatch):
    import copy

    from langflow.api.v1 import custom_component_policy

    client, _, _ = editor
    catalog = (await client.get("/api/v1/all")).json()
    template = copy.deepcopy(
        next(group["ChatInput"] for group in catalog.values() if isinstance(group, dict) and "ChatInput" in group)
    )
    trusted = template["template"]["code"]["value"]
    # 模拟截断hash匹配但字节不同的注册表边界, 检查最终持久化的body实例。
    template["template"]["code"]["value"] = trusted + "\n# simulated collision"
    monkeypatch.setattr(custom_component_policy, "code_hash_matches_any_template", lambda *_args: True)
    monkeypatch.setattr(custom_component_policy, "get_trusted_code_for_validation", lambda _code: trusted)
    graph = {**GRAPH, "nodes": [{"id": "trusted-node", "data": {"type": "ChatInput", "node": template}}]}
    result = await client.post("/api/v1/flows/", json={"name": "trusted-copy", "data": graph})
    assert result.status_code == 201, result.text[:300]
    flow = (await client.get(f"/api/v1/flows/{result.json()['id']}")).json()
    assert flow["data"]["nodes"][0]["data"]["node"]["template"]["code"]["value"] == trusted


@pytest.mark.parametrize(
    "fields",
    [{"auth_settings": {"auth_type": "apikey"}}, {"flows_list": [str(uuid4())]}, {"components_list": [str(uuid4())]}],
)
async def test_project_side_effect_payloads_are_rejected(editor, fields):
    client, _, _ = editor
    name = f"rejected-project-{uuid4().hex}"
    response = await client.post("/api/v1/projects/", json={"name": name, **fields})
    assert response.status_code == 403
    async with session_scope() as session:
        assert (await session.exec(select(Folder).where(Folder.name == name))).first() is None


async def test_personal_reads_cannot_be_widened_by_an_authorization_plugin(editor, monkeypatch):
    from langflow.api.v1 import flows, projects
    from langflow.services.deps import get_authorization_service

    client, _, _ = editor
    foreign_owner = uuid4()
    async with session_scope() as session:
        folder = Folder(name=f"other-{uuid4().hex}", user_id=foreign_owner)
        session.add(folder)
        await session.flush()
        flow = Flow(name="foreign", user_id=foreign_owner, folder_id=folder.id, data=GRAPH)
        session.add(flow)
        await session.flush()
        folder_id, flow_id = str(folder.id), str(flow.id)

    async def enabled(*_args, **_kwargs):
        return True

    async def broad_scope(*_args, **_kwargs):
        return object()

    authz = get_authorization_service()
    monkeypatch.setattr(authz, "supports_cross_user_fetch", enabled)
    monkeypatch.setattr(authz, "is_enabled", enabled)
    for module in (flows, projects):
        monkeypatch.setattr(module, "visible_scope_prefilter", broad_scope)
        monkeypatch.setattr(module, "restrict_to_owned_or_visible_scope", lambda stmt, **_kwargs: stmt)
    assert (await client.get(f"/api/v1/flows/{flow_id}")).status_code == 404
    assert (await client.patch(f"/api/v1/flows/{flow_id}", json={"name": "stolen"})).status_code == 404
    assert (await client.get(f"/api/v1/projects/{folder_id}")).status_code == 404
    assert flow_id not in [row["id"] for row in (await client.get("/api/v1/flows/")).json()]
    assert folder_id not in [row["id"] for row in (await client.get("/api/v1/projects/")).json()]


@pytest.mark.parametrize("actions", [[{}], ["read", "delete"], [], None])
async def test_invalid_platform_actions_fail_closed(editor, actions):
    client, _, live = editor
    live["actions"] = actions
    assert (await client.get("/api/v1/flows/")).status_code == 503


@pytest.mark.parametrize(
    "template",
    [
        {"api_key": {"value": "not-a-real-secret"}},
        {"credential": {"password": True, "value": "not-a-real-secret"}},
        {"credential": {"load_from_db": True, "value": "OTHER_PROJECT_SECRET"}},
        {"file": {"type": "file", "value": ["other-user/file.txt"]}},
        {"code": {"value": "print('untrusted')"}},
        {"nested": [{"type": "MCPTools"}]},
    ],
)
async def test_graph_boundaries_reject_before_persisting(editor, template):
    client, _, _ = editor
    flow = await create(client)
    graph = {**GRAPH, "nodes": [{"id": "node", "data": {"node": {"template": template}}}]}
    assert (await client.post("/api/v1/flows/", json={"name": "unsafe", "data": graph})).status_code in {403, 503}
    assert (
        await client.patch(
            f"/api/v1/flows/{flow['id']}", json={"data": graph}, headers={"If-Match": flow["version_token"]}
        )
    ).status_code in {403, 503}
    assert (await client.get(f"/api/v1/flows/{flow['id']}")).json()["data"] == GRAPH
