"""Platform model bridge: real SSO/save/Graph, fake external Nest transport only."""
# ruff: noqa: F811 - imported pytest fixtures are injected by name.

import json
from uuid import UUID

import pytest
from fastapi import HTTPException
from langflow.services.database.models.flow.model import Flow
from langflow.services.deps import session_scope

from tests.unit.api.v1.test_platform_sso_editing import builtin, editor, isolated_auth_configuration  # noqa: F401
from tests.unit.api.v2.test_platform_dry_run import text_flow

CONNECTION = "33333333-3333-4333-8333-333333333333"


async def test_platform_node_is_available_without_exposing_any_service_credential(editor):
    client, _, _ = editor
    catalog = (await client.get("/api/v1/all")).json()
    assert "PlatformModel" in catalog.get("platform", {}), "Platform model node is missing"
    source = catalog["platform"]["PlatformModel"]["template"]["code"]["value"]
    assert "test-editing-service-secret" not in source
    assert "api_key" not in catalog["platform"]["PlatformModel"]["template"]


async def model_flow(client):
    flow = await text_flow(client, "saved question")
    from langflow.services.auth.platform_models import template

    definition = template(legacy=True)
    for name, value in {
        "connection_id": CONNECTION,
        "model_name": "company-model",
        "temperature": 0,
        "max_tokens": 128,
    }.items():
        definition["template"][name]["value"] = value
    data = flow["data"]
    data["nodes"].append(
        {
            "id": "model-b5",
            "type": "genericNode",
            "position": {"x": 0, "y": 0},
            "data": {"id": "model-b5", "type": "PlatformModel", "node": definition},
        }
    )
    data["edges"][0].update(target="model-b5", targetHandle=json.dumps({"id": "model-b5", "fieldName": "input_value"}))
    data["edges"].append(
        {
            "id": "model-edge",
            "source": "model-b5",
            "target": "TextOutput-b5",
            "sourceHandle": json.dumps({"id": "model-b5", "name": "text"}),
            "targetHandle": json.dumps({"id": "TextOutput-b5", "fieldName": "input_value"}),
        }
    )
    response = await client.patch(
        f"/api/v1/flows/{flow['id']}", json={"data": data}, headers={"If-Match": flow["version_token"]}
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def gateway(monkeypatch):
    from langflow.services.auth import platform_models

    calls = []

    async def call(action, body, _settings):
        if action == "sync-references":
            return {"registered": True}
        assert body["grant"] == "g" * 43
        calls.append((action, body))
        if action == "catalog":
            if body.get("catalogVersion") == 2:
                return {"defaultConnectionModelId": None, "defaultState": "unset", "items": []}
            return {
                "connections": [{"id": CONNECTION, "name": "我的模型", "provider": "openai_compatible"}],
                "models": [],
            }
        if action == "references":
            return {"registered": True}
        assert action == "invoke"
        assert body["connectionId"] == CONNECTION
        assert body["input"] == "saved question"
        assert body["modelName"] == "company-model"
        assert set(body) == {
            "grant",
            "connectionId",
            "modelName",
            "input",
            "flowId",
            "versionToken",
            "runId",
            "executionId",
            "temperature",
            "maxTokens",
        }
        return {"runId": body["runId"], "text": "受控平台回答"}

    monkeypatch.setattr(platform_models, "call", call)
    return calls


@pytest.mark.parametrize("canvas_handles", [False, True])
async def test_saved_model_graph_executes_via_platform_and_preserves_version(editor, gateway, canvas_handles):
    client, _, live = editor
    live["actions"].append("dry_run")
    flow = await model_flow(client)
    if canvas_handles:
        for edge in flow["data"]["edges"]:
            for key in ("sourceHandle", "targetHandle"):
                edge[key] = edge[key].replace('"', "œ")
        saved = await client.patch(
            f"/api/v1/flows/{flow['id']}", json={"data": flow["data"]}, headers={"If-Match": flow["version_token"]}
        )
        assert saved.status_code == 200, saved.text
        flow = saved.json()
    result = await client.post(
        f"/api/v2/platform-flows/{flow['id']}/dry-run", json={}, headers={"If-Match": flow["version_token"]}
    )
    assert result.status_code == 200, result.text
    assert result.json()["outputs"] == [{"node_id": "TextOutput-b5", "text": "受控平台回答"}]
    assert len([action for action, _ in gateway if action == "invoke"]) == 1
    invocation = next(body for action, body in gateway if action == "invoke")
    assert invocation["executionId"] == result.json()["run_id"]
    assert invocation["runId"] != result.json()["run_id"]
    restored = (await client.get(f"/api/v1/flows/{flow['id']}")).json()
    assert restored["data"] == flow["data"]
    assert restored["version_token"] == flow["version_token"]


async def test_model_catalog_permission_and_refresh_do_not_execute_model(editor, gateway):
    from tests.unit.api.v1.test_platform_sso_editing import create, refresh_payload

    client, _, live = editor
    # 目录只需要工作流读取与平台 Secret 元数据权限, 不因编辑节点而隐式开放调试。
    assert (await client.get("/api/v2/platform-models")).status_code == 200
    live["actions"].append("dry_run")
    result = await client.get("/api/v2/platform-models")
    assert result.status_code == 200, result.text
    flow = await create(client)
    from langflow.services.auth.platform_models import template

    node = template(legacy=True)
    refreshed = await client.post(
        "/api/v1/custom_component/update",
        params={"flow_id": flow["id"]},
        json=refresh_payload(node, "connection_id", CONNECTION),
    )
    assert refreshed.status_code == 200, refreshed.text
    assert CONNECTION in refreshed.json()["template"]["connection_id"]["options"]
    assert all(action != "invoke" for action, _ in gateway)


async def test_model_runtime_expiry_uses_the_existing_host_login_protocol(editor, gateway, monkeypatch):
    from langflow.services.auth import platform_models

    client, _, live = editor
    live["actions"].append("dry_run")
    flow = await model_flow(client)

    async def expired(*_args):
        live["active"] = False
        raise HTTPException(401, "expired")

    monkeypatch.setattr(platform_models, "call", expired)
    result = await client.post(
        f"/api/v2/platform-flows/{flow['id']}/dry-run", json={}, headers={"If-Match": flow["version_token"]}
    )
    assert result.status_code == 401
    assert result.json()["code"] == "PLATFORM_SSO_REQUIRED"
    assert result.headers["x-workflow-actions"] == ""
    assert all(action != "invoke" for action, _ in gateway)


@pytest.mark.parametrize("attack", ["code", "edge", "credentials", "default", "tokens"])
async def test_unsafe_saved_model_graph_never_reaches_gateway(editor, gateway, attack):
    client, _, live = editor
    live["actions"].append("dry_run")
    flow = await model_flow(client)
    data = flow["data"]
    template = data["nodes"][2]["data"]["node"]["template"]
    if attack == "code":
        template["code"]["value"] = "raise RuntimeError('forged')"
    elif attack == "edge":
        data["edges"][1]["target"] = "model-b5"
    elif attack == "credentials":
        template["api_key"] = {"value": "forged"}
    elif attack == "default":
        template["connection_id"]["value"] = "platform_default"
    else:
        template["max_tokens"]["value"] = 100000
    async with session_scope() as session:
        row = await session.get(Flow, UUID(flow["id"]))
        row.data = data
        session.add(row)
    result = await client.post(
        f"/api/v2/platform-flows/{flow['id']}/dry-run", json={}, headers={"If-Match": flow["version_token"]}
    )
    assert result.status_code in {403, 422}, result.text
    assert all(action != "invoke" for action, _ in gateway)


async def test_platform_denial_is_not_mistaken_for_login_failure_or_success(editor, gateway, monkeypatch):
    from langflow.services.auth import platform_models

    client, _, live = editor
    live["actions"].append("dry_run")
    flow = await model_flow(client)

    async def denied(*_args):
        raise HTTPException(403, "没有模型使用权限")

    monkeypatch.setattr(platform_models, "call", denied)
    result = await client.post(
        f"/api/v2/platform-flows/{flow['id']}/dry-run", json={}, headers={"If-Match": flow["version_token"]}
    )
    assert result.status_code == 403, result.text
    assert "受控平台回答" not in result.text
    assert all(action != "invoke" for action, _ in gateway)


async def test_concurrent_graphs_do_not_share_model_invocation_context():
    import asyncio

    from langflow.services.auth.platform_dry_run import execute_model_chain

    async def execute(text):
        async def invoke(**parameters):
            await asyncio.sleep(0)
            assert parameters["input"] == text
            return {"text": f"answer:{text}"}

        return await execute_model_chain(
            "input",
            "output",
            text,
            "model",
            {"connection_id": CONNECTION, "model_name": "company-model", "temperature": 0, "max_tokens": 128},
            invoke=invoke,
            flow_id=f"flow-{text}",
            user_id=f"user-{text}",
            run_id=f"run-{text}",
        )

    assert await asyncio.gather(execute("first"), execute("second")) == ["answer:first", "answer:second"]


@pytest.mark.parametrize("failure", ["foreign", "invalid", "expired"])
async def test_model_reference_save_failures_preserve_the_saved_graph(editor, gateway, monkeypatch, failure):
    from copy import deepcopy

    from langflow.services.auth import platform_models

    client, _, live = editor
    flow = await model_flow(client)  # 编辑与模型目录权限不隐式依赖 dry_run。
    data = deepcopy(flow["data"])
    fields = data["nodes"][2]["data"]["node"]["template"]
    fields["model_name"]["value"] = "changed draft"
    if failure == "foreign":
        fields["model_name"]["value"] = "changed-draft"
        fields["connection_id"]["value"] = "55555555-5555-4555-8555-555555555555"
        expected = 404
    elif failure == "invalid":
        expected = 422
    else:
        fields["model_name"]["value"] = "changed-draft"

        async def expired(*_args):
            live["active"] = False
            raise HTTPException(401, "expired")

        monkeypatch.setattr(platform_models, "call", expired)
        expected = 401
    result = await client.patch(
        f"/api/v1/flows/{flow['id']}", json={"data": data}, headers={"If-Match": flow["version_token"]}
    )
    assert result.status_code == expected, result.text
    if failure == "expired":
        assert result.json()["code"] == "PLATFORM_SSO_REQUIRED"
        assert result.headers["x-workflow-actions"] == ""
    async with session_scope() as session:
        row = await session.get(Flow, UUID(flow["id"]))
        assert row.data == flow["data"]
        assert str(row.version_token) == flow["version_token"]
    assert all(action != "invoke" for action, _ in gateway)


@pytest.mark.parametrize("blocked", ["PlatformModel", "platform.PlatformModel", "PlatformModelComponent"])
async def test_operator_catalog_block_also_refuses_platform_model_source(editor, blocked):
    from types import SimpleNamespace

    from langflow.api.v1.custom_component_policy import resolve_component_code_for_action
    from langflow.services.auth.platform_models import source
    from lfx.services.catalog_policy.base import CatalogPolicySnapshot

    client, _, _ = editor
    await client.get("/api/v1/all")
    with pytest.raises(HTTPException) as failure:
        resolve_component_code_for_action(
            source(),
            user=SimpleNamespace(is_superuser=False),
            settings=SimpleNamespace(allow_custom_components=False),
            snapshot=CatalogPolicySnapshot(blocked_component_keys={blocked}),
            disabled_detail="disabled",
            admin_only_detail="admin",
        )
    assert failure.value.status_code == 403


@pytest.mark.parametrize(
    ("response_status", "payload", "expected"),
    [
        (200, {"statusCode": 200, "data": {"connections": [], "models": []}}, 200),
        (403, {"message": "private-provider-material"}, 403),
        (401, {}, 401),
        (409, {}, 409),
        (500, {"message": "private-provider-material"}, 502),
        (302, {}, 502),
        (200, [], 502),
    ],
)
async def test_internal_transport_authenticates_and_sanitizes_errors(monkeypatch, response_status, payload, expected):
    from types import SimpleNamespace

    import httpx
    from langflow.services.auth import platform_models
    from pydantic import SecretStr

    def transport(request):
        assert request.url == "https://platform.example/api/internal/workflow/models/catalog"
        assert request.headers["x-workflow-client"] == "flow-a"
        assert request.headers["x-workflow-secret"] == "test-service-secret"
        assert json.loads(request.content) == {"grant": "g" * 43}
        return httpx.Response(response_status, json=payload)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        platform_models.httpx,
        "AsyncClient",
        lambda **options: client(**options, transport=httpx.MockTransport(transport)),
    )
    settings = SimpleNamespace(
        platform_url="https://platform.example",
        client_id="flow-a",
        client_secret=SecretStr("test-service-secret"),
        ca_bundle=None,
    )
    if expected == 200:
        assert await platform_models.call("catalog", {"grant": "g" * 43}, settings) == {"connections": [], "models": []}
    else:
        with pytest.raises(HTTPException) as failure:
            await platform_models.call("catalog", {"grant": "g" * 43}, settings)
        assert failure.value.status_code == expected
        assert "private-provider-material" not in str(failure.value.detail)
