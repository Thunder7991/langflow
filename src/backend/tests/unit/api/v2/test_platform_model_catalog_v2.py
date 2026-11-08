"""企业模型选择贯穿刷新、草稿和发布执行; 只替换跨进程模型服务."""
# ruff: noqa: F811 - fixtures are intentionally imported by pytest name.

from copy import deepcopy

import pytest

from tests.unit.api.v1.test_platform_sso_editing import (
    builtin,
    create,
    editor,  # noqa: F401
    isolated_auth_configuration,  # noqa: F401
    refresh_payload,
)
from tests.unit.api.v2.test_platform_models import CONNECTION, model_flow

MODEL = "66666666-6666-4666-8666-666666666666"
CATALOG = {
    "defaultConnectionModelId": MODEL,
    "defaultState": "configured",
    "items": [
        {
            "id": MODEL,
            "connectionId": CONNECTION,
            "connectionName": "企业渠道",
            "providerKey": "openai_compatible",
            "modelName": "company-model",
            "displayName": "企业模型",
        }
    ],
}


@pytest.fixture
def enterprise_gateway(monkeypatch):
    from langflow.services.auth import platform_models

    calls = []

    async def call(action, body, _settings):
        calls.append((action, deepcopy(body)))
        if action == "catalog":
            assert body["catalogVersion"] == 2
            return deepcopy(CATALOG)
        if action in {"references", "sync-references"}:
            return {"registered": True}
        assert action == "invoke"
        assert body["selectionMode"] in {"explicit", "tenant_default"}
        assert "connectionId" not in body
        assert "modelName" not in body
        if body["selectionMode"] == "explicit":
            assert body["connectionModelId"] == MODEL
        else:
            assert "connectionModelId" not in body
        return {"runId": body["runId"], "text": "企业模型回答"}

    monkeypatch.setattr(platform_models, "call", call)
    return calls


async def test_new_model_template_uses_records_instead_of_free_model_names(editor):
    client, _, _ = editor
    fields = (await builtin(client, "PlatformModel"))["template"]
    assert fields["selection_mode"]["value"] == "tenant_default"
    assert set(fields["selection_mode"]["options"]) == {"tenant_default", "explicit"}
    assert "connection_id" not in fields
    assert "model_name" not in fields
    assert not fields["connection_model_id"].get("combobox", False)


async def test_catalog_and_refresh_expose_real_record_labels_without_invocation(editor, enterprise_gateway):
    client, _, _ = editor
    result = await client.get("/api/v2/platform-models")
    assert result.status_code == 200, result.text
    assert result.json() == CATALOG
    flow = await create(client)
    node = await builtin(client, "PlatformModel")
    result = await client.post(
        "/api/v1/custom_component/update",
        params={"flow_id": flow["id"]},
        json=refresh_payload(node, "selection_mode", "explicit"),
    )
    assert result.status_code == 200, result.text
    fields = result.json()["template"]
    assert fields["connection_model_id"]["show"] is True
    assert fields["connection_model_id"]["options"] == [MODEL]
    assert "企业渠道" in fields["connection_model_id"]["options_metadata"][0]["description"]
    assert all(action != "invoke" for action, _ in enterprise_gateway)


async def enterprise_flow(client, mode):
    import json

    from tests.unit.api.v2.test_platform_dry_run import text_flow

    flow = await text_flow(client, "企业问题")
    definition = await builtin(client, "PlatformModel")
    for name, value in {
        "selection_mode": mode,
        "connection_model_id": MODEL if mode == "explicit" else "",
        "temperature": 0,
        "max_tokens": 128,
    }.items():
        definition["template"][name]["value"] = value
    graph = flow["data"]
    graph["nodes"].append(
        {
            "id": "enterprise-model",
            "type": "genericNode",
            "position": {"x": 0, "y": 0},
            "data": {"id": "enterprise-model", "type": "PlatformModel", "node": definition},
        }
    )
    graph["edges"][0].update(
        target="enterprise-model", targetHandle=json.dumps({"id": "enterprise-model", "fieldName": "input_value"})
    )
    graph["edges"].append(
        {
            "id": "model-output",
            "source": "enterprise-model",
            "target": "TextOutput-b5",
            "sourceHandle": json.dumps({"id": "enterprise-model", "name": "text"}),
            "targetHandle": json.dumps({"id": "TextOutput-b5", "fieldName": "input_value"}),
        }
    )
    response = await client.patch(
        f"/api/v1/flows/{flow['id']}", json={"data": graph}, headers={"If-Match": flow["version_token"]}
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("mode", ["explicit", "tenant_default"])
async def test_saved_enterprise_selection_reaches_draft_and_published_execution(editor, enterprise_gateway, mode):
    client, _, live = editor
    live["actions"].extend(["dry_run", "publish", "run"])
    flow = await enterprise_flow(client, mode)
    result = await client.post(
        f"/api/v2/platform-flows/{flow['id']}/dry-run", json={}, headers={"If-Match": flow["version_token"]}
    )
    assert result.status_code == 200, result.text
    assert result.json()["outputs"] == [{"node_id": "TextOutput-b5", "text": "企业模型回答"}]
    published = await client.post(
        f"/api/v2/platform-workflow/flows/{flow['id']}/publish",
        json={"published": True, "publication_token": None},
        headers={"If-Match": flow["version_token"]},
    )
    assert published.status_code == 200, published.text
    result = await client.post(f"/api/v2/platform-workflow/flows/{flow['id']}/run", json={})
    assert result.status_code == 200, result.text
    assert result.json()["outputs"] == [{"node_id": "TextOutput-b5", "text": "企业模型回答"}]
    assert len([action for action, _ in enterprise_gateway if action == "invoke"]) == 2
    reservations = [body for action, body in enterprise_gateway if action == "references"]
    assert reservations, "保存必须先登记模型依赖,防止模型删除竞态"
    assert all("apiKey" not in str(body) for body in reservations)


@pytest.mark.usefixtures("enterprise_gateway")
async def test_default_switch_clears_explicit_record_without_saving_current_default(editor):
    client, _, _ = editor
    flow = await create(client)
    node = await builtin(client, "PlatformModel")
    node["template"]["selection_mode"]["value"] = "explicit"
    node["template"]["connection_model_id"]["value"] = MODEL
    result = await client.post(
        "/api/v1/custom_component/update",
        params={"flow_id": flow["id"]},
        json=refresh_payload(node, "selection_mode", "tenant_default"),
    )
    assert result.status_code == 200, result.text
    assert result.json()["template"]["connection_model_id"]["value"] == ""
    assert result.json()["template"]["connection_model_id"]["show"] is False


@pytest.mark.parametrize("state", ["unset", "unavailable"])
@pytest.mark.usefixtures("enterprise_gateway")
async def test_missing_default_is_reported_without_selecting_first_item(editor, monkeypatch, state):
    from langflow.services.auth import platform_models

    client, _, _ = editor
    flow = await create(client)
    node = await builtin(client, "PlatformModel")

    async def call(action, _body, _settings):
        if action == "sync-references":
            return {"registered": True}
        assert action == "catalog"
        return {**CATALOG, "defaultState": state, "defaultConnectionModelId": None}

    monkeypatch.setattr(platform_models, "call", call)
    result = await client.post(
        "/api/v1/custom_component/update",
        params={"flow_id": flow["id"]},
        json=refresh_payload(node, "selection_mode", "tenant_default"),
    )
    assert result.status_code == 200, result.text
    fields = result.json()["template"]
    assert fields["connection_model_id"]["value"] == ""
    assert (
        "未设置" in fields["selection_mode"]["info"]
        if state == "unset"
        else "不可用" in fields["selection_mode"]["info"]
    )


async def test_mixed_selection_is_rejected_before_any_provider_call(editor, enterprise_gateway):
    client, _, _ = editor
    flow = await enterprise_flow(client, "explicit")
    fields = flow["data"]["nodes"][-1]["data"]["node"]["template"]
    fields["model_name"] = {"value": "injected-model"}
    result = await client.patch(
        f"/api/v1/flows/{flow['id']}", json={"data": flow["data"]}, headers={"If-Match": flow["version_token"]}
    )
    assert result.status_code in {403, 422}, result.text
    assert all(action != "invoke" for action, _ in enterprise_gateway)


async def test_historical_inventory_includes_saved_legacy_model_before_marking_complete(editor, monkeypatch):
    import hashlib
    import json
    from types import SimpleNamespace

    from langflow.services.auth import platform_models

    client, _, _ = editor

    async def save_gateway(action, _body, _settings):
        return (
            {"registered": True}
            if action in {"references", "sync-references"}
            else {"connections": [{"id": CONNECTION}], "models": []}
        )

    monkeypatch.setattr(platform_models, "call", save_gateway)
    flow = await model_flow(client)
    received = []

    async def inventory_gateway(action, body, _settings):
        assert action == "sync-references"
        received.append(body)
        return {"registered": True}

    monkeypatch.setattr(platform_models, "call", inventory_gateway)
    await platform_models.synchronize_references(SimpleNamespace())
    refs = [item for body in received for item in body["references"]]
    digest = hashlib.sha256(
        json.dumps(flow["data"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert {"graphDigest": digest, "connectionId": CONNECTION, "modelName": "company-model"} in refs
    assert received[0].get("begin") is True
    assert received[-1].get("complete") is True
